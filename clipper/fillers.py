"""« Euh » collés aux mots : détection acoustique + coupe validée par la transcription.

Retour d'Arthur (08/10/2026, short 1 de E22) : « faut que tu enlèves les euhhh, l'invité le fait beaucoup donc c'est
pas assez dynamique ». Ni `tighten` (trous entre mots) ni `verify` (verbatim) ne les voyaient : Whisper rattache le
« euh » au mot voisin, qui dure alors anormalement longtemps (« humaine » 3,2 s, « qu'il » 1,7 s) et le verbatim ne
l'écrit pas toujours.

Signal retenu : un « euh » (ou un mot traîné « humaineeee ») est une VOYELLE TENUE — de la voix dont le spectre ne
bouge presque pas pendant ≥ 0,28 s — alors qu'un vrai mot change sans arrêt. On coupe chaque passage tenu (bords posés
au point le plus silencieux à ±0,06 s), puis on VALIDE : la transcription normale du passage coupé doit garder tous les
mots porteurs de sens de l'original ; sinon cette coupe est annulée (on garde l'hésitation plutôt que de perdre un mot).
"""
from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

import numpy as np

from .media import FFMPEG
from .verify import FILLERS, _model, _norm, _quiet


def _load(wav: Path, a: float, b: float) -> np.ndarray:
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(wav), "-ac", "1",
                          "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def held_vowels(wav: Path, a: float, b: float, min_len: float = 0.2, pct: float = 40) -> list[tuple[float, float]]:
    """Passages de voix au spectre stable (flux spectral lissé sous le `pct`e centile de la parole du passage)."""
    x = _load(wav, a, b)
    hop, win = 160, 512                      # 10 ms / 32 ms à 16 kHz
    n = (len(x) - win) // hop
    if n < 30:
        return []
    fr = np.stack([x[i * hop:i * hop + win] for i in range(n)]) * np.hanning(win)
    ls = np.log(np.abs(np.fft.rfft(fr, axis=1))[:, 4:128] + 1e-6)          # 125–4000 Hz
    rms = np.sqrt((fr ** 2).mean(1))
    flux = np.convolve(np.r_[0, np.abs(np.diff(ls, axis=0)).mean(1)], np.ones(5) / 5, "same")
    speech = rms > 0.25 * np.percentile(rms, 90)
    if speech.sum() < 20:
        return []
    held = speech & (flux < np.percentile(flux[speech], pct))
    out, k = [], 0
    while k < n:
        if not held[k]:
            k += 1
            continue
        j = k
        while j < n and (held[j] or (j + 3 < n and held[j:j + 3].any())):
            j += 1
        if (j - k) * 0.01 >= min_len:
            out.append((a + k * 0.01, a + j * 0.01))
        k = j
    return out


def _words(audio: np.ndarray, cfg=None) -> list[str]:
    """Transcription NORMALE (sans invite d'hésitations) -> mots normalisés porteurs de sens."""
    segs, _ = _model(cfg).transcribe(audio.astype(np.float32), language="fr", beam_size=5, vad_filter=False,
                                     condition_on_previous_text=False)
    ws = [_norm(t) for s in segs for t in s.text.replace("'", "' ").split()]
    return [w for w in ws if len(w) >= 3 and w not in FILLERS]


def _without(audio: np.ndarray, a: float, holes: list[tuple[float, float]]) -> np.ndarray:
    keep, cur = [], 0
    for p, q in holes:
        i, j = int((p - a) * 16000), int((q - a) * 16000)
        keep.append(audio[cur:i])
        cur = j
    keep.append(audio[cur:])
    return np.concatenate(keep)


def cut_fillers(seg: dict, wav: Path, cfg=None, log=print, min_piece: float = 0.25) -> list[dict]:
    """Découpe `seg` sans ses voyelles tenues ; chaque coupe est validée par la transcription. Renvoie [seg] si rien.

    Rapidité (sans perte de qualité) : on tente d'abord TOUTES les coupes du passage d'un coup — une seule
    transcription ; si aucun mot n'est perdu, elles sont toutes validées. Sinon, validation une par une (Whisper
    traite toujours des blocs de 30 s : des fenêtres plus courtes ne coûteraient pas moins cher)."""
    s0, s1 = float(seg["start"]), float(seg["end"])
    cands = [(p, q) for p, q in held_vowels(wav, s0, s1) if q < s1 - 0.15]
    if not cands:
        return [seg]
    audio = _load(wav, s0, s1)
    ref = Counter(_words(audio, cfg))

    def lost_with(holes_try: list[tuple[float, float]]) -> Counter:
        got = Counter(_words(_without(audio, s0, sorted(holes_try)), cfg))
        # un mot RÉPÉTÉ peut perdre une occurrence (« humaine, euh, humaine » -> « humaine ») ; un mot dit une seule
        # fois ne doit jamais disparaître
        return Counter({w: k for w, k in (ref - got).items() if got[w] == 0})

    def plan(validate: bool) -> list[tuple[float, float]]:
        holes: list[tuple[float, float]] = []
        for p, q in cands:
            a = _quiet(wav, p - 0.06, p + 0.06)
            b = _quiet(wav, q - 0.06, q + 0.06)
            if b - a < 0.15:
                continue
            # chaque morceau gardé doit rester assez long (jamais de morceau jeté sans avoir été validé)
            edges = sorted([s0, s1] + [x for h in holes for x in h])
            lo = max((e for e in edges if e <= a), default=s0)
            hi = min((e for e in edges if e >= b), default=s1)
            merged = None
            if lo == s0 and a - lo < min_piece:      # hésitation en tout début de passage : on démarre après elle
                a = s0
            elif a - lo < min_piece and any(h[1] == lo for h in holes):   # deux « euh » quasi collés : une coupe
                merged = next(h for h in holes if h[1] == lo)
                a = merged[0]
            elif a - lo < min_piece or hi - b < min_piece:
                continue
            others = [h for h in holes if h != merged]
            if validate:
                lost = lost_with(others + [(a, b)])
                if lost:
                    log(f"    {a:.2f}-{b:.2f} gardé (la coupe ferait perdre : {', '.join(lost)})")
                    continue
                log(f"    {a:.2f}-{b:.2f} retiré ({b - a:.2f} s){' (fusionné avec la coupe précédente)' if merged else ''}")
            holes = sorted(others + [(a, b)])
        return holes

    holes = plan(validate=False)
    if holes and not lost_with(holes):
        for a, b in holes:
            log(f"    {a:.2f}-{b:.2f} retiré ({b - a:.2f} s)")
        log(f"    {len(holes)} coupe(s) validée(s) en une seule écoute")
    else:
        holes = plan(validate=True)
    if not holes:
        return [seg]
    pieces, cur = [], s0
    for a, b in sorted(holes):
        if a > cur:
            pieces.append((cur, a))
        cur = b
    pieces.append((cur, s1))
    out = []
    for i, (a, b) in enumerate(pieces):
        pc = dict(seg, start=round(a, 3), end=round(b, 3), duration=round(b - a, 3))
        if i < len(pieces) - 1:
            pc["end_text"] = ""
        if i > 0:
            pc["start_text"] = ""
        out.append(pc)
    return out


def clean_clip(clip: dict, wav: Path, cfg=None, log=print, work: Path | None = None) -> tuple[dict, str]:
    """Retire les voyelles tenues de tous les segments d'un clip, puis écoute le résultat (`verify.audit`) : un raccord
    qui tombe au milieu d'un mot (« qu ‖ 'il ») annule la coupe correspondante (on garde l'hésitation)."""
    import tempfile

    from .verify import audit, audit_joins
    orig = clip["segments"]
    segs = []
    for sg in orig:
        log(f"  segment {float(sg['start']):.2f}-{float(sg['end']):.2f}")
        segs += cut_fillers(sg, wav, cfg, log)
    out_wav = (work or Path(tempfile.gettempdir())) / f"fillers_audit_{clip.get('index', 0):02d}.wav"
    # joins d'origine (entre segments de départ) : on ne touche qu'aux raccords créés ici
    new_join = lambda k: segs[k]["end"] not in [o["end"] for o in orig]
    heard = audit(segs, wav, out_wav, cfg)
    for _ in range(3):
        bad = [k for k in audit_joins(heard) if k + 1 < len(segs) and new_join(k)]
        if not bad:
            break
        for k in sorted(bad, reverse=True):
            log(f"    raccord dans un mot à {segs[k]['end']:.2f} : coupe annulée")
            segs[k] = dict(segs[k], end=segs[k + 1]["end"], end_text=segs[k + 1].get("end_text", ""),
                           duration=round(segs[k + 1]["end"] - segs[k]["start"], 3))
            del segs[k + 1]
        heard = audit(segs, wav, out_wav, cfg)
    clip = dict(clip, segments=segs, duration=round(sum(s["duration"] for s in segs), 2))
    return clip, heard
