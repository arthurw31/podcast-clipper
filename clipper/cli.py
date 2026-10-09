"""Interface en ligne de commande.

  python -m clipper run       --brand <slug> --input episode.mp4 [--guest "…" --company "…"] [--n 6] [--formats 9x16,16x9] [--no-render]
  python -m clipper transcribe --brand <slug> --input episode.mp4
  python -m clipper propose   --brand <slug> --input episode.mp4 --guest … --company … [--n 10]  # passages candidats
  python -m clipper pick      --brand <slug> --input episode.mp4 --ids 1,3,4,7,9                   # choix -> clips.json
  python -m clipper passages  --brand <slug> --input episode.mp4 --file passages.yaml            # passages déjà choisis
  python -m clipper find      --brand <slug> --input episode.mp4 "mots de la phrase"               # retrouver un passage
  python -m clipper check     --brand <slug> --input episode.mp4                                   # contrôle des coupes
  python -m clipper select    --brand <slug> --input episode.mp4 [--force]        # (re)sélection LLM directe
  python -m clipper build     --brand <slug> --input episode.mp4 [--only 1,3]     # projets HyperFrames
  python -m clipper preview   --brand <slug> --input episode.mp4 [--stop]         # aperçu instantané, sans rendu
  python -m clipper render    --brand <slug> --input episode.mp4 [--only 1]       # MP4
  python -m clipper posts     --brand <slug> --input episode.mp4 [--episode-url URL] # post LinkedIn + description par clip
  python -m clipper episode-plan|episode-render --brand <slug> --input E.mp4 [--proxy]   # épisode complet (rushs)
  python -m clipper fetch     <lien Dropbox> [--dest depot]                       # rushs : reprise auto, tailles vérifiées
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

from .config import BRANDS_DIR, FORMATS, OUTPUT_DIR, ROOT as ROOT_DIR, Brand, list_brands
from .compose import build_clip, slugify
from .posts import write_posts
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
    return transcribe(video, ep / "transcript.json", brand.cfg, ep / "work", force=a.force,
                      names=[getattr(a, "guest", ""), getattr(a, "company", "")])


def cmd_select(a: argparse.Namespace) -> dict:
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    transcript = transcribe(video, ep / "transcript.json", brand.cfg, ep / "work", names=[a.guest, a.company])
    if getattr(a, "ranges", None):
        data = manual_clips(transcript, _parse_ranges(a.ranges), a.guest, a.company)
        data["brand"] = brand.slug
        data["host_side"] = a.host_side or brand.cfg.framing.get("host_side", "")
        (ep / "clips.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        console.print(f"[green]{len(data['clips'])} extraits manuels[/green] → {ep / 'clips.json'}")
        return data
    return select_clips(transcript, brand, ep / "clips.json", n_clips=a.n, guest=a.guest, company=a.company,
                        force=a.force, extra_instructions=a.instructions or "", host_side=a.host_side)


def _ts(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def _clip_text(transcript: dict, clip: dict) -> str:
    from .transcribe import words_between
    parts = []
    for sg in clip.get("segments") or [clip]:
        parts.append(" ".join(w["w"] for w in words_between(transcript, float(sg["start"]), float(sg["end"]))))
    return " […] ".join(parts)


def cmd_propose(a: argparse.Namespace) -> dict:
    """Étape 2 du workflow : ~10 passages candidats (thème, titre, timecodes, résumé) à soumettre à l'humain."""
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    transcript = transcribe(video, ep / "transcript.json", brand.cfg, ep / "work", names=[a.guest, a.company])
    data = select_clips(transcript, brand, ep / "candidates.json", n_clips=a.n or 10, guest=a.guest, company=a.company,
                        force=a.force or not (ep / "candidates.json").exists(), extra_instructions=a.instructions or "",
                        host_side=a.host_side)
    lines = [f"# Passages proposés — {data.get('guest', '')} ({data.get('company', '')})", "",
             "Choisissez-en 5 : `python -m clipper pick … --ids 1,3,4,7,9`", ""]
    for c in data["clips"]:
        segs = " + ".join(f"{_ts(sg['start'])}→{_ts(sg['end'])}" for sg in c["segments"])
        lines += [f"## {c['index']}. {c.get('hook_title') or c['title']}",
                  f"- **Thème** : {c['title']} · angle : {c.get('angle', '').split(':')[0] or '—'}",
                  f"- **Passages** : {segs} ({c['duration']:.0f} s) · score {c.get('score', '')}",
                  f"- **Pourquoi** : {c.get('why', '')}",
                  f"- **Ce qu'on entend** : « {_clip_text(transcript, c)[:420]}… »", ""]
    (ep / "candidates.md").write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]{len(data['clips'])} passages proposés[/green] → {ep / 'candidates.md'}")
    for c in data["clips"]:
        console.print(f"  {c['index']:2d}. ({c['duration']:.0f}s) {c.get('hook_title') or c['title']}")
    return data


def cmd_pick(a: argparse.Namespace) -> dict:
    """Étape 3 : garde les candidats choisis (dans l'ordre donné) -> clips.json, prêt pour build."""
    brand = Brand(a.brand)
    ep = episode_dir(brand, resolve_input(brand, a.input))
    cand = json.loads((ep / "candidates.json").read_text(encoding="utf-8"))
    by_id = {c["index"]: c for c in cand["clips"]}
    ids = [int(x) for x in a.ids.split(",") if x.strip()]
    missing = [i for i in ids if i not in by_id]
    if missing:
        sys.exit(f"Candidats inconnus : {missing} (disponibles : {sorted(by_id)})")
    clips = [dict(by_id[i], index=n, candidate=i) for n, i in enumerate(ids, 1)]
    out = dict(cand, clips=clips)
    if (ep / "clips.json").exists():
        shutil.copy2(ep / "clips.json", ep / "clips_previous.json")
    (ep / "clips.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print(f"[green]{len(clips)} shorts retenus[/green] → {ep / 'clips.json'}")
    for c in clips:
        console.print(f"  #{c['index']} (candidat {c['candidate']}) {c.get('hook_title') or c['title']}")
    return out


def _parse_tc(tc: str) -> float:
    """« 01:29 », « 00:18:17 », « 00:18:17:09 » (HH:MM:SS:images, 24 i/s) ou « 1097.5 » -> secondes."""
    tc = str(tc).strip().lstrip("~").strip()
    parts = tc.split(":")
    if len(parts) == 1:
        return float(parts[0])
    nums = [float(x) for x in parts]
    if len(nums) == 4:
        h, m, s, f = nums
        return h * 3600 + m * 60 + s + f / 24.0
    if len(nums) == 3:
        h, m, s = nums
        return h * 3600 + m * 60 + s
    m, s = nums
    return m * 60 + s


def cmd_passages(a: argparse.Namespace) -> dict:
    """Passages déjà choisis par l'équipe (timecodes approximatifs + premiers / derniers mots) -> clips.json.

    Fichier YAML : liste de {start: "00:18:17:09", end: "00:18:50:19", start_text: "Et donc, il y a un aspect
    humain", end_text: "le temps et l'envie de le faire", hook_title: "…" (facultatif), title: "…" (facultatif)}.
    Les mots font foi : chaque passage est calé sur les mots cités, cherchés autour du timecode."""
    import yaml
    from .select_clips import _snap, snap_to_quotes
    from .transcribe import all_words, sentences
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    transcript = transcribe(video, ep / "transcript.json", brand.cfg, ep / "work", names=[a.guest, a.company])
    W, S = all_words(transcript), sentences(transcript)
    spec = yaml.safe_load(Path(a.file).read_text(encoding="utf-8")) or []
    clips = []
    for i, p in enumerate(spec, 1):
        s0, e0 = _parse_tc(p["start"]), _parse_tc(p.get("end") or p["start"]) or 0
        if e0 <= s0:
            e0 = s0 + 40
        s1, e1, note = snap_to_quotes(W, S, s0, e0, str(p.get("start_text", "")), str(p.get("end_text", "")),
                                      max(e0 - s0, 20) + 15)
        ss, ee = _snap(W, s1, e1)
        nxt = [w for w in W if w["s"] >= ee]
        tail = round(max(0.0, (nxt[0]["s"] - ee) if nxt else 1.0), 3)
        inside = [w["w"] for w in W if w["s"] >= ss - 0.05 and w["e"] <= ee + 0.05]
        sg = {"start": ss, "end": ee, "duration": round(ee - ss, 3), "tail_silence": tail,
              "start_text": " ".join(inside[:6]), "end_text": " ".join(inside[-6:]), "role": "passage"}
        clips.append({"index": i, "title": p.get("title") or p.get("hook_title") or f"Short {i}",
                      "hook_title": p.get("hook_title", ""), "start": ss, "end": ee, "duration": sg["duration"],
                      "segments": [sg], "tail_silence": tail, "why": p.get("why", "passage choisi par l'équipe"),
                      "score": 10, "start_text": sg["start_text"], "end_text": sg["end_text"], "keywords": [],
                      "turns": [{"at": float(t["at"]) if not isinstance(t["at"], str) else _parse_tc(t["at"]), "speaker": t["speaker"]}
                                for t in (p.get("turns") or [])],
                      "broll": [], "angle": "choix de l'équipe", "guest": a.guest, "company": a.company})
        console.print(f"  #{i} {ss:.2f}→{ee:.2f} ({ee - ss:.1f}s){'  · ' + note if note else ''}")
    out = {"brand": brand.slug, "guest": a.guest, "company": a.company, "guest_role": a.guest_role,
           "host_side": a.host_side or brand.cfg.framing.get("host_side", ""), "clips": clips}
    if (ep / "clips.json").exists():
        shutil.copy2(ep / "clips.json", ep / "clips_previous.json")
    (ep / "clips.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print(f"[green]{len(clips)} passages[/green] → {ep / 'clips.json'} — vérifier avec `check`, compléter "
                  f"hook_title et turns (qui parle) si absents")
    return out


def cmd_find(a: argparse.Namespace) -> None:
    """Retrouve une phrase dans la transcription (demande du type « il faut le passage où il dit … »)."""
    import re as _re
    import unicodedata as _ud
    from .transcribe import sentences

    def norm(x: str) -> str:
        x = _ud.normalize("NFD", x.lower())
        return _re.sub(r"[^a-z0-9 ]", "", "".join(ch for ch in x if _ud.category(ch) != "Mn"))

    brand = Brand(a.brand)
    ep = episode_dir(brand, resolve_input(brand, a.input))
    transcript = json.loads((ep / "transcript.json").read_text(encoding="utf-8"))
    sents = sentences(transcript)
    q = set(norm(a.text).split())
    scored = []
    for i in range(len(sents)):
        window = " ".join(x["text"] for x in sents[i:i + 3])
        scored.append((len(q & set(norm(window).split())) / max(1, len(q)), i))
    scored.sort(reverse=True)
    shown: list[int] = []
    for score, i in scored:
        if len(shown) >= a.n or score < 0.3:
            break
        if any(abs(i - j) <= 2 for j in shown):
            continue
        shown.append(i)
        console.print(f"[bold]{_ts(sents[i]['start'])}[/bold] ({sents[i]['start']:.1f}s) · correspondance {score:.0%}")
        for p in sents[max(0, i - 1): i + 3]:
            console.print(f"   [{p['start']:.1f} → {p['end']:.1f}] {p['text']}")
    if not shown:
        console.print("[yellow]Aucun passage ne correspond (essayer d'autres mots-clés)[/yellow]")


def cmd_check(a: argparse.Namespace) -> None:
    """Contrôle « jamais couper une pensée » : mots juste avant / au début / à la fin / juste après chaque passage."""
    from .transcribe import all_words
    brand = Brand(a.brand)
    ep = episode_dir(brand, resolve_input(brand, a.input))
    transcript = json.loads((ep / "transcript.json").read_text(encoding="utf-8"))
    W = all_words(transcript)
    clips = json.loads((ep / "clips.json").read_text(encoding="utf-8"))
    only = _parse_only(a.only)
    for c in clips["clips"]:
        if only and c["index"] not in only:
            continue
        console.rule(f"#{c['index']} {c.get('hook_title') or c['title']} ({c['duration']:.0f}s)")
        for sg in c["segments"]:
            before = [w["w"] for w in W if w["e"] <= sg["start"]][-6:]
            inside = [w["w"] for w in W if w["s"] >= sg["start"] - 0.05 and w["e"] <= sg["end"] + 0.05]
            after = [w["w"] for w in W if w["s"] >= sg["end"]][:6]
            console.print(f"[dim]{sg['start']:.2f}→{sg['end']:.2f} ({sg['duration']:.1f}s) · silence après {sg.get('tail_silence', 0):.2f}s[/dim]")
            console.print(f"  avant : …{' '.join(before)}")
            console.print(f"  [green]début : {' '.join(inside[:10])}[/green]")
            console.print(f"  [green]fin   : …{' '.join(inside[-10:])}[/green]")
            console.print(f"  après : {' '.join(after)}…")


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
    # seulement l'ANCIENNE version d'un short de clips.json (même numéro, autre titre) ; jamais le teaser (99) ni ses
    # variantes (97, 98…) qui ne sont pas dans clips.json (09/10/2026 : un build avait supprimé le teaser en plein rendu)
    idx_keep = {c["index"] for c in clips["clips"]}
    for d in (ep / "clips").glob("clip_*") if (ep / "clips").exists() else []:
        try:
            d_idx = int(d.name.split("_")[1])
        except (IndexError, ValueError):
            continue
        if d.is_dir() and d.name not in keep and d_idx in idx_keep:
            shutil.rmtree(d, ignore_errors=True)
            console.print(f"[dim]projet obsolète supprimé : {d.name}[/dim]")
    todo = []
    for clip in clips["clips"]:
        if only and clip["index"] not in only:
            continue
        clip.setdefault("guest", clips.get("guest", ""))
        clip.setdefault("company", clips.get("company", ""))
        clip.setdefault("host_side", clips.get("host_side", ""))
        todo.append(clip)
    jobs = _jobs(a, brand.cfg.build.get("jobs"), len(todo))
    projects: list[Path] = []
    results: list[tuple[Path, list[tuple[str, bool, list[dict]]]]] = []
    if jobs <= 1:
        for clip in todo:
            results.append(_build_one(a.brand, str(video), str(ep / "transcript.json"), clip, str(ep), formats,
                                      a.force, not a.no_broll))
    else:
        # un processus par clip : découpe ffmpeg + analyse OpenCV + lint sont indépendants d'un clip à l'autre
        from concurrent.futures import ProcessPoolExecutor

        console.print(f"[bold]Build parallèle[/bold] · {len(todo)} clip(s) · {jobs} processus")
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futs = [pool.submit(_build_one, a.brand, str(video), str(ep / "transcript.json"), clip, str(ep), formats,
                                a.force, not a.no_broll) for clip in todo]
            for fut in futs:
                results.append(fut.result())
    for proj, lints in results:
        projects.append(proj)
        console.print(f"[bold]{proj.name}[/bold]")
        for fmt, ok, findings in lints:
            errs = [f for f in findings if str(f.get("severity", "")).lower() in ("error", "warning")]
            status = "[green]lint OK[/green]" if ok else "[red]lint ERREURS[/red]"
            console.print(f"  {fmt:5s} {status}" + (f" · {len(errs)} avertissement(s)/erreur(s)" if errs else ""))
            for f in errs[:8]:
                console.print(f"     - {f.get('code', '?')}: {str(f.get('message', ''))[:160]}")
    _write_summary(ep, clips, projects)
    return projects


def _jobs(a: argparse.Namespace, cfg_jobs: int | str | None, n_tasks: int) -> int:
    """Nombre de tâches en parallèle : --jobs > config > 1 ; borné par le nombre de tâches."""
    import os

    raw = getattr(a, "jobs", None) or cfg_jobs or 1
    jobs = max(1, (os.cpu_count() or 2) // 4) if str(raw) == "auto" else int(raw)
    return max(1, min(jobs, n_tasks or 1))


def _build_one(slug: str, video: str, transcript_path: str, clip: dict, ep: str, formats: list[str] | None,
               force: bool, with_broll: bool) -> tuple[Path, list[tuple[str, bool, list[dict]]]]:
    """Construit un clip (exécutable dans un processus fils : arguments simples, Brand rechargée ici)."""
    brand = Brand(slug)
    transcript = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    proj = build_clip(brand, Path(video), transcript, clip, Path(ep), formats=formats, force=force, with_broll=with_broll)
    lints = []
    if brand.cfg.render.lint:
        for fmt, info in json.loads((proj / "clip.json").read_text(encoding="utf-8"))["formats"].items():
            ok, findings = lint(proj / info["dir"])
            lints.append((fmt, ok, findings))
    return proj, lints


def cmd_render(a: argparse.Namespace) -> list[Path]:
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    only = _parse_only(a.only)
    clips = json.loads((ep / "clips.json").read_text(encoding="utf-8"))
    keep = {f"clip_{c['index']:02d}_{slugify(c.get('title', ''))}" for c in clips["clips"]}
    outputs = []
    tasks: list[tuple[Path, Path]] = []
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
            tasks.append((proj / info["dir"], out))
    quality, fps = a.quality or brand.cfg.render.quality, int(brand.cfg.fps)

    def one(t: tuple[Path, Path]) -> Path | None:
        proj_dir, out = t
        try:
            res = render(proj_dir, out, quality=quality, fps=fps,
                         crf=None if a.quality else (int(brand.cfg.render.get("crf") or 0) or None),
                         frame_format=str(brand.cfg.render.get("video_frame_format") or ""))
            console.print(f"[green]✓ {out.name}[/green] ({out.stat().st_size/1e6:.1f} Mo)")
            return res
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]✗ {out.name} : {e}[/red]")
            return None

    jobs = _jobs(a, brand.cfg.render.get("jobs"), len(tasks))
    if jobs <= 1:
        done = [one(t) for t in tasks]
    else:
        # chaque rendu est un `npx hyperframes render` (Chromium) indépendant : N en parallèle sur les cœurs disponibles
        from concurrent.futures import ThreadPoolExecutor

        console.print(f"[bold]Rendu parallèle[/bold] · {len(tasks)} rendu(s) · {jobs} en même temps")
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            done = list(pool.map(one, tasks))
    outputs += [d for d in done if d]
    return outputs


def cmd_posts(a: argparse.Namespace) -> dict:
    """Post LinkedIn + description courte pour chaque clip (brief : brands/<slug>/posts.md)."""
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    transcript = json.loads((ep / "transcript.json").read_text(encoding="utf-8"))
    clips = json.loads((ep / "clips.json").read_text(encoding="utf-8"))
    if getattr(a, "guest_role", ""):
        clips["guest_role"] = a.guest_role
    clips = write_posts(brand, ep, clips, transcript, episode_url=getattr(a, "episode_url", "") or "",
                        only=_parse_only(getattr(a, "only", "")), force=a.force)
    _write_summary(ep, clips)
    return clips


def cmd_run(a: argparse.Namespace) -> None:
    cmd_transcribe(a)
    cmd_select(a)
    cmd_build(a)
    try:
        cmd_posts(a)
    except Exception as e:  # noqa: BLE001 — les textes ne doivent pas bloquer le rendu
        console.print(f"[yellow]Posts non générés : {e}[/yellow] (relancer : python -m clipper posts …)")
    if not a.no_render:
        cmd_render(a)
    brand = Brand(a.brand)
    ep = episode_dir(brand, resolve_input(brand, a.input))
    console.rule("[bold green]Terminé[/bold green]")
    console.print(f"Dossier épisode : {ep}\n  clips.json (sélection éditable), clips/<clip>/ (projets HyperFrames), renders/ (MP4), posts/ (textes), summary.md")


def _write_summary(ep: Path, clips: dict, projects: list[Path] | None = None) -> None:
    lines = [f"# Clips — {clips.get('guest', '')} ({clips.get('company', '')})", ""]
    for c in clips["clips"]:
        segs = " + ".join(f"{sg['start']:.1f}→{sg['end']:.1f}" for sg in c.get("segments") or [])
        lines += [f"## #{c['index']} · {c['title']}",
                  f"- **Timecode** : {c['start']:.1f}s → {c['end']:.1f}s ({c['duration']:.0f}s) · score {c.get('score', '')}"
                  + (f" · segments {segs}" if len(c.get("segments") or []) > 1 else ""),
                  f"- **Pourquoi** : {c.get('why', '')}",
                  f"- **Accroche** : {c.get('hook_title', '')}",
                  f"- **Mots-clés** : {', '.join(c.get('keywords', []))}",
                  f"- **B-roll** : {', '.join(b['query'] for b in c.get('broll', [])) or '—'}", ""]
        if c.get("linkedin_post"):
            lines += ["**Post LinkedIn :**", "", "```", c["linkedin_post"], "```", "",
                      "**Description courte (Reels / Shorts) :**", "", "```", c.get("short_description", ""), "```", ""]
        else:
            lines += ["*Post LinkedIn pas encore rédigé : `python -m clipper posts …`*", ""]
    (ep / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def _free_port(port: int, target: Path) -> None:
    """Un aperçu d'un AUTRE short (autre épisode…) peut occuper le port : on l'arrête, sinon le lien afficherait l'ancien
    short (bug du 07/10/2026 : lien E22 -> short E21 resté ouvert sur 3002)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/projects", timeout=2) as r:
            projects = json.loads(r.read().decode("utf-8")).get("projects", [])
    except Exception:  # noqa: BLE001 — rien n'écoute sur ce port
        return
    from .render import _npx
    for pr in projects:
        d = Path(pr.get("dir", ""))
        if d and d.resolve() != target.resolve():
            _npx(["preview", str(d), "--stop"], ROOT_DIR, timeout=60)


def cmd_preview(a: argparse.Namespace) -> None:
    """Aperçu instantané (sans rendu) : le short est joué en direct dans le navigateur par HyperFrames Studio.

    Un serveur en arrière-plan par short (port 3002, 3003, …) ; ils se rechargent tout seuls après un
    `build --only N`. `--stop` les arrête tous. À valider avant le rendu final."""
    import time

    from .render import _npx
    brand = Brand(a.brand)
    ep = episode_dir(brand, resolve_input(brand, a.input))
    fmt = a.formats or brand.cfg.formats[0]
    only = None if str(a.clip).lower() in ("", "all", "tous") else _parse_only(a.clip)
    projs = [p for p in sorted((ep / "clips").glob("clip_*")) if p.is_dir() and (p / fmt / "index.html").exists()
             and (only is None or int(p.name.split("_")[1]) in only)]
    if not projs:
        sys.exit(f"Aucun short monté dans {ep / 'clips'} (lancer build d'abord)")
    if a.mp4:
        # aperçu en MP4 (qualité brouillon, ~5 min par minute de vidéo) à ouvrir dans un lecteur vidéo : Arthur,
        # 08/10/2026 : « les aperçus dans HyperFrames buguent à chaque fois, je ne peux pas bien voir s'il y a des
        # problèmes ». Ce n'est PAS le rendu final : nom « _apercu », dossier apercus/.
        out_dir = ep / "apercus"

        def one_preview(proj: Path) -> None:
            dst = out_dir / f"{proj.name}_{fmt}_apercu.mp4"
            console.print(f"  rendu de l'aperçu {proj.name} ({fmt}, brouillon)…")
            # 12 i/s par défaut (Arthur, 08/10/2026 : « fais le genre en 240p pour que ce soit rapide sur la phase
            # d'itération ») : HyperFrames ne sait pas rendre plus petit que la composition, le coût est par image
            render(proj / fmt, dst, quality="draft", fps=int(a.fps))
            console.print(f"  [green]{dst.resolve()}[/green]")

        # 2 aperçus à la fois (mesuré : 2 rendus en parallèle ≈ 1,6× plus vite sur ce PC ; plus = contention)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2 if len(projs) > 1 else 1) as ex:
            list(ex.map(one_preview, projs))
        return
    for proj in projs:
        idx = int(proj.name.split("_")[1])
        target = proj / fmt
        if a.stop:
            _npx(["preview", str(target), "--stop"], ROOT_DIR, timeout=60)
            console.print(f"  #{idx} aperçu arrêté")
            continue
        port = int(a.port) + idx - 1
        _free_port(port, target)
        res = _npx(["preview", str(target), "--background", "--port", str(port), "--open" if getattr(a, "open", False) else "--no-open"],
                   ROOT_DIR, timeout=180)
        ok = res.returncode == 0
        # ?v=… : le navigateur ne réaffiche jamais un ancien short servi auparavant sur le même port (cache)
        url = f"http://localhost:{port}/?v={int(time.time())}#project/{fmt}"
        console.print(f"  #{idx} {'[green]' if ok else '[red]'}{url}{'[/green]' if ok else ' (échec)[/red]'}"
                      f"  {proj.name}")
        if not ok:
            console.print(res.stdout[-800:] + res.stderr[-800:])
    if not a.stop:
        console.print("Lecture en direct (▶ sous l'image, plein écran à droite). Après une retouche : build --only N, "
                      "l'aperçu se recharge. Une fois validé : render, puis preview --stop.")


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
    from .render import _bundled_chrome_blocked, fallback_browser
    if env("HYPERFRAMES_BROWSER_PATH"):
        bp = env("HYPERFRAMES_BROWSER_PATH")
        check("navigateur de rendu", Path(bp).exists(), bp, "HYPERFRAMES_BROWSER_PATH (.env) pointe vers un fichier absent")
    elif _bundled_chrome_blocked():
        alt = fallback_browser()
        check("navigateur de rendu", bool(alt), f"Chrome embarqué bloqué par Windows -> {alt or '?'}",
              "installer Google Chrome, ou renseigner HYPERFRAMES_BROWSER_PATH dans .env")
    for mod, pipname in [("faster_whisper", "faster-whisper"), ("cv2", "opencv-python"), ("jinja2", "jinja2"), ("yaml", "pyyaml"), ("rich", "rich"), ("requests", "requests"), ("dotenv", "python-dotenv")]:
        check(f"python: {pipname}", importlib.util.find_spec(mod) is not None, "", "pip install -r requirements.txt")
    check("modèle visages (YuNet)", (ROOT / "models" / "face_detection_yunet_2023mar.onnx").exists(), "", "fichier models/face_detection_yunet_2023mar.onnx manquant (voir README)")
    # Pexels ne sert qu'au B-roll : facultatif (AI Corner n'en utilise pas) — signalé sans bloquer
    if env("PEXELS_API_KEY"):
        check("PEXELS_API_KEY", True, "")
    else:
        console.print("  [yellow]--[/yellow]  PEXELS_API_KEY               facultatif : absent -> pas de B-roll "
                      "(clé gratuite sur pexels.com/api, à mettre dans .env)")
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


def cmd_thumbnail(a: argparse.Namespace) -> None:
    """Miniatures YouTube : photos choisies dans les gros plans, détourées, logos 3D, titre proposé par le LLM."""
    from .multicam import load_spec
    from .thumbnail import make
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    spec = load_spec(video)
    if not spec:
        sys.exit(f"Pas de {video.with_suffix('.multicam.json').name} : les miniatures partent des gros plans des rushs")
    cj = ep / "clips.json"
    meta = json.loads(cj.read_text(encoding="utf-8")) if cj.exists() else {}
    guest, company = a.guest or meta.get("guest", ""), a.company or meta.get("company", "")
    transcript = transcribe(spec.get("audio") or video, ep / "transcript.json", brand.cfg, ep / "work",
                            names=[guest, company])
    outs = make(brand, spec, transcript, ep, guest, company, n=a.n, title=a.title or None,
                host_t=a.host_frame, guest_t=a.guest_frame, force=a.force)
    console.print(f"[green]{len(outs)} miniatures[/green] -> {ep / 'miniatures'} (planche.jpg, titres.md)")


def cmd_thumbnail_template(a: argparse.Namespace) -> None:
    """Design Canva -> brands/<m>/assets/thumbnail/ (calques + template.json) : `thumbnail` le reproduit ensuite."""
    from .canva_template import import_pptx
    brand = Brand(a.brand)
    out = brand.assets_dir / "thumbnail"
    tpl = import_pptx(Path(a.pptx), out, Path(a.png) if a.png else None, brand.assets_dir)
    console.print(f"[green]Gabarit importé[/green] -> {out} ({len(tpl['layers'])} calques, titre "
                  f"{tpl['title']['pill_style']['family']} {tpl['title']['pill_style']['weight']})")


def cmd_fetch(a: argparse.Namespace) -> None:
    from .fetch import fetch
    dest = Path(a.dest) if a.dest else ROOT_DIR / "depot"
    done = fetch(a.url, dest, skip=[x for x in a.skip.split(",") if x], jobs=a.jobs)
    if not done:
        sys.exit(1)


def _episode_inputs(a: argparse.Namespace):
    from .multicam import load_spec
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    spec = load_spec(video)
    if not spec:
        sys.exit(f"Pas de {video.with_suffix('.multicam.json').name} : le montage complet part des rushs multicam")
    transcript = transcribe(spec.get("audio") or video, ep / "transcript.json", brand.cfg, ep / "work",
                            names=[a.guest, a.company])
    return brand, video, ep, spec, transcript


def _episode_logo(brand: Brand) -> dict | None:
    lg = brand.cfg.get("episode_logo") or {}
    f = brand.asset(lg.get("file")) if lg else None
    return {"file": f, "width": lg.get("width", 0.12), "margin": lg.get("margin", 0.03)} if f else None


def cmd_episode_plan(a: argparse.Namespace) -> None:
    """Montage complet, étape 1 : dérushage (LLM), qui parle (voix), liste de plans, teaser, chapitres -> à relire."""
    import numpy as np

    from .diarize import diarize
    from .episode import _refine_boundaries, _ts, build_edl, keep_ranges, make_plan, make_teaser, quantize, teaser_clip
    from .transcribe import all_words
    brand, video, ep, spec, transcript = _episode_inputs(a)
    plan = make_plan(transcript, brand, ep / "episode_plan.json", a.guest, a.company, a.host, force=a.force)
    wf = ep / "work" / "diarized_words.json"
    if wf.exists() and not a.force:
        words = json.loads(wf.read_text(encoding="utf-8"))
    else:
        console.print("Qui parle (empreintes vocales)…")
        t0 = float(plan["start"]["t"])
        words = diarize(all_words(transcript), spec["audio"], host_ref=(t0, t0 + 15))
        wf.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    _refine_boundaries(words)
    ranges, notes = keep_ranges(words, plan)
    # reprises après coupe : ni « euh », ni mot répété, ni coupe dans un son, ni long blanc (écoute + énergie du micro)
    from .episode import clean_join_starts
    ranges = clean_join_starts(ranges, Path(spec["audio"]), brand.cfg, words=words, log=lambda m: (console.print(m), notes.append(m.strip())))
    shots = build_edl(words, ranges)
    (ep / "work" / "edl.json").write_text(json.dumps({"ranges": ranges, "shots": shots}, indent=0), encoding="utf-8")
    teaser_plan = make_teaser(transcript, words, ranges, brand, ep / "teaser_plan.json", a.guest, a.company, a.host,
                              force=a.force or a.new_teaser)
    from .episode import editorial_order
    teaser = teaser_clip(words, teaser_plan, a.guest, a.company, wav=spec["audio"], transcript=transcript,
                         editor=lambda ext: editorial_order(ext, brand))
    # blanc trop long en fin d'extrait (> 0,5 s) ramené à 0,35 s après la voix (Arthur, 09/10/2026)
    from .verify import trim_tail
    segs_t = teaser["segments"]
    for k, sg in enumerate(segs_t):
        if k + 1 == len(segs_t) or segs_t[k + 1].get("orig") != sg.get("orig"):
            e = trim_tail(Path(spec["audio"]), float(sg["start"]), float(sg["end"]))
            if e < float(sg["end"]):
                sg["end"], sg["duration"] = e, round(e - float(sg["start"]), 3)
    # réactions vides (« Super intéressant »…) : jamais dans le teaser (Arthur, 09/10/2026 : « ça n'apporte rien »)
    from .fillers import drop_reactions
    segs = []
    for sg in teaser["segments"]:
        segs += drop_reactions(sg, words, Path(spec["audio"]), log=console.print)
    teaser["segments"] = segs
    # écoute finale : ce que le spectateur entendra, mot pour mot (‖ = raccord)
    from .verify import audit, audit_flags, audit_joins
    heard = audit(teaser["segments"], spec["audio"], ep / "work" / "teaser_audit.wav")
    # raccord signalé au milieu d'un mot : on annule ce retrait (on garde l'hésitation) et on réécoute
    for _ in range(3):
        segs = teaser["segments"]
        fix = [k for k in audit_joins(heard) if k + 1 < len(segs) and segs[k].get("orig") == segs[k + 1].get("orig")]
        if not fix:
            break
        for k in sorted(fix, reverse=True):
            segs[k]["end"] = segs[k + 1]["end"]
            segs[k]["duration"] = round(segs[k]["end"] - segs[k]["start"], 3)
            del segs[k + 1]
        console.print(f"  teaser : {len(fix)} raccord(s) dans un mot annulé(s), réécoute…")
        heard = audit(segs, spec["audio"], ep / "work" / "teaser_audit.wav")
    teaser["duration"] = round(sum(s["duration"] for s in teaser["segments"]), 2)
    (ep / "teaser_audit.txt").write_text(heard + "\n", encoding="utf-8")
    teaser["audit_flags"] = audit_flags(heard)
    (ep / "teaser_clip.json").write_text(json.dumps(teaser, ensure_ascii=False, indent=1), encoding="utf-8")
    q = quantize(shots, 24)
    L = np.array([s["frames"] / 24 for s in q])
    tot = max(1, sum(s["frames"] for s in q))
    lines = [f"# Montage complet — {plan.get('youtube_title', '')}", "",
             f"Épisode : {_ts(ranges[0][0])} → {_ts(ranges[-1][1])} des rushs · durée montée {_ts(L.sum())} "
             f"(+ teaser {teaser['duration']:.0f} s) · {len(q)} plans (médiane {np.median(L):.1f} s)", "",
             "## Dérushage", ""]
    lines += [f"- {n}" for n in notes] or ["- aucune coupe"]
    lines += ["", "## Teaser (ce qu'on entend, ‖ = raccord)", "", heard, ""]
    lines += [f"- ⚠ {f}" for f in teaser.get("audit_flags", [])]
    lines += [f"- {_ts(sg['start'])} « {sg['start_text']} … {sg['end_text']} »" for sg in teaser["segments"]]
    lines += ["", "## Plans", ""]
    lines += [f"- {c} : {100 * sum(s['frames'] for s in q if s['cam'] == c) / tot:.0f} %" for c in ("host", "guest", "wide", "split")]
    (ep / "episode_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print("\n".join(lines))


def cmd_episode_render(a: argparse.Namespace) -> None:
    """Montage complet, étape 2 : teaser (HyperFrames) + corps (plans) + fin -> un MP4. --proxy = aperçu 540p rapide."""
    from .episode import _normalize, assemble, description, end_card, end_duration, render_body, teaser_brand
    from .media import FFMPEG, probe
    from .media import run as _run
    from .render import render as hf_render
    brand, video, ep, spec, transcript = _episode_inputs(a)
    edl = json.loads((ep / "work" / "edl.json").read_text(encoding="utf-8"))
    plan = json.loads((ep / "episode_plan.json").read_text(encoding="utf-8"))
    words = json.loads((ep / "work" / "diarized_words.json").read_text(encoding="utf-8"))
    teaser = json.loads((ep / "teaser_clip.json").read_text(encoding="utf-8"))
    proxy, fps = a.proxy, 24
    size = (960, 540) if proxy else (1920, 1080)
    work = (ep / "work" / "episode").resolve()
    work.mkdir(parents=True, exist_ok=True)
    tag = "_apercu" if proxy else ""
    shots = edl["shots"]
    if a.minutes:
        shots = [s for s in shots if s["start"] < edl["ranges"][0][0] + 60 * a.minutes]
    # teaser : HyperFrames (sous-titres de la charte), puis aux réglages des plans du corps
    tb = teaser_brand(brand)
    proj = build_clip(tb, video, transcript, teaser, ep, formats=["16x9"], force=a.force)
    t_mp4 = ep / "renders" / f"teaser{tag}.mp4"
    if a.force or not t_mp4.exists():
        console.print("Rendu du teaser (HyperFrames)…")
        hf_render(proj / "16x9", t_mp4, quality="draft" if proxy else tb.cfg.render.quality, fps=fps,
                  crf=None if proxy else (int(tb.cfg.render.get("crf") or 0) or None),
                  frame_format="" if proxy else str(tb.cfg.render.get("video_frame_format") or ""))
    t_v = _normalize(t_mp4, work / f"teaser_v{tag}.mp4", size, fps, proxy)
    t_a = work / f"teaser_a{tag}.wav"
    _run([FFMPEG, "-y", "-v", "error", "-i", str(t_mp4), "-vn", "-ar", "48000", "-ac", "2", str(t_a)])
    console.print("Corps de l'épisode…")
    body_v = render_body(spec, shots, edl["ranges"], work, None, fps=fps, proxy=proxy, jobs=int(a.jobs or 3),
                         split_order=("host", "guest") if a.host_side != "right" else ("guest", "host"),
                         audio_delay=float(spec.get("audio_delay", 0)), logo=_episode_logo(brand))
    end = end_card(brand, work / f"end{tag}.mp4", size, fps, proxy)
    out_dir = ep / "episode"
    out_dir.mkdir(exist_ok=True)
    suffix = f"_{a.minutes}min" if a.minutes else ""
    out = out_dir / f"{video.stem}_episode{tag}{suffix}.mp4"
    assemble([t_v, body_v, end], [t_a, work / "body_audio.wav", end_duration(brand)], out, work)
    t_len = float(probe(t_v)["duration"])
    (out_dir / "description_youtube.md").write_text(description(plan, edl["shots"], words, t_len, a.guest, a.company),
                                                   encoding="utf-8")
    console.print(f"[green]✓ {out}[/green]  ({probe(out)['duration'] / 60:.1f} min)")


def cmd_verify(a: argparse.Namespace) -> None:
    """Contrôle verbatim des coupes (« euh », bégaiements, mots coupés) des shorts de clips.json ; --fix corrige."""
    from .verify import verify_clip
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    spec = video.with_suffix(".multicam.json")
    wav = (video.parent / json.loads(spec.read_text(encoding="utf-8"))["audio"]) if spec.exists() else video
    data = json.loads((ep / "clips.json").read_text(encoding="utf-8"))
    from .transcribe import all_words
    transcript = json.loads((ep / "transcript.json").read_text(encoding="utf-8"))
    dw = ep / "work" / "diarized_words.json"   # qui parle (si déjà calculé) : un changement d'orateur est une fin naturelle
    words = json.loads(dw.read_text(encoding="utf-8")) if dw.exists() else all_words(transcript)
    only = _parse_only(a.only)
    if a.fix and not (ep / "clips_before_verify.json").exists():
        shutil.copy2(ep / "clips.json", ep / "clips_before_verify.json")
    for i, c in enumerate(data["clips"]):
        if only and c["index"] not in only:
            continue
        c2, notes = verify_clip(c, wav, brand.cfg, fix=a.fix, words=words)
        if a.fix:
            c2 = _mark(c2, "verify")
        data["clips"][i] = c2
        console.rule(f"#{c['index']} {c.get('hook_title') or c['title']}")
        console.print("\n".join(notes) if notes else "RAS")
        # écoute finale : ce que le spectateur entendra, mot pour mot (‖ = raccord)
        from .verify import audit, audit_flags
        heard = audit(c2["segments"], wav, ep / "work" / f"audit_{c['index']:02d}.wav", brand.cfg)
        console.print(f"[bold]On entend[/bold] : {heard}")
        for f in audit_flags(heard):
            console.print(f"[yellow]⚠ {f}[/yellow]")
    if a.fix:
        (ep / "clips.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_fillers(a: argparse.Namespace) -> None:
    """Retire les « euh » collés aux mots (voyelles tenues, détectées dans le son ; chaque coupe validée par la
    transcription et l'écoute) -> clips.json. À lancer après verify, avant build."""
    from .fillers import clean_clip
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    spec = video.with_suffix(".multicam.json")
    wav = (video.parent / json.loads(spec.read_text(encoding="utf-8"))["audio"]) if spec.exists() else video
    data = json.loads((ep / "clips.json").read_text(encoding="utf-8"))
    if not (ep / "clips_before_fillers.json").exists():
        shutil.copy2(ep / "clips.json", ep / "clips_before_fillers.json")
    only = _parse_only(a.only)
    for i, c in enumerate(data["clips"]):
        if only and c["index"] not in only:
            continue
        console.rule(f"#{c['index']} {c.get('hook_title') or c['title']}")
        c2, heard = clean_clip(c, wav, brand.cfg, log=console.print, work=ep / "work")
        c2 = _mark(c2, "fillers")
        console.print(f"{c['duration']:.1f} s -> {c2['duration']:.1f} s")
        console.print(f"[bold]On entend[/bold] : {heard}")
        data["clips"][i] = c2
    (ep / "clips.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_tighten(a: argparse.Namespace) -> None:
    """Retire les « euh » (cachés dans les trous entre mots) sans rendre le clip saccadé -> clips.json."""
    from .tighten import tighten_clip
    from .transcribe import all_words
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    spec = video.with_suffix(".multicam.json")
    wav = (video.parent / json.loads(spec.read_text(encoding="utf-8"))["audio"]) if spec.exists() else video
    transcript = json.loads((ep / "transcript.json").read_text(encoding="utf-8"))
    W = all_words(transcript)
    data = json.loads((ep / "clips.json").read_text(encoding="utf-8"))
    only = _parse_only(a.only)
    if not (ep / "clips_before_tighten.json").exists():
        shutil.copy2(ep / "clips.json", ep / "clips_before_tighten.json")
    for i, c in enumerate(data["clips"]):
        if only and c["index"] not in only:
            continue
        old = c["duration"]
        c2, removed = tighten_clip(c, transcript, wav, min_gap=a.min_gap)
        c2 = _mark(c2, "tighten")
        data["clips"][i] = c2
        console.print(f"#{c['index']} {c.get('hook_title') or c['title']} : {len(removed)} retrait(s), {old:.1f}s -> {c2['duration']:.1f}s")
        for t0, t1, _ in removed:
            before = " ".join(w["w"] for w in W if w["e"] <= t0 + 0.1)[-40:]
            after = " ".join(w["w"] for w in W if w["s"] >= t1 - 0.1)[:40]
            console.print(f"    {t0:.2f}→{t1:.2f} ({t1 - t0:.2f}s) …{before} | {after}…")
    (ep / "clips.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _wav_for(video: Path) -> Path:
    """Audio de référence : le WAV du micro (rushs multicam) ou la vidéo elle-même."""
    spec = video.with_suffix(".multicam.json")
    return (video.parent / json.loads(spec.read_text(encoding="utf-8"))["audio"]) if spec.exists() else video


def _mark(clip: dict, step: str) -> dict:
    """Étape de nettoyage déjà faite sur ce clip : `polish` ne la refait pas (ni perte de temps, ni double coupe)."""
    import datetime
    return dict(clip, checks=dict(clip.get("checks") or {}, **{step: datetime.datetime.now().isoformat(timespec="seconds")}))


def _project(ep: Path, clip: dict) -> Path:
    return ep / "clips" / f"clip_{clip['index']:02d}_{slugify(clip.get('title', ''))}"


def cmd_qa(a: argparse.Namespace) -> str:
    """Contrôle qualité : rapport par short (`output/…/qa/`), --render = aussi le MP4 (final ou aperçu), --episode =
    l'épisode complet. À lancer avant de montrer quoi que ce soit à Arthur ; un ÉCHEC se corrige d'abord."""
    from .qa import FAIL, qa_episode, qa_short
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    out_dir = ep / "qa"
    out_dir.mkdir(exist_ok=True)
    worst = "OK"
    if a.episode:
        mp4 = ep / "episode" / f"{video.stem}_episode{'_apercu' if a.proxy else ''}.mp4"
        from .episode import end_duration
        status, lines = qa_episode(ep, mp4, brand.cfg, end_len=end_duration(brand))
        (out_dir / "episode.md").write_text(f"# QA épisode complet — {status}\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
        console.rule(f"Épisode complet : {status}")
        console.print("\n".join(lines))
        return status
    wav = _wav_for(video)
    data = json.loads((ep / "clips.json").read_text(encoding="utf-8"))
    only = _parse_only(a.only)
    fmt = a.formats or brand.cfg.formats[0]
    for c in data["clips"]:
        if only and c["index"] not in only:
            continue
        proj = _project(ep, c)
        if not (proj / "clip.json").exists():
            console.print(f"[yellow]#{c['index']} pas encore monté (build)[/yellow]")
            continue
        render_f = None
        if a.render:
            final = ep / "renders" / f"{proj.name}_{fmt}.mp4"
            apercu = ep / "apercus" / f"{proj.name}_{fmt}_apercu.mp4"
            # après un aperçu (polish) on contrôle l'APERÇU ; sinon le MP4 final s'il existe
            render_f = apercu if getattr(a, "preview_check", False) or not final.exists() else final
        status, lines = qa_short(c, proj, wav, brand.cfg, fmt=fmt, render=render_f, work=out_dir)
        (out_dir / f"clip_{c['index']:02d}.md").write_text(
            f"# QA short {c['index']} — {c.get('hook_title') or c.get('title')} — {status}\n\n" + "\n".join(lines) + "\n",
            encoding="utf-8")
        console.rule(f"#{c['index']} {c.get('hook_title') or c.get('title')} : {status}")
        console.print("\n".join(lines))
        worst = FAIL if FAIL in (worst, status) else ("ATTENTION" if "ATTENTION" in (worst, status) else worst)
    return worst


def cmd_polish(a: argparse.Namespace) -> None:
    """Chaîne complète d'un short après le choix des passages (`pick`), avec les contrôles à chaque étape :
    tighten -> verify --fix -> fillers -> build (sous-titres doublement contrôlés) -> qa -> aperçu MP4 -> qa de
    l'aperçu. Tout dans UN processus (Whisper chargé une seule fois) ; étapes déjà faites sautées (`checks` dans
    clips.json, `--redo` pour tout refaire depuis clips_before_tighten.json). Ne rend JAMAIS le MP4 final."""
    from .fillers import clean_clip
    from .tighten import tighten_clip
    from .transcribe import all_words
    from .verify import verify_clip
    brand = Brand(a.brand)
    video = resolve_input(brand, a.input)
    ep = episode_dir(brand, video)
    wav = _wav_for(video)
    only = _parse_only(a.only)
    if a.redo and (ep / "clips_before_tighten.json").exists():
        orig = {c["index"]: c for c in json.loads((ep / "clips_before_tighten.json").read_text(encoding="utf-8"))["clips"]}
    else:
        orig = {}
    data = json.loads((ep / "clips.json").read_text(encoding="utf-8"))
    transcript = json.loads((ep / "transcript.json").read_text(encoding="utf-8"))
    dw = ep / "work" / "diarized_words.json"
    words = json.loads(dw.read_text(encoding="utf-8")) if dw.exists() else all_words(transcript)
    for name in ("clips_before_tighten.json", "clips_before_verify.json", "clips_before_fillers.json"):
        if not (ep / name).exists():
            shutil.copy2(ep / "clips.json", ep / name)
    idx = []
    for i, c in enumerate(data["clips"]):
        if only and c["index"] not in only:
            continue
        idx.append(c["index"])
        if a.redo and c["index"] in orig:
            c = dict(orig[c["index"]], checks={})
        done = c.get("checks") or {}
        console.rule(f"#{c['index']} {c.get('hook_title') or c['title']}")
        if "tighten" not in done:
            c2, removed = tighten_clip(c, transcript, wav)
            console.print(f"  1/3 blancs + « euh » entre les mots : {len(removed)} retrait(s), {c['duration']:.1f} -> {c2['duration']:.1f} s")
            c = _mark(c2, "tighten")
        if "verify" not in done:
            c2, notes = verify_clip(c, wav, brand.cfg, fix=True, words=words)
            console.print("  2/3 contrôle à l'oreille (fins de phrase, bords, « euh » isolés) : " + ("; ".join(notes) or "RAS"))
            c = _mark(c2, "verify")
        if "fillers" not in done:
            c2, _heard = clean_clip(c, wav, brand.cfg, log=lambda m: console.print(m), work=ep / "work")
            console.print(f"  3/3 « euh » collés aux mots : {c['duration']:.1f} -> {c2['duration']:.1f} s")
            c = _mark(c2, "fillers")
        if "reactions" not in done:
            from .fillers import drop_reactions
            segs = []
            for sg in c["segments"]:
                segs += drop_reactions(sg, words, wav, log=lambda m: console.print(m))
            c = _mark(dict(c, segments=segs, duration=round(sum(float(x["duration"]) for x in segs), 2)), "reactions")
            console.print("  réactions vides (« super intéressant »…) : contrôlées")
        cap = brand.cfg.captions
        if cap.get("highlight_keywords") and cap.get("auto_keywords") and not c.get("keywords"):
            from .caption_check import pick_keywords
            text = " ".join(w["w"] for sg in c["segments"] for w in words
                            if float(sg["start"]) - 0.05 <= float(w["s"]) <= float(sg["end"]))
            c = dict(c, keywords=pick_keywords(text, brand.cfg))
            console.print(f"  mots-clés des sous-titres : {', '.join(c['keywords']) or 'aucun'}")
        data["clips"][i] = c
        (ep / "clips.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if not idx:
        sys.exit("Aucun clip sélectionné")
    sel = ",".join(str(k) for k in idx)
    console.rule("Montage (sous-titres contrôlés à l'écoute)")
    cmd_build(argparse.Namespace(**{**vars(a), "only": sel, "formats": a.formats, "force": False, "no_broll": False,
                                    "jobs": a.jobs}))
    console.rule("Contrôle qualité du montage")
    status = cmd_qa(argparse.Namespace(**{**vars(a), "only": sel, "render": False, "episode": False}))
    if status == "ÉCHEC" and not a.anyway:
        sys.exit("Contrôle qualité en ÉCHEC : corriger avant l'aperçu (voir output/…/qa/). --anyway pour passer outre.")
    console.rule("Aperçus MP4")
    cmd_preview(argparse.Namespace(brand=a.brand, input=a.input, clip=sel, formats=a.formats, port="3002",
                                   no_open=True, stop=False, mp4=True, fps=a.fps))
    console.rule("Contrôle qualité des aperçus")
    cmd_qa(argparse.Namespace(**{**vars(a), "only": sel, "render": True, "episode": False, "preview_check": True}))
    console.print("[bold]Prochaine étape[/bold] : regarder les planches d'images (output/…/qa/), envoyer les liens des aperçus "
                  "à Arthur ; après sa validation seulement : render --only N, puis qa --render --only N.")


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
        sp.add_argument("--jobs", default="", help="tâches en parallèle (build : clips, render : rendus) ; auto = cœurs/4 ; défaut : brand.yaml")

    def render_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--quality", default="", help="draft | looks | delivery")

    def posts_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--episode-url", default="", help="URL de l'episode complet (mise en clair dans le CTA)")
        sp.add_argument("--guest-role", default="", help="role de l'invite, ex: 'CDO at MAIF'")

    sp = sub.add_parser("run", help="pipeline complet"); common(sp); selection_args(sp); build_args(sp); render_args(sp); posts_args(sp)
    sp.add_argument("--no-render", action="store_true"); sp.set_defaults(fn=cmd_run)
    sp = sub.add_parser("transcribe"); common(sp)
    sp.add_argument("--guest", default="", help="nom de l'invité (aide Whisper à l'écrire correctement)")
    sp.add_argument("--company", default="", help="entreprise de l'invité")
    sp.set_defaults(fn=cmd_transcribe)
    sp = sub.add_parser("select"); common(sp); selection_args(sp); sp.set_defaults(fn=cmd_select)
    sp = sub.add_parser("build"); common(sp); build_args(sp); sp.set_defaults(fn=cmd_build)
    sp = sub.add_parser("render"); common(sp); build_args(sp); render_args(sp); sp.set_defaults(fn=cmd_render)
    sp = sub.add_parser("propose", help="~10 passages candidats à soumettre (candidates.md)"); common(sp); selection_args(sp)
    sp.set_defaults(fn=cmd_propose)
    sp = sub.add_parser("pick", help="garde les candidats choisis -> clips.json"); common(sp)
    sp.add_argument("--ids", required=True, help="numéros des candidats, ex: 1,3,4,7,9"); sp.set_defaults(fn=cmd_pick)
    sp = sub.add_parser("passages", help="passages déjà choisis (fichier YAML) -> clips.json"); common(sp); selection_args(sp)
    sp.add_argument("--file", required=True, help="YAML : start, end, start_text, end_text, hook_title, turns")
    sp.add_argument("--guest-role", default="", help="rôle de l'invité, ex: 'CEO of Mendo'"); sp.set_defaults(fn=cmd_passages)
    sp = sub.add_parser("find", help="retrouve une phrase dans la transcription"); common(sp)
    sp.add_argument("text", help="mots de la phrase cherchée"); sp.add_argument("--n", type=int, default=5)
    sp.set_defaults(fn=cmd_find)
    sp = sub.add_parser("check", help="mots autour de chaque coupe (jamais couper une pensée)"); common(sp)
    sp.add_argument("--only", default=""); sp.set_defaults(fn=cmd_check)
    sp = sub.add_parser("posts", help="post LinkedIn + description courte pour chaque clip"); common(sp); posts_args(sp)
    sp.add_argument("--only", default="", help="index de clips, ex: 1,3"); sp.set_defaults(fn=cmd_posts)
    sp = sub.add_parser("preview", help="aperçu instantané des shorts dans le navigateur (sans rendu)"); common(sp)
    sp.add_argument("--clip", default="all", help="all (défaut) ou index, ex: 1,3")
    sp.add_argument("--formats", default="", help="format à ouvrir (ex: 9x16)")
    sp.add_argument("--port", default="3002", help="port du short n°1 (les suivants : +1, +2…)")
    sp.add_argument("--no-open", action="store_true", help="(par défaut désormais : aucun navigateur ouvert)")
    sp.add_argument("--open", action="store_true", help="ouvrir le Studio dans le navigateur (sinon : rien ne s'ouvre)")
    sp.add_argument("--stop", action="store_true", help="arrête les aperçus")
    sp.add_argument("--mp4", action="store_true", help="aperçu en MP4 brouillon (dossier apercus/) au lieu du Studio")
    sp.add_argument("--fps", default="12", help="images/s de l'aperçu MP4 (12 = rapide pour itérer ; 24/30 = fluide)")
    sp.set_defaults(fn=cmd_preview)
    sp = sub.add_parser("new-brand"); sp.add_argument("slug"); sp.set_defaults(fn=cmd_new_brand)
    sp = sub.add_parser("brands"); sp.set_defaults(fn=cmd_brands)
    for name, fn, hlp in (("episode-plan", cmd_episode_plan, "montage complet depuis les rushs : dérushage, voix, plans, teaser"),
                          ("episode-render", cmd_episode_render, "montage complet : rendu (--proxy = aperçu 540p)")):
        sp = sub.add_parser(name, help=hlp); common(sp)
        sp.add_argument("--guest", default=""); sp.add_argument("--company", default="")
        sp.add_argument("--host", default="", help="animateur, ex: 'Thomas Spitz (CEO AI Partners)'")
        sp.add_argument("--host-side", default="left", choices=["left", "right"], help="côté de l'animateur dans le plan large")
        sp.add_argument("--proxy", action="store_true"); sp.add_argument("--minutes", type=int, default=0)
        sp.add_argument("--jobs", default="3"); sp.add_argument("--new-teaser", action="store_true")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("verify", help="contrôle verbatim des coupes (euh, bégaiements, mots coupés) ; --fix corrige"); common(sp)
    sp.add_argument("--only", default=""); sp.add_argument("--fix", action="store_true"); sp.set_defaults(fn=cmd_verify)
    sp = sub.add_parser("polish", help="short après pick : tighten -> verify -> fillers -> build -> qa -> aperçu MP4 -> qa")
    common(sp); sp.add_argument("--only", default=""); sp.add_argument("--formats", default="")
    sp.add_argument("--jobs", default=""); sp.add_argument("--fps", default="12")
    sp.add_argument("--redo", action="store_true", help="tout refaire depuis clips_before_tighten.json")
    sp.add_argument("--anyway", action="store_true", help="faire l'aperçu même si le contrôle qualité échoue")
    sp.set_defaults(fn=cmd_polish, render=False, episode=False, proxy=False)
    sp = sub.add_parser("qa", help="contrôle qualité : rapport par short (output/…/qa/) ; --render ; --episode")
    common(sp); sp.add_argument("--only", default=""); sp.add_argument("--formats", default="")
    sp.add_argument("--render", action="store_true", help="contrôle aussi le MP4 rendu (ou l'aperçu)")
    sp.add_argument("--episode", action="store_true", help="contrôle l'épisode complet (MP4 final)")
    sp.add_argument("--proxy", action="store_true", help="avec --episode : l'aperçu 540p")
    sp.set_defaults(fn=cmd_qa)
    sp = sub.add_parser("fillers", help="retire les « euh » collés aux mots (voyelles tenues) -> clips.json")
    common(sp); sp.add_argument("--only", default=""); sp.set_defaults(fn=cmd_fillers)
    sp = sub.add_parser("tighten", help="retire les « euh » et longs blancs (sans saccades) dans clips.json"); common(sp)
    sp.add_argument("--only", default=""); sp.add_argument("--min-gap", type=float, default=0.5)
    sp.set_defaults(fn=cmd_tighten)
    sp = sub.add_parser("fetch", help="télécharge les rushs d'un lien Dropbox (reprise automatique, tailles vérifiées)")
    sp.add_argument("url", help="lien Dropbox partagé (dossier ou fichier)")
    sp.add_argument("--dest", default="", help="dossier de destination (défaut : depot/)")
    sp.add_argument("--skip", default="", help="fichiers à ignorer (morceaux de nom, ex: MIC,.wav)")
    sp.add_argument("--jobs", type=int, default=3, help="téléchargements en parallèle")
    sp.set_defaults(fn=cmd_fetch)
    sp = sub.add_parser("thumbnail", help="miniatures YouTube de l'épisode (rushs multicam) -> output/…/miniatures/")
    common(sp)
    sp.add_argument("--guest", default="", help="nom de l'invité (défaut : clips.json)")
    sp.add_argument("--company", default="", help="entreprise invitée -> logo brands/<m>/assets/guests/<entreprise>.svg|png")
    sp.add_argument("--n", type=int, default=4, help="nombre de variantes")
    sp.add_argument("--title", default="", help="titre imposé « ligne 1 | ligne 2 » ; * devant la ligne à mettre sur le bandeau")
    sp.add_argument("--host-frame", type=float, default=None, help="instant (s) de la photo de l'animateur")
    sp.add_argument("--guest-frame", type=float, default=None, help="instant (s) de la photo de l'invité")
    sp.set_defaults(fn=cmd_thumbnail)
    sp = sub.add_parser("thumbnail-template", help="importe un design Canva (export PPTX + PNG) comme gabarit des miniatures")
    sp.add_argument("--brand", required=True)
    sp.add_argument("--pptx", required=True, help="export PowerPoint du design Canva")
    sp.add_argument("--png", default="", help="export PNG du même design (mesure visages et titre)")
    sp.set_defaults(fn=cmd_thumbnail_template)
    sp = sub.add_parser("doctor", help="vérifie l'installation (FFmpeg, Node, HyperFrames, Python, clés)"); sp.set_defaults(fn=cmd_doctor)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
