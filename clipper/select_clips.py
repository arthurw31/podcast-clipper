"""Sélection des meilleurs extraits par le LLM, puis calage sur les mots.

Sortie : clips.json  (éditable à la main, puis `python -m clipper build …`)
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rich.console import Console

from .config import Brand
from .llm import ask_json
from .transcribe import all_words, is_terminal, sentences, to_timed_text

console = Console()

SYSTEM_PROMPT = """Tu es un monteur vidéo senior spécialisé dans les formats courts (TikTok, Reels, Shorts, LinkedIn)
tirés de podcasts. Tu reçois la transcription horodatée d'un épisode et les consignes éditoriales de la marque.
Tu dois choisir les extraits les plus forts et préparer tout ce qu'il faut pour les monter automatiquement.

La transcription est fournie PHRASE PAR PHRASE, chaque ligne avec son horodatage `[début → fin]`.

Règles absolues :
- Un extrait = une suite de phrases ENTIÈRES : `start` est le « début » de la première phrase retenue,
  `end` est la « fin » (le temps après la flèche →) de la dernière phrase retenue. Jamais le début d'une phrase
  comme `end`, jamais une coupe au milieu d'une phrase ou d'une idée.
- La PENSÉE doit être complète : l'argument démarre, se développe et se conclut. Si la conclusion de l'idée
  arrive 5 à 10 secondes après la durée max, inclus-la quand même : mieux vaut un extrait un peu plus long
  qu'une idée tronquée. Ne retiens pas un passage dont la conclusion n'existe pas.
- `start_text` = les 5 à 8 PREMIERS mots exacts de l'extrait, `end_text` = les 5 à 8 DERNIERS mots exacts
  (copiés tels quels depuis la transcription). Ces mots font foi pour le découpage : `end_text` doit être la fin
  d'une phrase complète qui conclut l'idée — jamais un mot de liaison (« et », « que », « donc », « mais »…).
- Chaque extrait doit être compréhensible seul, avec une accroche dès les premiers mots.
- MONTAGE MULTI-SEGMENTS (si `max_segments` > 1) : un clip peut assembler 2 à `max_segments` passages pris à des
  endroits DIFFÉRENTS de l'épisode, dans l'ordre de ton choix, autour d'un même thème : d'abord une accroche
  (souvent une phrase choc, une question ou une position tranchée), puis les passages qui développent et concluent.
  Chaque segment respecte les règles ci-dessus (phrases entières, pensée complète, `start_text`/`end_text`).
  Les segments doivent s'enchaîner naturellement à l'oreille (pas de « comme je disais » qui renvoie à un passage absent).
  Renseigne `segments` (liste ordonnée) ; `start`/`end` du clip = ceux du premier et du dernier segment.
  Si un seul passage suffit, `segments` contient un seul élément.
- Respecte les durées demandées (min/max) sur la DURÉE TOTALE du clip, avec la tolérance ci-dessus pour finir une idée.
- Les extraits ne se chevauchent pas (un même passage ne sert qu'à un seul clip).
- `keywords` : mots EXACTS tels qu'ils apparaissent dans la transcription (même orthographe), à surligner.
  Uniquement des mots pleins (chiffres, noms, verbes forts). 1 à 2 par phrase maximum.
- `broll` : uniquement si les consignes l'autorisent et seulement sur une image mentale concrète.
  `at` = timestamp absolu (secondes) du mot qui déclenche l'image ; `query` en ANGLAIS, 2 à 4 mots, très concret.
- `turns` : tours de parole DANS l'extrait : liste des changements de locuteur, chacun {"at": secondes absolues, "speaker": "host" | "guest"}.
  Le premier élément commence à `start`. L'animateur (host) pose les questions et relance ; l'invité (guest) raconte.
- `hook_title` : titre de 4 à 8 mots, percutant, sans point final, pour un éventuel bandeau.
- `post` : texte de publication prêt à poster (2-3 lignes + hashtags), dans la langue du podcast.
- Réponds UNIQUEMENT avec un objet JSON valide, sans commentaire, de la forme :
{
  "clips": [
    {
      "title": "…", "hook_title": "…",
      "start": 123.4, "end": 165.2,
      "start_text": "les premiers mots exacts de l'extrait", "end_text": "les derniers mots exacts de l'extrait.",
      "segments": [
        {"start": 123.4, "end": 141.0, "start_text": "…", "end_text": "…", "role": "accroche"},
        {"start": 800.2, "end": 824.5, "start_text": "…", "end_text": "…", "role": "développement"}
      ],
      "why": "…", "score": 8.5,
      "keywords": ["…"],
      "turns": [{"at": 123.4, "speaker": "host"}, {"at": 131.0, "speaker": "guest"}],
      "broll": [{"at": 140.2, "duration": 2.5, "query": "…", "type": "video"}],
      "post": "…"
    }
  ]
}"""


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s)


def _snap(words: list[dict], start: float, end: float, pad_in: float = 0.12, pad_out: float = 0.6) -> tuple[float, float]:
    """Cale start/end sur les mots réels, avec une marge bornée par le silence disponible
    (on n'attrape jamais le mot précédent ni le début de la phrase suivante)."""
    inside = [w for w in words if w["e"] > start and w["s"] < end]
    if not inside:
        return start, end
    first, last = inside[0], inside[-1]
    prev = [w for w in words if w["e"] <= first["s"]]
    nxt = [w for w in words if w["s"] >= last["e"]]
    s = first["s"] - pad_in
    if prev:
        s = max(s, prev[-1]["e"] + 0.03)
    s = max(0.0, min(s, first["s"]))
    e = last["e"] + pad_out
    if nxt:
        e = min(e, nxt[0]["s"] - 0.05)
    e = max(e, last["e"] + 0.12)
    return round(s, 3), round(e, 3)


def _find_phrase(words: list[dict], phrase: str, around: float, window: float = 25.0) -> tuple[int, int] | None:
    """Cherche la suite de mots `phrase` dans le transcript, près de `around` (±window s).

    Retourne (index_premier_mot, index_dernier_mot) ou None. Tolère la ponctuation/accents ; accepte une
    correspondance partielle (≥ 3 mots consécutifs en fin ou début de phrase citée) si la citation exacte échoue.
    """
    target = [_norm(w) for w in re.split(r"\s+", phrase.strip()) if _norm(w)]
    if len(target) < 2:
        return None
    idx = [i for i, w in enumerate(words) if abs(w["s"] - around) <= window]
    if not idx:
        return None
    lo, hi = idx[0], idx[-1]
    norm = [_norm(w["w"]) for w in words]

    def search(seq: list[str]) -> tuple[int, int] | None:
        L = len(seq)
        best = None
        for i in range(lo, hi + 1):
            if norm[i:i + L] == seq:
                cand = (i, i + L - 1)
                # le plus proche de `around`
                if best is None or abs(words[i]["s"] - around) < abs(words[best[0]]["s"] - around):
                    best = cand
        return best

    hit = search(target)
    if hit:
        return hit
    # correspondance partielle : fin de la citation (pour end_text) puis début (pour start_text)
    for L in range(min(len(target) - 1, 6), 2, -1):
        hit = search(target[-L:]) or search(target[:L])
        if hit:
            return hit
    return None


def snap_to_quotes(words: list[dict], sents: list[dict], start: float, end: float, start_text: str, end_text: str,
                   max_duration: float, tolerance: float = 12.0) -> tuple[float, float, str]:
    """Cale l'extrait sur les mots cités par le LLM (start_text / end_text).

    Si une citation est introuvable, on retombe sur snap_to_sentences pour cette borne.
    Un `end_text` qui finirait sur un connecteur est prolongé jusqu'à la prochaine fin de phrase.
    """
    from .transcribe import _is_connector

    notes = []
    s_hit = _find_phrase(words, start_text or "", start) if start_text else None
    e_hit = _find_phrase(words, end_text or "", end) if end_text else None
    # citation absente autour du timestamp : le modèle s'est trompé de chiffre, pas de mots -> recherche globale
    if start_text and not s_hit:
        g = _find_phrase(words, start_text, start, window=1e9)
        if g:
            s_hit = g
            notes.append(f"début : citation trouvée ailleurs ({words[g[0]]['s']:.0f}s au lieu de {start:.0f}s)")
    if end_text and not e_hit and s_hit:
        anchor = words[s_hit[0]]["s"]
        g = _find_phrase(words, end_text, anchor + (end - start), window=max_duration + tolerance)
        if g and words[g[1]]["e"] > anchor:
            e_hit = g
            notes.append(f"fin : citation trouvée ailleurs ({words[g[1]]['e']:.0f}s)")
    if s_hit and e_hit and words[e_hit[1]]["e"] <= words[s_hit[0]]["s"]:
        e_hit = None
    if s_hit and (abs(words[s_hit[0]]["s"] - start) > 30):
        # on recale aussi la borne de secours sur la citation
        start = words[s_hit[0]]["s"]
        end = words[e_hit[1]]["e"] if e_hit else start + max(3.0, min(max_duration, end - start if end > start else 10.0))
    fb_s, fb_e, fb_note = snap_to_sentences(sents, start, end, max_duration, tolerance)
    new_start = words[s_hit[0]]["s"] if s_hit else fb_s
    new_end = words[e_hit[1]]["e"] if e_hit else fb_e
    if not s_hit:
        notes.append("début : citation introuvable → phrases")
    if not e_hit:
        notes.append("fin : citation introuvable → phrases")
    elif _is_connector(words[e_hit[1]]["w"]):
        # le modèle a coupé sur un mot de liaison : on finit la phrase
        _, ext, _ = snap_to_sentences(sents, new_start, new_end + 0.01, max_duration, tolerance)
        notes.append(f"fin sur un connecteur « {words[e_hit[1]]['w']} » → prolongée {new_end:.1f}→{ext:.1f}")
        new_end = ext
    if new_end - new_start > max_duration + tolerance:
        notes.append(f"⚠ extrait long ({new_end - new_start:.0f}s)")
    if abs(new_start - start) > 0.5 or abs(new_end - end) > 0.5:
        notes.append(f"recalé {start:.1f}-{end:.1f} → {new_start:.1f}-{new_end:.1f}")
    return round(new_start, 3), round(new_end, 3), " ; ".join(notes)


def snap_to_sentences(sents: list[dict], start: float, end: float, max_duration: float,
                      tolerance: float = 12.0, back_limit: float = 10.0, hard_lookahead: float = 6.0) -> tuple[float, float, str]:
    """Force un extrait à commencer au début d'une phrase et à finir sur une fin de phrase.

    - début : phrase contenant `start` ; on remonte tant que la phrase précédente n'est pas une fin (≤ back_limit)
    - fin   : phrase contenant `end` ; si elle finit sur une coupe technique on avance jusqu'à une fin ;
              si elle finit sur une pause ("soft") et qu'un point ("hard") arrive dans les `hard_lookahead` s, on
              prolonge jusqu'au point ; limite : max_duration + tolerance, sinon on recule.
    Retourne (start, end, note).
    """
    if not sents:
        return start, end, ""
    n = len(sents)
    note = []
    kind = lambda i: sents[i].get("terminal", "none")  # noqa: E731

    s_idx = n - 1
    for i, p in enumerate(sents):
        if p["start"] <= start < p["end"]:
            frac = (start - p["start"]) / max(p["end"] - p["start"], 0.01)
            s_idx = i if frac <= 0.4 else min(i + 1, n - 1)
            break
        if start < p["start"]:
            s_idx = i
            break
    j = s_idx
    while j > 0 and kind(j - 1) == "none" and sents[s_idx]["start"] - sents[j - 1]["start"] <= back_limit:
        j -= 1
    s_idx = j
    new_start = sents[s_idx]["start"]
    if abs(new_start - start) > 0.3:
        note.append(f"début recalé {start:.1f}→{new_start:.1f}")

    e_idx = n - 1
    for i, p in enumerate(sents):
        if p["start"] < end <= p["end"] + 0.05:
            e_idx = i
            break
        if end <= p["start"]:
            e_idx = max(i - 1, s_idx)
            break
    e_idx = max(e_idx, s_idx)
    limit = max_duration + tolerance
    at_boundary = abs(end - sents[e_idx]["end"]) < 0.6

    def next_of(kinds: tuple, frm: int, max_gap: float | None = None) -> int | None:
        k = frm
        while k + 1 < n:
            k += 1
            if sents[k]["end"] - new_start > limit or (max_gap is not None and sents[k]["end"] - sents[frm]["end"] > max_gap):
                return None
            if kind(k) in kinds:
                return k
        return None

    def prev_of(kinds: tuple, frm: int) -> int | None:
        k = frm
        while k > s_idx:
            k -= 1
            if kind(k) in kinds:
                return k
        return None

    j = e_idx
    if at_boundary and kind(j) == "hard":
        pass                                   # choix du LLM sur une vraie fin : on garde
    elif at_boundary and kind(j) == "soft":
        h = next_of(("hard",), j, max_gap=hard_lookahead)
        if h is not None:                       # un point arrive juste après : on finit la phrase
            j = h
    else:
        # fin au milieu d'une phrase (ou coupe technique) : prochaine vraie fin, sinon pause, sinon on recule
        cand = next_of(("hard",), j)
        if cand is None:
            cand = next_of(("soft",), j)
        if cand is None:
            cand = prev_of(("hard",), j + 1) or prev_of(("soft",), j + 1)
        if cand is not None:
            j = cand
    while j > s_idx and sents[j]["end"] - new_start > limit:
        j = prev_of(("hard", "soft"), j) or s_idx
    new_end = sents[j]["end"]
    if abs(new_end - end) > 0.3:
        note.append(f"fin recalée {end:.1f}→{new_end:.1f}")
    if kind(j) == "none":
        note.append("⚠ aucune fin de phrase trouvée à proximité")
    return new_start, new_end, " ; ".join(note)


JURY_PROMPT = """Tu es rédacteur en chef d'un podcast. Plusieurs monteurs ont chacun proposé, selon un angle éditorial
différent, des extraits candidats pour les clips d'un épisode. Tu reçois tous les candidats (angle, titre, raison,
score du monteur, durée, transcription) et tu dois composer la sélection finale.

Critères : force de l'accroche, idée complète et compréhensible seule, concret (chiffre, cas, décision), variété des
thèmes (jamais deux clips sur la même idée — garde le meilleur des deux), respect des consignes de la marque.
Ordonne du plus fort au plus faible. Tu peux reformuler `title` et `hook_title` mais pas les timecodes.
Réponds UNIQUEMENT avec un objet JSON :
{"selected": [{"id": 3, "score": 9.2, "title": "…", "hook_title": "…", "why": "…"}]}"""


def _overlap(a: dict, b: dict) -> float:
    """Part (0-1) du plus court des deux clips couverte par l'autre (sur leurs segments)."""
    inter = 0.0
    for x in a["segments"]:
        for y in b["segments"]:
            inter += max(0.0, min(x["end"], y["end"]) - max(x["start"], y["start"]))
    return inter / max(0.1, min(a["duration"], b["duration"]))


def _dedupe(clips: list[dict], max_overlap: float = 0.4) -> list[dict]:
    """Garde le mieux noté quand deux candidats (d'angles différents) reprennent le même passage."""
    kept: list[dict] = []
    for c in sorted(clips, key=lambda c: -float(c.get("score") or 0)):
        if all(_overlap(c, k) < max_overlap for k in kept):
            kept.append(c)
        else:
            console.print(f"  [dim]candidat écarté (doublon) : {c.get('title', '')}[/dim]")
    return kept


def _jury(clips: list[dict], n: int, transcript: dict, brand: Brand, guest: str, company: str) -> list[dict]:
    """Agrégation : un appel court (sans la transcription complète) qui choisit et ordonne les n finalistes."""
    words = all_words(transcript)
    cands = []
    for c in clips:
        text = " […] ".join(" ".join(w["w"] for w in words if w["s"] >= sg["start"] - 0.05 and w["e"] <= sg["end"] + 0.05)
                            for sg in c["segments"])
        cands.append(f"""### Candidat {c['index']} — angle : {c.get('angle', '')}
- titre : {c.get('title', '')}
- accroche : {c.get('hook_title', '')}
- raison : {c.get('why', '')}
- score monteur : {c.get('score', '')}
- durée : {c['duration']:.0f}s ({len(c['segments'])} segment(s))
- transcription : {text}
""")
    user = f"""## Consignes éditoriales de la marque « {brand.cfg.name} »
{brand.guidelines or "(aucune)"}

## Épisode
Invité : {guest or "inconnu"} · Entreprise : {company or "inconnue"}

## Demande
Choisis les {n} meilleurs candidats (variés, sans doublon de thème), ordonnés du plus fort au plus faible.

## Candidats
{chr(10).join(cands)}
"""
    sel = brand.cfg.selection
    data = ask_json(JURY_PROMPT, user, model=sel.llm_model, backend=sel.llm_backend, max_tokens=4000)
    by_id = {c["index"]: c for c in clips}
    final = []
    for pick in data.get("selected", []) or []:
        try:
            c = by_id.pop(int(pick["id"]))
        except (KeyError, TypeError, ValueError):
            continue
        for k in ("title", "hook_title", "why"):
            if pick.get(k):
                c[k] = str(pick[k])
        if pick.get("score") is not None:
            c["score"] = pick["score"]
        final.append(c)
        if len(final) >= n:
            break
    if not final:  # réponse inexploitable : repli sur les scores des monteurs
        return sorted(clips, key=lambda c: -float(c.get("score") or 0))[:n]
    return final


def select_clips(transcript: dict, brand: Brand, out_json: Path, n_clips: int | None = None,
                 guest: str = "", company: str = "", force: bool = False, extra_instructions: str = "",
                 host_side: str = "") -> dict:
    if out_json.exists() and not force:
        console.print(f"[dim]Sélection en cache : {out_json.name}[/dim]")
        return json.loads(out_json.read_text(encoding="utf-8"))

    cfg = brand.cfg
    sel = cfg.selection
    n = n_clips or int(sel.n_clips)
    user = f"""## Consignes éditoriales de la marque « {cfg.name} »
{brand.guidelines or "(aucune consigne spécifique)"}

## Contexte de l'épisode
- Invité : {guest or "inconnu"}  · Entreprise : {company or "inconnue"}
- Durée totale : {transcript['duration']/60:.1f} min · Langue : {transcript.get('language','fr')}
- B-roll autorisé par la config : {"oui" if cfg.broll.enabled else "non"} (max {cfg.broll.max_per_clip} par clip)

## Demande
Sélectionne {n} extraits (score décroissant), durée totale entre {sel.min_duration}s et {sel.max_duration}s, cible {sel.target_duration}s.
max_segments = {int(sel.get("max_segments", 1))}{" (un seul passage continu par clip)" if int(sel.get("max_segments", 1)) <= 1 else " : privilégie le montage accroche + développement + conclusion à partir de passages différents de l'épisode"}.
{extra_instructions}

## Transcription horodatée
{to_timed_text(transcript)}
"""
    angles = [str(a) for a in (sel.get("angles") or []) if str(a).strip()]
    parallel = len(angles) >= 2
    if parallel:
        # Pattern « parallelization workflow » : une passe LLM par angle éditorial (attention focalisée),
        # en parallèle, puis agrégation (dédoublonnage + jury) — voir README « Parallélisme ».
        k = math.ceil(n / len(angles)) + 1
        console.print(f"[bold]Sélection parallèle[/bold] · {len(angles)} angles × {k} candidats")

        def one(angle: str) -> list[dict]:
            u = user.replace(f"Sélectionne {n} extraits", f"Sélectionne {k} extraits") + (
                f"\n## Angle de cette passe (ne retiens QUE des extraits qui y correspondent)\n{angle}\n")
            try:
                got = ask_json(SYSTEM_PROMPT, u, model=sel.llm_model, backend=sel.llm_backend, max_tokens=12000)
            except Exception as e:  # noqa: BLE001 — un angle en échec ne bloque pas les autres
                console.print(f"[yellow]angle « {angle[:40]}… » en échec : {e}[/yellow]")
                return []
            for c in got.get("clips", []) or []:
                c["angle"] = angle
            return got.get("clips", []) or []

        with ThreadPoolExecutor(max_workers=len(angles)) as pool:
            data = {"clips": [c for lst in pool.map(one, angles) for c in lst]}
    else:
        data = ask_json(SYSTEM_PROMPT, user, model=sel.llm_model, backend=sel.llm_backend, max_tokens=12000)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    (out_json.parent / "llm_raw.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    words = all_words(transcript)
    sents = sentences(transcript)
    clips = []
    max_segments = max(1, int(sel.get("max_segments", 1)))
    for i, c in enumerate(data.get("clips", [])):
        raw_segments = c.get("segments") if isinstance(c.get("segments"), list) and c.get("segments") else [c]
        raw_segments = raw_segments[:max_segments]
        segments = []
        for j, sg in enumerate(raw_segments):
            try:
                s0, e0 = float(sg["start"]), float(sg["end"])
            except (KeyError, ValueError, TypeError):
                continue
            # durée max par segment : celle du clip (un segment unique) ou une part raisonnable
            s1, e1, note = snap_to_quotes(words, sents, s0, e0, str(sg.get("start_text", "")), str(sg.get("end_text", "")),
                                          float(sel.max_duration))
            if note:
                console.print(f"  [dim]extrait {i+1} · segment {j+1} : {note}[/dim]")
            ss, ee = _snap(words, s1, e1)
            nxt = [w for w in words if w["s"] >= ee]
            tail = round(max(0.0, (nxt[0]["s"] - ee) if nxt else 5.0), 3)
            segments.append({"start": ss, "end": ee, "duration": round(ee - ss, 3), "tail_silence": tail,
                             "start_text": sg.get("start_text", ""), "end_text": sg.get("end_text", ""),
                             "role": sg.get("role", "")})
        if not segments:
            continue
        total = round(sum(sg["duration"] for sg in segments), 3)
        if total < float(sel.min_duration) * 0.6:
            console.print(f"[yellow]Extrait {i+1} ignoré (trop court : {total:.1f}s)[/yellow]")
            continue
        s, e = segments[0]["start"], segments[-1]["end"]
        inside = lambda t: any(sg["start"] <= t <= sg["end"] for sg in segments)  # noqa: E731
        kw = [k for k in c.get("keywords", []) if isinstance(k, str) and len(_norm(k)) >= 2][: int(sel.max_keywords)]
        brolls = []
        for b in c.get("broll", []) or []:
            try:
                at = float(b["at"])
            except (KeyError, ValueError, TypeError):
                continue
            if inside(at):
                brolls.append({
                    "at": at,
                    "duration": float(b.get("duration") or cfg.broll.duration),
                    "query": str(b.get("query", "")).strip(),
                    "type": b.get("type", cfg.broll.prefer),
                })
        turns = []
        for t in c.get("turns", []) or []:
            try:
                at = float(t["at"])
            except (KeyError, ValueError, TypeError):
                continue
            if t.get("speaker") in ("host", "guest"):
                turns.append({"at": at, "speaker": t["speaker"]})
        turns.sort(key=lambda t: t["at"])
        clips.append({
            "index": len(clips) + 1,
            "title": c.get("title", f"Clip {i+1}"),
            "hook_title": c.get("hook_title", ""),
            "start": s,
            "end": e,
            "duration": total,
            "segments": segments,
            "tail_silence": segments[-1]["tail_silence"],
            "why": c.get("why", ""),
            "score": c.get("score", 0),
            "start_text": segments[0]["start_text"],
            "end_text": segments[-1]["end_text"],
            "keywords": kw,
            "turns": turns,
            "broll": brolls[: int(cfg.broll.max_per_clip)] if cfg.broll.enabled else [],
            "post": c.get("post", ""),
            "angle": c.get("angle", ""),
            "guest": guest,
            "company": company,
        })
    if parallel:
        clips = _dedupe(clips)
        if len(clips) > n and sel.get("jury", True):
            clips = _jury(clips, n, transcript, brand, guest, company)
        else:
            clips = sorted(clips, key=lambda c: -float(c.get("score") or 0))[:n]
    else:
        clips.sort(key=lambda c: -float(c.get("score") or 0))
    for i, c in enumerate(clips):
        c["index"] = i + 1
    result = {"brand": brand.slug, "guest": guest, "company": company,
              "host_side": host_side or brand.cfg.framing.get("host_side", ""), "clips": clips}
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print(f"[green]{len(clips)} extraits sélectionnés[/green] → {out_json}")
    for c in clips:
        segs = " + ".join(f"{sg['start']:.0f}-{sg['end']:.0f}" for sg in c["segments"])
        console.print(f"  #{c['index']}  [{segs}]  ({c['duration']:.0f}s)  ★{c['score']}  {c['title']}")
    return result


def manual_clips(transcript: dict, ranges: list[tuple[float, float]], guest: str = "", company: str = "") -> dict:
    """Sélection manuelle (--ranges 120-160,900-940) : pas de LLM, pas de mots-clés."""
    words = all_words(transcript)
    sents = sentences(transcript)
    clips = []
    for i, (s0, e0) in enumerate(ranges):
        s1, e1, _ = snap_to_sentences(sents, s0, e0, max_duration=e0 - s0)
        s, e = _snap(words, s1, e1)
        nxt = [w for w in words if w["s"] >= e]
        tail = round(max(0.0, (nxt[0]["s"] - e) if nxt else 5.0), 3)
        nxt = [w for w in words if w["s"] >= e]
        tail = round(max(0.0, (nxt[0]["s"] - e) if nxt else 5.0), 3)
        clips.append({"index": i + 1, "title": f"Clip {i+1}", "hook_title": "", "start": s, "end": e,
                      "duration": round(e - s, 3), "tail_silence": tail,
                      "segments": [{"start": s, "end": e, "duration": round(e - s, 3), "tail_silence": tail}],
                      "why": "manuel", "score": 0, "keywords": [], "broll": [], "turns": [],
                      "post": "", "guest": guest, "company": company})
    return {"guest": guest, "company": company, "clips": clips}
