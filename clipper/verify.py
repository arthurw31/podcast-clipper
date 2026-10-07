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
HALLU = re.compile(r"sous.?titr|amara|st'?$|^\d{3,}$|merci d'avoir regard", re.I)
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


def check_and_fix(segs: list[dict], wav: Path, cfg=None, passes: int = 2, log=None, on_fail: str = "restore") -> list[dict]:
    """Contrôle + correction en `passes` passes (une correction peut révéler un défaut voisin). Deux morceaux issus du
    même passage ne se chevauchent jamais (sinon on entendrait deux fois la même syllabe)."""
    for i, sg in enumerate(segs):
        sg.setdefault("orig", i)
    originals = {sg["orig"]: dict(sg) for sg in segs}
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


def verify_clip(clip: dict, wav: Path, cfg=None, fix: bool = True) -> tuple[dict, list[str]]:
    """Contrôle (et corrige si fix) chaque segment du clip ; un 2e passage vérifie le résultat."""
    notes, new = [], []
    for sg in clip["segments"]:
        rep = inspect_segment(sg, wav, cfg)
        msgs = describe(rep)
        if msgs:
            notes.append(f"{sg['start']:.1f}s : " + " ; ".join(msgs))
        new += fix_segment(sg, rep, wav) if (fix and msgs) else [sg]
    if fix and notes:
        left = []
        for sg in new:
            m = describe(inspect_segment(sg, wav, cfg))
            if m:
                left.append(f"{sg['start']:.1f}s : " + " ; ".join(m))
        notes.append("après correction : " + ("RAS" if not left else " | ".join(left)))
        clip = dict(clip, segments=new, duration=round(sum(float(s["duration"]) for s in new), 2))
    return clip, notes
