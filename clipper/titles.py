"""Titre d'épisode : propositions -> contrôles -> validation par l'équipe marketing -> vignette + description YouTube.

Demande d'Arthur (09/10/2026) : « pour les titres des épisodes, l'équipe m'a retourné qu'ils n'étaient pas ouf : un process
pour les rendre mieux, et une étape où quelqu'un du marketing valide un titre parmi 5 propositions », à partir des
titres des 16 épisodes déjà publiés (brands/<m>/titles.md).

Cause du problème : le titre YouTube était un sous-produit du prompt de dérushage (`"youtube_title": "<titre YouTube>"`, aucun
brief) et la vignette avait son propre générateur : deux titres qui ne se parlaient pas, aucun format, aucune validation.

Un seul générateur, un seul titre par épisode, ce qui sert pour YouTube ET pour la vignette :
1. `propose` : Claude écrit ~12 candidats (5 formules relevées sur les titres publiés), chacun adossé à un passage
   RÉEL de l'épisode (horodatage + citation exacte) ;
2. `check` : contrôles automatiques (longueur, chiffres et noms présents dans la transcription, pas de nom d'invité,
   pas trop proche d'un titre publié, ton sobre, extrait retrouvé) ;
3. jury : Claude garde 5 titres variés (≥ 4 formules) avec une phrase d'explication pour l'équipe ;
4. rendu : chaque titre est posé sur la vignette réelle et présenté comme sur la chaîne YouTube (planche numérotée) ;
5. `validate_pick` : l'équipe choisit un numéro (ou corrige les lignes) -> `titre_valide.json`, repris par `thumbnail` et par
   la description YouTube. Les propositions écartées et la remarque sont gardées (matière pour affiner titles.md).
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw
from rich.console import Console

console = Console()

FORMULAS = ("comment", "tension", "promesse", "enjeu", "constat")
# seuils relevés sur les 16 titres publiés (titles.md) : lignes de vignette ≤ 39 (3 au-delà de 34), titre + préfixe ≤ 78
RULES = {"max_line": 34, "hard_line": 40, "max_yt": 80, "hard_yt": 95, "similar": 0.72, "copy": 0.85, "min_formulas": 4}
KEYWORDS = {"ia", "ai", "agent", "agents", "data", "donnees", "geo", "adoption", "transformation", "strategie", "entreprise",
            "entreprises", "automatisation", "chatbot", "llm", "copilot", "productivite", "gouvernance"}
SENSATIONAL = ("incroyable", "choquant", "secret", "revele", "scandale", "jamais vu", "n'allez pas", "enorme", "dingue",
               "explose", "terrifiant", "bombe")
KNOWN = {"IA", "AI", "GEO", "Comment", "Pourquoi", "Faut", "Et", "Quel", "Quelle", "Quels", "Combien", "Les", "Le", "La", "L",
         "De", "Du", "Des", "Un", "Une", "Voici", "Vs", "Europe", "États", "Unis", "Peut", "Doit", "Est", "Que", "Qui", "Ce"}


# ---------------------------------------------------------------------------------------------------------------
# Brief et titres publiés
# ---------------------------------------------------------------------------------------------------------------

def brief(brand) -> str:
    p = brand.dir / "titles.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def published(brand) -> list[dict]:
    """[{n: "E21", title, lines: [l1, l2], pill: 0|1|None}] lus dans la section « Titres publiés » de titles.md."""
    txt = brief(brand)
    if "## Titres publiés" not in txt:
        return []
    sec = txt.split("## Titres publiés", 1)[1].split("\n## ", 1)[0]
    out = []
    for line in sec.splitlines():
        m = re.match(r"- (E\d+) \| (.+?) \| (.+)$", line.strip())
        if not m:
            continue
        parts = [p.strip() for p in m.group(3).split(" / ")]
        pill = next((i for i, p in enumerate(parts) if p.startswith("[")), None)
        out.append({"n": m.group(1), "title": m.group(2), "lines": [re.sub(r"[\[\]]", "", p) for p in parts], "pill": pill})
    return out


def refused(brand) -> list[str]:
    """Titres déjà refusés par l'équipe (rubrique « À éviter » de titles.md, entre accents graves) : jamais reproposés."""
    txt = brief(brand)
    if "## À éviter" not in txt:
        return []
    sec = txt.split("## À éviter", 1)[1]
    out = []
    for m in re.finditer(r"^- `([^`]+)`", sec, re.M):
        out.append(m.group(1).replace(" | AI Corner", "").replace(" / ", " ").strip())
    return out


def episode_number(video: Path, override: str = "") -> str:
    m = re.match(r"(E\d+)", override or video.stem, re.I)
    if not m:
        raise SystemExit(f"Numéro d'épisode introuvable dans « {video.stem} » : ajouter --number E23")
    return m.group(1).upper()


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.replace("’", "'")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", s)).strip()


def youtube_title(number: str, lines: list[str]) -> str:
    text = re.sub(r"\s+", " ", " ".join(l.strip() for l in lines)).strip()
    text = re.sub(r"\s+([?!])", r" \1", text)         # espace avant ? et ! : comme les titres publiés
    return f"AI Corner {number} | {text}"


# ---------------------------------------------------------------------------------------------------------------
# Contrôles automatiques
# ---------------------------------------------------------------------------------------------------------------

def _digits(text: str) -> str:
    return re.sub(r"(?<=\d)[\s  .](?=\d{3}\b)", "", text)


def find_evidence(segments: list[dict], quote: str, t: float | None = None) -> tuple[float | None, float]:
    """Passage où l'on trouve `quote` : (début en s, part de la citation retrouvée d'un seul tenant dans une fenêtre de 3
    phrases). Un seul tenant : des mots courants dispersés ne suffisent pas à « prouver » une citation inventée."""
    q = norm(quote)
    if len(q) < 12:
        return None, 0.0
    texts = [norm(s["text"]) for s in segments]
    best, best_t = 0.0, None
    for i in range(len(segments)):
        w = " ".join(texts[i:i + 3])
        m = difflib.SequenceMatcher(None, w, q, autojunk=False).find_longest_match(0, len(w), 0, len(q))
        score = m.size / len(q)
        if score > best or (score == best and t is not None and best_t is not None
                            and abs(segments[i]["start"] - t) < abs(best_t - t)):
            best, best_t = score, float(segments[i]["start"])
    return best_t, best


def check(prop: dict, ctx: dict) -> list[dict]:
    """Contrôles d'une proposition. ctx : transcript, segments, published, guest, number. Niveaux : OK / ATTENTION / ÉCHEC."""
    res: list[dict] = []

    def add(level: str, msg: str):
        res.append({"level": level, "msg": msg})

    lines = [str(l).strip() for l in prop.get("lines", [])]
    if len(lines) != 2 or not all(lines):
        add("ÉCHEC", "il faut exactement 2 lignes non vides")
        return res
    if prop.get("highlight") not in (0, 1):
        add("ÉCHEC", "la ligne du bandeau (highlight) doit être 0 ou 1")
    longest = max(len(l) for l in lines)
    if longest > RULES["hard_line"]:
        add("ÉCHEC", f"ligne trop longue ({longest} caractères, maximum {RULES['hard_line']})")
    elif longest > RULES["max_line"]:
        add("ATTENTION", f"ligne longue ({longest} caractères) : le texte sera réduit sur la vignette")
    yt = youtube_title(ctx["number"], lines)
    if len(yt) > RULES["hard_yt"]:
        add("ÉCHEC", f"titre YouTube trop long ({len(yt)} caractères)")
    elif len(yt) > RULES["max_yt"]:
        add("ATTENTION", f"titre YouTube long ({len(yt)} caractères ; les titres publiés vont jusqu'à 78)")
    text = " ".join(lines)
    n = norm(text)
    if prop.get("formula") not in FORMULAS:
        add("ATTENTION", f"formule inconnue : {prop.get('formula')!r}")
    if ":" in text or re.search(r"[\U00010000-\U0010ffff☀-➿]", text):
        add("ATTENTION", "deux-points ou emoji : aucun titre publié n'en contient")
    if any(w in n for w in SENSATIONAL):
        add("ÉCHEC", "mot sensationnel : les 16 titres publiés restent sobres")
    words = set(re.split(r"[ ']+", n))                 # « l'IA » compte comme « IA »
    if not (words & KEYWORDS):
        add("ATTENTION", "aucun mot du métier (IA, adoption, transformation, agents…)")
    # faits : chiffres et noms propres doivent venir de l'épisode
    full = ctx.get("full_norm") or norm(" ".join(s["text"] for s in ctx["segments"]))
    digits_in_transcript = norm(_digits(" ".join(s["text"] for s in ctx["segments"])))
    for num in re.findall(r"\d[\d\s  .,]*\d|\d", text):
        d = re.sub(r"\D", "", num)
        if d == str(date.today().year):
            continue
        if d and d not in digits_in_transcript.replace(" ", ""):
            add("ÉCHEC", f"le chiffre « {num.strip()} » n'est pas dans la transcription")
    for w in re.findall(r"[A-ZÀ-Ý][\wÀ-ÿ'’-]{2,}", text):
        if w in KNOWN or w == lines[0].split()[0]:
            continue
        if norm(w) not in full:
            add("ATTENTION", f"« {w} » n'apparaît pas dans la transcription")
    guest_tokens = [t for t in norm(ctx.get("guest", "")).split() if len(t) >= 3]
    if guest_tokens and any(t in words for t in guest_tokens):
        add("ÉCHEC", "le nom de l'invité est dans la vignette : aucun titre publié ne le met")
    # trop proche d'un titre publié
    for p in ctx.get("published", []):
        r = difflib.SequenceMatcher(None, n, norm(" ".join(p["lines"]))).ratio()
        if r > RULES["copy"]:
            add("ÉCHEC", f"reprend le titre de {p['n']} ({p['title']})")
            break
        if r > RULES["similar"]:
            add("ATTENTION", f"très proche du titre de {p['n']} ({p['title']})")
            break
    for r in ctx.get("refused", []):
        if difflib.SequenceMatcher(None, n, norm(r)).ratio() > 0.8:
            add("ÉCHEC", f"déjà refusé par l'équipe : {r}")
            break
    # le titre doit être tenu par l'épisode
    ev = prop.get("evidence") or {}
    if not ev.get("quote"):
        add("ÉCHEC", "aucun passage de l'épisode cité : la promesse du titre n'est pas prouvée")
    else:
        t, score = find_evidence(ctx["segments"], ev["quote"], ev.get("t"))
        if score < 0.6:
            add("ÉCHEC", "la citation n'est pas dans la transcription")
        else:
            if score < 0.85:
                add("ATTENTION", "citation approximative")
            if t is not None:
                ev["t"] = t
    if not any(r["level"] != "OK" for r in res):
        add("OK", "tous les contrôles passent")
    return res


def status(checks: list[dict]) -> str:
    levels = {c["level"] for c in checks}
    return "ÉCHEC" if "ÉCHEC" in levels else "ATTENTION" if "ATTENTION" in levels else "OK"


# ---------------------------------------------------------------------------------------------------------------
# Génération (Claude) : candidats puis jury
# ---------------------------------------------------------------------------------------------------------------

GEN_PROMPT = """Tu écris le titre d'un épisode du podcast vidéo AI Corner (AI Partners) : Thomas Spitz, animateur, reçoit un
dirigeant qui déploie l'IA en entreprise. Le public : des décideurs et professionnels qui déploient l'IA dans leur organisation.

BRIEF ÉDITORIAL (relevé des 16 titres publiés : suis-le, il prime sur tes habitudes) :
{brief}

MISSION : propose {n} titres candidats, au moins 2 pour chacune des 5 formules (comment, tension, promesse, enjeu, constat).
Un titre = UNE phrase coupée en 2 lignes de vignette (34 caractères maximum par ligne). `highlight` = la ligne (0 ou 1) qui
porte le SUJET et ira sur le bandeau bleu.

Règles strictes :
- poser la question ou l'enjeu du SPECTATEUR ; l'invité et son entreprise servent de preuve. Aucun fait interne à l'entreprise
  présenté comme un titre (« Chez X il y a 3000 agents ») ;
- chaque titre doit être TENU par l'épisode : `evidence` = un passage où l'invité répond à ce que le titre annonce, avec `t`
  (secondes) et `quote` = 8 à 20 mots COPIÉS EXACTEMENT de la transcription ;
- n'invente aucun chiffre, nom ou fait : tout chiffre et nom propre du titre figure dans la transcription ;
- ni le nom de l'invité, ni deux-points, ni emoji, ni superlatif, ni promesse que l'épisode ne tient pas ;
- ne recopie aucun titre publié et n'emprunte pas l'angle du dernier épisode publié ;
- un seul sujet par titre, vocabulaire du métier (IA, adoption, transformation, agents, stratégie…) ;
- les sujets annoncés par l'animateur dans son introduction (début de l'enregistrement) sont un indice fort du sujet.

Réponds UNIQUEMENT en JSON :
{{"candidates": [{{"formula": "comment|tension|promesse|enjeu|constat", "lines": ["ligne 1", "ligne 2"], "highlight": 0,
  "evidence": {{"t": 0, "quote": "mots exacts de l'épisode"}}, "viewer_question": "la question du spectateur à laquelle le titre répond"}}]}}"""

JURY_PROMPT = """Tu es le rédacteur en chef du podcast AI Corner. On te donne des titres candidats (déjà contrôlés
automatiquement) pour l'épisode {number}. Choisis les {n} meilleurs : l'équipe marketing en validera UN.

Critères, dans l'ordre :
1. la question du spectateur est claire en 2 secondes ;
2. l'épisode tient vraiment ce que le titre annonce (voir la citation) ;
3. variété : au moins {min_formulas} formules différentes parmi les {n}, jamais deux titres sur le même sous-sujet ;
4. aucune redite avec les derniers titres publiés ;
5. ton sobre, celui des titres publiés.

Pour chaque titre retenu, écris `why` : UNE phrase simple pour l'équipe marketing (pourquoi ce titre donne envie, ce que
l'épisode apporte), sans jargon technique, sans « formule » ni « highlight ».
Réponds UNIQUEMENT en JSON : {{"picks": [{{"id": 0, "why": "…"}}]}} (du meilleur au moins bon)."""


def generate(brand, transcript: dict, number: str, guest: str, company: str, n: int = 12, model: str | None = None) -> list[dict]:
    from .llm import ask_json
    from .transcribe import to_timed_text
    pub = published(brand)
    system = GEN_PROMPT.format(brief=brief(brand), n=n)
    last = pub[0] if pub else None
    user = (f"Épisode {number}. Invité : {guest} ({company}). Animateur : Thomas Spitz.\n"
            + (f"Dernier épisode publié : {last['n']} | {last['title']}\n" if last else "")
            + f"\nTRANSCRIPTION (phrase par phrase, [début → fin]) :\n{to_timed_text(transcript)}")
    model = model or str((brand.cfg.get("titles") or {}).get("model") or "claude-opus-5")
    data = ask_json(system, user, model=model, backend=brand.cfg.selection.llm_backend, max_tokens=8000)
    return [c for c in data.get("candidates", []) if isinstance(c, dict)]


def jury(brand, cands: list[dict], number: str, n: int, model: str | None = None) -> list[dict]:
    from .llm import ask_json
    pub = published(brand)[:6]
    system = JURY_PROMPT.format(number=number, n=n, min_formulas=RULES["min_formulas"])
    lines = []
    for c in cands:
        notes = "; ".join(x["msg"] for x in c["checks"] if x["level"] == "ATTENTION")
        lines.append(f"id {c['id']} · {c['formula']} · {' / '.join(c['lines'])} · preuve : « {c['evidence'].get('quote', '')} »"
                     + (f" · à surveiller : {notes}" if notes else ""))
    user = ("DERNIERS TITRES PUBLIÉS :\n" + "\n".join(f"{p['n']} | {p['title']}" for p in pub)
            + "\n\nCANDIDATS :\n" + "\n".join(lines))
    model = model or str((brand.cfg.get("titles") or {}).get("model") or "claude-opus-5")
    data = ask_json(system, user, model=model, backend=brand.cfg.selection.llm_backend, max_tokens=3000)
    by_id = {c["id"]: c for c in cands}
    picks = []
    for p in data.get("picks", []):
        try:
            c = by_id.get(int(p["id"]))
        except (KeyError, TypeError, ValueError):
            continue
        if c and c not in picks:
            c["why"] = str(p.get("why", "")).strip()
            picks.append(c)
    return picks[:n]


def propose(brand, transcript: dict, number: str, guest: str, company: str, out_dir: Path, n: int = 5,
            import_file: Path | None = None) -> dict:
    """Propositions contrôlées -> out_dir/propositions.json. `import_file` : titres déjà écrits (même format que `candidates`),
    contrôlés mais sans appel à Claude (ex. limite d'abonnement atteinte)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    segs = transcript["segments"]
    ctx = {"number": number, "guest": guest, "segments": segs, "published": published(brand), "refused": refused(brand),
           "full_norm": norm(" ".join(s["text"] for s in segs))}
    if import_file:
        raw = json.loads(import_file.read_text(encoding="utf-8"))
        cands = raw.get("proposals") or raw.get("candidates") or raw
        source = "import"
    else:
        console.print("Claude écrit les titres candidats (≈ 3 min)…")
        cands = generate(brand, transcript, number, guest, company)
        source = "claude"
    for i, c in enumerate(cands, 1):
        c["id"] = i
        c["checks"] = check(c, ctx)
        c["status"] = status(c["checks"])
    ok = [c for c in cands if c["status"] != "ÉCHEC"]
    dropped = [c for c in cands if c["status"] == "ÉCHEC"]
    if import_file:
        picks = ok[:n]
    else:
        clean = [c for c in ok if c["status"] == "OK"]
        pool = clean if len(clean) >= n + 2 else ok
        picks = jury(brand, pool, number, n) if len(pool) > n else pool
        for c in ok:                      # complète si le jury en a retenu moins de n
            if len(picks) >= n:
                break
            if c not in picks:
                c.setdefault("why", "")
                picks.append(c)
    for k, c in enumerate(picks, 1):
        c["rank"] = k
        c["youtube"] = youtube_title(number, c["lines"])
    formulas = {c.get("formula") for c in picks}
    result = {"episode": number, "guest": guest, "company": company, "generated": date.today().isoformat(), "source": source,
              "proposals": picks, "dropped": [{"lines": d.get("lines"), "checks": d["checks"]} for d in dropped],
              "formulas": sorted(f for f in formulas if f)}
    if len(formulas) < RULES["min_formulas"]:
        console.print(f"[yellow]Seulement {len(formulas)} formule(s) différente(s) parmi les propositions.[/yellow]")
    (out_dir / "propositions.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------------------------------------------
# Présentation à l'équipe : planche « chaîne YouTube » + liste
# ---------------------------------------------------------------------------------------------------------------

def _ts(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def write_md(res: dict, out: Path, command: str, with_sheet: bool = False) -> Path:
    md = [f"# Titre de l'épisode {res['episode']} : 5 propositions à valider", "",
          f"Invité : {res['guest']} ({res['company']}). L'équipe marketing choisit UN titre ; les miniatures sont faites "
          "ensuite avec ce titre." + (" Planche facultative : `propositions.jpg`." if with_sheet else ""), ""]
    for p in res["proposals"]:
        mark = " / ".join(f"[{l}]" if i == p["highlight"] else l for i, l in enumerate(p["lines"]))
        ev = p.get("evidence") or {}
        md += [f"## {p['rank']}. {p['youtube']}", "",
               f"- Vignette : {mark}",
               f"- Pourquoi : {p.get('why') or '(à compléter)'}",
               f"- Tenu par l'épisode à {_ts(float(ev['t']))} : « {ev.get('quote', '')} »" if ev.get("t") is not None else "",
               f"- Formule : {p.get('formula', '?')} · contrôles : {p['status']}"
               + ("".join(f"\n  - {c['level']} : {c['msg']}" for c in p["checks"] if c["level"] != "OK")), ""]
    md += ["## Valider", "",
           f"`{command} --pick N` (N = numéro du titre retenu). Pour corriger une ligne : "
           f"`{command} --pick N --lines \"ligne 1 | ligne 2\"` (`*` devant la ligne à mettre sur le bandeau)."]
    out.write_text("\n".join(x for x in md if x is not None) + "\n", encoding="utf-8")
    return out


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int, max_lines: int = 2) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while draw.textlength(lines[-1] + "…", font=font) > width and " " in lines[-1]:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        lines[-1] += "…"
    return lines


def cards_sheet(brand, cards: list[dict], out: Path, cols: int = 3) -> Path:
    """Planche comme la page « Vidéos » de la chaîne : vignette, durée, titre sur 2 lignes. Chaque carte :
    {image: Path, title: str, number: int | None, note: str}. Les cartes sans numéro sont des références."""
    from .canva_template import font
    W, TH, GAP, PAD = 640, 360, 28, 28
    f_title = font(brand.assets_dir, "Montserrat", "SemiBold", 21)
    f_meta = font(brand.assets_dir, "Montserrat", "Medium", 16)
    f_badge = font(brand.assets_dir, "Montserrat", "Bold", 34)
    f_dur = font(brand.assets_dir, "Montserrat", "SemiBold", 17)
    card_h = TH + 118
    rows = (len(cards) + cols - 1) // cols
    sheet = Image.new("RGB", (PAD * 2 + cols * W + (cols - 1) * GAP, PAD * 2 + rows * card_h + (rows - 1) * GAP), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for i, c in enumerate(cards):
        x, y = PAD + (i % cols) * (W + GAP), PAD + (i // cols) * (card_h + GAP)
        im = Image.open(c["image"]).convert("RGB").resize((W, TH), Image.Resampling.LANCZOS)
        m = Image.new("L", (W, TH), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, W - 1, TH - 1], 16, fill=255)
        sheet.paste(im, (x, y), m)
        if c.get("duration"):                       # durée facultative : jamais inventée
            dw = d.textlength(c["duration"], font=f_dur)
            d.rounded_rectangle([x + W - dw - 26, y + TH - 36, x + W - 10, y + TH - 10], 6, fill=(20, 20, 20))
            d.text((x + W - dw - 18, y + TH - 33), c["duration"], font=f_dur, fill=(255, 255, 255))
        if c.get("number") is not None:
            d.ellipse([x + 14, y + 14, x + 74, y + 74], fill=(37, 138, 243))
            nw = d.textlength(str(c["number"]), font=f_badge)
            d.text((x + 44 - nw / 2, y + 22), str(c["number"]), font=f_badge, fill=(255, 255, 255))
        for k, line in enumerate(_wrap(d, c["title"], f_title, W - 8)):
            d.text((x + 4, y + TH + 14 + k * 30), line, font=f_title, fill=(15, 15, 15))
        d.text((x + 4, y + TH + 82), c.get("note", "AI Partners"), font=f_meta, fill=(96, 96, 96))
    sheet.save(out, quality=92)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Validation par l'équipe
# ---------------------------------------------------------------------------------------------------------------

def load_validated(ep: Path) -> dict | None:
    f = ep / "titres" / "titre_valide.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def episode_title(ep: Path) -> str:
    """Titre YouTube pour la description et le récapitulatif du montage : celui que l'équipe a validé."""
    val = load_validated(ep)
    return val["youtube"] if val else "(titre à valider : python -m clipper titles --pick N)"


def parse_lines(spec: str) -> tuple[list[str], int]:
    """« ligne 1 | *ligne 2 » -> ([l1, l2], index du bandeau). Sans `*` : le bandeau est sur la 2e ligne (cas le plus fréquent)."""
    parts = [p.strip() for p in spec.split("|")]
    if len(parts) != 2 or not all(parts):
        raise SystemExit("--lines attend deux lignes séparées par « | », ex. « Comment éviter les pièges | du marché de l'IA ? »")
    hl = 0 if parts[0].startswith("*") else 1
    return [p.lstrip("* ").strip() for p in parts], hl


def validate_pick(ep: Path, number: str, pick: int | None, lines: str = "", youtube: str = "", by: str = "",
                  note: str = "") -> dict:
    props = json.loads((ep / "titres" / "propositions.json").read_text(encoding="utf-8")) if (ep / "titres" / "propositions.json").exists() else None
    if props is None and not lines:
        raise SystemExit("Pas de propositions : lancer d'abord `python -m clipper titles` (sans --pick)")
    chosen = next((p for p in (props or {}).get("proposals", []) if p["rank"] == pick), None) if pick else None
    if pick and chosen is None:
        raise SystemExit(f"Proposition {pick} introuvable (1 à {len((props or {}).get('proposals', []))})")
    if lines:
        l, hl = parse_lines(lines)
    else:
        l, hl = chosen["lines"], chosen["highlight"]
    rec = {"episode": number, "pick": pick, "lines": l, "highlight": hl,
           "youtube": youtube.strip() or youtube_title(number, l), "formula": (chosen or {}).get("formula"),
           "evidence": (chosen or {}).get("evidence"), "edited": bool(lines or youtube),
           "validated_by": by, "validated_on": date.today().isoformat(), "note": note,
           "rejected": [{"rank": p["rank"], "lines": p["lines"]} for p in (props or {}).get("proposals", []) if p["rank"] != pick]}
    (ep / "titres").mkdir(parents=True, exist_ok=True)
    (ep / "titres" / "titre_valide.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    desc = ep / "episode" / "description_youtube.md"          # le titre validé remplace celui du dérushage
    if desc.exists():
        rows = desc.read_text(encoding="utf-8").splitlines()
        if rows and rows[0].startswith("# "):
            rows[0] = f"# {rec['youtube']}"
            desc.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return rec
