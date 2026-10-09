"""Miniature YouTube d'un épisode, dans le style des miniatures AI Corner (références : brands/<m>/references/thumbnails/).

Gabarit (demande d'Arthur, 09/10/2026 — « automatise la création de miniatures, même style que les nôtres ») :
animateur à gauche et invité à droite, chacun détouré depuis SA caméra (rushs multicam), sur le studio flouté et
étalonné bleu ; deux logos « icône d'app 3D » au centre (AI Partners / entreprise invitée) ; titre de 2 lignes en bas,
la 2e ligne sur un bandeau bleu arrondi.

1. `sample_frames` : images clés toutes les ~8 s de chaque gros plan (décodage des seules images clés : rapide).
2. `score_frame` : visage YuNet -> netteté, taille, regard vers le centre, sourire ; on garde les meilleures,
   espacées dans le temps (planche `candidats_<rôle>.jpg` pour choisir à la main avec --host-frame / --guest-frame).
3. `cutout` : détourage BiRefNet-portrait (ONNX, onnxruntime seul : `rembg` importe numba, DLL bloquée par le
   contrôle des applications Windows).
4. `compose` : PIL, 2560×1440 puis export 1280×720 (YouTube) + HD.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from rich.console import Console

from .analysis import _detector
from .canva_template import compose_template
from .media import FFMPEG, FFPROBE

console = Console()
ROOT = Path(__file__).resolve().parent.parent
SEG_MODEL = ROOT / "models" / "birefnet_portrait.onnx"
SEG_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/BiRefNet-portrait-epoch_150.onnx"

W, H = 2560, 1440


# ---------------------------------------------------------------------------------------------------------------
# 1-2. Choix des images
# ---------------------------------------------------------------------------------------------------------------

def sample_frames(video: Path, out_dir: Path, every: float = 8.0, skip: float = 90.0) -> list[Path]:
    """Images clés espacées d'au moins `every` s (cache : ne refait rien si le dossier est rempli)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    done = sorted(out_dir.glob("f_*.jpg"))
    if done:
        return done
    console.print(f"[dim]Images clés de {video.name}…[/dim]")
    subprocess.run([FFMPEG, "-v", "error", "-skip_frame", "nokey", "-ss", str(skip), "-i", str(video),
                    "-vf", f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{every})'",
                    "-fps_mode", "vfr", "-frame_pts", "1", "-q:v", "2", str(out_dir / "f_%08d.jpg")], check=True)
    # nom = pts ; on le convertit en secondes pour retrouver l'instant
    tb = _time_base(video)
    for p in out_dir.glob("f_*.jpg"):
        t = int(p.stem[2:]) * tb + skip
        p.rename(out_dir / f"f_{t:08.2f}.jpg")
    return sorted(out_dir.glob("f_*.jpg"))


def _time_base(video: Path) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=time_base", "-of", "json", str(video)],
                         capture_output=True, text=True).stdout
    num, den = json.loads(out)["streams"][0]["time_base"].split("/")
    return int(num) / int(den)


_DET: dict = {}
EMO_MODEL = ROOT / "models" / "emotion_ferplus.onnx"
EMO_URL = ("https://github.com/onnx/models/raw/main/validated/vision/body_analysis/emotion_ferplus/model/"
           "emotion-ferplus-8.onnx")
_EMO = None


def happiness(gray: np.ndarray, box) -> float:
    """Probabilité de « joie » (FER+, ONNX, 8 émotions) du visage `box` (x, y, w, h en px). Remplace l'ancien
    indice largeur de bouche, qui prenait une bouche ouverte en pleine phrase pour un sourire."""
    global _EMO
    if _EMO is None:
        import onnxruntime as ort
        if not EMO_MODEL.exists():
            import requests
            EMO_MODEL.write_bytes(requests.get(EMO_URL, timeout=300).content)
        _EMO = ort.InferenceSession(str(EMO_MODEL), providers=["CPUExecutionProvider"])
    x, y, w, h = box
    side = max(w, h) * 1.1
    cx, cy = x + w / 2, y + h / 2
    x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    crop = gray[y0:int(cy + side / 2), x0:int(cx + side / 2)]
    if crop.size < 100:
        return 0.0
    inp = cv2.resize(crop, (64, 64)).astype(np.float32)[None, None]
    out = _EMO.run(None, {_EMO.get_inputs()[0].name: inp})[0][0]
    e = np.exp(out - out.max())
    return float(e[1] / e.sum())          # ordre FER+ : neutre, joie, surprise, tristesse, colère, dégoût, peur, mépris


def score_frame(path: Path, side: str) -> dict | None:
    """Note d'une image pour la miniature. side = 'left' (personne placée à gauche : doit regarder à droite)."""
    # imdecode : cv2.imread ne lit pas les chemins accentués sous Windows (« Création clip… »)
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    s = 960 / w
    small = cv2.resize(img, (960, int(h * s)))
    key = small.shape[:2]
    if key not in _DET:
        _DET[key] = _detector(small.shape[1], small.shape[0])
    _, faces = _DET[key].detect(small)
    if faces is None or not len(faces):
        return None
    f = max(faces, key=lambda r: r[2] * r[3])
    x, y, fw, fh = (f[:4] / s)
    lm = (f[4:14].reshape(5, 2) / s)
    conf = float(f[14])
    re, le, nose, mr, ml = lm
    eye_d = float(np.linalg.norm(le - re)) + 1e-6
    yaw = float((nose[0] - (re[0] + le[0]) / 2) / eye_d)       # > 0 : tourné vers la droite de l'image
    smile = float(np.linalg.norm(ml - mr) / eye_d)             # bouche large = sourire
    pitch = float((nose[1] - (re[1] + le[1]) / 2) / eye_d)     # grand = tête baissée
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    happy = happiness(gray, (x, y, fw, fh))
    x0, y0 = max(0, int(x)), max(0, int(y))
    face = gray[y0:int(y + fh), x0:int(x + fw)]
    if face.size < 100:
        return None
    face = cv2.resize(face, (256, int(256 * face.shape[0] / face.shape[1])))
    sharp = float(cv2.Laplacian(face, cv2.CV_64F).var())
    # yeux ouverts : contraste vertical autour de chaque œil (paupière fermée = zone plate)
    eyes = []
    r = int(eye_d * 0.18)
    for e in (re, le):
        patch = gray[max(0, int(e[1]) - r):int(e[1]) + r, max(0, int(e[0]) - r):int(e[0]) + r]
        eyes.append(float(np.abs(np.diff(patch.astype(np.float32), axis=0)).mean()) if patch.size else 0.0)
    want = 1 if side == "left" else -1
    look = want * yaw                                          # regard vers le centre de la miniature
    return {"path": str(path), "t": float(path.stem[2:]), "conf": conf, "sharp": sharp, "yaw": yaw,
            "look": look, "pitch": pitch, "smile": smile, "happy": happy, "eyes": min(eyes), "face_h": float(fh / h),
            "face_cx": float((x + fw / 2) / w), "face_cy": float((y + fh / 2) / h)}


def rank(scores: list[dict]) -> list[dict]:
    """Classement : netteté et yeux ouverts d'abord, puis sourire et regard vers le centre."""
    if not scores:
        return []
    def z(key):
        v = np.array([s[key] for s in scores], np.float64)
        return (v - np.median(v)) / (v.std() + 1e-6)
    zs, ze, zm = z("sharp"), z("eyes"), z("smile")
    pitch0 = float(np.median([s["pitch"] for s in scores]))
    for i, s in enumerate(scores):
        look = s["look"]
        look_pen = 0.0 if 0.0 <= look <= 0.35 else abs(look - min(max(look, 0.0), 0.35)) * 6
        # le sourire compte le plus (Arthur, 09/10/2026 : « les deux se regardent avec une expression souriante »)
        s["score"] = float(0.6 * min(zs[i], 2.0) + 0.6 * min(ze[i], 1.5) + 6.0 * s.get("happy", 0.0)
                           - look_pen - 12 * max(0.0, s["pitch"] - pitch0 - 0.06)   # tête baissée
                           - (2.0 if s["conf"] < 0.8 else 0.0))
    return sorted(scores, key=lambda s: -s["score"])


def best_frames(ranked: list[dict], n: int = 6, gap: float = 60.0) -> list[dict]:
    out: list[dict] = []
    for s in ranked:
        if all(abs(s["t"] - o["t"]) >= gap for o in out):
            out.append(s)
        if len(out) >= n:
            break
    return out


def contact_sheet(cands: list[dict], out: Path, label: str, tile: int = 360, cols: int = 4) -> Path:
    """Planche numérotée, recadrée sur le VISAGE (tête + épaules) : l'expression doit se lire en petit."""
    tiles = []
    for i, c in enumerate(cands):
        im = Image.open(c["path"]).convert("RGB")
        fh = c["face_h"] * im.height
        cx, cy = c["face_cx"] * im.width, c["face_cy"] * im.height
        half = fh * 1.15
        im = im.crop((int(cx - half), int(cy - half * 0.9), int(cx + half), int(cy + half * 1.1))).resize((tile, tile))
        d = ImageDraw.Draw(im)
        m, s = divmod(int(c["t"]), 60)
        d.rectangle([0, 0, tile, 30], fill=(0, 0, 0))
        d.text((6, 4), f"{label} {i + 1}  ({m}:{s:02d})", fill=(255, 255, 255), font=ImageFont.load_default(20))
        tiles.append(im)
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile, rows * tile), (20, 20, 20))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * tile, (i // cols) * tile))
    sheet.save(out, quality=88)
    return out


# ---------------------------------------------------------------------------------------------------------------
# 3. Détourage
# ---------------------------------------------------------------------------------------------------------------

_SESSION = None


def _seg_session():
    global _SESSION
    if _SESSION is None:
        import onnxruntime as ort
        if not SEG_MODEL.exists():
            import requests
            console.print("[dim]Téléchargement du modèle de détourage (~1 Go, une seule fois)…[/dim]")
            SEG_MODEL.parent.mkdir(parents=True, exist_ok=True)
            tmp = SEG_MODEL.with_suffix(".part")
            with requests.get(SEG_URL, stream=True, timeout=600) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
            tmp.rename(SEG_MODEL)
        _SESSION = ort.InferenceSession(str(SEG_MODEL), providers=["CPUExecutionProvider"])
    return _SESSION


def cutout(img: Image.Image) -> Image.Image:
    """Personne détourée (RGBA) — BiRefNet-portrait, entrée 1024×1024."""
    sess = _seg_session()
    im = img.convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    a = np.asarray(im, np.float32) / 255.0
    a = (a - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])
    x = a.transpose(2, 0, 1)[None].astype(np.float32)
    pred = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    pred = 1 / (1 + np.exp(-pred))
    pred = (pred - pred.min()) / (pred.max() - pred.min() + 1e-6)
    mask = Image.fromarray((pred * 255).astype(np.uint8)).resize(img.size, Image.Resampling.LANCZOS)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


# ---------------------------------------------------------------------------------------------------------------
# 4. Composition
# ---------------------------------------------------------------------------------------------------------------

STYLE = {
    "size": [1920, 1080],
    "bg_dark": [10, 22, 62],           # bleu nuit (haut et bords)
    "bg_light": [38, 100, 236],        # halo central
    "bg_blur": 34,
    "face_h": 0.33,                    # hauteur du cadre visage YuNet / hauteur de l'image (réf. DUST ≈ 0,33)
    "host_face": [0.19, 0.37],         # centre du visage de l'animateur (fractions)
    "guest_face": [0.81, 0.39],
    "tile_size": 0.27,                 # côté des icônes 3D / hauteur
    "tile_host": [0.0, 0.42, -26],     # x (calculé : derrière la tête/l'épaule), y, rotation 3D (° ; vers l'invité)
    "tile_guest": [0.0, 0.45, 26],
    "tile_overlap": 0.16,              # part de l'icône cachée par la personne (réf. : ≈ 1/5)
    "tile_x_range": [0.27, 0.43],      # centre de l'icône de gauche entre ces bornes (symétrique à droite)
    "tile_pitch": 6,                   # vue légèrement du dessus : la tranche se voit dessous
    "tile_depth": 0.22,                # épaisseur / côté
    "aip_tile": [[92, 104, 122], [44, 51, 68], [52, 60, 80]],   # haut, bas, tranche
    "guest_tile": [[255, 255, 255], [236, 238, 242], [168, 172, 182]],
    "font": "Metropolis-ExtraBold.ttf",
    "title_size": 0.098,               # corps du titre / hauteur
    "title_max_w": 0.66,
    "title_y": 0.705,                  # haut de la 1re ligne
    "pill": [[26, 102, 206], [14, 80, 180]],
}


def _rounded(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size * 4 - 1, size * 4 - 1], radius * 4, fill=255)
    return m.resize((size, size), Image.Resampling.LANCZOS)


def _vgrad(w: int, h: int, top, bottom) -> Image.Image:
    t = np.linspace(0, 1, h)[:, None, None]
    a = np.array(top, np.float32) * (1 - t) + np.array(bottom, np.float32) * t
    return Image.fromarray(np.repeat(a, w, axis=1).astype(np.uint8), "RGB")


def _persp_coeffs(dst, src):
    """Coefficients PIL PERSPECTIVE : point de sortie (dst) -> point source (src)."""
    m = []
    for (x, y), (u, v) in zip(dst, src):
        m.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        m.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    return np.linalg.solve(np.array(m, np.float64), np.array(src, np.float64).reshape(8))


def tile_3d(logo: Image.Image, size: int, colors, yaw: float, pitch: float = 12.0, depth: float = 0.16,
            logo_frac: float = 0.62) -> Image.Image:
    """Icône d'app en vraie 3D : carré arrondi épais vu en perspective, tourné de `yaw` degrés autour de l'axe
    vertical (< 0 : le bord gauche vient vers nous, la face regarde à droite — icône de gauche tournée vers
    l'invité) et de `pitch` vers le bas (la tranche se voit dessous). Ombre portée douce."""
    top, bottom, edge = colors
    face = _vgrad(size, size, top, bottom).convert("RGBA")
    gl = Image.new("L", (size, size), 0)       # reflet doux en haut
    ImageDraw.Draw(gl).ellipse([-size * 0.3, -size * 0.9, size * 1.3, size * 0.45], fill=38)
    face = Image.composite(Image.new("RGBA", (size, size), (255, 255, 255, 255)), face,
                           gl.filter(ImageFilter.GaussianBlur(size * 0.08)))
    lg = logo.copy()
    lg.thumbnail((int(size * logo_frac), int(size * logo_frac)), Image.Resampling.LANCZOS)
    # logo un peu décalé vers le côté visible (l'autre côté passe derrière la personne)
    shift = int(size * 0.06) * (1 if yaw < 0 else -1 if yaw > 0 else 0)
    face.alpha_composite(lg, ((size - lg.width) // 2 + shift, (size - lg.height) // 2))
    mask = _rounded(size, int(size * 0.22))
    face.putalpha(mask)

    ty, tp = np.radians(yaw), np.radians(pitch)
    f = size * 1.7                              # focale courte : perspective bien visible
    pad = int(size * 0.7)
    W = H = size + 2 * pad

    def proj(x, y, z):
        x1 = x * np.cos(ty) + z * np.sin(ty)
        z1 = -x * np.sin(ty) + z * np.cos(ty)
        y1 = y * np.cos(tp) + z1 * np.sin(tp)
        z2 = -y * np.sin(tp) + z1 * np.cos(tp)
        k = f / (f + z2)
        return (W / 2 + x1 * k, H / 2 + y1 * k)

    src = [(0, 0), (size, 0), (size, size), (0, size)]
    corners = [(-size / 2, -size / 2), (size / 2, -size / 2), (size / 2, size / 2), (-size / 2, size / 2)]

    def warp(img, z):
        dst = [proj(x, y, z) for x, y in corners]
        return img.transform((W, H), Image.Transform.PERSPECTIVE, _persp_coeffs(dst, src), Image.Resampling.BICUBIC)

    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = depth * size
    # épaisseur « cube » (retour d'Arthur, 09/10/2026 : « encore plus en 3D, presque deux cubes ») : la face avant est
    # tournée modérément (une vraie perspective forte la déforme en losange) et l'épaisseur part vers le BAS et vers
    # l'EXTÉRIEUR — le dessous reste visible même quand le côté extérieur passe derrière la personne
    out_dir = -1 if yaw < 0 else 1 if yaw > 0 else 0
    ox, oy = out_dir * 0.45, 0.9
    silhouette = warp(Image.merge("RGBA", (*[Image.new("L", (size, size), 0)] * 3, mask)), 0).getchannel("A")
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh.paste((0, 0, 0, 160), (int(ox * d + size * 0.04), int(oy * d + size * 0.10)), silhouette)
    out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(size * 0.08)))
    steps = max(10, int(d / 1.5))
    for i in range(steps, 0, -1):
        k = 0.72 + 0.28 * (1 - i / steps)       # plus sombre vers l'arrière
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer.paste(tuple(int(c * k) for c in edge) + (255,),
                    (int(round(ox * d * i / steps)), int(round(oy * d * i / steps))), silhouette)
        out.alpha_composite(layer)
    out.alpha_composite(warp(face, 0))
    return out


def _trim(im: Image.Image) -> Image.Image:
    box = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return im.crop(box) if box else im


def load_logo(path: Path, on_light: bool, height: int = 600) -> Image.Image:
    """Logo en RGBA (SVG rendu par Chrome/Edge headless). Un logo blanc posé sur une icône claire passe en noir."""
    im = _svg_png(path, height) if path.suffix.lower() == ".svg" else Image.open(path).convert("RGBA")
    im = _trim(im)
    a = np.asarray(im)
    vis = a[..., 3] > 128
    if on_light and vis.any() and a[vis][:, :3].mean() > 225:
        alpha = im.getchannel("A")
        im = Image.new("RGBA", im.size, (17, 17, 17, 255))
        im.putalpha(alpha)
    return im


def _browser() -> str | None:
    import shutil
    for c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if Path(c).exists():
            return c
    return shutil.which("google-chrome") or shutil.which("chromium")


def _svg_png(svg: Path, height: int) -> Image.Image:
    """SVG -> PNG transparent. Pas de cairo/resvg sous Windows (DLL bloquées) ; Chrome est déjà requis par HyperFrames."""
    import re
    import tempfile
    text = svg.read_text(encoding="utf-8")
    vb = re.search(r'viewBox="([\d.\s-]+)"', text)
    vw, vh = (float(v) for v in vb.group(1).split()[2:4]) if vb else (1000.0, 1000.0)
    width = int(height * vw / vh)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        html = Path(td) / "logo.html"
        html.write_text(f'<html><body style="margin:0;background:transparent">'
                        f'<img src="{svg.resolve().as_uri()}" style="width:{width}px;height:{height}px;display:block">'
                        f'</body></html>', encoding="utf-8")
        out = Path(td) / "logo.png"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)      # aucune fenêtre chez l'utilisateur
        subprocess.run([_browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--default-background-color=00000000", f"--window-size={width},{height}",
                        f"--screenshot={out}", html.as_uri()], capture_output=True, timeout=60, creationflags=flags)
        return Image.open(out).convert("RGBA")


def background(wide: Image.Image, style: dict) -> Image.Image:
    """Studio flouté, recoloré en bleu (halo clair au centre, bleu nuit sur les bords) — les formes restent devinables."""
    w, h = style["size"]
    bg = wide.convert("RGB").resize((w // 8, h // 8), Image.Resampling.BILINEAR)
    bg = bg.filter(ImageFilter.GaussianBlur(style["bg_blur"] / 8)).resize((w, h), Image.Resampling.BICUBIC)
    lum = np.asarray(bg.convert("L"), np.float32) / 255.0
    lum = (lum - lum.mean()) / (lum.std() + 1e-6) * 0.18
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt(((xx - w * 0.5) / (w * 0.55)) ** 2 + ((yy - h * 0.52) / (h * 0.6)) ** 2)
    glow = np.clip(1 - r, 0, 1) ** 1.6
    glow = np.clip(glow + lum * 0.8 + 0.08, 0, 1)[..., None]
    dark, light = np.array(style["bg_dark"], np.float32), np.array(style["bg_light"], np.float32)
    rgb = dark * (1 - glow) + light * glow
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")


def place_person(size: tuple[int, int], person: Image.Image, face: dict, target, face_h: float) -> Image.Image:
    """Calque (taille du cadre) avec la personne détourée : visage à la hauteur et au centre voulus, bas du cadre atteint."""
    w, h = size
    scale = face_h * h / (face["face_h"] * person.height)
    # le bas de l'image source doit atteindre le bas du cadre : sinon on agrandit (plutôt que de descendre la tête)
    scale = max(scale, h * (1 - target[1]) / ((1 - face["face_cy"]) * person.height))
    p = person.resize((int(person.width * scale), int(person.height * scale)), Image.Resampling.LANCZOS)
    fx, fy = face["face_cx"] * p.width, face["face_cy"] * p.height
    x, y = int(target[0] * w - fx), int(target[1] * h - fy)
    if y + p.height < h:              # jamais de vide sous la personne
        y = h - p.height
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cx, cy = max(0, -x), max(0, -y)
    layer.alpha_composite(p.crop((cx, cy, p.width, p.height)), (max(0, x), max(0, y)))
    return layer


def tile_x(layer: Image.Image, cy: float, size: int, side: str, overlap: float) -> float:
    """Centre x d'une icône posée JUSTE DERRIÈRE la tête/l'épaule de la personne (calque `layer`) : la personne
    recouvre `overlap` de la largeur de l'icône, mesuré sur la bande verticale de l'icône."""
    a = np.asarray(layer.getchannel("A"))
    y0, y1 = int(cy - size * 0.35), int(cy + size * 0.35)
    band = a[max(0, y0):y1] > 128
    cols = np.where(band.any(axis=0))[0]
    w = a.shape[1]
    if not len(cols):
        return w * (0.33 if side == "left" else 0.67)
    if side == "left":                # bord droit de l'animateur
        return float(cols.max() + size * (0.5 - overlap))
    return float(cols.min() - size * (0.5 - overlap))


def draw_title(canvas: Image.Image, lines: list[str], highlight: int, font_path: Path, style: dict):
    """2 lignes centrées ; la ligne `highlight` (0 ou 1) sur un bandeau bleu arrondi."""
    w, h = canvas.size
    size = int(style["title_size"] * h)
    while size > 40:
        f = ImageFont.truetype(str(font_path), size)
        if max(f.getlength(t) for t in lines) <= style["title_max_w"] * w:
            break
        size -= 4
    f = ImageFont.truetype(str(font_path), size)
    asc, _ = f.getmetrics()
    y = int(style["title_y"] * h)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i, text in enumerate(lines):
        tw = f.getlength(text)
        x = (w - tw) / 2
        ty = y + i * int(size * 1.3)
        if i == highlight:
            # le bleu entoure VRAIMENT le texte (Arthur, 09/10/2026 : « un peu short en bas ») : boîte = encre réelle
            # du texte (jambages g/p/q, accents compris), marge égale tout autour
            l, t, r, b = d.textbbox((x, ty), text, font=f)
            cap_top = d.textbbox((x, ty), "H", font=f)[1]
            t, b = min(t, cap_top), max(b, ty + asc + int(size * 0.04))
            px, py = int(size * 0.24), int(size * 0.16)
            box = [l - px, t - py, r + px, b + py]
            sd.rounded_rectangle([box[0], box[1] + 8, box[2], box[3] + 12], int(size * 0.16), fill=(0, 0, 0, 120))
            pill = _vgrad(int(box[2] - box[0]), int(box[3] - box[1]), *style["pill"]).convert("RGBA")
            m = Image.new("L", pill.size, 0)
            ImageDraw.Draw(m).rounded_rectangle([0, 0, pill.width - 1, pill.height - 1], int(size * 0.16), fill=255)
            layer.paste(pill, (int(box[0]), int(box[1])), m)
        else:
            sd.text((x, ty + 6), text, font=f, fill=(0, 0, 0, 170))
        d.text((x, ty), text, font=f, fill=(255, 255, 255, 255))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(size * 0.12)))
    canvas.alpha_composite(layer)


def grade_person(p: Image.Image) -> Image.Image:
    """Léger contraste/saturation : les visages des miniatures de référence sont « punchy »."""
    from PIL import ImageEnhance
    rgb = ImageEnhance.Contrast(p.convert("RGB")).enhance(1.08)
    rgb = ImageEnhance.Color(rgb).enhance(1.06)
    out = rgb.convert("RGBA")
    out.putalpha(p.getchannel("A"))
    return out


def compose(host, guest, wide: Image.Image, aip_logo: Image.Image, guest_logo: Image.Image | None,
            lines: list[str], highlight: int, font_path: Path, style: dict | None = None) -> Image.Image:
    """host / guest = (image détourée RGBA, mesures du visage de score_frame). Ordre des calques (comme les
    miniatures de référence) : fond -> icônes 3D -> personnes (qui cachent un bout des icônes) -> titre."""
    st = {**STYLE, **(style or {})}
    w, h = st["size"]
    canvas = background(wide, st).convert("RGBA")
    layers = [place_person((w, h), grade_person(img), face, target, st["face_h"])
              for (img, face), target in ((host, st["host_face"]), (guest, st["guest_face"]))]
    ts = int(st["tile_size"] * h)
    for logo, (_, fy, yaw), colors, layer, side in (
            (aip_logo, st["tile_host"], st["aip_tile"], layers[0], "left"),
            (guest_logo, st["tile_guest"], st["guest_tile"], layers[1], "right")):
        if logo is None:
            continue
        cy = fy * h
        cx = tile_x(layer, cy, ts, side, st["tile_overlap"])
        lo, hi = st["tile_x_range"]               # jamais collée au centre ni perdue sur le bord
        cx = min(max(cx, lo * w), hi * w) if side == "left" else min(max(cx, (1 - hi) * w), (1 - lo) * w)
        t = tile_3d(logo, ts, colors, yaw, st["tile_pitch"], st["tile_depth"])
        canvas.alpha_composite(t, (int(cx - t.width / 2), int(cy - t.height / 2)))
    for layer in layers:
        canvas.alpha_composite(layer)
    draw_title(canvas, lines, highlight, font_path, st)
    return canvas.convert("RGB")


# ---------------------------------------------------------------------------------------------------------------
# 5. Choix par le LLM : images (planches) et titres
# ---------------------------------------------------------------------------------------------------------------

JURY_PROMPT = """Tu choisis les photos d'une miniature YouTube de podcast : les deux personnes sont détourées et
placées face à face, elles doivent avoir l'air de SE REGARDER EN SOURIANT (complicité, bonne humeur).
Image 1 = planche de l'ANIMATEUR (placé à GAUCHE de la miniature : il doit regarder vers la DROITE de l'image).
Image 2 = planche de l'INVITÉ (placé à DROITE : il doit regarder vers la GAUCHE de l'image).
Chaque vignette (gros plan du visage) est numérotée (« host 3 », « guest 5 »…).
Critères, dans l'ordre :
1. un VRAI sourire (bouche souriante, joues relevées, yeux rieurs) — un visage neutre, sérieux, qui parle la bouche
   ouverte en pleine phrase ou qui grimace est à écarter, même net ;
2. regard tourné vers l'autre personne (côté indiqué ci-dessus), jamais vers le bas ni face caméra ;
3. yeux ouverts, visage net, pas caché par une main ou le micro.
S'il n'y a aucun vrai sourire pour une personne, prends l'expression la plus chaleureuse et dis-le dans "why".
Réponds UNIQUEMENT en JSON : {"host": [numéros du meilleur au moins bon, 3 maximum], "guest": [...],
"host_smile": true/false, "guest_smile": true/false, "why": "1 phrase"}"""

def jury(sheets: list[Path], model: str = "claude-sonnet-5") -> dict | None:
    from .llm import ask_json
    try:
        return ask_json(JURY_PROMPT, "Choisis les photos.", model=model, images=sheets)
    except Exception as e:                  # noqa: BLE001 — le classement automatique reste disponible
        console.print(f"[yellow]Jury visuel indisponible ({e}) : classement automatique.[/yellow]")
        return None


# ---------------------------------------------------------------------------------------------------------------
# 6. Orchestration (commande `thumbnail`)
# ---------------------------------------------------------------------------------------------------------------

def _slug(s: str) -> str:
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def guest_logo_path(brand, company: str) -> Path | None:
    d = brand.assets_dir / "guests"
    for ext in (".svg", ".png", ".jpg", ".webp"):
        p = d / f"{_slug(company)}{ext}"
        if company and p.exists():
            return p
    return None


def _cut_cached(frame: dict, work: Path) -> Image.Image:
    f = work / "cutouts" / (Path(frame["path"]).parent.name + "_" + Path(frame["path"]).stem + ".png")
    if f.exists():
        return Image.open(f).convert("RGBA")
    f.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"[dim]Détourage {Path(frame['path']).parent.name} {frame['t']:.0f} s…[/dim]")
    im = cutout(Image.open(frame["path"]))
    im.save(f)
    return im


def _frame_at(scores: list[dict], t: float) -> dict:
    return min(scores, key=lambda s: abs(s["t"] - t))


def _context(brand, spec: dict, ep: Path, company: str, host_t: float | None = None, guest_t: float | None = None,
             force: bool = False) -> dict:
    """Photos retenues (classement + jury visuel) et éléments de la vignette (gabarit Canva, logos). Mis en cache."""
    cfg = brand.cfg.get("thumbnail") or {}
    work = ep / "work" / "thumbnail"
    cams = spec["cams"]
    picks = {}
    for role, side in (("host", "left"), ("guest", "right")):
        # toutes les 3 s : un sourire dure peu (à 8 s, Thomas n'avait aucune image souriante)
        frames = sample_frames(Path(cams[role]), work / f"frames_{role}_3s", every=3.0)
        sf = work / f"scores_{role}_3s.json"
        if sf.exists() and not force:
            ranked = json.loads(sf.read_text(encoding="utf-8"))
        else:
            ranked = rank([s for p in frames if (s := score_frame(p, side))])
            sf.write_text(json.dumps(ranked, indent=0), encoding="utf-8")
        picks[role] = ranked
    sheets, best = [], {}
    for role in ("host", "guest"):
        best[role] = best_frames(picks[role], 16, gap=20.0)
        sheets.append(contact_sheet(best[role], work / f"candidats_{role}.jpg", role))
    jf = work / "jury_v2.json"
    verdict = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() and not force else jury(sheets)
    if verdict:
        jf.write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
    chosen = {}
    for role, forced in (("host", host_t), ("guest", guest_t)):
        if forced is not None:
            chosen[role] = [_frame_at(picks[role], forced)]
            continue
        order = [int(i) - 1 for i in (verdict or {}).get(role, []) if 1 <= int(i) <= len(best[role])]
        chosen[role] = [best[role][i] for i in order] or best[role][:3]

    def mmss(t: float) -> str:
        return f"{int(t) // 60}:{int(t) % 60:02d}"
    console.print("Photos retenues : " + " ; ".join(
        r + " " + ", ".join(mmss(c["t"]) for c in chosen[r][:2]) for r in chosen))

    wide_f = work / "wide.jpg"
    if not wide_f.exists() and cams.get("wide"):
        subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", str(chosen["guest"][0]["t"]), "-i", str(cams["wide"]),
                        "-frames:v", "1", "-q:v", "2", str(wide_f)], check=True)
    wide = Image.open(wide_f) if wide_f.exists() else Image.open(chosen["guest"][0]["path"])
    gpath = guest_logo_path(brand, company)
    if not gpath:
        console.print(f"[yellow]Pas de logo pour « {company} » dans {brand.assets_dir / 'guests'} : icône invité omise.[/yellow]")
    tpl_dir = brand.assets_dir / cfg.get("template", "thumbnail")
    tpl = json.loads((tpl_dir / "template.json").read_text(encoding="utf-8")) if (tpl_dir / "template.json").exists() else None
    if tpl:
        console.print(f"[dim]Gabarit Canva : {tpl_dir}[/dim]")
    return {"brand": brand, "work": work, "chosen": chosen, "wide": wide, "tpl": tpl, "tpl_dir": tpl_dir,
            "aip": load_logo(brand.asset(cfg.get("host_icon", "logo_icon.png")), on_light=False),
            "glogo": load_logo(gpath, on_light=True, height=800 if tpl else 600) if gpath else None,
            "font": brand.asset(cfg.get("font", "fonts/" + STYLE["font"])), "style": cfg.get("style") or {}}


def _compose_one(ctx: dict, h: dict, g: dict, lines: list[str], highlight: int) -> Image.Image:
    work = ctx["work"]
    if ctx["tpl"]:
        return compose_template(ctx["tpl"], ctx["tpl_dir"], (_cut_cached(h, work), h), (_cut_cached(g, work), g),
                                ctx["glogo"], lines, highlight, ctx["brand"])
    return compose((_cut_cached(h, work), h), (_cut_cached(g, work), g), ctx["wide"], ctx["aip"], ctx["glogo"],
                   lines, highlight, ctx["font"], ctx["style"])


def render_proposals(brand, spec: dict, ep: Path, company: str, proposals: list[dict], force: bool = False) -> list[Path]:
    """Chaque titre proposé posé sur la meilleure paire de photos : ep/titres/vignettes/titre_N.jpg (présentation à l'équipe)."""
    ctx = _context(brand, spec, ep, company, force=force)
    h, g = ctx["chosen"]["host"][0], ctx["chosen"]["guest"][0]
    out_dir = ep / "titres" / "vignettes"
    out_dir.mkdir(parents=True, exist_ok=True)
    outs = []
    for p in proposals:
        img = _compose_one(ctx, h, g, p["lines"], p["highlight"])
        f = out_dir / f"titre_{p['rank']}.jpg"
        img.resize((1280, 720), Image.Resampling.LANCZOS).save(f, quality=92)
        outs.append(f)
    return outs


def make(brand, spec: dict, ep: Path, company: str, n: int = 4, title: str | None = None, host_t: float | None = None,
         guest_t: float | None = None, force: bool = False, out_name: str = "miniatures") -> list[Path]:
    """Miniatures de l'épisode -> ep/<out_name>/ (défaut miniatures/) : variante_N.jpg (1280×720) + variante_N_HD.png, planche.jpg et titres.md.
    Le titre est CELUI QUE L'ÉQUIPE A VALIDÉ (`titles --pick`) ; les variantes diffèrent par les photos."""
    from .titles import load_validated, parse_lines
    if title:
        lines, hl = parse_lines(title)
        title_note = "titre imposé en ligne de commande"
    else:
        val = load_validated(ep)
        if not val:
            raise SystemExit("Aucun titre validé pour cet épisode : lancer d'abord `python -m clipper titles` (5 propositions), "
                             "faire valider un titre par l'équipe marketing, puis `titles --pick N`. "
                             "Pour passer outre : --title \"ligne 1 | ligne 2\".")
        lines, hl = val["lines"], val["highlight"]
        title_note = f"validé par {val.get('validated_by') or 'l’équipe'} le {val.get('validated_on')}"
    ctx = _context(brand, spec, ep, company, host_t, guest_t, force)
    out_dir = ep / out_name
    out_dir.mkdir(parents=True, exist_ok=True)
    hs, gs = ctx["chosen"]["host"], ctx["chosen"]["guest"]
    combos = [(0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (0, 2)]
    outs, rows = [], []
    for i, (hi, gi) in enumerate(combos[:n]):
        h, g = hs[hi % len(hs)], gs[gi % len(gs)]
        img = _compose_one(ctx, h, g, lines, hl)
        hd = out_dir / f"variante_{i + 1}_HD.png"
        img.save(hd)
        sm = out_dir / f"variante_{i + 1}.jpg"     # 16:9 YouTube (le gabarit Canva fait 1,8:1 : écart invisible)
        img.resize((1280, 720), Image.Resampling.LANCZOS).save(sm, quality=92)
        outs.append(sm)
        m = lambda t: f"{int(t) // 60}:{int(t) % 60:02d}"
        rows.append(f"{i + 1}. {sm.name} : photo animateur {m(h['t'])}, photo invité {m(g['t'])}")
    if outs:
        thumbs = [Image.open(p).resize((640, 360)) for p in outs]
        cols = 2
        sheet = Image.new("RGB", (cols * 650, ((len(thumbs) + 1) // cols) * 370), (24, 24, 24))
        for k, t in enumerate(thumbs):
            sheet.paste(t, ((k % cols) * 650 + 5, (k // cols) * 370 + 5))
        sheet.save(out_dir / "planche.jpg", quality=90)
    mark = " / ".join(f"[{l}]" if j == hl else l for j, l in enumerate(lines))
    (out_dir / "titres.md").write_text(f"# Miniatures\n\nTitre ({title_note}) : {mark}\n\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return outs
