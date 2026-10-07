"""Retire les « euh » (et les longs blancs) d'un passage sans le rendre saccadé.

Whisper n'écrit jamais les « euh » : ils se cachent dans les trous entre deux mots. On regarde l'énergie du micro dans
chaque trou ; un trou long qui contient de la voix = hésitation. Règles (demande d'Arthur, 07/10/2026 : « enlève les
euh, sans couper trop, pas saccadé ») :
  - seuls les trous >= `min_gap` s (0,5) sont touchés — les petites hésitations restent, c'est la respiration normale ;
  - on garde ~`keep` s (0,15) de silence de chaque côté du raccord, et on coupe toujours dans un creux (jamais dans une
    syllabe) ;
  - le raccord est un nouveau « segment » du clip : compose y applique le léger zoom des jonctions et recommence le bloc
    de sous-titres, comme pour un clip multi-passages.
Un blanc long SANS voix (> `max_silence` s) est raccourci de la même façon.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .media import FFMPEG
from .transcribe import all_words

FRAME = 0.02


def _rms(wav: Path, t0: float, t1: float) -> np.ndarray:
    p = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(wav), "-ac", "1",
                        "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(p, np.int16).astype(np.float64) / 32768
    n = len(a) // 320
    return np.sqrt((a[: n * 320].reshape(n, 320) ** 2).mean(1)) if n else np.zeros(0)


def tighten_segment(seg: dict, transcript: dict, wav: Path, min_gap: float = 0.5, keep: float = 0.15,
                    max_silence: float = 1.0, voiced_share: float = 0.25, min_cut: float = 0.6) -> list[dict]:
    """Découpe `seg` en morceaux sans les hésitations ; renvoie [seg] inchangé s'il n'y a rien à retirer."""
    s0, s1 = float(seg["start"]), float(seg["end"])
    words = [w for w in all_words(transcript) if s0 - 0.05 <= w["s"] and w["e"] <= s1 + 0.05]
    if len(words) < 3:
        return [seg]
    rms = _rms(wav, s0, s1)
    if not len(rms):
        return [seg]
    speech = rms[rms > 0.01]
    thr = 0.35 * float(np.median(speech)) if len(speech) else 0.004
    quiet = rms <= thr
    cuts: list[tuple[float, float, str]] = []
    for a, b in zip(words, words[1:]):
        g0, g1 = a["e"], b["s"]
        if g1 - g0 < min_gap or g0 < s0 or g1 > s1:
            continue
        i0, i1 = int((g0 - s0) / FRAME), int(np.ceil((g1 - s0) / FRAME))
        win = quiet[i0:i1]
        if not len(win):
            continue
        voiced = 1 - win.mean()
        kind = "euh" if voiced >= voiced_share else ("blanc" if g1 - g0 > max_silence else "")
        if not kind:
            continue
        # raccord dans les creux : premier creux après le mot qui précède, dernier creux avant le mot qui suit
        q = np.flatnonzero(win)
        if len(q) < 2:
            continue
        out_t = s0 + (i0 + q[0]) * FRAME + keep / 2
        in_t = s0 + (i0 + q[-1] + 1) * FRAME - keep / 2
        if kind == "blanc":  # blanc sans voix : on le ramène à `keep` + une respiration
            out_t, in_t = g0 + 0.25, g1 - 0.25
        if in_t - out_t >= min_cut:
            cuts.append((round(out_t, 3), round(in_t, 3), kind))
    if not cuts:
        return [seg]
    out, cur = [], s0
    for c0, c1, _ in cuts:
        out.append((cur, c0))
        cur = c1
    out.append((cur, s1))
    pieces = []
    for i, (a, b) in enumerate(out):
        p = dict(seg)
        p["start"], p["end"], p["duration"] = round(a, 3), round(b, 3), round(b - a, 3)
        if i < len(out) - 1:
            p["tail_silence"] = round(cuts[i][1] - cuts[i][0], 3)
            p["end_text"] = ""
        if i > 0:
            p["start_text"] = ""
        pieces.append(p)
    return pieces


def tighten_clip(clip: dict, transcript: dict, wav: Path, **kw) -> tuple[dict, list[tuple[float, float, str]]]:
    """Applique tighten_segment à chaque segment du clip ; renvoie (clip modifié, retraits [(début, fin, type)])."""
    new, removed = [], []
    for sg in clip["segments"]:
        parts = tighten_segment(sg, transcript, wav, **kw)
        removed += [(a["end"], b["start"], "euh/blanc") for a, b in zip(parts, parts[1:])]
        new += parts
    clip = dict(clip)
    clip["segments"] = new
    clip["duration"] = round(sum(float(s["duration"]) for s in new), 2)
    return clip, removed
