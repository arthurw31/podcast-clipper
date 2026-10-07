"""Construit un projet HyperFrames par clip (un index par format) à partir de :
brand + clip (clips.json) + transcript + analyse vidéo.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from rich.console import Console

from . import broll as broll_mod
from .analysis import analyze
from .captions import group_words
from .config import FORMATS, TEMPLATES_DIR, Brand, Cfg, deep_merge
from .media import (concat_segments, cut_segment, extract_frame, make_blurred_still, prepare_outro_video, probe,
                    reframe_video)
from .multicam import cut_multicam, load_spec as load_multicam
from .reframe import build_plan
from .transcribe import words_between

console = Console()
HYPERFRAMES_VERSION = "0.8.46"


def slugify(s: str, n: int = 40) -> str:
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:n].strip("-") or "clip"


def cfg_for_format(cfg: Cfg, fmt: str) -> Cfg:
    ov = (cfg.get("format_overrides") or {}).get(fmt) or {}
    return Cfg(deep_merge(dict(cfg), ov))


def _pos_css(position: str, margin_px: int) -> str:
    v, h = position.split("-")
    return f"{'top' if v == 'top' else 'bottom'}:{margin_px}px; {'left' if h == 'left' else 'right'}:{margin_px}px;"


def _cam_geometry(cam: dict, fmt: str, sw: int, sh: int) -> dict:
    W, H = FORMATS[fmt]
    slot_h = H if cam["slot"] == "full" else (H // 2 if cam["slot"] == "top" else H - H // 2)
    crop = cam["crop"]
    s = slot_h / crop["h"]
    out = dict(cam)
    out["media_offset"] = 0.001 if cam["slot"] == "bottom" else 0.0
    out.update({
        "width": round(sw * s, 2),
        "height": round(sh * s, 2),
        "left": round(-crop["x"] * s, 2),
        "top": round(-crop["y"] * s, 2),
        "ox": round((crop["x"] + crop["w"] / 2) * s, 2),
        "oy": round((crop["y"] + crop["h"] / 2) * s, 2),
    })
    return out


def _link_assets(fmt_dir: Path, assets: Path) -> None:
    """assets/ du dossier format -> jonction/lien vers les assets partagés du clip (copie en dernier recours)."""
    link = fmt_dir / "assets"
    if link.exists() or link.is_symlink():
        return
    try:
        os.symlink(assets, link, target_is_directory=True)
        return
    except OSError:
        pass
    if os.name == "nt":
        res = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(assets)], capture_output=True)
        if res.returncode == 0 and link.exists():
            return
    shutil.copytree(assets, link)


def _faces_out(plan: list[dict], t0: float, t1: float, sw: int, sh: int, fmt: str) -> list[tuple[float, float]]:
    """Visages visibles pendant [t0, t1] (tous les plans traversés) : (y_centre, hauteur) en px de sortie."""
    W, H = FORMATS[fmt]
    out = []
    for e in plan:
        if e["t1"] > t0 and e["t0"] < t1:
            for cam in e["cams"]:
                crop = cam["crop"]
                slot_y = 0 if cam["slot"] in ("full", "top") else H // 2
                slot_h = H if cam["slot"] == "full" else (H // 2 if cam["slot"] == "top" else H - H // 2)
                sc = slot_h / crop["h"]
                for f in ([cam["face"]] if "face" in cam else cam.get("faces", [])):
                    out.append((slot_y + (f["cy"] * sh - crop["y"]) * sc, f["fh"] * sh * sc))
    return out


def _pip_box(plan: list[dict], t0: float, t1: float, sw: int, sh: int, fmt: str, br: Cfg, caption_top: int) -> dict | None:
    """Fenêtre PiP (px) dans un espace libre : hors visages et hors zone de sous-titres.

    Cherche le premier intervalle vertical libre (de haut en bas) qui accepte la fenêtre ; sinon
    la réduit dans le plus grand intervalle ; sinon retourne None (=> B-roll plein écran).
    """
    W, H = FORMATS[fmt]
    pip_w = int(W * float(br.pip_width))
    pip_h = int(pip_w / float(br.pip_aspect))
    margin = int(H * 0.025)
    faces = _faces_out(plan, t0, t1, sw, sh, fmt)
    default_top = int(H * float(br.pip_position_y))
    if not faces:
        return {"w": pip_w, "h": pip_h, "left": (W - pip_w) // 2, "top": default_top}
    # zones interdites : visages (avec marge cheveux/menton) + sous-titres jusqu'en bas
    blocked = sorted([(y - 0.8 * h, y + 0.9 * h) for y, h in faces] + [(caption_top - margin, H)])
    gaps: list[tuple[float, float]] = []
    cur = float(margin)
    for a, b in blocked:
        if a - cur >= 2 * margin:
            gaps.append((cur, a))
        cur = max(cur, b)
    if H - margin - cur >= 2 * margin:
        gaps.append((cur, H - margin))
    for a, b in gaps:
        if b - a >= pip_h + 2 * margin:
            top = int(a + margin)
            # le placement par défaut (haut de cadre) est préféré s'il tient dans cet intervalle
            if a + margin <= default_top and default_top + pip_h <= b - margin:
                top = default_top
            return {"w": pip_w, "h": pip_h, "left": (W - pip_w) // 2, "top": top}
    if gaps:
        a, b = max(gaps, key=lambda g: g[1] - g[0])
        avail = b - a - 2 * margin
        if avail >= H * 0.12:
            h2 = int(avail)
            w2 = min(int(h2 * float(br.pip_aspect)), W - 2 * margin)
            h2 = int(w2 / float(br.pip_aspect))
            return {"w": w2, "h": h2, "left": (W - w2) // 2, "top": int(a + margin)}
    return None


def _same_camera(a: dict, b: dict, tol: float = 0.25) -> bool:
    """Deux plans « single » cadrés sur la même caméra / la même personne (recadrage léger, même zoom) ?"""
    if a["layout"] != "single" or b["layout"] != "single":
        return False
    ca, cb = a["cams"][0]["crop"], b["cams"][0]["crop"]
    return (abs(ca["w"] - cb["w"]) <= 0.03 * ca["w"] and abs(ca["x"] - cb["x"]) <= tol * ca["w"]
            and abs(ca["y"] - cb["y"]) <= tol * ca["h"])


def _frame_diff(video: Path, t: float, dt: float = 0.12) -> float:
    """Différence moyenne (0-255) entre deux vignettes en niveaux de gris de part et d'autre de `t` :
    ~0-10 = même plan continu (fausse coupe détectée), > 30 = vraie coupe de la source."""
    import numpy as np

    def frame(tt: float):
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, tt):.3f}", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=64:36", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
        return np.frombuffer(r.stdout, dtype=np.uint8).astype(float)

    a, b = frame(t - dt), frame(t + dt)
    if a.size != b.size or a.size == 0:
        return 255.0
    return float(np.abs(a - b).mean())


def _smooth_plan(plan: list[dict], shots: list[dict], junctions: list[float], fr: Cfg,
                 frame_diff=None) -> list[dict]:
    """Fluidité : supprime les micro-coupes que l'œil perçoit comme des saccades.

    1. un plan de moins de `min_flash` s fusionne avec son voisin issu du même plan source (sinon : flash
       d'une autre personne pendant quelques images) ;
    2. deux plans consécutifs de la même caméra (petit recadrage, même zoom) fusionnent si l'un dure moins
       de `min_reframe_len` s, ou si la coupe tombe à moins de `junction_gap` s d'une jonction de segments
       (la jonction fait déjà une coupe) — la position du cadre est la moyenne pondérée des deux.
    Les punch-in (zoom différent) et les changements de personne sont conservés : c'est le rythme voulu.
    """
    if not plan:
        return plan
    min_flash = float(fr.get("min_flash", 0.5))
    min_reframe = float(fr.get("min_reframe_len", 1.2))
    jgap = float(fr.get("junction_gap", 1.5))

    def shot_of(e: dict) -> int:
        mid = (e["t0"] + e["t1"]) / 2
        return next((i for i, sh in enumerate(shots) if sh["t0"] - 0.05 <= mid <= sh["t1"] + 0.05), -1)

    def merge(a: dict, b: dict, average: bool) -> dict:
        da, db = a["t1"] - a["t0"], b["t1"] - b["t0"]
        keep = a if (da >= db or not average) else b
        out = dict(keep, t0=a["t0"], t1=b["t1"])
        if average:
            ca, cb = a["cams"][0]["crop"], b["cams"][0]["crop"]
            crop = dict(ca, x=round((ca["x"] * da + cb["x"] * db) / (da + db), 1),
                        y=round((ca["y"] * da + cb["y"] * db) / (da + db), 1))
            out["cams"] = [dict(keep["cams"][0], crop=crop)]
        return out

    changed = True
    while changed and len(plan) > 1:
        changed = False
        for k in range(len(plan)):
            e = plan[k]
            d = e["t1"] - e["t0"]
            prev = plan[k - 1] if k > 0 else None
            nxt = plan[k + 1] if k + 1 < len(plan) else None
            # 1. flash très court : avec le voisin du même plan source (le plus long des deux)
            if d < min_flash:
                # seulement avec un voisin du même plan source : appliquer un cadrage calculé sur d'autres images
                # décadrerait la personne (un vrai plan court du montage source est masqué ailleurs)
                cands = [n for n in (prev, nxt) if n is not None and shot_of(n) == shot_of(e)]
                if not cands:
                    continue
                n = max(cands, key=lambda x: x["t1"] - x["t0"])
                if n is prev:
                    plan[k - 1:k + 1] = [merge(prev, e, False)]
                else:
                    plan[k:k + 2] = [dict(merge(e, nxt, False), cams=nxt["cams"], layout=nxt["layout"])]
                changed = True
                break
            if nxt is None:
                continue
            cut = e["t1"]
            at_junction = any(abs(cut - j) <= 0.05 for j in junctions)
            at_shot_cut = any(abs(cut - sh["t0"]) <= 0.05 for sh in shots)
            # 2. fausse coupe : la détection a vu un changement de plan mais l'image source est continue
            #    (différence d'image faible) -> un seul cadrage, sans saut
            if at_shot_cut and not at_junction and frame_diff is not None and e["layout"] == nxt["layout"] == "single"                     and frame_diff(cut) < float(fr.get("false_cut_diff", 15)):
                same_zoom = abs(e["cams"][0]["crop"]["w"] - nxt["cams"][0]["crop"]["w"]) <= 0.03 * e["cams"][0]["crop"]["w"]
                plan[k:k + 2] = [merge(e, nxt, same_zoom)]
                changed = True
                break
            # 3. recadrage de la même caméra trop rapproché (sans mesure d'image disponible)
            if frame_diff is None and _same_camera(e, nxt) and not at_junction:
                near_junction = any(abs(cut - j) <= jgap for j in junctions)
                if d < min_reframe or (nxt["t1"] - nxt["t0"]) < min_reframe or near_junction:
                    plan[k:k + 2] = [merge(e, nxt, True)]
                    changed = True
                    break
    return _punch_junctions(plan, junctions, fr)


def _punch_junctions(plan: list[dict], junctions: list[float], fr: Cfg) -> list[dict]:
    """Jonction de segments sur la même caméra = « jump cut » (même cadre, le visage saute). Comme un monteur,
    on en fait un punch-in : le morceau après la jonction est resserré (`junction_punch`, ex. 1.15)."""
    z = float(fr.get("junction_punch", 1.0))
    if z <= 1.0:
        return plan
    out: list[dict] = []
    for e in plan:
        cut_js = [j for j in junctions if e["t0"] + 0.3 < j < e["t1"] - 0.3]
        pieces = []
        t = e["t0"]
        for j in cut_js:
            pieces.append(dict(e, t0=t, t1=j))
            t = j
        pieces.append(dict(e, t0=t, t1=e["t1"]))
        out.extend(pieces)
    for k in range(1, len(out)):
        a, b = out[k - 1], out[k]
        if not any(abs(b["t0"] - j) <= 0.05 for j in junctions) or not _same_camera(a, b):
            continue
        ca = a["cams"][0]["crop"]
        cb = dict(b["cams"][0]["crop"])
        nw, nh = cb["w"] / z, cb["h"] / z
        cb.update(x=round(cb["x"] + (cb["w"] - nw) / 2, 1), y=round(cb["y"] + (cb["h"] - nh) * 0.3, 1),
                  w=round(nw, 1), h=round(nh, 1))
        if abs(ca["w"] - cb["w"]) > 0.03 * ca["w"]:
            out[k] = dict(b, cams=[dict(b["cams"][0], crop=cb)])
    return out


def _img_aspect(path: Path) -> float:
    """Rapport largeur / hauteur d'un logo (SVG : viewBox ou width/height ; sinon PIL)."""
    try:
        if path.suffix.lower() == ".svg":
            txt = path.read_text(encoding="utf-8", errors="ignore")[:2000]
            m = re.search(r'viewBox="\s*[-\d.]+[ ,]+[-\d.]+[ ,]+([\d.]+)[ ,]+([\d.]+)', txt)
            if m:
                return float(m.group(1)) / float(m.group(2))
            w = re.search(r'width="([\d.]+)', txt)
            h = re.search(r'height="([\d.]+)', txt)
            return float(w.group(1)) / float(h.group(1))
        from PIL import Image

        with Image.open(path) as im:
            return im.width / im.height
    except Exception:  # noqa: BLE001
        return 4.0


def _logo_bar(W: int, H: int, files: list[Path], cfg: Cfg) -> dict:
    """Barre de logos centrée en haut (style shorts AI Partners) : même hauteur, centrés sur une même ligne,
    espacés de `gap`, réduits ensemble si la largeur totale dépasse `max_width`."""
    h = H * float(cfg.get("bar_height", 0.036))
    gap = W * float(cfg.get("bar_gap", 0.08))
    aspects = [_img_aspect(f) for f in files]
    total = sum(a * h for a in aspects) + gap * (len(files) - 1)
    limit = W * float(cfg.get("bar_max_width", 0.86))
    if total > limit:
        h *= (limit - gap * (len(files) - 1)) / (total - gap * (len(files) - 1))
    return {"height_px": int(round(h)), "gap_px": int(round(gap)),
            "center_y_px": int(round(H * float(cfg.get("bar_center_y", 0.064))))}


def _hook_duration(hk: Cfg, segments: list[dict], caps: list[dict], D_speech: float) -> float:
    """Durée du titre. `auto` (style shorts AI Partners) : le temps de l'accroche — fin du 1er segment,
    bornée à [min_duration, max_duration], puis calée sur la fin d'un bloc de sous-titres (jamais en plein mot)."""
    d = hk.get("duration", 3.5)
    if str(d) != "auto":
        return round(min(float(d), D_speech), 3)
    lo, hi = float(hk.get("min_duration", 7.0)), float(hk.get("max_duration", 11.0))
    target = float(segments[0]["duration"]) if len(segments) > 1 else hi
    target = max(lo, min(hi, target))
    ends = [g["end"] for g in caps if lo - 0.5 <= g["end"] <= hi + 1.5]
    if ends:
        target = min(ends, key=lambda e: abs(e - target))
    return round(min(target, D_speech), 3)


def _split_share(plan: list[dict], t0: float, t1: float) -> float:
    """Fraction de [t0, t1] passée en écran partagé."""
    if t1 <= t0:
        return 0.0
    split = sum(max(0.0, min(e["t1"], t1) - max(e["t0"], t0)) for e in plan if e["layout"] == "split")
    return split / (t1 - t0)


def _copy_logo(src: Path, dst: Path, max_w: int = 2000) -> None:
    """Copie le logo en le réduisant s'il est énorme (les PNG de charte font souvent 8000 px)."""
    try:
        from PIL import Image

        im = Image.open(src)
        if src.suffix.lower() in (".png", ".webp") and im.mode in ("RGBA", "LA", "P"):
            # marges transparentes retirées : sinon le logo paraît décentré dans la barre de logos
            im = im.convert("RGBA")
            bbox = im.getchannel("A").getbbox()
            if bbox and bbox != (0, 0, im.width, im.height):
                im = im.crop(bbox)
        if im.width > max_w and src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            im = im.convert("RGBA") if src.suffix.lower() == ".png" else im
            im.thumbnail((max_w, max_w * 4), Image.LANCZOS)
        if im.size != Image.open(src).size:
            im.save(dst)
            return
    except Exception:  # noqa: BLE001
        pass
    shutil.copy2(src, dst)


def _write_project_files(proj: Path, name: str) -> None:
    (proj / "package.json").write_text(json.dumps({
        "name": name, "private": True, "type": "module",
        "scripts": {
            "dev": f"npx --yes hyperframes@{HYPERFRAMES_VERSION} preview",
            "check": f"npx --yes hyperframes@{HYPERFRAMES_VERSION} check",
            "render": f"npx --yes hyperframes@{HYPERFRAMES_VERSION} render",
        },
    }, indent=2), encoding="utf-8")
    (proj / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        "media": {"autoProxy": True},
    }, indent=2), encoding="utf-8")
    if not (proj / "meta.json").exists():
        (proj / "meta.json").write_text(json.dumps({"id": name, "name": name}, indent=2), encoding="utf-8")


def build_clip(brand: Brand, source: Path, transcript: dict, clip: dict, episode_dir: Path,
               formats: list[str] | None = None, force: bool = False, with_broll: bool = True) -> Path:
    cfg = brand.cfg
    formats = formats or list(cfg.formats)
    name = f"clip_{clip['index']:02d}_{slugify(clip.get('title', ''))}"
    proj = episode_dir / "clips" / name
    assets = proj / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    segs_txt = " + ".join(f"{float(sg['start']):.0f}-{float(sg['end']):.0f}" for sg in (clip.get("segments") or [clip]))
    console.rule(f"[bold]{name}[/bold]  [{segs_txt}]")

    # ---- 1. segments source -> une seule vidéo continue (avec une queue audio sous la carte de fin) ----
    info = probe(source)
    segments = clip.get("segments") or [{"start": float(clip["start"]), "end": float(clip["end"]),
                                         "duration": float(clip["duration"]), "tail_silence": clip.get("tail_silence", 5.0)}]
    outro_d = float(cfg.outro.duration) if cfg.outro.enabled else 0.0
    # offsets : temps absolu (épisode) -> temps relatif (clip assemblé)
    offsets = []
    t = 0.0
    for sg in segments:
        offsets.append(t)
        t += float(sg["duration"])
    D_speech = round(t, 3)
    D_total = round(D_speech + outro_d, 3)
    junctions = [round(o, 3) for o in offsets[1:]]

    def abs_to_rel(ta: float) -> float | None:
        for sg, off in zip(segments, offsets):
            if float(sg["start"]) - 0.05 <= ta <= float(sg["end"]) + 0.05:
                return round(off + (ta - float(sg["start"])), 3)
        return None

    src_clip = assets / "source.mp4"
    # découpe en cache trop courte (carte de fin allongée depuis) alors que l'épisode a de quoi la couvrir : on redécoupe
    stale = (src_clip.exists() and probe(src_clip)["duration"] < D_total - 0.05
             and info["duration"] - float(segments[-1]["start"]) > float(segments[-1]["duration"]) + outro_d - 0.05)
    # rushs multicam (E22+) : gros plan de la personne qui parle, selon les tours de parole du clip
    multicam = load_multicam(source)
    if multicam:
        stamp = json.dumps([segments, clip.get("turns", []), outro_d], sort_keys=True, default=str)
        stamp_f = assets / "source.multicam.stamp"
        stale = stale or not stamp_f.exists() or stamp_f.read_text(encoding="utf-8") != stamp
    if force or stale or not src_clip.exists():
        console.print(f"  découpe de {len(segments)} segment(s) source…")
        parts = []
        for j, sg in enumerate(segments):
            last = j == len(segments) - 1
            dur = float(sg["duration"]) + (outro_d if last else 0.0)
            dur = min(dur, max(0.0, info["duration"] - float(sg["start"])))
            part = assets / (f"seg_{j+1}.mp4" if len(segments) > 1 else "source.mp4")
            # source 4K conservée (jusqu'à render.max_source_height) : le recadrage vertical y puise sa netteté
            h_max = min(info["height"], int(cfg.render.get("max_source_height", 2160)))
            if multicam:
                cut_multicam(multicam, part, float(sg["start"]), dur, clip.get("turns", []), height=h_max,
                             normalize_audio=bool(cfg.audio.normalize), fps=int(cfg.fps))
            else:
                cut_segment(source, part, float(sg["start"]), dur, height=h_max,
                            normalize_audio=bool(cfg.audio.normalize), fps=int(cfg.fps))
            parts.append(part)
        if len(parts) > 1:
            concat_segments(parts, src_clip)
            for part in parts:
                part.unlink(missing_ok=True)
        if multicam:
            stamp_f.write_text(stamp, encoding="utf-8")
    # l'audio sous la carte de fin s'arrête avant que le locuteur suivant reprenne
    tail = float(segments[-1].get("tail_silence", clip.get("tail_silence", 5.0)))
    A_fade = max(0.08, min(float(cfg.outro.audio_fade), tail, outro_d)) if outro_d > 0 else 0.0
    A_dur = round(D_speech + A_fade, 3)
    seg_info = probe(src_clip)
    if seg_info["duration"] < D_total - 0.05:
        # fin de fichier source : on raccourcit la carte de fin
        D_total = round(seg_info["duration"], 3)
        outro_d = max(0.0, D_total - D_speech)

    # ---- 2. analyse (plans, visages, locuteur) ----
    analysis = analyze(src_clip, cfg, proj / "analysis.json", force=force)

    # ---- 3. mots (temps relatifs au clip assemblé) ----
    words_rel = []
    for sg, off in zip(segments, offsets):
        for w in words_between(transcript, float(sg["start"]), float(sg["end"])):
            words_rel.append({"w": w["w"], "s": round(off + w["s"] - float(sg["start"]), 3),
                              "e": round(off + w["e"] - float(sg["start"]), 3)})
    turns_rel = []
    for tr in clip.get("turns", []):
        r = abs_to_rel(float(tr["at"]))
        if r is not None:
            turns_rel.append({"at": r, "speaker": tr["speaker"]})
    # au début de chaque segment, le locuteur courant doit être connu
    for sg, off in zip(segments, offsets):
        spk = None
        for tr in clip.get("turns", []):
            if float(tr["at"]) <= float(sg["start"]) + 0.05:
                spk = tr["speaker"]
        if spk and not any(abs(t["at"] - off) < 0.05 for t in turns_rel):
            turns_rel.append({"at": round(off, 3), "speaker": spk})
    turns_rel.sort(key=lambda t: t["at"])

    # ---- 4. b-roll ----
    brolls = []
    if with_broll and cfg.broll.enabled and clip.get("broll"):
        bdir = assets / "broll"
        for i, b in enumerate(clip["broll"][: int(cfg.broll.max_per_clip)]):
            at = abs_to_rel(float(b["at"]))
            dur = round(float(b.get("duration") or cfg.broll.duration), 3)
            if at is None or at < 0.8 or at + dur > D_speech - 0.4:
                continue
            orientation = "portrait" if (cfg.broll.mode == "fullscreen" and "9x16" in formats) else "landscape"
            got = broll_mod.fetch_broll(b.get("query", ""), b.get("type", cfg.broll.prefer), dur, bdir, f"b{i+1}", orientation=orientation)
            if got:
                got.update({"id": i + 1, "at": at, "duration": dur})
                brolls.append(got)
                console.print(f"  b-roll #{i+1} « {got['query']} » ({got['kind']}, © {got['credit']}) à {at:.1f}s")

    # ---- 5. fond de la carte de fin ----
    outro_bg = None
    if cfg.outro.enabled and cfg.outro.background == "blur-freeze":
        frame = extract_frame(src_clip, max(0.0, D_speech - 0.08), proj / "last_frame.jpg")
        make_blurred_still(frame, assets / "outro_bg.jpg", blur=int(cfg.outro.blur), darken=float(cfg.outro.darken))
        outro_bg = "outro_bg.jpg"
    elif cfg.outro.enabled and cfg.outro.background == "image" and brand.asset(cfg.outro.get("image")):
        src_bg = brand.asset(cfg.outro.get("image"))
        outro_bg = "outro_bg" + src_bg.suffix.lower()
        shutil.copy2(src_bg, assets / outro_bg)
    outro_logo_file = None
    if cfg.outro.enabled and cfg.outro.get("logo_file") and brand.asset(cfg.outro.get("logo_file")):
        src_l = brand.asset(cfg.outro.get("logo_file"))
        outro_logo_file = "outro_logo" + src_l.suffix.lower()
        _copy_logo(src_l, assets / outro_logo_file)

    # ---- 6. polices, logo, musique ----
    fonts_dir = assets / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    italic = brand.font_path("caption")
    upright = brand.font_path("caption_upright") or brand.font_path("display") or italic
    if not italic or not upright:
        raise FileNotFoundError("Police introuvable : vérifiez fonts.caption / fonts.caption_upright dans brand.yaml")
    shutil.copy2(italic, fonts_dir / italic.name)
    shutil.copy2(upright, fonts_dir / upright.name)
    hook_font = brand.font_path("hook")   # police propre au titre (fonts.hook), sinon celle de la marque
    if hook_font:
        shutil.copy2(hook_font, fonts_dir / hook_font.name)
    # police propre aux sous-titres (fonts.captions_alt) : embarquée, sinon HyperFrames remplace une police
    # système (« Arial ») par une autre au rendu
    cap_font = brand.font_path("captions_alt")
    if cap_font:
        shutil.copy2(cap_font, fonts_dir / cap_font.name)
    # logo de l'invité / de son entreprise (style shorts AI Partners : en haut à gauche)
    guest_logo_file = None
    gl_cfg = cfg.get("guest_logo") or {}
    if gl_cfg.get("enabled"):
        src_gl = None
        if clip.get("guest_logo"):
            src_gl = brand.asset(clip["guest_logo"]) or (Path(clip["guest_logo"]) if Path(clip["guest_logo"]).exists() else None)
        if not src_gl:
            for key in (clip.get("company", ""), clip.get("guest", "")):
                for ext in (".svg", ".png", ".webp", ".jpg"):
                    cand = brand.asset(f"guests/{slugify(key)}{ext}") if key else None
                    if cand:
                        src_gl = cand
                        break
                if src_gl:
                    break
        if src_gl:
            guest_logo_file = "guest_logo" + src_gl.suffix.lower()
            if src_gl.suffix.lower() == ".svg":
                shutil.copy2(src_gl, assets / guest_logo_file)
            else:
                _copy_logo(src_gl, assets / guest_logo_file)
        else:
            console.print(f"  [yellow]logo invité introuvable (brands/{brand.slug}/assets/guests/{slugify(clip.get('company', '') or 'entreprise')}.svg|png)[/yellow]")
    logo_file = None
    if cfg.logo.enabled and cfg.logo.mode in ("auto", "image") and brand.logo_path:
        logo_file = "logo" + brand.logo_path.suffix.lower()
        _copy_logo(brand.logo_path, assets / logo_file)
    logo_mode = "none" if not cfg.logo.enabled or cfg.logo.mode == "none" else ("image" if logo_file else "text")
    music = None
    if brand.music_path:
        shutil.copy2(brand.music_path, assets / brand.music_path.name)
        music = {"file": brand.music_path.name, "volume": float(cfg.audio.music_volume)}

    # ---- 7. un index par format ----
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), undefined=StrictUndefined, autoescape=False)
    tpl = env.get_template("clip.html.j2")
    sw, sh = analysis["width"], analysis["height"]
    built = {}
    for fmt in formats:
        fcfg = cfg_for_format(cfg, fmt)
        W, H = FORMATS[fmt]
        plan = build_plan(analysis, fmt, fcfg, words_rel, turns=turns_rel, host_side=clip.get("host_side", ""))
        for e in plan:
            e["t1"] = min(e["t1"], D_speech)
        # un changement de plan détecté juste après une jonction de segments (coupe de l'export source) :
        # on le cale sur la jonction, sinon 1-2 images du segment suivant passent avec l'ancien cadrage
        for j in junctions:
            for k in range(len(plan) - 1):
                if abs(plan[k]["t1"] - j) <= 0.2:
                    plan[k]["t1"] = j
                    plan[k + 1]["t0"] = j
        plan = [e for e in plan if e["t1"] - e["t0"] > 0.05]
        diffs: dict[float, float] = {}

        def frame_diff(t: float) -> float:
            if t not in diffs:
                diffs[t] = _frame_diff(src_clip, t)
            return diffs[t]

        plan = _smooth_plan(plan, analysis["shots"], junctions, fcfg.framing, frame_diff=frame_diff)
        # dernier plan très court venu d'un autre plan source (ex. contrechamp de 0,3 s juste avant la fin) :
        # la carte de fin démarre un peu plus tôt et le masque (la voix continue dessous)
        outro_start = D_speech
        if cfg.outro.enabled and len(plan) >= 2 and plan[-1]["t1"] - plan[-1]["t0"] < float(fcfg.framing.get("min_flash", 0.5)):
            outro_start = round(plan[-1]["t0"], 3)
            plan = plan[:-1]
        for i, e in enumerate(plan):
            e["id"] = i + 1   # renumérotation : le lissage fusionne / découpe des plans (ids uniques exigés)
            e["cams"] = [_cam_geometry(c, fmt, sw, sh) for c in e["cams"]]
        if plan:
            plan[-1]["t1"] = D_speech
        caps = group_words(words_rel, 0.0, D_speech, fcfg, clip.get("keywords", []), turns_rel=turns_rel, breaks=junctions)

        c = fcfg.captions
        margin_px = int(W * float(c.side_margin))
        cap_ctx = dict(c)
        cap_ctx.update({
            "margin_px": margin_px,
            "top_px": int(H * float(c.position_y)),
            "font_px": int(c.font_size),
            "font_file": cap_font.name if cap_font else "",
            "stagger_px": int(W * float(c.stagger_offset)),
        })
        lg = fcfg.logo
        logo_w = int(W * float(lg.width))
        logo_ctx = dict(lg)
        logo_ctx.update({"mode": logo_mode, "file": logo_file, "width_px": logo_w,
                         "css_pos": _pos_css(lg.position, int(W * float(lg.margin))),
                         "text_px": int(logo_w / max(len(max(lg.text_lines, key=len)), 4) / 0.74)})
        glc = Cfg(fcfg.get("guest_logo") or {})
        guest_logo_ctx = dict(glc)
        guest_logo_ctx.update({"file": guest_logo_file,
                               "height_px": int(H * float(glc.get("height", 0.036))),
                               "max_w_px": int(W * float(glc.get("max_width", 0.36))),
                               "css_pos": _pos_css(glc.get("position", "top-left"), int(W * float(glc.get("margin", lg.margin)))),
                               "opacity": float(glc.get("opacity", 1.0))})
        # barre de logos centrée : logo de l'invité + logo de la marque côte à côte, même hauteur
        guest_logo_ctx["bar"] = None
        if glc.get("bar") and guest_logo_file and logo_mode == "image":
            guest_logo_ctx["bar"] = _logo_bar(W, H, [assets / guest_logo_file, assets / logo_file], glc)
        o = fcfg.outro
        outro_video = None
        if o.enabled and o.background == "video" and brand.asset(o.get("video")) and outro_d > 0.2:
            # animation de la charte, recadrée au format et accélérée pour durer exactement la carte de fin
            ov_dur = round(outro_d + D_speech - outro_start, 3)
            outro_video = f"outro_{fmt}.mp4"
            ov_path = assets / outro_video
            stamp = f"{brand.asset(o.get('video')).stat().st_mtime}-{ov_dur}-{o.get('video_focus_x', 0.5)}-{o.get('video_focus_y', 0.5)}"
            stamp_file = assets / f"outro_{fmt}.stamp"
            if force or not ov_path.exists() or not stamp_file.exists() or stamp_file.read_text() != stamp:
                prepare_outro_video(brand.asset(o.get("video")), ov_path, W, H, ov_dur,
                                    focus_x=float(o.get("video_focus_x", 0.5)), focus_y=float(o.get("video_focus_y", 0.5)),
                                    fps=int(cfg.fps))
                stamp_file.write_text(stamp)
        outro_logo_w = int(W * float(o.logo_width))
        outro_ctx = dict(o)
        cta = dict(o.get("cta") or {})
        vars_ = {"podcast_name": cfg.get("podcast_name") or cfg.name, "guest": clip.get("guest", ""), "company": clip.get("company", "")}

        def _fmt(txt: str) -> str:
            try:
                return str(txt or "").format(**vars_).strip()
            except (KeyError, IndexError, ValueError):
                return str(txt or "")

        cta.update({
            "enabled": bool(cta.get("enabled", False)),
            "text_rendered": _fmt(cta.get("text", "")),
            "subtext_rendered": _fmt(cta.get("subtext", "")),
            "font_px": int(cta.get("font_size", 34)),
            "subtext_px": int(cta.get("subtext_font_size", 28)),
            "pill_color_resolved": cta.get("pill_color") or cfg.colors.primary,
            "text_color_resolved": cta.get("text_color") or cfg.colors.primary,
            "delay": float(cta.get("delay", 0.5)),
        })
        outro_ctx["cta"] = cta
        outro_ctx.update({"enabled": bool(o.enabled) and outro_d > 0.2, "duration": round(outro_d + D_speech - outro_start, 3), "bg_file": None if outro_video else outro_bg,
                          "bg_is_image": bool(outro_bg) and o.background == "image" and not outro_video,
                          "video_file": outro_video, "scrim": o.get("scrim", ""), "layout": o.get("layout", "center"),
                          "image_position": o.get("image_position", "center"),
                          "logo_file": outro_logo_file,
                          "company_italic": bool(o.get("company_italic", fcfg.captions.italic)),
                          "logo_px": outro_logo_w,
                          "logo_text_px": int(outro_logo_w / max(len(max(lg.text_lines, key=len)), 4) / 0.74),
                          "guest_px": int(o.guest_font_size), "company_px": int(o.company_font_size)})
        hk = fcfg.hook
        hook_ctx = dict(hk)
        hook_ctx.update({"text": clip.get("hook_title", ""), "top_px": int(H * float(hk.position_y)), "font_px": int(hk.font_size),
                         "style": hk.get("style", "banner"), "font_file": hook_font.name if hook_font else "",
                         "max_w_px": int(W * float(hk.get("max_width", 0.86))), "duration": _hook_duration(hk, segments, caps, D_speech)})
        br = fcfg.broll
        broll_ctx = dict(br)
        brolls_fmt = []
        for b in brolls:
            bb = dict(b)
            box = None if br.mode == "fullscreen" else _pip_box(plan, b["at"], b["at"] + b["duration"], sw, sh, fmt, br, cap_ctx["top_px"])
            bb["box"] = box
            bb["fullscreen"] = box is None
            brolls_fmt.append(bb)
        # sous-titres : en écran partagé, le bloc est centré sur la ligne de séparation
        for g in caps:
            if c.get("split_center", True) and _split_share(plan, g["start"], g["end"]) >= 0.4:
                block_h = len(g["lines"]) * cap_ctx["font_px"] * float(c.line_height)
                g["top_px"] = int(H / 2 - block_h / 2)
            else:
                g["top_px"] = cap_ctx["top_px"]

        # Netteté : le recadrage est appliqué par FFmpeg (Lanczos + accentuation) en une vidéo au format de
        # sortie, jouée 1:1 par HyperFrames — au lieu d'agrandir ×1,8 dans le navigateur au moment du rendu.
        plan_render = plan
        rr = fcfg.render
        if rr.get("prereframe", True) and not fcfg.framing.get("slow_zoom", False) and any(e["layout"] != "full" for e in plan):
            ref_name = f"reframed_{fmt}.mp4"
            reframe_video(src_clip, assets / ref_name, plan, W, H, D_speech, fps=int(cfg.fps),
                          sharpen=float(rr.get("sharpen", 0.0)), crf=int(rr.get("intermediate_crf", 10)))
            full = {"slot": "full", "src": ref_name, "media_offset": 0.0, "width": W, "height": H, "left": 0, "top": 0,
                    "ox": W / 2, "oy": H / 2}
            plan_render = [{"id": 1, "t0": 0.0, "t1": D_speech, "layout": "full", "zoom_from": 1.0, "zoom_to": 1.0,
                            "cams": [full]}]
            console.print(f"  {fmt:5s} recadrage FFmpeg → {ref_name}")

        html = tpl.render(
            lang=cfg.get("language", "fr"), title=f"{clip.get('title','')} · {fmt}", W=W, H=H, fps=int(cfg.fps),
            D_total=D_total, D_speech=D_speech, A_dur=A_dur, A_fade=round(A_fade, 3),
            font={"family": cfg.fonts.family, "italic_file": italic.name, "upright_file": upright.name},
            colors=dict(cfg.colors), plan=plan_render, captions=caps, cap=Cfg(cap_ctx), logo=Cfg(logo_ctx),
            outro=Cfg(outro_ctx), hook=Cfg(hook_ctx), broll=Cfg(broll_ctx), brolls=brolls_fmt, music=music,
            guest_logo=Cfg(guest_logo_ctx), outro_start=outro_start,
            guest=clip.get("guest", ""), company=clip.get("company", ""),
            junctions=junctions, join_transition=fcfg.montage.get("join_transition", "cut"), flash_color=fcfg.montage.get("flash_color", "#FFFFFF"),
            split_divider=int(fcfg.framing.get("split_divider", 0)), split_divider_color=fcfg.framing.get("split_divider_color", "#FFFFFF"),
        )
        fmt_dir = proj / fmt
        fmt_dir.mkdir(exist_ok=True)
        (fmt_dir / "index.html").write_text(html, encoding="utf-8")
        _write_project_files(fmt_dir, f"{name}_{fmt}")
        _link_assets(fmt_dir, assets)
        built[fmt] = {"dir": fmt, "html": "index.html", "plan": plan, "captions": len(caps)}
        console.print(f"  {fmt:5s} → {fmt}/index.html  ({len(plan)} plans, {len(caps)} blocs de sous-titres)")

    (proj / "clip.json").write_text(json.dumps({
        "clip": clip, "formats": built, "brolls": brolls, "D_speech": D_speech, "D_total": D_total, "source": str(source),
        "segments": segments, "junctions": junctions,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return proj
