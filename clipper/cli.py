"""Interface en ligne de commande.

  python -m clipper run       --brand <slug> --input episode.mp4 [--guest "…" --company "…"] [--n 6] [--formats 9x16,16x9] [--no-render]
  python -m clipper transcribe --brand <slug> --input episode.mp4
  python -m clipper select    --brand <slug> --input episode.mp4 [--force]        # (re)sélection LLM
  python -m clipper build     --brand <slug> --input episode.mp4 [--only 1,3]     # projets HyperFrames
  python -m clipper render    --brand <slug> --input episode.mp4 [--only 1]       # MP4
  python -m clipper new-brand <slug>
  python -m clipper brands
  python -m clipper doctor
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from rich.console import Console

from .config import BRANDS_DIR, FORMATS, OUTPUT_DIR, Brand, list_brands
from .compose import build_clip, slugify
from .render import lint, render
from .select_clips import manual_clips, select_clips
from .transcribe import transcribe

console = Console()


def resolve_input(brand: Brand, given: str) -> Path:
    """`--input` : chemin complet, ou simple nom de fichier cherché dans brands/<slug>/episodes/."""
    p = Path(given)
    if p.exists():
        return p.resolve()
    cand = brand.dir / "episodes" / given
    if cand.exists():
        return cand.resolve()
    matches = list((brand.dir / "episodes").glob(f"*{given}*")) if (brand.dir / "episodes").exists() else []
    if len(matches) == 1:
        return matches[0].resolve()
    sys.exit(f"Épisode introuvable : {given} (ni comme chemin, ni dans {brand.dir / 'episodes'})")


def episode_dir(brand: Brand, video: Path) -> Path:
    d = OUTPUT_DIR / brand.slug / slugify(video.stem, 60)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_only(s: str | None) -> set[int] | None:
    if not s:
        return None
    return {int(x) for x in s.split(",") if x.strip()}


def _parse_ranges(s: str | None) -> list[tuple[float, float]]:
    out = []
    for part in (s or "").split(","):
        if "-" in part:
            a, b = part.split("-")
            out.append((float(a), float(b)))
    return out


def cmd_transcribe(a: argparse.Namespace) -> dict:
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    return transcribe(video, ep / "transcript.json", brand.cfg, ep / "work", force=a.force)


def cmd_select(a: argparse.Namespace) -> dict:
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    transcript = transcribe(video, ep / "transcript.json", brand.cfg, ep / "work")
    if getattr(a, "ranges", None):
        data = manual_clips(transcript, _parse_ranges(a.ranges), a.guest, a.company)
        data["brand"] = brand.slug
        data["host_side"] = a.host_side or brand.cfg.framing.get("host_side", "")
        (ep / "clips.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        console.print(f"[green]{len(data['clips'])} extraits manuels[/green] → {ep / 'clips.json'}")
        return data
    return select_clips(transcript, brand, ep / "clips.json", n_clips=a.n, guest=a.guest, company=a.company,
                        force=a.force, extra_instructions=a.instructions or "", host_side=a.host_side)


def cmd_build(a: argparse.Namespace) -> list[Path]:
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    transcript = json.loads((ep / "transcript.json").read_text(encoding="utf-8"))
    clips = json.loads((ep / "clips.json").read_text(encoding="utf-8"))
    formats = [f.strip() for f in a.formats.split(",")] if a.formats else None
    for f in formats or []:
        if f not in FORMATS:
            sys.exit(f"Format inconnu : {f} (choix : {', '.join(FORMATS)})")
    only = _parse_only(a.only)
    # projets d'une ancienne sélection : on les retire pour ne pas les rendre par erreur
    keep = {f"clip_{c['index']:02d}_{slugify(c.get('title', ''))}" for c in clips["clips"]}
    for d in (ep / "clips").glob("clip_*") if (ep / "clips").exists() else []:
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)
            console.print(f"[dim]projet obsolète supprimé : {d.name}[/dim]")
    projects = []
    for clip in clips["clips"]:
        if only and clip["index"] not in only:
            continue
        clip.setdefault("guest", clips.get("guest", ""))
        clip.setdefault("company", clips.get("company", ""))
        clip.setdefault("host_side", clips.get("host_side", ""))
        proj = build_clip(brand, video, transcript, clip, ep, formats=formats, force=a.force, with_broll=not a.no_broll)
        projects.append(proj)
        if brand.cfg.render.lint:
            for fmt, info in json.loads((proj / "clip.json").read_text(encoding="utf-8"))["formats"].items():
                ok, findings = lint(proj / info["dir"])
                errs = [f for f in findings if str(f.get("severity", "")).lower() in ("error", "warning")]
                status = "[green]lint OK[/green]" if ok else "[red]lint ERREURS[/red]"
                console.print(f"  {fmt:5s} {status}" + (f" · {len(errs)} avertissement(s)/erreur(s)" if errs else ""))
                for f in errs[:8]:
                    console.print(f"     - {f.get('code', '?')}: {str(f.get('message', ''))[:160]}")
    _write_summary(ep, clips, projects)
    return projects


def cmd_render(a: argparse.Namespace) -> list[Path]:
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    only = _parse_only(a.only)
    clips = json.loads((ep / "clips.json").read_text(encoding="utf-8"))
    keep = {f"clip_{c['index']:02d}_{slugify(c.get('title', ''))}" for c in clips["clips"]}
    outputs = []
    for proj in sorted((ep / "clips").glob("clip_*")):
        idx = int(proj.name.split("_")[1])
        if proj.name not in keep or (only and idx not in only):
            continue
        meta = json.loads((proj / "clip.json").read_text(encoding="utf-8"))
        for fmt, info in meta["formats"].items():
            if a.formats and fmt not in a.formats.split(","):
                continue
            out = ep / "renders" / f"{proj.name}_{fmt}.mp4"
            if out.exists() and not a.force:
                console.print(f"[dim]déjà rendu : {out.name}[/dim]")
                outputs.append(out)
                continue
            try:
                outputs.append(render(proj / info["dir"], out, quality=a.quality or brand.cfg.render.quality, fps=int(brand.cfg.fps)))
                console.print(f"[green]✓ {out.name}[/green] ({out.stat().st_size/1e6:.1f} Mo)")
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]✗ {proj.name} {fmt} : {e}[/red]")
    return outputs


def cmd_run(a: argparse.Namespace) -> None:
    cmd_transcribe(a)
    cmd_select(a)
    cmd_build(a)
    if not a.no_render:
        cmd_render(a)
    brand = Brand(a.brand)
    ep = episode_dir(brand, resolve_input(brand, a.input))
    console.rule("[bold green]Terminé[/bold green]")
    console.print(f"Dossier épisode : {ep}\n  clips.json (sélection éditable), clips/<clip>/ (projets HyperFrames), renders/ (MP4), summary.md")


def _write_summary(ep: Path, clips: dict, projects: list[Path]) -> None:
    lines = [f"# Clips — {clips.get('guest', '')} ({clips.get('company', '')})", ""]
    for c in clips["clips"]:
        lines += [f"## #{c['index']} · {c['title']}",
                  f"- **Timecode** : {c['start']:.1f}s → {c['end']:.1f}s ({c['duration']:.0f}s) · score {c.get('score', '')}",
                  f"- **Pourquoi** : {c.get('why', '')}",
                  f"- **Accroche** : {c.get('hook_title', '')}",
                  f"- **Mots-clés** : {', '.join(c.get('keywords', []))}",
                  f"- **B-roll** : {', '.join(b['query'] for b in c.get('broll', [])) or '—'}",
                  "", "**Texte de publication :**", "", "```", c.get("post", ""), "```", ""]
    (ep / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def cmd_preview(a: argparse.Namespace) -> None:
    """Ouvre le Studio HyperFrames (éditeur timeline) sur un clip pour retouches manuelles."""
    import subprocess
    brand = Brand(a.brand)
    ep = episode_dir(brand, resolve_input(brand, a.input))
    idx = int(a.clip)
    proj = next((p for p in sorted((ep / "clips").glob("clip_*")) if int(p.name.split("_")[1]) == idx), None)
    if not proj:
        sys.exit(f"Clip #{idx} introuvable dans {ep / 'clips'}")
    fmt = a.formats or brand.cfg.formats[0]
    target = proj / fmt
    console.print(f"Studio HyperFrames → {target}")
    console.print(f"(Ctrl+C pour arrêter ; rendu ensuite avec : python -m clipper render … --only {idx})")
    subprocess.run(["npx", "hyperframes", "preview"], cwd=str(target), shell=(sys.platform == "win32"))


def cmd_doctor(_: argparse.Namespace) -> None:
    """Vérifie que tout est en place pour lancer le pipeline sur cette machine."""
    import importlib.util
    import os
    import subprocess

    from .config import ROOT, env

    ok_all = True

    def check(label: str, ok: bool, detail: str = "", hint: str = "") -> None:
        nonlocal ok_all
        ok_all &= ok
        mark = "[green]OK[/green]" if ok else "[red]KO[/red]"
        line = f"  {mark}  {label:28s} {detail}"
        if hint and not ok:
            line += chr(10) + "       -> " + hint
        console.print(line)

    def which_version(cmd: list[str]) -> str | None:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, shell=(os.name == "nt"))
            return (r.stdout or r.stderr).strip().splitlines()[0] if r.returncode == 0 else None
        except Exception:  # noqa: BLE001
            return None

    console.rule("[bold]clipper doctor[/bold]")
    v = which_version(["ffmpeg", "-version"]); check("FFmpeg", bool(v), (v or "")[:60], "installer FFmpeg et l'ajouter au PATH (winget install Gyan.FFmpeg)")
    v = which_version(["node", "--version"]); check("Node.js ≥ 22", bool(v) and int((v or "v0").lstrip("v").split(".")[0]) >= 22, v or "", "installer Node 22+ (nodejs.org)")
    check("HyperFrames CLI", (ROOT / "node_modules" / "hyperframes").exists(), "", "npm install (dans le dossier du projet)")
    for mod, pipname in [("faster_whisper", "faster-whisper"), ("cv2", "opencv-python"), ("jinja2", "jinja2"), ("yaml", "pyyaml"), ("rich", "rich"), ("requests", "requests"), ("dotenv", "python-dotenv")]:
        check(f"python: {pipname}", importlib.util.find_spec(mod) is not None, "", "pip install -r requirements.txt")
    check("modèle visages (YuNet)", (ROOT / "models" / "face_detection_yunet_2023mar.onnx").exists(), "", "fichier models/face_detection_yunet_2023mar.onnx manquant (voir README)")
    check("PEXELS_API_KEY", bool(env("PEXELS_API_KEY")), "", "copier .env.example en .env et renseigner la clé (pexels.com/api)")
    llm = "SDK Anthropic (ANTHROPIC_API_KEY)" if env("ANTHROPIC_API_KEY") else ("claude CLI" if shutil.which("claude") else "")
    check("LLM (sélection)", bool(llm), llm, "définir ANTHROPIC_API_KEY dans .env ou installer Claude Code (commande `claude`)")
    check("marques", bool(list_brands()), ", ".join(list_brands()), "python -m clipper new-brand <slug>")
    try:
        import torch  # noqa

        gpu = torch.cuda.is_available()
        console.print(f"  [dim]GPU : {'oui' if gpu else 'non (transcription sur CPU ≈ 0,4× temps réel)'}[/dim]")
    except Exception:  # noqa: BLE001
        pass
    console.print("[green]Tout est prêt.[/green]" if ok_all else "[red]Corrigez les points KO avant de lancer le pipeline.[/red]")
    if not ok_all:
        sys.exit(1)


def cmd_new_brand(a: argparse.Namespace) -> None:
    dst = BRANDS_DIR / a.slug
    if dst.exists():
        sys.exit(f"Le dossier {dst} existe déjà.")
    shutil.copytree(BRANDS_DIR / "_template", dst)
    console.print(f"[green]Marque créée[/green] : {dst}\n  → éditez brand.yaml, guidelines.md et déposez logo.png dans assets/")


def cmd_brands(_: argparse.Namespace) -> None:
    for b in list_brands():
        brand = Brand(b)
        console.print(f"- {b:30s} {brand.cfg.name} · preset {brand.cfg.montage.preset} · formats {', '.join(brand.cfg.formats)}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="clipper", description="Clips verticaux automatiques à partir d'un podcast (HyperFrames).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser, need_input: bool = True) -> None:
        sp.add_argument("--brand", required=True, help="slug du dossier brands/<slug>")
        if need_input:
            sp.add_argument("--input", required=True, help="podcast source : chemin, ou nom de fichier dans brands/<slug>/episodes/")
        sp.add_argument("--force", action="store_true", help="ignore les caches")

    def selection_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--guest", default="", help="nom de l'invité (carte de fin)")
        sp.add_argument("--company", default="", help="entreprise de l'invité")
        sp.add_argument("--n", type=int, default=None, help="nombre d'extraits")
        sp.add_argument("--instructions", default="", help="consignes supplémentaires pour la sélection")
        sp.add_argument("--ranges", default="", help="sélection manuelle, ex: 120-160,900-940 (secondes)")
        sp.add_argument("--host-side", default="", choices=["", "left", "right"], help="côté de l'animateur dans le plan large")

    def build_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--formats", default="", help="ex: 9x16,16x9 (défaut : brand.yaml)")
        sp.add_argument("--only", default="", help="index de clips, ex: 1,3")
        sp.add_argument("--no-broll", action="store_true")

    def render_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--quality", default="", help="draft | looks | delivery")

    sp = sub.add_parser("run", help="pipeline complet"); common(sp); selection_args(sp); build_args(sp); render_args(sp)
    sp.add_argument("--no-render", action="store_true"); sp.set_defaults(fn=cmd_run)
    sp = sub.add_parser("transcribe"); common(sp); sp.set_defaults(fn=cmd_transcribe)
    sp = sub.add_parser("select"); common(sp); selection_args(sp); sp.set_defaults(fn=cmd_select)
    sp = sub.add_parser("build"); common(sp); build_args(sp); sp.set_defaults(fn=cmd_build)
    sp = sub.add_parser("render"); common(sp); build_args(sp); render_args(sp); sp.set_defaults(fn=cmd_render)
    sp = sub.add_parser("preview", help="ouvre le Studio HyperFrames sur un clip"); common(sp)
    sp.add_argument("--clip", required=True, help="index du clip (ex: 1)"); sp.add_argument("--formats", default="", help="format à ouvrir (ex: 9x16)")
    sp.set_defaults(fn=cmd_preview)
    sp = sub.add_parser("new-brand"); sp.add_argument("slug"); sp.set_defaults(fn=cmd_new_brand)
    sp = sub.add_parser("brands"); sp.set_defaults(fn=cmd_brands)
    sp = sub.add_parser("doctor", help="vérifie l'installation (FFmpeg, Node, HyperFrames, Python, clés)"); sp.set_defaults(fn=cmd_doctor)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
