"""Analyse d'un extrait : coupes de plan, visages (YuNet), locuteur actif.

Indépendant du format de sortie. Produit `analysis.json` :
{
  "width": 1280, "height": 720, "duration": 42.0, "sample_fps": 5,
  "shots": [ {"t0": 0, "t1": 4.2, "persons": [ {"id": 0, "cx": 0.31, "cy": 0.42, "fw": 0.08, "fh": 0.14, "activity": 0.61} ],
               "segments": [ {"t0": 0, "t1": 2.1, "speaker": 0, "confidence": 0.7} ] } ]
}
Toutes les coordonnées sont des fractions de la taille source.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from rich.console import Console

from .config import MODELS_DIR, Cfg
from .media import detect_scene_cuts, probe

console = Console()
YUNET = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
DET_W = 640


@dataclass
class Face:
    t: float
    x: float
    y: float
    w: float
    h: float
    conf: float
    lm: np.ndarray  # 5 points (x,y) : oeil D, oeil G, nez, bouche D, bouche G
    mouth: np.ndarray | None = None  # imagette bouche (gris) pour l'activité


@dataclass
class Track:
    id: int
    faces: list[Face] = field(default_factory=list)

    @property
    def cx(self) -> float:
        return float(np.median([f.x + f.w / 2 for f in self.faces]))

    @property
    def cy(self) -> float:
        return float(np.median([f.y + f.h / 2 for f in self.faces]))

    @property
    def fw(self) -> float:
        return float(np.median([f.w for f in self.faces]))

    @property
    def fh(self) -> float:
        return float(np.median([f.h for f in self.faces]))

    def motion(self) -> float:
        """Écart-type de la position du nez, normalisé par la largeur du visage."""
        if len(self.faces) < 4:
            return 1.0
        nose = np.array([f.lm[2] for f in self.faces])
        return float((nose[:, 0].std() + nose[:, 1].std()) / max(self.fw, 1e-6))


def _detector(w: int, h: int):
    # chargé depuis un buffer : OpenCV ne lit pas les chemins accentués sous Windows
    buf = np.frombuffer(YUNET.read_bytes(), dtype=np.uint8)
    det = cv2.FaceDetectorYN.create("onnx", buf, np.array([], dtype=np.uint8), (w, h), 0.6, 0.3, 500)
    det.setInputSize((w, h))
    return det


def _mouth_patch(gray: np.ndarray, lm: np.ndarray) -> np.ndarray | None:
    """Imagette normalisée de la bouche (repères en pixels de `gray`)."""
    nose, mr, ml = lm[2], lm[3], lm[4]
    mouth_y = (mr[1] + ml[1]) / 2
    d = max(mouth_y - nose[1], 4.0)
    x0 = int(min(mr[0], ml[0]) - 0.35 * d)
    x1 = int(max(mr[0], ml[0]) + 0.35 * d)
    y0 = int(nose[1] + 0.45 * d)
    y1 = int(mouth_y + 0.9 * d)
    h, w = gray.shape
    x0, x1 = max(0, x0), min(w, x1)
    y0, y1 = max(0, y0), min(h, y1)
    if x1 - x0 < 4 or y1 - y0 < 3:
        return None
    patch = cv2.resize(gray[y0:y1, x0:x1], (32, 16), interpolation=cv2.INTER_AREA).astype(np.float32)
    # normalisation d'éclairage : la mesure d'activité ne doit dépendre que du mouvement
    return (patch - patch.mean()) / (patch.std() + 1e-3)


def sample_faces(video: Path, sample_fps: float, ignore_regions: list[dict]) -> tuple[list[list[Face]], dict]:
    """Détecte les visages sur des images échantillonnées. Coordonnées en fractions."""
    info = probe(video)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or info["fps"] or 30
    step = max(1, int(round(fps / sample_fps)))
    sw, sh = info["width"], info["height"]
    dw = DET_W
    dh = int(round(sh * dw / sw))
    det = _detector(dw, dh)
    frames: list[list[Face]] = []
    times: list[float] = []
    i = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            t = i / fps
            small = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)
            gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            _, faces = det.detect(small)
            found: list[Face] = []
            for f in (faces if faces is not None else []):
                x, y, w, h = f[:4]
                conf = float(f[-1])
                lm = f[4:14].reshape(5, 2)
                cx, cy = (x + w / 2) / dw, (y + h / 2) / dh
                if any(r["x"] <= cx <= r["x"] + r["w"] and r["y"] <= cy <= r["y"] + r["h"] for r in ignore_regions):
                    continue
                lm_full = lm * np.array([sw / dw, sh / dh])
                found.append(Face(t=t, x=x / dw, y=y / dh, w=w / dw, h=h / dh, conf=conf,
                                  lm=lm / np.array([dw, dh]), mouth=_mouth_patch(gray_full, lm_full)))
            frames.append(found)
            times.append(t)
        i += 1
    cap.release()
    meta = {"width": sw, "height": sh, "duration": info["duration"], "fps": fps, "sample_fps": fps / step, "times": times}
    return frames, meta


def build_tracks(frames: list[list[Face]], t0: float, t1: float, min_presence: float = 0.35,
                 min_motion: float = 0.012) -> list[Track]:
    """Regroupe les visages d'un plan en pistes (par proximité du centre)."""
    tracks: list[Track] = []
    n_frames = 0
    for faces in frames:
        if not faces:
            continue
        if faces[0].t < t0 or faces[0].t >= t1:
            continue
        n_frames += 1
        for f in faces:
            cx, cy = f.x + f.w / 2, f.y + f.h / 2
            best, best_d = None, 1e9
            for tr in tracks:
                last = tr.faces[-1]
                d = abs(last.x + last.w / 2 - cx) + abs(last.y + last.h / 2 - cy)
                if d < best_d and d < max(last.w, 0.04) * 1.2:
                    best, best_d = tr, d
            if best is None:
                best = Track(id=len(tracks))
                tracks.append(best)
            best.faces.append(f)
    if n_frames == 0:
        return []
    kept = []
    for tr in tracks:
        presence = len(tr.faces) / n_frames
        if presence < min_presence:
            continue
        if tr.motion() < min_motion:
            continue
        kept.append(tr)
    kept.sort(key=lambda t: t.cx)
    for i, tr in enumerate(kept):
        tr.id = i
    return kept


def _mouth_diffs(track: Track, t0: float, t1: float) -> list[float]:
    prev = None
    vals = []
    for f in track.faces:
        if f.t < t0 or f.t >= t1 or f.mouth is None:
            continue
        if prev is not None and prev.shape == f.mouth.shape:
            vals.append(float(np.abs(f.mouth - prev).mean()))
        prev = f.mouth
    return vals


def mouth_activity(track: Track, t0: float, t1: float, floor: float = 0.0) -> float:
    """Activité moyenne de la bouche (différence inter-images) sur [t0, t1], moins le bruit de fond."""
    vals = _mouth_diffs(track, t0, t1)
    return max(0.0, float(np.mean(vals)) - floor) if vals else 0.0


def noise_floor(track: Track) -> float:
    """Bruit de fond d'une piste : 25e percentile de ses différences (quand la bouche est immobile)."""
    vals = _mouth_diffs(track, -1, 1e9)
    return float(np.percentile(vals, 25)) if len(vals) >= 6 else 0.0


def speaker_segments(tracks: list[Track], t0: float, t1: float, window: float = 1.5,
                     min_seg: float = 1.4, min_conf: float = 0.58) -> list[dict]:
    """Découpe [t0,t1] en segments de locuteur actif (heuristique bouche)."""
    if len(tracks) < 2:
        return [{"t0": t0, "t1": t1, "speaker": 0 if tracks else None, "confidence": 1.0}]
    floors = [noise_floor(tr) for tr in tracks]
    wins = []
    t = t0
    while t < t1 - 1e-6:
        te = min(t1, t + window)
        acts = np.array([mouth_activity(tr, t, te, fl) for tr, fl in zip(tracks, floors)])
        total = acts.sum()
        if total <= 1e-6:
            wins.append({"t0": t, "t1": te, "speaker": None, "confidence": 0.0})
        else:
            k = int(acts.argmax())
            wins.append({"t0": t, "t1": te, "speaker": k, "confidence": float(acts[k] / total)})
        t = te
    # lissage : fenêtres peu sûres héritent du voisin précédent
    for i, w in enumerate(wins):
        if w["speaker"] is None or w["confidence"] < min_conf:
            w["speaker"] = wins[i - 1]["speaker"] if i > 0 else None
            w["confidence"] = min(w["confidence"], min_conf - 0.01)
    # fusion des fenêtres consécutives
    segs: list[dict] = []
    for w in wins:
        if segs and segs[-1]["speaker"] == w["speaker"]:
            segs[-1]["t1"] = w["t1"]
            segs[-1]["confidence"] = max(segs[-1]["confidence"], w["confidence"])
        else:
            segs.append(dict(w))
    # segments trop courts absorbés par le voisin le plus long
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, s in enumerate(segs):
            if s["t1"] - s["t0"] < min_seg:
                if i > 0:
                    segs[i - 1]["t1"] = s["t1"]
                else:
                    segs[i + 1]["t0"] = s["t0"]
                segs.pop(i)
                changed = True
                break
    return segs


def _signature(faces: list[Face]) -> tuple:
    return tuple(sorted((round((f.x + f.w / 2) * 8), round(f.h * 6)) for f in faces))


def signature_cuts(frames: list[list[Face]], times: list[float], hold: int = 3) -> list[float]:
    """Instants où la configuration des visages change durablement (coupe manquée par ffmpeg)."""
    cuts: list[float] = []
    if not frames:
        return cuts
    cur = _signature(frames[0])
    i = 1
    while i < len(frames):
        sig = _signature(frames[i])
        if sig != cur:
            # la nouvelle configuration doit tenir `hold` échantillons
            if all(_signature(frames[j]) == sig for j in range(i, min(len(frames), i + hold))):
                cuts.append(times[i])
                cur = sig
                i += hold
                continue
        i += 1
    return cuts


def analyze(video: Path, cfg: Cfg, out_json: Path, force: bool = False) -> dict:
    if out_json.exists() and not force:
        return json.loads(out_json.read_text(encoding="utf-8"))
    fr = cfg.framing
    console.print("[bold]Analyse[/bold] · coupes + visages + locuteur")
    cuts = detect_scene_cuts(video, float(fr.scene_threshold))
    frames, meta = sample_faces(video, float(fr.sample_fps), list(fr.ignore_regions or []))
    D = meta["duration"]
    sig_cuts = [round(c, 3) for c in signature_cuts(frames, meta["times"]) if all(abs(c - k) > 0.4 for k in cuts)]
    cuts = sorted(set(cuts) | set(sig_cuts))
    bounds = [0.0] + [c for c in cuts if 0.3 < c < D - 0.3] + [D]
    # fusion des plans trop courts
    merged = [bounds[0]]
    for b in bounds[1:]:
        if b - merged[-1] < float(fr.min_shot_len) and b != D:
            continue
        merged.append(b)
    if merged[-1] != D:
        merged[-1] = D
    shots = []
    for a, b in zip(merged[:-1], merged[1:]):
        tracks = build_tracks(frames, a, b, min_motion=float(fr.min_face_motion))
        segs = speaker_segments(tracks, a, b, min_conf=float(fr.speaker_min_confidence))
        shots.append({
            "t0": round(a, 3), "t1": round(b, 3),
            "persons": [{"id": tr.id, "cx": round(tr.cx, 4), "cy": round(tr.cy, 4), "fw": round(tr.fw, 4),
                         "fh": round(tr.fh, 4), "activity": round(mouth_activity(tr, a, b), 4)} for tr in tracks],
            "segments": [{"t0": round(s["t0"], 3), "t1": round(s["t1"], 3), "speaker": s["speaker"],
                          "confidence": round(s["confidence"], 3)} for s in segs],
        })
    data = {"width": meta["width"], "height": meta["height"], "duration": D, "sample_fps": meta["sample_fps"], "shots": shots}
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print(f"  {len(shots)} plans · visages/plan : {[len(s['persons']) for s in shots]}")
    return data
