"""Montage de l'épisode COMPLET à partir des rushs multicam (3 caméras + micro) — + teaser d'ouverture.

Demande d'Arthur (07/10/2026) : « monte le podcast en entier à partir des 3 longs rushs et de la piste audio, fais aussi
le teaser ». Style mesuré sur les épisodes montés par le monteur (E20 MAIF, E21 Mendo — même studio que E22) :

- E21 : plan médian ≈ 10 s ; gros plans 76 % du temps, plan large 14 %, écran partagé (deux gros plans côte à côte)
  10 %. Quand quelqu'un parle, on est sur SON gros plan 97 % du temps ; les longues réponses sont aérées par un
  plan large (~7 s) et/ou un écran partagé (~7 s) toutes les 15–25 s ; ~1 fois par minute, un plan de réaction de
  1,5 s sur la personne qui écoute (sourire, hochement), puis retour.
- E20 : teaser d'ouverture ≈ 55 s (4–5 phrases fortes de l'invité, sous-titrées) + logo AI Partners, puis
  l'accroche et l'introduction de l'animateur ; fin sur l'animation AI Partners.

Étapes (`episode-plan` puis `episode-render`) :
1. dérushage par le LLM (vrai début = dernière prise de l'intro, vraie fin = au revoir, passages hors micro / prises
   ratées / relances à retirer, extraits du teaser, chapitres YouTube) — citations calées sur les mots ;
2. qui parle : `diarize` (empreintes vocales) sur les mots gardés ;
3. liste de plans (EDL) selon les règles ci-dessus, coupes posées dans les silences, alignées sur les images (24 i/s) ;
4. rendu : un morceau H.264 par plan (en parallèle), concaténés sans réencodage ; audio = micro, mêmes intervalles ;
   teaser rendu par HyperFrames (sous-titres de la charte) ; logo de fin.
"""
from __future__ import annotations

import json
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from rich.console import Console

from .config import Brand
from .media import FFMPEG, probe, run
from .select_clips import _find_phrase
from .transcribe import all_words, sentences, to_timed_text

console = Console()

PLAN_PROMPT = """Tu es le monteur d'un podcast vidéo B2B (AI Corner, AI Partners). On te donne la transcription
COMPLÈTE de l'enregistrement brut (rushs), phrase par phrase avec `[début → fin]` (mm:ss.s). L'enregistrement
contient des réglages avant le début, des prises ratées, des discussions hors antenne, et la suite après l'au revoir.

Rends UNIQUEMENT un JSON :
{
  "start": {"t": <secondes>, "start_text": "<6 à 10 premiers mots EXACTS de la 1re phrase de l'épisode>"},
  "end":   {"t": <secondes>, "end_text": "<6 à 10 derniers mots EXACTS de la dernière phrase de l'épisode>"},
  "cuts": [ {"start": <s>, "end": <s>, "start_text": "<premiers mots EXACTS retirés>", "end_text": "<derniers mots
             EXACTS retirés>", "reason": "<pourquoi>"} ],
  "teaser": [ {"start": <s>, "end": <s>, "start_text": "<premiers mots EXACTS>", "end_text": "<derniers mots EXACTS>",
               "why": "<pourquoi c'est fort>"} ],
  "chapters": [ {"t": <s>, "start_text": "<premiers mots EXACTS>", "title": "<titre court>"} ]
}

Règles :
- start : la DERNIÈRE prise complète de l'introduction de l'animateur (« Bonjour à tous, bienvenue… »). Tout ce qui
  précède (réglages, prises ratées, consignes) est retiré.
- end : la dernière phrase de l'épisode (remerciements / au revoir). Tout ce qui suit est retiré.
- cuts : à l'intérieur de l'épisode, uniquement ce qui n'est PAS destiné au public : discussion technique ou hors
  antenne (« tu peux recommencer », « on pourra couper ici », l'heure, le son), question reposée (garder la MEILLEURE
  prise, en général la dernière), phrase abandonnée puis reprise à l'identique. Ne retire JAMAIS du contenu, des
  hésitations normales, des digressions intéressantes. Chaque coupe commence au DÉBUT d'une phrase et finit à la
  FIN d'une phrase ; ce qui reste doit s'enchaîner naturellement (une question suivie de sa réponse). Peu de coupes.
- teaser : 3 à 5 extraits, chacun 6 à 20 s, total 40 à 60 s, ordre d'enchaînement voulu (le plus fort en premier) :
  phrases fortes, tranchées ou surprenantes de l'invité (au plus une de l'animateur), compréhensibles hors contexte,
  qui donnent envie de regarder. Phrases complètes, jamais dans une partie retirée.
- chapters : 6 à 10 chapitres YouTube (le premier au début de l'épisode), titres courts et concrets.
- Les textes cités sont recopiés MOT POUR MOT depuis la transcription (ils servent à caler les coupes)."""


def _ts(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{int(s):02d}" if h else f"{m:d}:{int(s):02d}"


# ------------------------------------------------------------------------------------------------ 1. dérushage

def _word_at(words: list[dict], text: str, around: float, last: bool) -> int | None:
    hit = _find_phrase(words, text or "", around, window=40) or _find_phrase(words, text or "", around, window=1e9)
    return None if hit is None else hit[1 if last else 0]


def keep_ranges(words: list[dict], plan: dict) -> tuple[list[tuple[float, float]], list[str]]:
    """Intervalles gardés (temps des rushs) à partir du plan du LLM, calés sur les mots."""
    notes = []
    i0 = _word_at(words, plan["start"].get("start_text", ""), float(plan["start"]["t"]), last=False)
    i1 = _word_at(words, plan["end"].get("end_text", ""), float(plan["end"]["t"]), last=True)
    if i0 is None or i1 is None:
        raise SystemExit("Début ou fin de l'épisode introuvable dans la transcription (voir episode_plan.json)")
    removed = []
    for c in plan.get("cuts", []):
        a = _word_at(words, c.get("start_text", ""), float(c["start"]), last=False)
        b = _word_at(words, c.get("end_text", ""), float(c["end"]), last=True)
        if a is None or b is None or b < a:
            notes.append(f"coupe ignorée (citation introuvable) : {c.get('reason', '')}")
            continue
        removed.append((a, b))
        notes.append(f"coupe {_ts(words[a]['s'])}–{_ts(words[b]['e'])} ({words[b]['e'] - words[a]['s']:.0f} s) : {c.get('reason', '')}")
    keep_idx = []
    cur = i0
    for a, b in sorted(removed):
        if b < cur or a > i1:
            continue
        if a > cur:
            keep_idx.append((cur, a - 1))
        cur = max(cur, b + 1)
    if cur <= i1:
        keep_idx.append((cur, i1))
    ranges = []
    for a, b in keep_idx:
        prev_e = words[a - 1]["e"] if a > 0 else 0.0
        next_s = words[b + 1]["s"] if b + 1 < len(words) else words[b]["e"] + 1.0
        s = max(words[a]["s"] - 0.12, (prev_e + words[a]["s"]) / 2)
        e = min(words[b]["e"] + 0.35, (words[b]["e"] + next_s) / 2)
        ranges.append((round(s, 3), round(e, 3)))
    return ranges, notes


def make_plan(transcript: dict, brand: Brand, out: Path, guest: str = "", company: str = "", host: str = "",
              force: bool = False) -> dict:
    from .llm import ask_json
    if out.exists() and not force:
        return json.loads(out.read_text(encoding="utf-8"))
    sel = brand.cfg.selection
    user = (f"Animateur : {host}. Invité : {guest} ({company}).\n\nTRANSCRIPTION :\n{to_timed_text(transcript)}")
    plan = ask_json(PLAN_PROMPT, user, model=sel.llm_model, backend=sel.llm_backend, max_tokens=12000)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return plan


# -------------------------------------------------------------------------------------- 2-3. qui parle + plans

def _refine_boundaries(words: list[dict], reach: int = 3) -> None:
    """Un changement d'orateur détecté avec 1–2 mots de retard (lissage) est recalé sur le plus grand silence voisin."""
    i = 1
    while i < len(words):
        if words[i]["spk"] != words[i - 1]["spk"]:
            new_spk = words[i]["spk"]
            lo, hi = max(1, i - reach), min(len(words) - 1, i + reach)
            j = max(range(lo, hi + 1), key=lambda k: words[k]["s"] - words[k - 1]["e"])
            for k in range(min(i, j), max(i, j)):
                words[k]["spk"] = new_spk if j < i else words[i - 1]["spk"]
            i = max(i, j) + 1
        else:
            i += 1


def _voice_blocks(wav: Path, a: float, b: float, min_gap: int = 3) -> list[tuple[float, float]]:
    """Blocs de voix du micro dans [a, b] séparés par des creux >= min_gap trames de 20 ms (< 25 % du niveau de parole)."""
    from .tighten import _rms
    r = _rms(wav, a, b)
    if not len(r):
        return []
    on = r > 0.25 * max(1e-6, float(np.percentile(r, 90)))
    blocks, k = [], 0
    while k < len(on):
        if not on[k]:
            k += 1
            continue
        j = k
        while j < len(on) and (on[j] or (j + min_gap < len(on) and on[j:j + min_gap].any())):
            j += 1
        blocks.append((a + k * 0.02, a + j * 0.02))
        k = j
    return blocks


def _lead_junk(text: str) -> int:
    """Nombre de mots parasites en tête de ce qu'on entend après un raccord : « euh », mot répété (« pour, pour »)."""
    from .verify import FILLERS, _norm
    toks = [_norm(t) for t in text.split()]
    toks = [t for t in toks if t]
    n = 0
    while n < len(toks) - 1:
        if toks[n] in FILLERS or toks[n] == toks[n + 1]:
            n += 1
        else:
            break
    return n


def _onset(wav: Path, a: float, b: float, rel: float = 0.3) -> float | None:
    """Premier instant de [a, b] où la voix dépasse `rel` × son niveau de parole (trames de 20 ms)."""
    from .tighten import _rms
    r = _rms(wav, a, b)
    if not len(r):
        return None
    lvl = float(np.percentile(r, 90))
    k = np.nonzero(r > rel * lvl)[0]
    return a + 0.02 * int(k[0]) if len(k) else None


def clean_join_starts(ranges: list[tuple[float, float]], wav: Path, cfg=None, log=print,
                      max_pause: float = 1.2, lead: float = 0.28, words: list[dict] | None = None) -> list[tuple[float, float]]:
    """Chaque reprise après une coupe de dérushage démarre proprement (retour d'Arthur, 08/10/2026 : « vérifie bien
    plusieurs fois qu'il n'y a pas de problème de coupure, de euh laissé au montage » — E22 reprenait sur « Euh, pour,
    pour finir » et laissait 3 s de blanc à un autre raccord).
    0. Reprise au milieu d'un son : reculée au creux qui le précède (on entend ce son en entier, puis 1.).
    0b. Voix entre la reprise et le 1er mot de la transcription normale (qui omet les hésitations) : parasite ->
       reprise `lead` s avant l'attaque de ce mot. C'est le signal le plus fiable.
    1. Mots parasites en tête (« euh », mot répété) : on écoute la reprise deux fois avec un contexte différent
       (`verify.audit`, le verbatim varie d'une écoute à l'autre : on garde le pire), on saute autant de blocs de voix
       du micro et on coupe au point le plus silencieux avant le bloc suivant.
    2. Blanc au raccord (fin de la phrase d'avant -> coupe -> reprise de la voix) > max_pause : la reprise est avancée
       à `lead` s avant la voix (respiration naturelle).
    La reprise corrigée est réécoutée ; si elle n'est pas propre, on garde l'original."""
    import tempfile

    from .verify import _quiet, audit
    out = [list(r) for r in ranges]
    tmp = Path(tempfile.gettempdir()) / "clipper_join_audit.wav"

    def junk(i: int, st: float) -> int:
        worst = 0
        for tail, head in (((3.0, 6.0), (10.0, 10.0)) if i else ((0.0, 6.0), (0.0, 10.0))):
            segs = ([{"start": out[i - 1][1] - tail, "end": out[i - 1][1]}] if i else []) + [{"start": st, "end": st + head}]
            h = audit(segs, wav, tmp, cfg)
            worst = max(worst, _lead_junk(h.split("‖")[-1] if i else h))
        return worst

    for i in range(len(out)):
        st = out[i][0]
        # reprise au milieu d'un son (le micro parle déjà à la coupe) : on recule au creux qui précède ce son
        bl0 = [bl for bl in _voice_blocks(wav, st - 1.5, st + 1) if bl[0] < st - 0.03 < bl[1]]
        if bl0 and i:
            new = _quiet(wav, bl0[0][0] - 0.25, bl0[0][0])
            log(f"  reprise {i + 1} : coupe au milieu d'un son ({st:.2f}) -> {new:.2f} s, avant ce son")
            st = new
        # hésitation non transcrite : la transcription normale n'écrit jamais « euh / ben, je… » ; toute voix entre la
        # reprise et le 1er mot transcrit est donc parasite (E22 41:06 : « Euh… ben, je… » 0,9 s + 2,6 s de blanc)
        if words and i:
            nxt = next((w for w in words if float(w["s"]) >= st - 0.05 and str(w["w"]).strip("?.!,… ")), None)
            if nxt and float(nxt["s"]) - st > 0.5:
                pre = [bl for bl in _voice_blocks(wav, st, float(nxt["s"]) - 0.3) if bl[1] - bl[0] >= 0.08]
                on = _onset(wav, float(nxt["s"]) - 0.4, float(nxt["s"]) + 0.8)
                if pre and on and on - lead > st:
                    log(f"  reprise {i + 1} : hésitation non transcrite avant « {nxt['w']} » -> reprise {st:.2f} -> {on - lead:.2f} s")
                    st = on - lead
        n = junk(i, st)
        if n:
            blocks = [bl for bl in _voice_blocks(wav, st - 0.3, st + 4) if bl[1] > st + 0.04]
            if len(blocks) > n:
                new = max(blocks[n - 1][1], min(_quiet(wav, blocks[n - 1][1], blocks[n][0]), blocks[n][0] - 0.05))
                if junk(i, new) == 0:
                    log(f"  reprise {i + 1} : {st:.2f} -> {new:.2f} s ({n} mot(s) parasite(s) retiré(s))")
                    st = new
                else:
                    log(f"  ⚠ reprise {i + 1} ({_ts(st)}) : correction non concluante à l'écoute — à vérifier")
            else:
                log(f"  ⚠ reprise {i + 1} ({_ts(st)}) : {n} mot(s) parasite(s), découpage introuvable — à vérifier")
        if i:
            before = [bl for bl in _voice_blocks(wav, out[i - 1][1] - 3, out[i - 1][1]) if bl[1] - bl[0] >= 0.1]
            after = [bl for bl in _voice_blocks(wav, st, st + 5) if bl[1] - bl[0] >= 0.1]
            if before and after:
                pause = (out[i - 1][1] - before[-1][1]) + (after[0][0] - st)
                if pause > max_pause and after[0][0] - lead > st:
                    new = max(st, after[0][0] - lead)
                    log(f"  reprise {i + 1} : blanc de {pause:.1f} s au raccord -> reprise {st:.2f} -> {new:.2f} s")
                    st = new
                    if junk(i, st):
                        log(f"  ⚠ reprise {i + 1} ({_ts(st)}) : encore un mot parasite à l'écoute — à vérifier")
        out[i][0] = round(st, 2)
    # fins de partie (avant chaque coupe et fin de l'épisode) : jamais pendant que quelqu'un parle + respiration
    from .verify import pad_end
    for i in range(len(out)):
        e = pad_end(wav, out[i][1])
        if e > out[i][1] + 0.02:
            log(f"  fin de partie {i + 1} : {out[i][1]:.2f} -> {e:.2f} s (voix pas finie / respiration)")
            out[i][1] = e
    return [tuple(r) for r in out]


def _snap_gap(words: list[dict], t: float, lo: float, hi: float) -> float:
    """Instant de coupe dans le silence entre deux mots le plus proche de t (dans [lo, hi])."""
    best, score = t, 1e9
    for a, b in zip(words, words[1:]):
        mid = (a["e"] + b["s"]) / 2
        if lo <= mid <= hi:
            sc = abs(mid - t) - 2.0 * min(0.5, b["s"] - a["e"])   # préfère les vraies pauses
            if sc < score:
                best, score = mid, sc
    return best


def build_edl(words: list[dict], ranges: list[tuple[float, float]], seed: int = 7,
              break_every: tuple[float, float] = (12.0, 19.0), break_len: tuple[float, float] = (5.5, 8.0),
              reaction_len: float = 1.5, reaction_rate: float = 0.0, min_shot: float = 2.0) -> list[dict]:
    """Liste de plans [{start, end, cam}] en temps des rushs ; cam ∈ host | guest | wide | split.

    Gros plan de la personne qui parle ; changement d'orateur -> coupe 0,15 s avant son premier mot (dans le silence) ;
    réponse longue -> respiration large / split toutes les 12–19 s (15–24 s avant le 09/10/2026, quand les
    réactions coupaient aussi les gros plans) ; jonction de dérushage -> changement de plan forcé
    (jamais de jump cut sur le même cadre). Puis `enforce_speaker` : ON VOIT TOUJOURS CELUI QUI PARLE.
    Réactions sur l'écoutant seul (`reaction_rate`, ~1/60 avant le 09/10/2026) : désactivées — elles montraient l'autre
    pendant 1,5 s en pleine phrase ; l'écoutant se voit dans les respirations large / écran partagé."""
    rng = random.Random(seed)
    shots: list[dict] = []
    cycle = [["wide"], ["split"], ["wide", "split"], ["split"], ["wide"]]
    ci = 0
    for r0, r1 in ranges:
        ws = [w for w in words if w["s"] >= r0 - 0.01 and w["e"] <= r1 + 0.01]
        if not ws:
            continue
        # tours de parole dans l'intervalle (réactions < 1,2 s rattachées au tour en cours)
        turns = []
        for w in ws:
            if turns and turns[-1]["spk"] == w["spk"]:
                turns[-1]["e"] = w["e"]
            else:
                turns.append({"spk": w["spk"], "s": w["s"], "e": w["e"]})
        merged = []
        for t in turns:
            if merged and (t["e"] - t["s"] < 1.2 or merged[-1]["spk"] == t["spk"]):
                merged[-1]["e"] = t["e"]
            else:
                merged.append(dict(t))
        first_of_range = True
        for k, t in enumerate(merged):
            a = r0 if k == 0 else _snap_gap(ws, t["s"] - 0.15, t["s"] - 0.6, t["s"])
            b = r1 if k == len(merged) - 1 else None   # fixé par le tour suivant
            spk, other = t["spk"], ("guest" if t["spk"] == "host" else "host")
            end = b if b is not None else _snap_gap(ws, merged[k + 1]["s"] - 0.15, merged[k + 1]["s"] - 0.6,
                                                     merged[k + 1]["s"])
            cur = a
            # jonction de dérushage : le premier plan ne doit pas reprendre le cadre du dernier plan
            if first_of_range and shots and shots[-1]["cam"] == spk:
                cut = min(end, cur + rng.uniform(4.0, 6.0))
                shots.append({"start": cur, "end": cut, "cam": "wide"})
                cur = cut
            first_of_range = False
            nxt_break = cur + rng.uniform(*break_every)
            while nxt_break + break_len[0] + 4.0 < end:
                bs = _snap_gap(ws, nxt_break, nxt_break - 2.5, nxt_break + 2.5)
                stretch = bs - cur
                # réaction sur l'écoutant au milieu d'un long gros plan (~1 par minute)
                if reaction_rate > 0 and stretch > 12 and rng.random() < min(1.0, stretch * reaction_rate * 1.6):
                    rs = _snap_gap(ws, cur + stretch * rng.uniform(0.4, 0.6), cur + 5, bs - 5)
                    shots.append({"start": cur, "end": rs, "cam": spk})
                    shots.append({"start": rs, "end": rs + reaction_len, "cam": other, "reaction": True})
                    cur = rs + reaction_len
                shots.append({"start": cur, "end": bs, "cam": spk})
                cur = bs
                for cam in cycle[ci % len(cycle)]:
                    be = min(end - 3.0, _snap_gap(ws, cur + rng.uniform(*break_len), cur + break_len[0] - 1,
                                                  cur + break_len[1] + 1.5))
                    if be - cur < min_shot:
                        break
                    shots.append({"start": cur, "end": be, "cam": cam})
                    cur = be
                ci += 1
                nxt_break = cur + rng.uniform(*break_every)
            shots.append({"start": cur, "end": end, "cam": spk})
    # nettoyage : plans trop courts fusionnés dans le précédent, plans contigus identiques réunis
    clean: list[dict] = []
    for s in shots:
        if s["end"] - s["start"] <= 0.04:
            continue
        if clean and clean[-1]["cam"] == s["cam"] and abs(clean[-1]["end"] - s["start"]) < 0.05:
            clean[-1]["end"] = s["end"]
        elif clean and s["end"] - s["start"] < min_shot and not s.get("reaction") and abs(clean[-1]["end"] - s["start"]) < 0.05:
            clean[-1]["end"] = s["end"]
        else:
            clean.append(dict(s))
    return enforce_speaker(clean, words, min_shot=min_shot)


def speaker_runs(shot: dict, words: list[dict], tol: float = 0.4, join: float = 0.6) -> list[tuple[float, float]]:
    """Passages d'un gros plan où l'on entend L'AUTRE personne (mots de l'autre réunis si < `join` s d'écart) :
    [(début, fin)] de plus de `tol` s (un « oui » isolé de 0,2 s est toléré). Plan large / écran partagé : []."""
    cam = shot["cam"]
    if cam not in ("host", "guest"):
        return []
    runs: list[list[float]] = []
    for w in words:
        m = (w["s"] + w["e"]) / 2
        if m < shot["start"] or m >= shot["end"] or w.get("spk", cam) == cam:
            continue
        a, b = max(shot["start"], w["s"]), min(shot["end"], w["e"])
        if runs and a - runs[-1][1] < join:
            runs[-1][1] = b
        else:
            runs.append([a, b])
    return [(a, b) for a, b in runs if b - a > tol]


def enforce_speaker(shots: list[dict], words: list[dict], min_shot: float = 2.0, solo: float = 2.5,
                    passes: int = 3) -> list[dict]:
    """On voit TOUJOURS celui qui parle (Arthur, 09/10/2026 : « soit un plan caméra avec que lui, soit un plan avec les
    deux, mais il faut qu'on voie toujours celui qui parle »). Dans chaque gros plan, un passage où parle l'autre
    devient : son gros plan s'il dure ≥ `solo` s, sinon un écran partagé (les deux visibles) d'au moins `min_shot` s ;
    un gros plan restant plus court que `min_shot` passe aussi en écran partagé. Répété (`passes`) car un nouveau gros
    plan peut à son tour contenir une réplique de l'autre (ping-pong rapide : E22 21:48 « globale ? » / « globale. Ok. »)."""
    for _ in range(passes):
        out: list[dict] = []
        changed = False
        for s in shots:
            runs = speaker_runs(s, words)
            if not runs:
                out.append(s)
                continue
            changed = True
            cam, other = s["cam"], ("guest" if s["cam"] == "host" else "host")
            lo, hi = s["start"], s["end"]
            pieces: list[list] = []
            for a, b in runs:
                a = max(lo, _snap_gap(words, a - 0.15, a - 0.6, a))
                b = min(hi, _snap_gap(words, b + 0.15, b, b + 0.6))
                c = other if b - a >= solo else "split"
                if c == "split" and b - a < min_shot:
                    pad = (min_shot - (b - a)) / 2
                    a, b = max(lo, a - pad), min(hi, b + pad)
                if pieces and a <= pieces[-1][1] + 1e-3:
                    pieces[-1][1] = max(pieces[-1][1], b)
                    if pieces[-1][2] != c:
                        pieces[-1][2] = "split"
                else:
                    pieces.append([a, b, c])
            seq, cur = [], lo
            for a, b, c in pieces:
                if a > cur:
                    seq.append([cur, a, cam])
                seq.append([a, b, c])
                cur = b
            if cur < hi:
                seq.append([cur, hi, cam])
            for p in seq:   # gros plan trop court -> écran partagé (les deux visibles : jamais faux)
                if p[2] in ("host", "guest") and p[1] - p[0] < min_shot:
                    p[2] = "split"
            out += [{"start": a, "end": b, "cam": c} for a, b, c in seq if b - a > 0.04]
        res: list[dict] = []
        for s in out:   # plans contigus identiques réunis
            if res and res[-1]["cam"] == s["cam"] and abs(res[-1]["end"] - s["start"]) < 0.05:
                res[-1]["end"] = s["end"]
            else:
                res.append(dict(s))
        shots = res
        if not changed:
            break
    return shots


# ------------------------------------------------------------------------------------------------ 4. rendu

def _face_x(video: Path, at: float) -> float:
    """Centre horizontal (0–1) du plus grand visage d'un gros plan (pour l'écran partagé)."""
    import cv2

    from .analysis import _detector
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-vf",
                          "scale=640:360", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
    img = np.frombuffer(raw, np.uint8).reshape(360, 640, 3)
    _, faces = _detector(640, 360).detect(img)
    if faces is None or not len(faces):
        return 0.5
    f = max(faces, key=lambda f: f[2] * f[3])
    return float((f[0] + f[2] / 2) / 640)


def render_piece(spec: dict, shot: dict, dst: Path, fps: int, size: tuple[int, int], proxy: bool,
                 split_order: tuple[str, str], face_x: dict[str, float], logo: dict | None = None) -> Path:
    cams = spec["cams"]
    n = int(shot["frames"])
    t0 = shot["f0"] / fps
    W, H = size
    enc = (["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"] if proxy else
           ["-c:v", "libx264", "-preset", "medium", "-crf", "16"])
    common = ["-an", "-frames:v", str(n), "-r", str(fps), "-pix_fmt", "yuv420p", "-g", str(fps * 2), *enc,
              "-video_track_timescale", "12288", str(dst)]
    # logo en haut à droite (dernière entrée) : largeur et marge en fraction de la largeur de l'image
    lg_in, lg_tail = [], ""
    if logo:
        lw, mg = int(W * float(logo["width"])), int(W * float(logo["margin"]))
        lg_in = ["-i", str(logo["file"])]
        lg_tail = f";[{{li}}:v]scale={lw}:-1,format=rgba[lg];[v0][lg]overlay=W-w-{mg}:{mg},format=yuv420p[v]"
    if shot["cam"] == "split":
        L, R = split_order
        def crop(name: str) -> str:
            x = min(max(face_x.get(name, 0.5) - 0.25, 0.0), 0.5)
            return f"crop=iw/2:ih:{x:.4f}*iw:0"
        graph = (f"[0:v]{crop(L)},scale={W // 2}:{H}[l];[1:v]{crop(R)},scale={W // 2}:{H}[r];"
                 f"[l][r]hstack=2,setsar=1[{'v0' if logo else 'v'}]" + lg_tail.replace("{li}", "2"))
        run([FFMPEG, "-y", "-v", "error", "-ss", f"{t0:.4f}", "-i", str(cams[L]), "-ss", f"{t0:.4f}", "-i", str(cams[R]),
             *lg_in, "-filter_complex", graph, "-map", "[v]", *common])
    else:
        graph = f"[0:v]scale={W}:{H},setsar=1[{'v0' if logo else 'v'}]" + lg_tail.replace("{li}", "1")
        run([FFMPEG, "-y", "-v", "error", "-ss", f"{t0:.4f}", "-i", str(cams[shot["cam"]]), *lg_in,
             "-filter_complex", graph, "-map", "[v]", *common])
    return dst


def quantize(shots: list[dict], fps: int) -> list[dict]:
    """Bornes sur la grille d'images ; les plans d'un même intervalle restent jointifs (aucune dérive audio/vidéo)."""
    out = []
    for s in shots:
        f0, f1 = int(round(s["start"] * fps)), int(round(s["end"] * fps))
        if f1 > f0:
            out.append(dict(s, f0=f0, frames=f1 - f0))
    return out


def render_body(spec: dict, shots: list[dict], ranges: list[tuple[float, float]], work: Path, out: Path | None,
                fps: int = 24, proxy: bool = False, jobs: int = 3, split_order: tuple[str, str] = ("host", "guest"),
                audio_delay: float = 0.0, logo: dict | None = None) -> Path:
    """Vidéo du corps de l'épisode (plans concaténés) + audio du micro sur les mêmes intervalles (images entières)."""
    work = work.resolve()
    pieces_dir = work / (("pieces_proxy" if proxy else "pieces") + (f"_logo{float(logo['width']):.3f}" if logo else ""))
    pieces_dir.mkdir(parents=True, exist_ok=True)
    size = (960, 540) if proxy else (1920, 1080)
    mid = sum(r[0] + r[1] for r in ranges[:1]) / 2 if ranges else 600
    face_x = {k: _face_x(spec["cams"][k], mid + 30) for k in ("host", "guest")}
    q = quantize(shots, fps)
    todo = []
    for i, s in enumerate(q):
        p = pieces_dir / f"{i:04d}_{s['cam']}_{s['f0']}_{s['frames']}.mp4"
        if not p.exists():
            todo.append((s, p))
    console.print(f"  {len(q)} plans, {len(todo)} à encoder ({'aperçu 540p' if proxy else '1080p'})…")
    done = [0]

    def one(arg):
        s, p = arg
        tmp = p.with_suffix(".tmp.mp4")
        render_piece(spec, s, tmp, fps, size, proxy, split_order, face_x, logo)
        tmp.replace(p)
        done[0] += 1
        if done[0] % 25 == 0:
            console.print(f"    {done[0]}/{len(todo)}")

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(one, todo))
    lst = work / ("concat_proxy.txt" if proxy else "concat.txt")
    lst.write_text("".join(f"file '{(pieces_dir / f'{i:04d}_{s['cam']}_{s['f0']}_{s['frames']}.mp4').as_posix()}'\n"
                           for i, s in enumerate(q)), encoding="utf-8")
    video = work / ("body_video_proxy.mp4" if proxy else "body_video.mp4")
    run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video)])
    # audio : intervalles = union des plans (images entières), micro décalé de audio_delay
    spans = []
    for s in q:
        a, b = s["f0"] / fps, (s["f0"] + s["frames"]) / fps
        if spans and abs(spans[-1][1] - a) < 1e-6:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    parts = []
    for i, (a, b) in enumerate(spans):
        parts.append(f"[0:a]atrim={max(0.0, a - audio_delay):.4f}:{b - audio_delay:.4f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.012,afade=t=out:st={max(0.0, b - a - 0.012):.4f}:d=0.012[a{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(len(spans))) + f"concat=n={len(spans)}:v=0:a=1[a]"
    gfile = work / "body_audio_graph.txt"
    gfile.write_text(graph, encoding="utf-8")
    audio = work / "body_audio.wav"
    run([FFMPEG, "-y", "-v", "error", "-i", str(spec["audio"]), "-/filter_complex", str(gfile), "-map", "[a]",
         "-ar", "48000", "-ac", "2", str(audio)])
    if out is None:
        return video
    run([FFMPEG, "-y", "-v", "error", "-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", str(out)])
    return out


def out_time(shots: list[dict], t_src: float, fps: int = 24) -> float | None:
    """Temps dans l'épisode monté correspondant à un instant des rushs (None s'il est coupé)."""
    acc = 0
    for s in quantize(shots, fps):
        a, b = s["f0"] / fps, (s["f0"] + s["frames"]) / fps
        if a <= t_src < b:
            return (acc + (t_src - a) * fps) / fps
        acc += s["frames"]
    return None


# ------------------------------------------------------------------------------------------------ teaser

TEASER_PROMPT = """Tu écris le SCRIPT du teaser d'ouverture d'un podcast vidéo B2B (AI Corner, AI Partners), sur le
modèle des teasers de « Dans la tête d'un CEO » (docs/TEASER_FRAMEWORK.md) : 35 à 45 secondes ULTRA catchy, débit
continu, une idée forte toutes les 3 secondes. On te donne la transcription de l'épisode monté, phrase par phrase,
avec `[début → fin]` et QUI parle (ANIMATEUR / INVITÉ).

Rends UNIQUEMENT un JSON :
{"teaser": [ {"start": <s>, "end": <s>, "start_text": "<premiers mots EXACTS>", "end_text": "<derniers mots EXACTS>",
              "speaker": "host|guest", "role": "these|developpement|pingpong|reaction|histoire|conviction|chute",
              "keywords": ["<1 à 3 mots EXACTS de l'extrait à mettre en couleur : chiffres, mots forts>"],
              "why": "<pourquoi ça accroche>"} ],
 "backup": [ <même format : 5 à 6 extraits de réserve variés, dont au moins 2 de l'animateur> ]}
(Les extraits sont ensuite contrôlés à l'oreille ; ceux qui ont un défaut sont remplacés par la réserve.)

Le script, DANS CET ORDRE (8 à 12 extraits) :
1. these — l'invité, une phrase tranchée, contre-intuitive, compréhensible sans contexte (4–7 s). C'est l'accroche :
   la plus forte de l'épisode.
2. developpement — la phrase qui précise ou durcit la thèse (3–6 s).
3. pingpong — une question COURTE et directe de l'animateur puis la réponse courte de l'invité, idéalement avec un
   CHIFFRE (2–5 s chacune, deux extraits qui se suivent).
4. reaction — INTERDIT d'utiliser une réaction vide (« Super intéressant », « Ah ouais », « Exactement »… : n'apporte rien, retour d'Arthur 09/10/2026) ; si l'animateur réagit, ce doit être une phrase qui relance avec du fond
   (1–3 s), seulement si l'épisode en contient une vraie.
5. histoire — une anecdote concrète, un moment vécu, un exemple chiffré (5–8 s).
6. conviction — ce que l'invité défend, sa vision (4–6 s).
7. chute — une phrase forte ou un moment humain qui donne envie de voir la suite (2–5 s).

Exigences (retour d'Arthur, 08/10/2026) :
- la THÈSE d'ouverture est dite d'une traite : AUCUNE hésitation (pas de « euh », « enfin », « en fait », reprise ou
  mot répété) — choisis une phrase fluide, même si elle est un peu moins forte ;
- le PING-PONG : une question courte, puis une réponse COURTE (2–5 s) qui se termine nettement par un point — une
  réponse qui part dans une longue explication ne convient pas ;
- la CHUTE est COURTE : 2 à 5 s, une seule phrase.

Règles :
- Chaque extrait COMMENCE au début d'une phrase et FINIT sur une fin de phrase (. ? !), jamais sur une virgule ;
  pas de mot d'appui en tête (« donc », « et », « en fait ») : commence juste après. Il se comprend SEUL.
- Les deux interlocuteurs parlent : au moins 3 extraits de l'animateur (questions qui piquent, réactions, punchline).
- Rien de technique ni de mise en contexte ; que de l'impact : chiffres, formules, prises de position, images fortes.
- keywords : 1 à 3 mots recopiés EXACTEMENT de l'extrait (ils seront en bleu dans les sous-titres géants).
- Textes cités MOT POUR MOT depuis la transcription (ils servent à caler les coupes)."""


def speaker_text(words: list[dict], transcript: dict, ranges: list[tuple[float, float]]) -> str:
    """Transcription des parties gardées, phrase par phrase, avec qui parle (pour le LLM du teaser)."""
    from .transcribe import _ts as ts
    spk = {round(w["s"], 3): w.get("spk", "") for w in words}
    out = []
    for p in sentences(transcript):
        if not any(a <= p["start"] < b for a, b in ranges):
            continue
        votes = [spk.get(round(w["s"], 3), "") for w in p["words"]]
        who = "ANIMATEUR" if votes.count("host") > len(votes) / 2 else "INVITÉ"
        out.append(f"[{ts(p['start'])} → {ts(p['end'])}] {who} : {p['text']}")
    return "\n".join(out)


def make_teaser(transcript: dict, words: list[dict], ranges: list[tuple[float, float]], brand: Brand, out: Path,
                guest: str = "", company: str = "", host: str = "", force: bool = False) -> dict:
    from .llm import ask_json
    if out.exists() and not force:
        return json.loads(out.read_text(encoding="utf-8"))
    sel = brand.cfg.selection
    user = f"Animateur : {host}. Invité : {guest} ({company}).\n\nTRANSCRIPTION :\n{speaker_text(words, transcript, ranges)}"
    # choix éditorial court mais décisif : modèle plus fort que pour la sélection (episode.teaser_model)
    model = str((brand.cfg.get("episode") or {}).get("teaser_model") or "claude-opus-5")
    data = ask_json(TEASER_PROMPT, user, model=model, backend=sel.llm_backend, max_tokens=6000)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def _extract(words: list[dict], x: dict, orig: int) -> dict | None:
    """Un extrait du LLM calé sur les mots (jusqu'à la fin de la phrase si la citation est approximative)."""
    from .select_clips import _snap
    a = _word_at(words, x.get("start_text", ""), float(x["start"]), last=False)
    b = _word_at(words, x.get("end_text", ""), float(x["end"]), last=True)
    if a is None or b is None or b < a:
        return None
    k = b
    while (k + 1 < len(words) and k - b < 8 and words[k]["w"][-1:] not in ".?!…"
           and words[k + 1]["s"] - words[k]["e"] < 0.5 and words[k + 1].get("spk") == words[b].get("spk")):
        k += 1
    if words[k]["w"][-1:] in ".?!…":
        b = k
    s, e = _snap(words, words[a]["s"], words[b]["e"], pad_in=0.08, pad_out=0.25)
    nxt = words[b + 1]["s"] if b + 1 < len(words) else e + 1
    e = round(min(e, nxt - 0.03), 3)   # _snap garde 0,12 s après le dernier mot, même si le suivant enchaîne
    return {"start": s, "end": e, "duration": round(e - s, 3), "tail_silence": round(max(0.0, nxt - words[b]["e"]), 3),
            "start_text": x.get("start_text", ""), "end_text": x.get("end_text", ""), "role": "teaser", "orig": orig}


def teaser_clip(words: list[dict], plan: dict, guest: str, company: str, wav: Path | None = None,
                transcript: dict | None = None, seed: int = 3, target: float = 42.0, editor=None) -> dict:
    """Clip « teaser » (format de clips.json), selon docs/TEASER_FRAMEWORK.md.

    1. extraits du script (+ réserve) calés sur de vraies fins de phrase (`verify.snap_extract`, « euh » retirés) ;
    2. débit continu : blancs internes > 0,3 s ramenés à 0,1 s (`tighten`), bords recalés (`trim_edges`) ;
    3. ordre du script conservé ; un extrait écarté au contrôle est remplacé par la réserve (même interlocuteur) ;
    4. plans : une coupe toutes les ~2 s — gros plan de celui qui parle, plan large, réaction de l'écoutant (~1 s) ;
       deux extraits du même orateur qui se suivent ne recommencent pas sur le même cadre (pas de jump cut) ;
    5. mots-clés du script -> `keywords` (en bleu dans les sous-titres)."""
    rng = random.Random(seed)
    main = [dict(e, spk_hint=x.get("speaker", ""), kw=x.get("keywords", []))
            for i, x in enumerate(plan.get("teaser", [])) if (e := _extract(words, x, i))]
    backup = [dict(e, spk_hint=x.get("speaker", ""), kw=x.get("keywords", []))
              for i, x in enumerate(plan.get("backup", [])) if (e := _extract(words, x, 100 + i))]
    ext = []
    for x in main + backup:
        pieces = [{"start": x["start"], "end": x["end"], "duration": x["duration"]}]
        notes = []
        if wav is not None:
            from .tighten import tighten_segment
            from .verify import snap_extract, trim_edges
            ia = next(i for i, w in enumerate(words) if w["s"] >= x["start"] - 0.05)
            ib = max(i for i, w in enumerate(words) if w["e"] <= x["end"] + 0.05)
            pieces, notes = snap_extract(words, ia, ib, wav)
            if notes:
                console.print(f"  teaser {x['start']:.1f}s : " + " ; ".join(notes))
            if not pieces:
                continue
            pieces[0], _ = trim_edges(pieces[0], wav)
            pieces[-1], _ = trim_edges(pieces[-1], wav)
            if transcript is not None:   # débit continu : pas de blanc dans un extrait
                tight = []
                for p in pieces:
                    tight += tighten_segment(p, transcript, wav, min_gap=0.3, keep=0.1, max_silence=0.3, min_cut=0.2)
                pieces = tight
        ws = [w for p in pieces for w in words if p["start"] - 0.05 <= w["s"] <= p["end"]]
        if not ws:
            continue
        role = (plan.get("teaser", [])[x["orig"]].get("role", "") if x["orig"] < 100 else "")
        dur = sum(p["end"] - p["start"] for p in pieces)
        # exigences par rôle : thèse d'une traite (aucune hésitation gardée), ping-pong / réaction / chute courts
        hesit = wav is not None and any(n.startswith("gardé") for n in (notes or []))
        too_long = {"these": 8.0, "pingpong": 6.0, "reaction": 3.0, "chute": 6.0}.get(role)
        if (role == "these" and hesit) or (too_long and dur > too_long):
            console.print(f"  teaser {x['start']:.1f}s : {role} écartée ({'hésitation' if hesit else f'{dur:.1f} s, trop long'})")
            continue
        spk = max(("host", "guest"), key=lambda k: sum(w.get("spk") == k for w in ws))
        ext.append({"orig": x["orig"], "pieces": pieces, "spk": spk, "kw": x["kw"], "ws": ws, "hesit": hesit,
                    "dur": sum(p["end"] - p["start"] for p in pieces)})
    mains = [e for e in ext if e["orig"] < 100]
    spare = [e for e in ext if e["orig"] >= 100]
    roles = [x.get("role", "") for x in plan.get("teaser", [])]
    lost = [i for i in range(len(roles)) if i not in {e["orig"] for e in mains}]
    # ping-pong : une question et sa réponse vont ensemble (E22 : la réponse écartée avait été remplacée par une réserve
    # sans rapport -> « C'est quoi le bon équilibre… ? » / « …c'est un marché de vendeur »)
    for i in list(lost):
        if roles[i] == "pingpong":
            for j in (i - 1, i + 1):
                if 0 <= j < len(roles) and roles[j] == "pingpong" and j not in lost:
                    lost.append(j)
    mains = [e for e in mains if e["orig"] not in lost]
    seq = list(mains)
    for i in [i for i in lost if roles[i] != "pingpong"]:   # remplacé par une réserve du même interlocuteur
        who = plan["teaser"][i].get("speaker", "guest")
        strict = roles[i] in ("these", "chute")      # remplaçant court et dit d'une traite
        rep = next((e for e in spare if e["spk"] == who and (not strict or (not e["hesit"] and e["dur"] <= 7.0))), None)
        if rep:
            spare.remove(rep)
            pos = sum(1 for e in seq if e["orig"] < i)
            seq.insert(pos, rep)
    while seq and sum(e["dur"] for e in seq) > target + 5 and len(seq) > 6:
        seq.pop(-2)         # trop long : on retire avant la chute (la chute reste la dernière)
    if editor is not None and ext:
        picked = editor(ext)
        if picked:
            seq = picked
    segs, turns, cams, keywords = [], [], [], []
    last_cam = None
    for e in seq:
        other = "guest" if e["spk"] == "host" else "host"
        for k, p in enumerate(e["pieces"]):
            segs.append({"start": p["start"], "end": p["end"], "duration": round(p["end"] - p["start"], 3),
                         "tail_silence": 0.1, "role": "teaser", "orig": e["orig"],
                         "start_text": "", "end_text": ""})
        for w in e["ws"]:
            if not turns or turns[-1]["speaker"] != w.get("spk"):
                turns.append({"at": round(w["s"], 3), "speaker": w.get("spk", e["spk"])})
        keywords += [k for k in e["kw"] if isinstance(k, str)]
        # plans : une coupe toutes les ~2 s, posées dans une micro-pause entre deux mots de l'extrait
        t0, t_end = e["pieces"][0]["start"], e["pieces"][-1]["end"]
        first = "wide" if last_cam == e["spk"] else e["spk"]
        cams.append({"at": round(t0, 3), "speaker": first, "force": True})
        cur, cur_cam, step = t0, first, 0
        while True:
            nxt = cur + rng.uniform(1.6, 2.8)
            if nxt > t_end - 1.2:
                break
            gaps = [((a["e"] + b["s"]) / 2, b["s"] - a["e"]) for a, b in zip(e["ws"], e["ws"][1:])
                    if nxt - 0.7 <= (a["e"] + b["s"]) / 2 <= nxt + 0.7]
            at = max(gaps, key=lambda g: g[1])[0] if gaps else nxt
            if cur_cam != e["spk"]:
                cam = e["spk"]                       # retour sur celui qui parle
            else:
                cam = ("wide", other)[step % 2]      # puis tour à tour plan large / réaction de l'écoutant
                step += 1
            if cam == other and at + 1.0 < t_end - 1.0:
                cams += [{"at": round(at, 3), "speaker": other, "force": True},
                         {"at": round(at + 1.0, 3), "speaker": e["spk"], "force": True}]
                cur, cur_cam = at + 1.0, e["spk"]
                continue
            if cam == other:
                cam = "wide"
            cams.append({"at": round(at, 3), "speaker": cam, "force": True})
            cur, cur_cam = at, cam
        last_cam = cur_cam
    cams.sort(key=lambda c: c["at"])
    return {"index": 99, "title": "teaser", "hook_title": "", "segments": segs, "turns": turns, "cams": cams,
            "start": segs[0]["start"] if segs else 0, "end": segs[-1]["end"] if segs else 0,
            "duration": round(sum(s["duration"] for s in segs), 2), "guest": guest, "company": company,
            "keywords": list(dict.fromkeys(keywords)), "broll": []}


EDITOR_PROMPT = """Tu es le monteur final du teaser d'ouverture d'un podcast vidéo B2B (AI Corner), au style des teasers de
« Dans la tête d'un CEO » (docs/TEASER_FRAMEWORK.md). On te donne des EXTRAITS DÉJÀ VÉRIFIÉS À L'OREILLE (texte exact
entendu, qui parle, durée, moment dans l'épisode). Compose l'enchaînement final.

Rends UNIQUEMENT un JSON : {"order": [<id>, …], "why": "<1 phrase>"}

Règles :
- 35 à 45 s au total ; 7 à 11 extraits ; les deux interlocuteurs parlent (au moins 3 extraits de l'animateur).
- Ordre du framework : THÈSE choc de l'invité (fluide, sans hésitation) → développement → PING-PONG (une question de
  l'animateur suivie de SA vraie réponse : la réponse doit répondre à CETTE question — vérifie le sens et la proximité
  dans l'épisode ; sinon pas de ping-pong) → réaction de l'animateur → histoire → conviction → CHUTE courte et forte.
- Pas de redite : deux extraits ne disent jamais la même idée (ex. « refonte des entreprises » et « transformations
  majeures des entreprises » = redite, garde la meilleure).
- Une question de l'animateur n'est jamais suivie d'une réponse hors sujet ; une question peut finir le teaser si
  elle donne envie de voir la suite.
- Chaque enchaînement doit sonner naturel à l'oral (« Super intéressant » après une affirmation de l'invité, etc.).
- N'utilise que les ids fournis, chacun au plus une fois."""


def editorial_order(ext: list[dict], brand: Brand) -> list[dict] | None:
    """Relecture éditoriale (LLM) sur des extraits déjà vérifiés : ordre final, ping-pong cohérent, pas de redite."""
    from .llm import ask_json
    pool = []
    for e in ext:
        txt = " ".join(w["w"] for w in e["ws"])
        pool.append(f'id {e["orig"]} · {"ANIMATEUR" if e["spk"] == "host" else "INVITÉ"} · {e["dur"]:.1f} s · '
                    f'{_ts(e["pieces"][0]["start"])} · {"hésitation gardée · " if e.get("hesit") else ""}« {txt} »')
    model = str((brand.cfg.get("episode") or {}).get("teaser_model") or "claude-opus-5")
    try:
        data = ask_json(EDITOR_PROMPT, "EXTRAITS :\n" + "\n".join(pool), model=model,
                        backend=brand.cfg.selection.llm_backend, max_tokens=2000)
    except Exception as ex:  # noqa: BLE001 — sans relecture, on garde l'ordre du script
        console.print(f"[yellow]  relecture éditoriale indisponible : {ex}[/yellow]")
        return None
    by_id = {e["orig"]: e for e in ext}
    order = [by_id[i] for i in data.get("order", []) if i in by_id]
    order = [e for k, e in enumerate(order) if e not in order[:k]]
    if len(order) < 5:
        return None
    console.print(f"  teaser : relecture éditoriale -> {len(order)} extraits ({data.get('why', '')})")
    return order


def teaser_brand(brand: Brand) -> Brand:
    """La marque, avec la section `teaser:` de brand.yaml appliquée par-dessus (16:9, pas de bulle, fin = logo)."""
    import copy

    from .config import Cfg, deep_merge
    b = copy.copy(brand)
    b.cfg = Cfg(deep_merge(dict(brand.cfg), dict(brand.cfg.get("teaser") or {})))
    return b


# ------------------------------------------------------------------------------------------------ assemblage

def _normalize(src: Path, dst: Path, size: tuple[int, int], fps: int, proxy: bool) -> Path:
    """Vidéo (sans son) aux mêmes réglages que les plans du corps -> concaténation sans réencodage."""
    W, H = size
    enc = ["-preset", "ultrafast", "-crf", "28"] if proxy else ["-preset", "medium", "-crf", "16"]
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-an", "-vf", f"scale={W}:{H},setsar=1,fps={fps}",
         "-r", str(fps), "-pix_fmt", "yuv420p", "-g", str(fps * 2), "-c:v", "libx264", *enc,
         "-video_track_timescale", "12288", str(dst)])
    return dst


def end_duration(brand: Brand) -> float:
    """Durée de la fin de l'épisode (`episode_end.duration` de brand.yaml ; 8 s = animation 2× plus lente qu'à 4 s)."""
    return float((brand.cfg.get("episode_end") or {}).get("duration") or 4.0)


def end_card(brand: Brand, dst: Path, size: tuple[int, int], fps: int, proxy: bool, duration: float | None = None) -> Path:
    """Fin de l'épisode (comme E20) : l'animation AI Partners + logo blanc centré."""
    from .media import prepare_outro_video
    duration = duration or end_duration(brand)
    o = brand.cfg.outro
    W, H = size
    anim = dst.with_name("end_anim.mp4")
    prepare_outro_video(brand.asset(o.get("video")), anim, W, H, duration, focus_x=0.68, fps=fps)
    logo = brand.asset(o.get("logo_file") or "logo.png")
    lw = int(W * 0.34)
    enc = ["-preset", "ultrafast", "-crf", "28"] if proxy else ["-preset", "medium", "-crf", "16"]
    # -loop 1 : sans lui le PNG n'a qu'une image (t = 0, encore transparente avant le fondu) répétée -> logo invisible
    run([FFMPEG, "-y", "-v", "error", "-i", str(anim), "-loop", "1", "-i", str(logo), "-filter_complex",
         f"[1:v]scale={lw}:-1,format=rgba,fade=t=in:st=0.4:d=0.6:alpha=1[l];[0:v][l]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[v]",
         "-map", "[v]", "-an", "-r", str(fps), "-g", str(fps * 2), "-c:v", "libx264", *enc,
         "-video_track_timescale", "12288", str(dst)])
    return dst


def assemble(parts_video: list[Path], parts_audio: list[Path | float], dst: Path, work: Path) -> Path:
    """Concatène les vidéos (sans réencodage) ; audio = morceaux concaténés (un nombre = silence de n s), volume
    normalisé PARTIE PAR PARTIE au même niveau (-16 LUFS ; dans le corps, le niveau de l'invité qui baisse quand son
    micro « tombe » est rattrapé). Retour d'Arthur, 08/10/2026 : « le teaser avait un volume trop fort par rapport au
    reste » — teaser rendu à -17 LUFS, micro brut à -37 : un loudnorm unique sur l'ensemble ne rattrapait pas l'écart."""
    lst = work / "final_concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts_video), encoding="utf-8")
    vid = work / "final_video.mp4"
    run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(vid)])
    inputs, chain = [], []
    for i, a in enumerate(parts_audio):
        if isinstance(a, (int, float)):
            inputs += ["-f", "lavfi", "-t", f"{a:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        else:
            inputs += ["-i", str(a)]
        norm = "" if isinstance(a, (int, float)) else ",loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000"
        chain.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo{norm}[x{i}]")
    n = len(parts_audio)
    graph = ";".join(chain) + ";" + "".join(f"[x{i}]" for i in range(n)) + \
        f"concat=n={n}:v=0:a=1[a]"
    aud = work / "final_audio.wav"
    run([FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[a]", "-ar", "48000", str(aud)])
    run([FFMPEG, "-y", "-v", "error", "-i", str(vid), "-i", str(aud), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", str(dst)])
    return dst


def description(plan: dict, shots: list[dict], words: list[dict], offset: float, guest: str, company: str,
                fps: int = 24, contact: str = "") -> str:
    """Titre + chapitres YouTube (temps de l'épisode monté, teaser compris) pour la description."""
    lines = [f"# {plan.get('youtube_title', '')}", "", f"Invité : {guest} ({company})", "", "## Chapitres", "",
             "0:00 Teaser"]
    for c in plan.get("chapters", []):
        i = _word_at(words, c.get("start_text", ""), float(c["t"]), last=False)
        t = words[i]["s"] if i is not None else float(c["t"])
        o = out_time(shots, t, fps)
        if o is not None:
            lines.append(f"{_ts(o + offset)} {c['title']}")
    if contact:
        lines += ["", contact]
    return "\n".join(lines) + "\n"
