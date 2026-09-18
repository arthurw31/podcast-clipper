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
from .media import concat_segments, cut_segment, extract_frame, make_blurred_still, probe
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
        if im.width > max_w and src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            im = im.convert("RGBA") if src.suffix.lower() == ".png" else im
            im.thumbnail((max_w, max_w * 4), Image.LANCZOS)
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
    if force or not src_clip.exists():
        console.print(f"  découpe de {len(segments)} segment(s) source…")
        parts = []
        for j, sg in enumerate(segments):
            last = j == len(segments) - 1
            dur = float(sg["duration"]) + (outro_d if last else 0.0)
            dur = min(dur, max(0.0, info["duration"] - float(sg["start"])))
            part = assets / (f"seg_{j+1}.mp4" if len(segments) > 1 else "source.mp4")
            cut_segment(source, part, float(sg["start"]), dur, height=min(info["height"], 1080),
                        normalize_audio=bool(cfg.audio.normalize), fps=int(cfg.fps))
            parts.append(part)
        if len(parts) > 1:
            concat_segments(parts, src_clip)
            for part in parts:
                part.unlink(missing_ok=True)
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
            e["cams"] = [_cam_geometry(c, fmt, sw, sh) for c in e["cams"]]
        plan = [e for e in plan if e["t1"] - e["t0"] > 0.05]
        if plan:
            plan[-1]["t1"] = D_speech
        caps = group_words(words_rel, 0.0, D_speech, fcfg, clip.get("keywords", []), turns_rel=turns_rel)

        c = fcfg.captions
        margin_px = int(W * float(c.side_margin))
        cap_ctx = dict(c)
        cap_ctx.update({
            "margin_px": margin_px,
            "top_px": int(H * float(c.position_y)),
            "font_px": int(c.font_size),
            "stagger_px": int(W * float(c.stagger_offset)),
        })
        lg = fcfg.logo
        logo_w = int(W * float(lg.width))
        logo_ctx = dict(lg)
        logo_ctx.update({"mode": logo_mode, "file": logo_file, "width_px": logo_w,
                         "css_pos": _pos_css(lg.position, int(W * float(lg.margin))),
                         "text_px": int(logo_w / max(len(max(lg.text_lines, key=len)), 4) / 0.74)})
        o = fcfg.outro
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
        outro_ctx.update({"enabled": bool(o.enabled) and outro_d > 0.2, "duration": round(outro_d, 3), "bg_file": outro_bg,
                          "bg_is_image": bool(outro_bg) and o.background == "image", "layout": o.get("layout", "center"),
                          "image_position": o.get("image_position", "center"),
                          "logo_file": outro_logo_file,
                          "company_italic": bool(o.get("company_italic", fcfg.captions.italic)),
                          "logo_px": outro_logo_w,
                          "logo_text_px": int(outro_logo_w / max(len(max(lg.text_lines, key=len)), 4) / 0.74),
                          "guest_px": int(o.guest_font_size), "company_px": int(o.company_font_size)})
        hk = fcfg.hook
        hook_ctx = dict(hk)
        hook_ctx.update({"text": clip.get("hook_title", ""), "top_px": int(H * float(hk.position_y)), "font_px": int(hk.font_size)})
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
            if _split_share(plan, g["start"], g["end"]) >= 0.4:
                block_h = len(g["lines"]) * cap_ctx["font_px"] * float(c.line_height)
                g["top_px"] = int(H / 2 - block_h / 2)
            else:
                g["top_px"] = cap_ctx["top_px"]

        html = tpl.render(
            lang=cfg.get("language", "fr"), title=f"{clip.get('title','')} · {fmt}", W=W, H=H, fps=int(cfg.fps),
            D_total=D_total, D_speech=D_speech, A_dur=A_dur, A_fade=round(A_fade, 3),
            font={"family": cfg.fonts.family, "italic_file": italic.name, "upright_file": upright.name},
            colors=dict(cfg.colors), plan=plan, captions=caps, cap=Cfg(cap_ctx), logo=Cfg(logo_ctx),
            outro=Cfg(outro_ctx), hook=Cfg(hook_ctx), broll=Cfg(broll_ctx), brolls=brolls_fmt, music=music,
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
