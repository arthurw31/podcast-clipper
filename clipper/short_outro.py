"""Outro des shorts : carte « vidéo YouTube » avec la miniature validée de l'épisode, ajoutée à la fin de chaque short.

Demande d'Arthur (09/10/2026) : « pour les shorts, mettre en outro la première des 4 outros en motion design (call to action
avec la miniature) ; on peut monter les shorts en parallèle, mais pour créer l'outro et la rajouter à la fin des shorts il
faut que la miniature soit validée ». Design = variante A du test du 09/10 (motion_design/outro_short/, autre session) :
animation AI Partners en fond, logo, « L'ÉPISODE COMPLET », la miniature en carte YouTube (bouton lecture, durée, barre de
progression), flèche, bouton « Regarder l'épisode — lien en description ». 7 s, 1080×1920, 30 i/s.

Dépendances : les shorts se montent, se valident et se rendent SANS carte de fin (`format_overrides.9x16.outro.enabled:
false`) ; cette étape n'attend que la miniature choisie (`thumbnail --pick`). L'outro est rendue UNE fois par épisode
(HyperFrames), puis collée à chaque short (fondu de 0,4 s, FFmpeg) -> `livrables/` : aucun short n'est re-rendu.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from rich.console import Console

from .media import FFMPEG, prepare_outro_video, probe

console = Console()
W, H, FPS = 1080, 1920, 30
TW = 900
TH = TW * 9 // 16
TX = (W - TW) // 2

PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="UTF-8" /><meta name="viewport" content="width=1080, height=1920" />
<title>outro shorts</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
@font-face {{ font-family: "MB"; src: url("assets/Metropolis-Bold.ttf") format("truetype"); font-weight: 700; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: 1080px; height: 1920px; overflow: hidden; background: #000; }}
#root {{ position: relative; width: 100%; height: 100%; overflow: hidden; background: #000; font-family: "MB", sans-serif; color: #fff; }}
.layer {{ position: absolute; left: 0; top: 0; width: 1080px; height: 1920px; }}
.bgv {{ position: absolute; left: 0; top: 0; width: 100%; height: 100%; object-fit: cover; }}
.veil {{ position: absolute; inset: 0; background: rgba(0,0,0,.35); }}
.title {{ position: absolute; left: 0; width: 100%; text-align: center; font-size: 68px; letter-spacing: .03em; text-transform: uppercase;
  text-shadow: 0 0 10px rgba(0,0,0,.5), 0 4px 30px rgba(0,0,0,.45); }}
.card {{ position: absolute; left: {tx}px; width: {tw}px; height: {th}px; border-radius: 28px; overflow: hidden; box-shadow: 0 24px 70px rgba(0,0,0,.6); }}
.card img {{ width: 100%; height: 100%; display: block; }}
.play {{ position: absolute; left: 50%; top: 50%; width: 124px; height: 124px; margin: -62px 0 0 -62px; border-radius: 50%; background: rgba(255,255,255,.93); }}
.play::after {{ content: ""; position: absolute; left: 48px; top: 31px; border-left: 52px solid #258AF3; border-top: 31px solid transparent; border-bottom: 31px solid transparent; }}
.dur {{ position: absolute; right: 18px; bottom: 34px; background: rgba(0,0,0,.78); border-radius: 8px; padding: 6px 12px; font-size: 30px; }}
.track {{ position: absolute; left: 18px; right: 18px; bottom: 12px; height: 10px; border-radius: 5px; background: rgba(255,255,255,.35); }}
.fill {{ position: absolute; left: 0; top: 0; height: 100%; width: 100%; border-radius: 5px; background: #258AF3; transform-origin: left center; }}
.btn {{ position: absolute; left: 50%; height: 96px; padding: 0 48px 0 96px; border-radius: 48px; background: #258AF3; white-space: nowrap;
  font-size: 38px; line-height: 96px; transform-origin: center center; box-shadow: 0 12px 34px rgba(37,138,243,.35); }}
.btn::before {{ content: ""; position: absolute; left: 46px; top: 30px; border-left: 30px solid #fff; border-top: 18px solid transparent; border-bottom: 18px solid transparent; }}
.logo {{ position: absolute; left: 50%; }}
</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{d}" data-width="1080" data-height="1920" data-fps="30">
<div class="layer" id="bgwrap"><video class="bgv" id="bgvid" src="assets/outro.mp4" data-start="0" data-duration="{d}" data-media-start="0" data-track-index="0" muted playsinline></video></div>
<div class="clip layer" id="ov" data-start="0" data-duration="{d}" data-track-index="2"><div class="veil"></div>
<img class="logo" id="lg" src="assets/logo.png" alt="" style="top:300px; width:420px; margin-left:-210px" />
<div class="title" id="tt" style="top:520px">{title}</div>
<div class="card" id="cd" style="top:{y}px"><img src="assets/thumb.jpg" alt="" /><div class="play" id="cd-play"></div>{dur}<div class="track"><div class="fill" id="cd-fill"></div></div></div>
<svg class="layer" viewBox="0 0 1080 1920"><path id="ar" d="M 770 {a0} Q 760 {a1} 640 {a2}" fill="none" stroke="#258AF3" stroke-width="9" stroke-linecap="round" stroke-dasharray="400" stroke-dashoffset="400"/>
<path id="ah" d="M 640 {a2} l 46 6 M 640 {a2} l 16 42" fill="none" stroke="#258AF3" stroke-width="9" stroke-linecap="round" opacity="0"/></svg>
<div class="btn" id="bt" style="top:{by}px">{cta}</div>
</div>
</div>
<script>
const tl = gsap.timeline({{ paused: true }});
tl.fromTo("#lg", {{ opacity: 0, y: -20 }}, {{ opacity: 1, y: 0, duration: .5, ease: "power2.out" }}, .15);
tl.fromTo("#tt", {{ opacity: 0, y: 24 }}, {{ opacity: 1, y: 0, duration: .5, ease: "power3.out" }}, .35);
tl.fromTo("#cd", {{ y: 380, opacity: 0, scale: .92 }}, {{ y: 0, opacity: 1, scale: 1, duration: .8, ease: "power3.out" }}, .6);
tl.fromTo("#cd-fill", {{ scaleX: 0 }}, {{ scaleX: .18, duration: 1.2, ease: "power1.inOut" }}, 1.5);
tl.fromTo("#cd-play", {{ scale: .6, opacity: 0 }}, {{ scale: 1, opacity: 1, duration: .45, ease: "back.out(2)" }}, 1.25);
tl.to("#ar", {{ attr: {{ "stroke-dashoffset": 0 }}, duration: .6, ease: "power2.out" }}, 2.0);
tl.to("#ah", {{ opacity: 1, duration: .15 }}, 2.55);
tl.fromTo("#bt", {{ xPercent: -50, y: 30, opacity: 0, scale: .9 }}, {{ xPercent: -50, y: 0, opacity: 1, scale: 1, duration: .5, ease: "back.out(1.7)" }}, 2.7);
tl.to("#bt", {{ scale: 1.045, duration: .45, ease: "sine.inOut", yoyo: true, repeat: 5 }}, 3.3);
window.__timelines = window.__timelines || {{}};
window.__timelines["main"] = tl;
tl.seek(0);
</script>
</body></html>
"""


def _mmss(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def episode_duration(ep: Path, video: Path) -> float | None:
    """Durée de l'épisode monté (badge de la carte) : MP4 final, sinon aperçu ; aucune durée inventée."""
    for name in (f"{video.stem}_episode.mp4", f"{video.stem}_episode_apercu.mp4"):
        f = ep / "episode" / name
        if f.exists():
            return float(probe(f)["duration"])
    return None


def thumbnail(ep: Path) -> Path:
    f = ep / "miniatures" / "miniature_finale.jpg"
    if not f.exists() or not (ep / "miniatures" / "miniature_choisie.json").exists():
        raise SystemExit("La miniature de l'épisode n'est pas encore choisie par l'équipe (`thumbnail --pick N`) : "
                         "l'outro des shorts la montre, elle se fait APRÈS ce choix. Les shorts, eux, peuvent avancer.")
    return f


def build(brand, ep: Path, video: Path, force: bool = False) -> Path:
    """Rend l'outro (7 s) une fois par épisode -> ep/outro_short/outro_9x16.mp4. Refaite si la miniature ou le texte change."""
    from .render import lint, render
    cfg = brand.cfg.get("short_outro") or {}
    d = float(cfg.get("duration", 7.0))
    thumb = thumbnail(ep)
    dur = episode_duration(ep, video)
    title = str(cfg.get("title", "L'épisode complet"))
    cta = str(cfg.get("cta", "Regarder l'épisode — lien en description"))
    proj = ep / "outro_short" / "9x16"
    out = ep / "outro_short" / "outro_9x16.mp4"
    stamp = hashlib.sha256(thumb.read_bytes() + f"{title}|{cta}|{dur and _mmss(dur)}|{d}".encode()).hexdigest()[:16]
    stamp_f = out.with_suffix(".stamp")
    if out.exists() and not force and stamp_f.exists() and stamp_f.read_text() == stamp:
        return out
    assets = proj / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    if force or not (assets / "outro.mp4").exists():
        prepare_outro_video(brand.asset(str(cfg.get("video", "outro_anim.mov"))), assets / "outro.mp4", W, H, d,
                            focus_x=float(cfg.get("video_focus_x", 0.62)), fps=FPS)
    shutil.copy2(thumb, assets / "thumb.jpg")
    shutil.copy2(brand.asset(str(cfg.get("logo", "logo.png"))), assets / "logo.png")
    shutil.copy2(brand.asset("fonts/Metropolis-Bold.ttf"), assets / "Metropolis-Bold.ttf")
    y = 640
    html = PAGE.format(tx=TX, tw=TW, th=TH, d=d, title=title, cta=cta, y=y, by=y + TH + 280,
                       a0=y + TH + 250, a1=y + TH + 140, a2=y + TH + 50,
                       dur=f'<div class="dur">{_mmss(dur)}</div>' if dur else "")
    (proj / "index.html").write_text(html, encoding="utf-8")
    ok, findings = lint(proj)
    if not ok:
        raise SystemExit("Outro : composition refusée par HyperFrames : " + "; ".join(str(f.get("message", f))[:120] for f in findings[:3]))
    console.print("Rendu de l'outro des shorts (HyperFrames, ~2 min)…")
    render(proj, out, quality="delivery", fps=FPS)
    stamp_f.write_text(stamp)
    return out


def has_builtin_outro(ep: Path, short_mp4: Path) -> bool:
    """Le short a-t-il été rendu AVEC l'ancienne carte de fin intégrée ? (sa composition contient `id="outro"`)."""
    m = re.match(r"(clip_\d+_.+)_(9x16|16x9|1x1)\.mp4$", short_mp4.name)
    idx = ep / "clips" / m.group(1) / m.group(2) / "index.html" if m else None
    return bool(idx and idx.exists() and 'id="outro"' in idx.read_text(encoding="utf-8", errors="replace"))


def shorts(ep: Path, only: set[int] | None = None) -> list[Path]:
    """Rendus finaux des shorts verticaux (pas les anciennes versions `_v1…`, pas le teaser)."""
    out = []
    for f in sorted((ep / "renders").glob("clip_*_9x16.mp4")):
        m = re.match(r"clip_(\d+)_", f.name)
        if m and int(m.group(1)) != 99 and (only is None or int(m.group(1)) in only):
            out.append(f)
    return out


def append(short: Path, outro: Path, dst: Path, fade: float = 0.4) -> Path:
    """short + outro -> dst. La dernière image du short est tenue `fade` s pendant que l'outro apparaît en fondu : la voix
    n'est JAMAIS raccourcie ni fondue (règle « jamais couper une fin de phrase »). Outro muette. H.264 CRF 14, AAC."""
    d = float(probe(short)["duration"])
    total = d + float(probe(outro)["duration"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    fc = (f"[0:v]fps={FPS},settb=AVTB,format=yuv420p,tpad=stop_mode=clone:stop_duration={fade}[a];"
          f"[1:v]fps={FPS},settb=AVTB,format=yuv420p,scale={W}:{H}[b];"
          f"[a][b]xfade=transition=fade:duration={fade}:offset={d:.3f}[v];"
          f"[0:a]aresample=48000,apad,atrim=0:{total:.3f}[aout]")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(short), "-i", str(outro), "-filter_complex", fc,
                    "-map", "[v]", "-map", "[aout]", "-c:v", "libx264", "-crf", "14", "-preset", "medium",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst)], check=True)
    return dst


def apply(brand, ep: Path, video: Path, only: set[int] | None = None, force: bool = False) -> list[dict]:
    """Outro rendue (si besoin) puis collée à chaque short rendu -> ep/livrables/<short>.mp4 ; rapport par short."""
    outro = build(brand, ep, video, force=force)
    rows = []
    for s in shorts(ep, only):
        if has_builtin_outro(ep, s):
            rows.append({"short": s.name, "status": "À REFAIRE",
                         "msg": "rendu avec l'ancienne carte de fin intégrée : `build --only N --force` puis `render --only N`"})
            continue
        dst = ep / "livrables" / s.name
        append(s, outro, dst)
        want = float(probe(s)["duration"]) + float(probe(outro)["duration"])
        got = float(probe(dst)["duration"])
        rows.append({"short": s.name, "status": "OK" if abs(got - want) < 0.25 else "ATTENTION",
                     "msg": f"{got:.1f} s (short {float(probe(s)['duration']):.1f} s + outro) -> livrables/{dst.name}"})
    (ep / "livrables").mkdir(exist_ok=True)
    (ep / "livrables" / "outro.json").write_text(json.dumps({"outro": str(outro), "shorts": rows}, ensure_ascii=False, indent=1),
                                                encoding="utf-8")
    return rows
