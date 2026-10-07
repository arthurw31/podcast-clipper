"""Vérification « à l'oreille » des coupes : retranscription VERBATIM de chaque passage, puis correction.

Pourquoi : la transcription normale (Whisper) nettoie le texte — pas de « euh », pas de bégaiement, mots coupés
invisibles — donc ni le LLM ni `check` ne voient ces défauts. Retour d'Arthur sur le teaser E22 (07/10/2026) : « des
euh qui restent, des phrases coupées un peu trop tôt, un bégaiement au tout début ». Ici, chaque passage (avec 0,4 s
avant / 0,6 s après) est retranscrit avec une consigne qui pousse Whisper à écrire les hésitations ; on repère :
  - « euh », « hum », « ben »… dans le passage, et les mots répétés (« je je », « c'est c'est ») -> retirés ;
  - un mot à cheval sur le début (début qui mange un mot) -> début avancé avant ce mot (ou après, si c'est un « euh ») ;
  - un mot à cheval sur la fin (fin coupée trop tôt) -> fin repoussée après ce mot (ou avant, si c'est un « euh »).
Sur E22, ce contrôle a retrouvé exactement les défauts entendus par Arthur. Coût ≈ 1,5× la durée contrôlée (CPU).
"""
from __future__ import annotations

import re
import subprocess
import unicodedata
from pathlib import Path

import numpy as np
from rich.console import Console

from .media import FFMPEG

console = Console()
PROMPT = "Euh, alors, euh... je, je pense que, hum, c'est, c'est... ben, voilà, euh."
HEDGES = {"enfin"}   # retirés seulement entre deux virgules (sinon c'est du sens : « enfin, on a compris que »)
FILLERS = {"euh", "heu", "euhm", "hum", "hmm", "hm", "ben", "bah", "beh", "eh", "mmh", "mh"}
# hallucinations classiques de Whisper sur un silence de fin
HALLU = re.compile(r"sous.?titr|amara|^st'?$|merci d'avoir regard", re.I)   # pas les nombres : « 3 000 »
_MODEL = None


def _model(cfg=None):
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        name = (cfg.transcribe.get("model") if cfg is not None else None) or "large-v3-turbo"
        _MODEL = WhisperModel(name, device="cpu", compute_type="int8")
    return _MODEL


def _norm(w: str) -> str:
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^a-z0-9']", "", "".join(c for c in w if not unicodedata.combining(c))).strip("'")


def verbatim(wav: Path, a: float, b: float, cfg=None) -> list[dict]:
    """Mots verbatim (temps absolus) entre a et b ; hésitations comprises, hallucinations retirées."""
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(wav), "-ac", "1",
                          "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
    audio = np.frombuffer(raw, np.float32)
    segs, _ = _model(cfg).transcribe(audio, language="fr", word_timestamps=True, initial_prompt=PROMPT,
                                     vad_filter=False, condition_on_previous_text=False, beam_size=5)
    out = []
    for s in segs:
        for w in s.words or []:
            txt = w.word.strip()
            if txt and not HALLU.search(txt):
                out.append({"s": round(a + w.start, 3), "e": round(a + w.end, 3), "w": txt, "n": _norm(txt),
                            "p": round(float(w.probability), 2)})
    return out


def _is_filler(w: dict) -> bool:
    return w["n"] in FILLERS


def inspect_segment(sg: dict, wav: Path, cfg=None, pre: float = 0.4, post: float = 0.6) -> dict:
    """Problèmes d'un passage : {"start_word", "end_word", "fillers": [...], "repeats": [...], "words": [...]}."""
    s0, s1 = float(sg["start"]), float(sg["end"])
    ws = verbatim(wav, max(0.0, s0 - pre), s1 + post, cfg)
    inside = [w for w in ws if w["s"] >= s0 - 0.02 and w["e"] <= s1 + 0.02]
    # auto-correction entre virgules (« qu'on est, enfin, plus de 3000 ») : hésitation, comme un « euh »
    hedges = [y for x, y in zip(inside, inside[1:]) if y["n"] in HEDGES and x["w"].endswith(",") and y["w"].endswith(",")]
    rep = {"words": ws, "fillers": [w for w in inside if _is_filler(w)] + hedges, "repeats": [],
           "start_word": next((w for w in ws if w["s"] < s0 - 0.02 < w["e"] - 0.04), None),
           "end_word": next((w for w in ws if w["s"] + 0.04 < s1 + 0.02 < w["e"]), None)}
    for x, y in zip(inside, inside[1:]):
        if x["n"] and x["n"] == y["n"] and len(x["n"]) <= 6 and y["s"] - x["e"] < 0.6:
            rep["repeats"].append(x)   # « je je », « c'est c'est » : on retire la 1re occurrence
    return rep


def _quiet(wav: Path, lo: float, hi: float) -> float:
    """Instant le plus silencieux du micro dans [lo, hi] (trames de 20 ms lissées) : les horodatages verbatim d'un
    « euh » varient de ±0,1 s d'une transcription à l'autre, l'énergie du son, non."""
    from .tighten import _rms
    if hi - lo < 0.04:
        return (lo + hi) / 2
    r = _rms(wav, lo, hi)
    if len(r) < 2:
        return (lo + hi) / 2
    r = np.convolve(r, np.ones(3) / 3, mode="same")
    return lo + (int(np.argmin(r)) + 0.5) * 0.02


def fix_segment(sg: dict, rep: dict, wav: Path) -> list[dict]:
    """Applique les corrections : bornes recalées, hésitations/répétitions retirées (le passage est découpé en morceaux,
    comme `tighten` : zoom de jonction + nouveau bloc de sous-titres à chaque raccord). Chaque coupe est posée au point
    le plus silencieux entre les deux mots concernés — jamais dans un mot voisin."""
    s0, s1 = float(sg["start"]), float(sg["end"])
    ws = rep["words"]

    def after(w):
        i = next(k for k, x in enumerate(ws) if x is w)
        return ws[i + 1] if i + 1 < len(ws) else None

    def before(w):
        i = next(k for k, x in enumerate(ws) if x is w)
        return ws[i - 1] if i > 0 else None

    sw, ew = rep["start_word"], rep["end_word"]
    if sw:
        if _is_filler(sw):          # le passage commençait dans un « euh » : on démarre après
            n = after(sw)
            s0 = _quiet(wav, sw["e"] - 0.08, (n["s"] if n else sw["e"] + 0.2) + 0.02)
        else:                       # il mangeait le début d'un mot : on démarre avant
            pv = before(sw)
            s0 = _quiet(wav, max(sw["s"] - 0.3, pv["e"] if pv else 0.0), sw["s"] + 0.03)
    if ew:
        if _is_filler(ew):          # il finissait dans un « euh » : on s'arrête avant
            pv = before(ew)
            s1 = _quiet(wav, (pv["e"] if pv else ew["s"] - 0.2) - 0.02, ew["s"] + 0.08)
        else:                       # il coupait le dernier mot : on va jusqu'au bout
            n = after(ew)
            s1 = _quiet(wav, ew["e"] - 0.03, min(ew["e"] + 0.3, n["s"] if n else ew["e"] + 0.3))
    holes = []
    for f in rep["fillers"] + rep["repeats"]:
        # voisins dans l'ORDRE des mots (E22 : un « euh » mal horodaté avait emporté « refonte »)
        pv, n = before(f), after(f)
        a = _quiet(wav, (pv["e"] if pv else f["s"]) - 0.03, f["s"] + 0.08)
        b = _quiet(wav, f["e"] - 0.08, (n["s"] if n else f["e"]) + 0.03)
        if s0 < a < b < s1 and b - a >= 0.12:
            holes.append((a, b))
    holes.sort()
    pieces, cur = [], s0
    for a, b in holes:
        if a - cur > 0.25:
            pieces.append((cur, a))
        cur = max(cur, b)
    if s1 - cur > 0.25:
        pieces.append((cur, s1))
    out = []
    for i, (a, b) in enumerate(pieces):
        p = dict(sg)
        p.update(start=round(a, 3), end=round(b, 3), duration=round(b - a, 3))
        if i < len(pieces) - 1:
            p["end_text"] = ""
        if i > 0:
            p["start_text"] = ""
        out.append(p)
    return out or [sg]


def _onset(v: np.ndarray, sustain: int = 5, max_k: int = 40) -> int | None:
    """Indice du démarrage de la vraie parole (≥ `sustain` trames voisées d'affilée) s'il est précédé d'au moins
    0,2 s sans parole (blanc, souffle ou fin de mot trop faible pour le seuil mais audible) ; sinon None."""
    for k in range(min(len(v) - sustain, max_k)):
        if v[k:k + sustain].all():
            return k if k >= 10 and not v[k - 10:k].any() else None
    return None


def trim_edges(sg: dict, wav: Path, look: float = 1.2) -> tuple[dict, list[str]]:
    """Bord qui commence sur la FIN d'un mot (ou finit sur le DÉBUT d'un mot) : un bout de voix, puis un blanc ≥ 0,2 s,
    puis la vraie parole -> on recale sur la parole (retour d'Arthur, teaser E22 : « il commence sur la fin d'un mot,
    coupe la demi-seconde du début »). Mesuré sur l'énergie du micro (trames de 20 ms), pas sur la transcription."""
    from .tighten import _rms
    s0, s1 = float(sg["start"]), float(sg["end"])
    notes = []
    r = _rms(wav, s0, min(s1, s0 + look))
    if len(r) > 15:
        k = _onset(r > 0.35 * float(np.percentile(r, 75)))
        if k is not None:
            s0 += k * 0.02 - 0.1
            notes.append(f"début sur une fin de mot ({k * 0.02 - 0.1:.2f} s retirées)")
    r = _rms(wav, max(s0, s1 - look), s1)
    if len(r) > 15:
        k = _onset((r > 0.35 * float(np.percentile(r, 75)))[::-1])
        if k is not None:
            s1 -= k * 0.02 - 0.15
            notes.append(f"fin sur un début de mot ({k * 0.02 - 0.15:.2f} s retirées)")
    if notes:
        sg = dict(sg, start=round(s0, 3), end=round(s1, 3), duration=round(s1 - s0, 3))
    return sg, notes


# ------------------------------------------------------------------------------- garde-fou de fin de phrase

TERMINAL = ".?!…"
# mots qui prolongent la phrase précédente : une coupe juste avant eux interrompt une pensée
CONTINUE = {"et", "parce", "car", "qui", "que", "qu", "dont", "où", "donc", "puisque", "pour", "avec", "en", "mais"}


def _ends_sentence(w: dict, nxt: dict | None) -> bool:
    """Fin de phrase : ponctuation finale (verbatim) et le mot suivant ne prolonge pas la phrase."""
    if w["w"].rstrip()[-1:] not in TERMINAL:
        return False
    if nxt is None:
        return True
    first = nxt["n"].split("'")[0]
    return not (first in CONTINUE and nxt["w"][:1].islower())


# ------------------------------------------------------- fins de phrase : transcription stable + vraie pause

def pause_after(wav: Path, t0: float, t1: float) -> float:
    """Plus long silence (s) autour de la frontière entre un mot (fin t0) et le suivant (début t1). Fenêtre élargie
    [t0 - 0,3 ; t1 + 0,5] : la transcription normale décale parfois les fins de mots de 0,2–0,3 s. Silence = trame de
    20 ms sous 25 % du niveau de parole voisin (90e centile sur ±1,5 s) — souffle compris. Calibré sur E22 :
    « venir ‖ et » 0,10 s, « leader ‖ En tout cas » 0,18 s (phrases qui continuent) ; « l'autre ‖ Donc » 0,34 s,
    « productivité ‖ Le » ≥ 0,36 s, « réinternaliser ‖ Mais » > 0,5 s (vraies fins)."""
    from .tighten import _rms
    ctx = _rms(wav, max(0.0, t0 - 1.5), t1 + 1.5)
    if not len(ctx):
        return 0.0
    thr = 0.25 * float(np.percentile(ctx, 90))
    r = _rms(wav, max(0.0, t0 - 0.3), t1 + 0.5)
    best = run = 0
    for x in r:
        run = run + 1 if x <= thr else 0
        best = max(best, run)
    return best * 0.02


def is_boundary(words: list[dict], i: int, wav: Path, min_pause: float = 0.25) -> bool:
    """Vraie fin de phrase après words[i] : ponctuation finale dans la transcription normale (stable sur tout
    l'épisode), mot suivant qui ne prolonge pas la phrase, ET une vraie pause dans le son (≥ min_pause). Le 3e indice
    ne dépend pas de Whisper : « …dans les années à venir. ‖ et je pense » (point, mais aucune pause) n'est PAS une fin
    — E22, teaser : la phrase était coupée net."""
    if i + 1 >= len(words):
        return True
    w, nx = words[i], words[i + 1]
    if w["w"].rstrip()[-1:] not in TERMINAL:
        return False
    if w.get("spk") and nx.get("spk") and w["spk"] != nx["spk"]:
        # l'autre reprend la parole : fin naturelle dès qu'il y a un petit blanc (sinon on sacrifiait la conclusion
        # de l'invité, E22 short 2 : « …à nos consommateurs. » suivi de Thomas à 0,1 s)
        return pause_after(wav, w["e"], nx["s"]) >= 0.08
    pause = pause_after(wav, w["e"], nx["s"])
    first = _norm(nx["w"]).split("'")[0]
    if first in CONTINUE and nx["w"][:1].islower() and pause < 0.5:
        return False   # « venir, et je pense » : la phrase continue ; après une vraie respiration, « donc » repart
    return pause >= min_pause


def sentence_bounds(words: list[dict], a: int, b: int, wav: Path, max_extend: float = 8.0,
                    max_back: float = 6.0) -> tuple[int, int, list[str]] | None:
    """Indices (début, fin) recalés sur de vraies frontières de phrase autour de words[a..b] ; None si impossible."""
    notes = []
    # début : words[a-1] doit être une frontière ; sinon on recule (≤ max_back s) puis on avance
    if a > 0 and not is_boundary(words, a - 1, wav):
        back = next((i for i in range(a - 1, 0, -1) if words[a]["s"] - words[i]["s"] <= max_back
                     and is_boundary(words, i - 1, wav)), None)
        fwd = next((i for i in range(a + 1, b) if is_boundary(words, i - 1, wav)), None)
        if back is not None:
            notes.append(f"début avancé à « {words[back]['w']} … » (début de phrase)")
            a = back
        elif fwd is not None:
            notes.append(f"début reporté à « {words[fwd]['w']} … » (début de phrase)")
            a = fwd
        else:
            return None
    # fin : words[b] doit être une frontière ; sinon on prolonge (≤ max_extend s) puis on raccourcit
    if not is_boundary(words, b, wav):
        fwd = next((i for i in range(b + 1, len(words)) if words[i]["e"] - words[b]["e"] <= max_extend
                    and words[i].get("spk", "") == words[b].get("spk", "") and is_boundary(words, i, wav)), None)
        back = next((i for i in range(b - 1, a, -1) if is_boundary(words, i, wav)), None)
        if fwd is not None:
            notes.append(f"fin prolongée jusqu'à « … {words[fwd]['w']} » (+{words[fwd]['e'] - words[b]['e']:.1f} s)")
            b = fwd
        elif back is not None and words[back]["e"] - words[a]["s"] >= 2.5:
            notes.append(f"fin ramenée à « … {words[back]['w']} »")
            b = back
        else:
            return None
    return a, b, notes


# ------------------------------------------------------------- découpe sur une transcription verbatim UNIQUE

def verbatim_sentences(ws: list[dict], max_gap: float = 1.0) -> list[list[dict]]:
    """Phrases du verbatim : coupure après une ponctuation finale (si le mot suivant ne prolonge pas la phrase) ou
    après un blanc > max_gap."""
    out, cur = [], []
    for i, w in enumerate(ws):
        cur.append(w)
        nxt = ws[i + 1] if i + 1 < len(ws) else None
        if nxt is None or _ends_sentence(w, nxt) or nxt["s"] - w["e"] > max_gap:
            out.append(cur)
            cur = []
    return out


def _marks(ws: list[dict]) -> set:
    """Indices des mots à retirer : « euh »…, « enfin » entre virgules, 1re occurrence d'un mot répété."""
    drop = {i for i, w in enumerate(ws) if _is_filler(w)}
    for i in range(1, len(ws) - 1):
        if ws[i]["n"] in HEDGES and ws[i - 1]["w"].endswith(",") and ws[i]["w"].endswith(","):
            drop.add(i)
    for i in range(len(ws) - 1):
        if ws[i]["n"] and ws[i]["n"] == ws[i + 1]["n"] and len(ws[i]["n"]) <= 6 and ws[i + 1]["s"] - ws[i]["e"] < 0.6:
            drop.add(i)
    return drop


def snap_extract(words: list[dict], a: int, b: int, wav: Path, cfg=None) -> tuple[list[dict], list[str]]:
    """Extrait words[a..b] (transcription normale) -> morceaux : bornes sur de VRAIES fins de phrase
    (`sentence_bounds` : ponctuation + pause réelle dans le son), puis UNE transcription verbatim de l'extrait pour
    retirer « euh » / répétitions à l'intérieur, chaque coupe au point le plus silencieux entre deux mots.

    Pourquoi pas le verbatim pour les bornes : sa ponctuation et ses « euh » changent d'une transcription à l'autre
    (E22 : « venir. » dans une passe, « venir, et » dans l'autre) — trop instable pour décider où une phrase finit."""
    r = sentence_bounds(words, a, b, wav)
    if r is None:
        return [], ["aucune vraie fin de phrase à portée"]
    a, b, notes = r
    prev_e = words[a - 1]["e"] if a > 0 else words[a]["s"] - 0.4
    next_s = words[b + 1]["s"] if b + 1 < len(words) else words[b]["e"] + 0.5
    s0 = _quiet(wav, max(prev_e, words[a]["s"] - 0.4), words[a]["s"] + 0.02)
    s1 = _quiet(wav, words[b]["e"] - 0.02, min(next_s, words[b]["e"] + 0.5))
    ws = verbatim(wav, s0, s1, cfg)
    drop = _marks(ws)
    holes = []
    for i in sorted(drop):
        f = ws[i]
        lo = ws[i - 1]["e"] if i > 0 else s0
        hi = ws[i + 1]["s"] if i + 1 < len(ws) else s1
        h0 = _quiet(wav, lo - 0.02, f["s"] + 0.06)
        h1 = _quiet(wav, f["e"] - 0.06, hi + 0.02)
        if s0 + 0.2 < h0 < h1 < s1 - 0.2 and h1 - h0 >= 0.12:
            holes.append((h0, h1))
    if drop:
        notes.append("retiré : " + " ".join(ws[i]["w"] for i in sorted(drop)))
    pieces, cur = [], s0
    for h0, h1 in sorted(holes):
        if h0 - cur > 0.25:
            pieces.append((cur, h0))
        cur = max(cur, h1)
    if s1 - cur > 0.25:
        pieces.append((cur, s1))
    return [{"start": round(x, 3), "end": round(y, 3), "duration": round(y - x, 3)} for x, y in pieces], notes


def complete_sentences(segs: list[dict], wav: Path, cfg=None, max_extend: float = 8.0, min_len: float = 2.5,
                       log=None) -> list[dict]:
    """Chaque passage (groupe `orig`) commence au début d'une phrase et finit à la fin d'une phrase.

    Retour d'Arthur (07/10/2026, teaser E22) : « des fois ça coupe quand même avant qu'il ait terminé sa phrase » —
    2 extraits sur 6 finissaient sur une virgule (« …de loin le leader, ‖ en tout cas, nous… »). La transcription
    VERBATIM fait foi (ponctuation + mot suivant) : fin hors phrase -> prolongée jusqu'à la fin de la phrase (≤ 8 s),
    sinon ramenée à la fin de phrase précédente ; aucune -> passage marqué `incomplete` (le teaser l'écarte). Chaque
    nouvelle borne est posée au point le plus silencieux entre les deux mots. Les morceaux internes (raccords « euh »)
    ne sont pas touchés : seules les bornes extérieures de chaque passage comptent."""
    groups: dict = {}
    for sg in segs:
        groups.setdefault(sg.get("orig", id(sg)), []).append(sg)
    out = []
    for o, ps in groups.items():
        first, last = ps[0], ps[-1]
        # --- fin
        ws = verbatim(wav, max(0.0, last["end"] - 4.0), last["end"] + max_extend + 1.0, cfg)
        k = max((i for i, w in enumerate(ws) if w["e"] <= last["end"] + 0.06), default=None)
        if k is not None and not _ends_sentence(ws[k], ws[k + 1] if k + 1 < len(ws) else None):
            fwd = next((i for i in range(k + 1, len(ws)) if _ends_sentence(ws[i], ws[i + 1] if i + 1 < len(ws) else None)
                        and ws[i]["e"] - last["end"] <= max_extend), None)
            back = next((i for i in range(k - 1, -1, -1) if ws[i]["s"] >= last["start"] and
                         _ends_sentence(ws[i], ws[i + 1])), None)
            if fwd is not None:
                j = fwd
                note = f"fin prolongée jusqu'à « {ws[j]['w']} » (+{ws[j]['e'] - last['end']:.1f} s)"
            elif back is not None and ws[back]["e"] - first["start"] >= min_len:
                j = back
                note = f"fin ramenée à « {ws[j]['w']} » ({ws[j]['e'] - last['end']:.1f} s)"
            else:
                j = None
                note = "phrase inachevée, aucune fin de phrase proche"
                for x in ps:
                    x["incomplete"] = True
            if j is not None:
                nx = ws[j + 1]["s"] if j + 1 < len(ws) else ws[j]["e"] + 0.4
                last["end"] = round(_quiet(wav, ws[j]["e"] - 0.02, min(nx, ws[j]["e"] + 0.4)), 3)
                last["duration"] = round(last["end"] - last["start"], 3)
            if log:
                log(f"{first['start']:.1f}s : {note}")
        elif k is not None and k + 1 < len(ws) and ws[k + 1]["s"] - ws[k]["e"] < 0.25:
            # bonne fin mais la phrase suivante enchaîne aussitôt : coupe au creux, jamais sur sa 1re syllabe
            last["end"] = round(_quiet(wav, ws[k]["e"] - 0.02, ws[k + 1]["s"] + 0.01), 3)
            last["duration"] = round(last["end"] - last["start"], 3)
        # --- début
        ws = verbatim(wav, max(0.0, first["start"] - 6.0), first["start"] + 3.0, cfg)
        k = next((i for i, w in enumerate(ws) if w["e"] > first["start"] + 0.06), None)
        if k is not None and k > 0:
            pv = ws[k - 1]
            if not (_ends_sentence(pv, ws[k]) or ws[k]["s"] - pv["e"] >= 0.6):
                st = next((i for i in range(k - 1, 0, -1) if _ends_sentence(ws[i - 1], ws[i])
                           and first["start"] - ws[i]["s"] <= 4.0), None)
                if st is not None:
                    first["start"] = round(_quiet(wav, ws[st - 1]["e"], ws[st]["s"] + 0.02), 3)
                    first["duration"] = round(first["end"] - first["start"], 3)
                    if log:
                        log(f"{first['start']:.1f}s : début avancé au début de la phrase « {ws[st]['w']} … »")
                elif log:
                    log(f"{first['start']:.1f}s : début en milieu de phrase (pas de début de phrase proche)")
        out += ps
    return out


def check_and_fix(segs: list[dict], wav: Path, cfg=None, passes: int = 2, log=None, on_fail: str = "restore") -> list[dict]:
    """Contrôle + correction en `passes` passes (une correction peut révéler un défaut voisin). Deux morceaux issus du
    même passage ne se chevauchent jamais (sinon on entendrait deux fois la même syllabe)."""
    for i, sg in enumerate(segs):
        sg.setdefault("orig", i)
    originals = {sg["orig"]: dict(sg) for sg in segs}
    segs = complete_sentences(segs, wav, cfg, log=log)
    for k in range(passes):
        out, changed = [], False
        for sg in segs:
            rep = inspect_segment(sg, wav, cfg)
            m = describe(rep)
            if m:
                changed = True
                if log:
                    log(f"passe {k + 1} · {sg['start']:.1f}s : " + " ; ".join(m))
                out += fix_segment(sg, rep, wav)
            else:
                out.append(sg)
        for x, y in zip(out, out[1:]):
            if x.get("orig") == y.get("orig") and y["start"] < x["end"] + 0.02:
                y["start"] = round(x["end"] + 0.02, 3)
                y["duration"] = round(y["end"] - y["start"], 3)
        segs = [x for x in out if x["duration"] > 0.25]
        if not changed:
            break
    trimmed = []
    for sg in segs:
        sg2, n = trim_edges(sg, wav)
        if n and log:
            log(f"{sg['start']:.1f}s : " + " ; ".join(n))
        trimmed.append(sg2)
    segs = trimmed
    # contrôle final : un passage qui garde un mot coupé (« euh » collé aux mots, sans silence où couper) n'est jamais
    # livré ainsi — teaser : on le retire (on_fail="drop") ; short : on revient à sa coupe d'origine (« restore »)
    bad = {sg["orig"] for sg in segs if any(m.startswith(("début coupé", "fin coupée"))
                                              for m in describe(inspect_segment(sg, wav, cfg)))}
    if bad:
        if log:
            log(f"défaut impossible à corriger proprement sur {len(bad)} passage(s) : "
                + ("retiré(s)" if on_fail == "drop" else "coupe d'origine conservée"))
        keep = []
        for o in sorted({sg["orig"] for sg in segs}, key=lambda o: [sg["orig"] for sg in segs].index(o)):
            if o not in bad:
                keep += [sg for sg in segs if sg["orig"] == o]
            elif on_fail != "drop":
                keep.append(dict(originals[o]))
        segs = keep
    return segs


def describe(rep: dict) -> list[str]:
    msgs = []
    if rep["start_word"]:
        msgs.append(f"début coupé dans « {rep['start_word']['w']} »")
    if rep["end_word"]:
        msgs.append(f"fin coupée dans « {rep['end_word']['w']} »")
    msgs += [f"« {f['w']} » à {f['s']:.1f}s" for f in rep["fillers"]]
    msgs += [f"répétition « {r['w']} » à {r['s']:.1f}s" for r in rep["repeats"]]
    return msgs


def verify_clip(clip: dict, wav: Path, cfg=None, fix: bool = True, words: list[dict] | None = None) -> tuple[dict, list[str]]:
    """Shorts : chaque segment du clip recalé comme le teaser (`snap_extract` : vraies fins de phrase, « euh » retirés,
    coupes dans les silences ; `trim_edges` sur les bords). Un segment sans fin de phrase propre garde sa coupe
    d'origine (jamais d'idée tronquée ni de mot amputé)."""
    notes, new = [], []
    for sg in clip["segments"]:
        if words is None:
            rep = inspect_segment(sg, wav, cfg)
            m = describe(rep)
            notes += [f"{sg['start']:.1f}s : " + " ; ".join(m)] if m else []
            new.append(sg)
            continue
        ia = next(i for i, w in enumerate(words) if w["s"] >= sg["start"] - 0.05)
        ib = max(i for i, w in enumerate(words) if w["e"] <= sg["end"] + 0.1)
        pieces, n = snap_extract(words, ia, ib, wav, cfg)
        if not pieces:
            notes.append(f"{sg['start']:.1f}s : {' ; '.join(n)} -> coupe d'origine conservée")
            new.append(sg)
            continue
        pieces[0], n0 = trim_edges(pieces[0], wav)
        pieces[-1], n1 = trim_edges(pieces[-1], wav)
        if n or n0 or n1:
            notes.append(f"{sg['start']:.1f}s : " + " ; ".join(n + n0 + n1))
        for k, p in enumerate(pieces):
            new.append(dict(sg, start=p["start"], end=p["end"], duration=round(p["end"] - p["start"], 3),
                            start_text=sg.get("start_text", "") if k == 0 else "",
                            end_text=sg.get("end_text", "") if k == len(pieces) - 1 else ""))
    if fix:
        clip = dict(clip, segments=new, duration=round(sum(float(s["duration"]) for s in new), 2))
    return clip, notes


# ------------------------------------------------------------------------- écoute finale du montage (audit)

def audit(segs: list[dict], wav: Path, out_wav: Path, cfg=None) -> str:
    """« Écoute » le montage : assemble l'audio des segments EXACTEMENT comme il sera monté, puis le retranscrit en
    verbatim d'un bloc. Renvoie le texte entendu, « ‖ » à chaque raccord. C'est le contrôle final : les autres
    vérifications lisent des horodatages (décalés de 0,1–0,3 s), celle-ci lit ce que le spectateur entendra."""
    parts = "".join(f"[0:a]atrim={s['start']:.3f}:{s['end']:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.01,"
                    f"afade=t=out:st={max(0.0, s['end'] - s['start'] - 0.01):.3f}:d=0.01[a{i}];"
                    for i, s in enumerate(segs))
    graph = parts + "".join(f"[a{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[a]"
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(wav), "-filter_complex", graph, "-map", "[a]", "-ac", "1",
                    "-ar", "16000", str(out_wav)], check=True)
    joins, t = [], 0.0
    for s in segs[:-1]:
        t += s["end"] - s["start"]
        joins.append(t)
    ws = verbatim(out_wav, 0.0, t + segs[-1]["end"] - segs[-1]["start"], cfg)
    txt, j = [], 0
    for w in ws:
        while j < len(joins) and w["s"] >= joins[j] - 0.05:
            txt.append("‖")
            j += 1
        txt.append(w["w"])
    return " ".join(txt)


def audit_flags(text: str) -> list[str]:
    """Alertes sur le texte entendu : hésitation restée, raccord au milieu d'un mot (« l ‖ 'expérimentation »)."""
    flags = []
    toks = text.split()
    for i, t in enumerate(toks):
        if _norm(t) in FILLERS:
            flags.append(f"« {t} » entendu")
        if t == "‖" and 0 < i < len(toks) - 1 and (toks[i + 1].startswith("'") or toks[i - 1].endswith("'")
                                                      or len(_norm(toks[i - 1])) == 1):
            flags.append(f"raccord dans un mot : « {toks[i - 1]} ‖ {toks[i + 1]} »")
    return flags
