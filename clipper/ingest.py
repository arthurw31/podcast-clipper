"""Rushs déposés dans depot/ -> épisode prêt pour tout le pipeline (commande `rushes`).

Demande d'Arthur (09/10/2026) : « on dépose les rushs MP4 et l'audio dans le dossier depot » — 3 caméras (gros plan
invité, plan large, gros plan animateur) + 1 WAV du micro. Pour E22 tout avait été fait à la main ; ici :
1. chaque fichier est vérifié (`ffprobe` : un fichier tronqué, « moov atom not found », est écarté) ;
2. rôle des caméras : le plan large = celle qui montre 2 visages ; les 2 gros plans sont attribués en comparant
   chaque personne aux 2 personnes du plan large (couleurs du visage et du haut du corps), l'animateur étant du côté
   `framing.host_side` du plan large ; planche `rushes_<E>.jpg` à REGARDER, `--host <fichier>` pour corriger ;
3. décalage du micro sur les caméras : corrélation du son témoin du plan large avec le WAV (E22 : 0,037 s) ;
4. rangement : `brands/<m>/episodes/rushes/<E>/{invite,large,animateur}_<prénom>.mp4`, WAV ->
   `episodes/<E>_<invite>_<entreprise>.wav`, `<base>.mp4` = lien dur vers le plan large, `<base>.multicam.json`.
Les fichiers sont DÉPLACÉS (même disque : instantané), jamais copiés (13–14 Go par caméra).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rich.console import Console

from .analysis import _detector
from .compose import slugify
from .media import FFMPEG, FFPROBE

console = Console()
VIDEO_EXT = {".mp4", ".mov", ".mxf", ".m4v"}
AUDIO_EXT = {".wav", ".aif", ".aiff"}


def probe(path: Path) -> dict | None:
    """Durée et flux, ou None si le fichier est illisible (téléchargement tronqué)."""
    res = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True)
    if res.returncode != 0:
        return None
    try:
        info = json.loads(res.stdout)
        return {"duration": float(info["format"]["duration"]),
                "video": any(s.get("codec_type") == "video" for s in info.get("streams", [])),
                "audio": any(s.get("codec_type") == "audio" for s in info.get("streams", []))}
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def _frame(video: Path, t: float, width: int = 960) -> np.ndarray | None:
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                          "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                         capture_output=True).stdout
    if not raw:
        return None
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def _faces(img: np.ndarray) -> list[np.ndarray]:
    det = _detector(img.shape[1], img.shape[0])
    _, faces = det.detect(img)
    return [] if faces is None else [f for f in faces if f[14] > 0.8]


def _signature(img: np.ndarray, face: np.ndarray) -> np.ndarray:
    """Histogramme HSV du visage + haut du corps (couleur de peau, cheveux, vêtement) : reconnaître une personne d'une
    caméra à l'autre sans modèle de reconnaissance faciale."""
    x, y, w, h = (int(v) for v in face[:4])
    x0, x1 = max(0, x - w // 2), min(img.shape[1], x + w + w // 2)
    y0, y1 = max(0, y - h // 4), min(img.shape[0], y + int(h * 2.2))
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [18, 8], [0, 180, 0, 256]).flatten()
    return hist / (hist.sum() + 1e-6)


SFACE = Path(__file__).resolve().parent.parent / "models" / "face_recognition_sface.onnx"
SFACE_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
_REF = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
                np.float32)
_SF = None


def embedding(img: np.ndarray, face: np.ndarray) -> np.ndarray:
    """Empreinte du visage (SFace, ONNX, 128 dim) : visage aligné sur les 5 repères YuNet, 112×112 RGB."""
    global _SF
    if _SF is None:
        import onnxruntime as ort
        if not SFACE.exists():
            import requests
            SFACE.write_bytes(requests.get(SFACE_URL, timeout=300).content)
        opt = ort.SessionOptions()
        opt.log_severity_level = 3
        _SF = ort.InferenceSession(str(SFACE), opt, providers=["CPUExecutionProvider"])
    m, _ = cv2.estimateAffinePartial2D(face[4:14].reshape(5, 2).astype(np.float32), _REF)
    crop = cv2.warpAffine(img, m, (112, 112))
    x = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32).transpose(2, 0, 1)[None]
    e = _SF.run(None, {_SF.get_inputs()[0].name: x})[0][0]
    return e / (np.linalg.norm(e) + 1e-9)


def host_reference(brand) -> np.ndarray | None:
    """Empreinte de l'animateur depuis `assets/host_face.jpg` (Thomas Spitz : présent dans tous les épisodes)."""
    p = brand.asset((brand.cfg.get("rushes") or {}).get("host_face", "host_face.jpg"))
    if not p:
        return None
    img = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)
    fs = _faces(img)
    return embedding(img, max(fs, key=lambda f: f[2] * f[3])) if fs else None


def identify(videos: list[Path], duration: float, host_side: str, host_ref: np.ndarray | None = None
             ) -> tuple[dict[str, Path], dict]:
    """{"host": …, "guest": …, "wide": …} + détails. Animateur reconnu à son visage si `host_ref` (le côté où il est
    assis change d'un épisode à l'autre : E21 à droite, E22 à gauche) ; sinon couleurs + `host_side` de la marque."""
    times = [duration * k for k in (0.2, 0.35, 0.5, 0.65, 0.8)]
    stats = {}
    for v in videos:
        frames = [f for t in times if (f := _frame(v, t)) is not None]
        faces = [_faces(f) for f in frames]
        two = sum(1 for fs in faces if len(fs) >= 2)
        sizes = [max(f[3] for f in fs) / fr.shape[0] for fr, fs in zip(frames, faces) if fs]
        stats[v] = {"frames": frames, "faces": faces, "two": two, "size": float(np.median(sizes)) if sizes else 0.0}
    wide = max(videos, key=lambda v: (stats[v]["two"], -stats[v]["size"]))
    close = [v for v in videos if v != wide]
    # personnes du plan large : gauche / droite
    left_sig, right_sig = [], []
    for fr, fs in zip(stats[wide]["frames"], stats[wide]["faces"]):
        if len(fs) >= 2:
            fs = sorted(fs, key=lambda f: f[0])
            left_sig.append(_signature(fr, fs[0]))
            right_sig.append(_signature(fr, fs[-1]))
    detail = {"wide": wide.name, "two_faces": {v.name: stats[v]["two"] for v in videos}}
    if host_ref is not None and len(close) == 2:
        def sim(v):
            es = [embedding(fr, max(fs, key=lambda f: f[3])) for fr, fs in zip(stats[v]["frames"], stats[v]["faces"]) if fs]
            return float(np.mean([e @ host_ref for e in es])) if es else -1.0
        s = {v: sim(v) for v in close}
        host = max(close, key=s.get)
        guest = next(v for v in close if v != host)
        # côté de l'animateur dans le plan large (utile à --host-side)
        sides = []
        for fr, fs in zip(stats[wide]["frames"], stats[wide]["faces"]):
            if len(fs) >= 2:
                fs = sorted(fs, key=lambda f: f[0])
                sides.append("left" if embedding(fr, fs[0]) @ host_ref > embedding(fr, fs[-1]) @ host_ref else "right")
        side = max(set(sides), key=sides.count) if sides else host_side
        detail.update({"face_similarity": {v.name: round(s[v], 3) for v in close}, "host_side": side,
                       "sure": s[host] > 0.35 and s[host] - s[guest] > 0.15})
        return {"wide": wide, "host": host, "guest": guest}, detail
    if not left_sig or len(close) != 2:
        return {"wide": wide, "host": close[0], "guest": close[-1]}, {**detail, "sure": False}
    L, R = np.mean(left_sig, axis=0), np.mean(right_sig, axis=0)
    sim = {}
    for v in close:
        sigs = [_signature(fr, max(fs, key=lambda f: f[3])) for fr, fs in zip(stats[v]["frames"], stats[v]["faces"]) if fs]
        s = np.mean(sigs, axis=0) if sigs else np.zeros_like(L)
        sim[v] = (float(cv2.compareHist(s.astype(np.float32), L.astype(np.float32), cv2.HISTCMP_CORREL)),
                  float(cv2.compareHist(s.astype(np.float32), R.astype(np.float32), cv2.HISTCMP_CORREL)))
    a, b = close
    # a = gauche si (a~L + b~R) > (a~R + b~L)
    a_left = sim[a][0] + sim[b][1] >= sim[a][1] + sim[b][0]
    left_cam, right_cam = (a, b) if a_left else (b, a)
    host, guest = (right_cam, left_cam) if host_side == "right" else (left_cam, right_cam)
    margin = abs((sim[a][0] + sim[b][1]) - (sim[a][1] + sim[b][0]))
    detail.update({"similarity": {v.name: [round(x, 3) for x in sim[v]] for v in close}, "margin": round(margin, 3),
                   "sure": margin > 0.15})
    return {"wide": wide, "host": host, "guest": guest}, detail


def _pcm(path: Path, start: float, dur: float, sr: int = 8000) -> np.ndarray:
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(path),
                          "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32)


def audio_delay(cam: Path, mic: Path, at: float, dur: float = 90.0, max_lag: float = 3.0, sr: int = 8000) -> tuple[float, float]:
    """Décalage (s) tel que le son de la caméra à l'instant t = micro à t - délai (convention de multicam.json :
    `a_start = start - audio_delay`). Corrélation croisée (FFT) des enveloppes, puis affinage sur le signal brut.
    Renvoie (délai, netteté du pic)."""
    a = _pcm(cam, at, dur, sr)
    b = _pcm(mic, at, dur, sr)
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    size = 1 << int(np.ceil(np.log2(2 * n)))
    corr = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
    lags = np.concatenate([np.arange(0, size // 2), np.arange(-size // 2, 0)])
    keep = np.abs(lags) <= max_lag * sr
    corr, lags = corr[keep], lags[keep]
    i = int(np.argmax(np.abs(corr)))
    peak = float(np.abs(corr[i]) / (np.sort(np.abs(corr))[-int(0.02 * len(corr)) - 1] + 1e-9))
    return float(lags[i] / sr), peak


def _label_font(size: int):
    for f in (Path(__file__).resolve().parent.parent / "brands" / "ai-corner" / "assets" / "fonts" / "Montserrat-VF.ttf",
              Path("C:/Windows/Fonts/arial.ttf"), Path("/Library/Fonts/Arial.ttf")):
        if f.exists():
            return ImageFont.truetype(str(f), size)
    return ImageFont.load_default(size)


def contact(roles: dict[str, Path], out: Path, labels: dict[str, str]) -> Path:
    tiles = []
    for role in ("guest", "wide", "host"):
        v = roles[role]
        dur = probe(v)["duration"]
        img = _frame(v, dur * 0.5, 640)
        im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)) if img is not None else Image.new("RGB", (640, 360))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, im.width, 54], fill=(0, 0, 0))
        d.text((8, 4), labels[role], fill=(255, 255, 255), font=_label_font(22))
        d.text((8, 30), v.name[:60], fill=(200, 200, 200), font=_label_font(16))
        tiles.append(im)
    sheet = Image.new("RGB", (sum(t.width for t in tiles), max(t.height for t in tiles)), (20, 20, 20))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width
    sheet.save(out, quality=88)
    return out


def ingest(brand, src: Path, episode: str, guest: str, company: str, host_file: str | None = None,
           dry_run: bool = False) -> dict:
    files = [p for p in sorted(src.iterdir()) if p.is_file()]
    videos, audios, broken = [], [], []
    for p in files:
        ext = p.suffix.lower()
        if ext not in VIDEO_EXT | AUDIO_EXT:
            continue
        info = probe(p)
        if info is None:
            broken.append(p)
        elif ext in VIDEO_EXT and info["video"]:
            videos.append(p)
        elif ext in AUDIO_EXT:
            audios.append(p)
    for p in broken:
        console.print(f"[yellow]Illisible (téléchargement tronqué ?) : {p.name} — ignoré[/yellow]")
    if len(videos) != 3 or not audios:
        raise SystemExit(f"Il faut 3 vidéos lisibles + 1 audio dans {src} : trouvé {len(videos)} vidéo(s) "
                         f"({', '.join(v.name for v in videos)}) et {len(audios)} audio(s)")
    mic = max(audios, key=lambda p: p.stat().st_size)
    durs = {v: probe(v)["duration"] for v in videos}
    dur = min(durs.values())
    if max(durs.values()) - dur > 2.0:
        console.print("[yellow]Les caméras n'ont pas la même durée (écart > 2 s) : synchro à vérifier[/yellow]")
    host_side = (brand.cfg.get("framing") or {}).get("host_side", "left")
    roles, detail = identify(videos, dur, host_side, host_reference(brand))
    if host_file:
        h = next(v for v in videos if host_file.lower() in v.name.lower())
        others = [v for v in videos if v not in (h, roles["wide"])]
        roles = {"wide": roles["wide"], "host": h, "guest": others[0]}
        detail["sure"] = True
    delay, peak = audio_delay(roles["wide"], mic, at=min(600.0, dur * 0.3))
    host_name = ((brand.cfg.get("publication") or {}).get("host_name")
                 or (brand.cfg.get("posts") or {}).get("host_name") or "animateur")
    g_first, h_first = slugify(guest.split()[0] if guest else "invite", 20), slugify(host_name.split()[0], 20)
    base = f"{episode}_{slugify(guest, 30)}" + (f"_{slugify(company, 20)}" if company else "")
    ep_dir = brand.dir / "episodes"
    rush_dir = ep_dir / "rushes" / episode
    names = {"guest": f"invite_{g_first}{roles['guest'].suffix.lower()}",
             "wide": f"large{roles['wide'].suffix.lower()}",
             "host": f"animateur_{h_first}{roles['host'].suffix.lower()}"}
    labels = {"guest": f"INVITÉ ({guest or '?'})", "wide": "PLAN LARGE", "host": f"ANIMATEUR ({host_name})"}
    sheet = contact(roles, src / f"rushes_{episode}.jpg" if dry_run else ep_dir / f"rushes_{episode}.jpg", labels)
    plan = {"episode": base, "roles": {r: roles[r].name for r in roles}, "renamed": names, "audio": mic.name,
            "audio_delay": round(delay, 3), "sync_peak": round(peak, 1), "sheet": str(sheet), **detail}
    if dry_run:
        return plan
    rush_dir.mkdir(parents=True, exist_ok=True)
    for role, v in roles.items():
        shutil.move(str(v), rush_dir / names[role])
    wav = ep_dir / f"{base}{mic.suffix.lower()}"
    shutil.move(str(mic), wav)
    master = ep_dir / f"{base}.mp4"
    if not master.exists():
        try:
            os.link(rush_dir / names["wide"], master)          # lien dur : 0 octet de plus
        except OSError:
            master.symlink_to(rush_dir / names["wide"])
    spec = {"cams": {r: f"rushes/{episode}/{names[r]}" for r in ("host", "guest", "wide")},
            "audio": wav.name, "audio_delay": round(delay, 3), "lead": 0.15, "min_shot": 2.0,
            "host_side": detail.get("host_side", host_side)}
    (ep_dir / f"{base}.multicam.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    plan["input"] = master.name
    return plan
