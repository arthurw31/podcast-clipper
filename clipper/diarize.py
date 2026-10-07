"""Qui parle quand, sur tout l'épisode, à partir du micro (mono : les deux voix sur la même piste).

Pourquoi pas la bouche : sur E22 le signal de bouche est net pour l'invité mais bruité pour l'animateur (lunettes,
hochements) — inutilisable seul sur 52 min. Ici : empreintes vocales (WeSpeaker ResNet34, ONNX, 256 dim) sur des
fenêtres de 1,5 s, regroupées en 2 voix (k-moyennes cosinus), puis chaque mot de la transcription reçoit la voix
des fenêtres qui le couvrent, lissée sur quelques mots. `sherpa-onnx` / `speechbrain` ne sont pas utilisables sur ce PC
(DLL bloquée par le contrôle des applications Windows ; pas de torchaudio pour torch 2.14) : seul onnxruntime est requis.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .media import FFMPEG

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "wespeaker_resnet34_LM.onnx"
MODEL_URL = "https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/main/voxceleb_resnet34_LM.onnx"
SR = 16000


def _model():
    import onnxruntime as ort
    if not MODEL.exists():
        import requests
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        MODEL.write_bytes(requests.get(MODEL_URL, timeout=300).content)
    return ort.InferenceSession(MODEL.read_bytes(), providers=["CPUExecutionProvider"])


def load_audio(path: Path) -> np.ndarray:
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32)  # échelle int16, comme Kaldi


def _mel_bank(n_mels: int = 80, n_fft: int = 512) -> np.ndarray:
    def mel(f):
        return 1127.0 * np.log(1 + f / 700.0)
    lo, hi = mel(20.0), mel(SR / 2)
    pts = np.linspace(lo, hi, n_mels + 2)
    freqs = mel(np.arange(n_fft // 2 + 1) * SR / n_fft)
    fb = np.zeros((n_mels, n_fft // 2 + 1), np.float32)
    for m in range(n_mels):
        l, c, r = pts[m], pts[m + 1], pts[m + 2]
        up = (freqs - l) / (c - l)
        down = (r - freqs) / (r - c)
        fb[m] = np.maximum(0, np.minimum(up, down))
    return fb


_FB = None


def fbank(x: np.ndarray) -> np.ndarray:
    """Log-mel 80 « façon Kaldi » (25 ms / 10 ms, préaccentuation 0,97, fenêtre de Povey), puis moyenne retirée."""
    global _FB
    if _FB is None:
        _FB = _mel_bank()
    win, hop = 400, 160
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    n = 1 + (len(x) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    fr -= fr.mean(1, keepdims=True)
    fr = np.concatenate([fr[:, :1], fr[:, 1:] - 0.97 * fr[:, :-1]], axis=1)
    fr *= (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(win) / (win - 1))) ** 0.85
    spec = np.abs(np.fft.rfft(fr, 512)) ** 2
    feats = np.log(np.maximum(spec @ _FB.T, 1.1920929e-07)).astype(np.float32)
    return feats - feats.mean(0, keepdims=True)


def embed_windows(audio: np.ndarray, spans: list[tuple[float, float]], batch: int = 32) -> np.ndarray:
    sess = _model()
    out = []
    for i in range(0, len(spans), batch):
        feats = [fbank(audio[int(a * SR):int(b * SR)]) for a, b in spans[i:i + batch]]
        T = max(f.shape[0] for f in feats)
        arr = np.stack([np.pad(f, ((0, T - f.shape[0]), (0, 0)), mode="edge") for f in feats])
        out.append(sess.run(None, {"feats": arr})[0])
    e = np.concatenate(out)
    return e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)


def _kmeans2(x: np.ndarray, iters: int = 30) -> np.ndarray:
    """k-moyennes cosinus à 2 classes, initialisées sur les deux points les plus éloignés."""
    a = x[np.argmin(x @ x.mean(0))]
    b = x[np.argmin(x @ a)]
    c = np.stack([a, b])
    for _ in range(iters):
        lab = np.argmax(x @ c.T, axis=1)
        new = np.stack([x[lab == k].mean(0) if (lab == k).any() else c[k] for k in range(2)])
        new /= np.linalg.norm(new, axis=1, keepdims=True)
        if np.allclose(new, c):
            break
        c = new
    return c


def diarize(words: list[dict], wav: Path, host_ref: tuple[float, float], win: float = 1.5, hop: float = 0.75,
            smooth: int = 5) -> list[dict]:
    """Ajoute `spk` ("host" / "guest") et `spk_conf` à chaque mot (clés s/e/w). `host_ref` : un intervalle où seul
    l'animateur parle (ex. son introduction) — sert à nommer les deux voix trouvées."""
    audio = load_audio(wav)
    dur = len(audio) / SR
    # fenêtres là où l'on parle (au moins 60 % de la fenêtre couverte par des mots)
    cover = np.zeros(int(dur * 100) + 1, bool)
    for w in words:
        cover[int(w["s"] * 100):int(w["e"] * 100) + 1] = True
    spans = [(t, t + win) for t in np.arange(0, dur - win, hop) if cover[int(t * 100):int((t + win) * 100)].mean() >= 0.6]
    emb = embed_windows(audio, spans)
    cent = _kmeans2(emb)
    mid = np.array([(a + b) / 2 for a, b in spans])
    ref = (mid >= host_ref[0]) & (mid <= host_ref[1])
    host_k = int(np.argmax((emb[ref] @ cent.T).mean(0))) if ref.any() else 0
    sim = emb @ cent.T                       # (fenêtres, 2)
    margin = sim[:, host_k] - sim[:, 1 - host_k]   # > 0 : animateur
    # chaque mot : moyenne des marges des fenêtres qui le recouvrent (pondérée par le recouvrement)
    starts = np.array([a for a, _ in spans])
    vals = []
    for w in words:
        i0 = np.searchsorted(starts, w["s"] - win)
        i1 = np.searchsorted(starts, w["e"])
        m = [(min(w["e"], starts[i] + win) - max(w["s"], starts[i]), margin[i]) for i in range(i0, i1)
             if starts[i] < w["e"] and starts[i] + win > w["s"]]
        tot = sum(o for o, _ in m)
        vals.append(sum(o * v for o, v in m) / tot if tot > 0 else 0.0)
    v = np.array(vals)
    if smooth > 1 and len(v) >= smooth:
        v = np.convolve(v, np.ones(smooth) / smooth, mode="same")
    for w, x in zip(words, v):
        w["spk"] = "host" if x > 0 else "guest"
        w["spk_conf"] = round(float(abs(x)), 3)
    return words


def turns_from_words(words: list[dict], min_turn: float = 1.2) -> list[dict]:
    """Tours de parole [{start, end, speaker}] ; les tours plus courts que `min_turn` (« oui », « exactement »)
    sont rattachés au tour précédent : on ne coupe pas la caméra pour une réaction brève."""
    turns: list[dict] = []
    for w in words:
        if turns and turns[-1]["speaker"] == w["spk"]:
            turns[-1]["end"] = w["e"]
        else:
            turns.append({"start": w["s"], "end": w["e"], "speaker": w["spk"]})
    out: list[dict] = []
    for t in turns:
        if out and (t["end"] - t["start"] < min_turn or out[-1]["speaker"] == t["speaker"]):
            out[-1]["end"] = t["end"]
        else:
            out.append(dict(t))
    return out
