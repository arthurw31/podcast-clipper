"""Contrôle qualité (« double / triple check ») — rapport lisible avant de montrer une vidéo à Arthur.

Demande d'Arthur (08/10/2026) : « intègre vraiment le workflow en mode double triple check ». Chaque retour des
derniers jours est devenu un contrôle automatique, relancé à chaque étape :
- 1er contrôle (montage, `qa`) : ce qu'on entend à chaque raccord (aucun mot coupé), débuts et fins de passage (jamais
  pendant que la voix continue, une respiration à la fin), sous-titres (aucun mot entendu oublié), composition
  HyperFrames valide, durée, transitions ;
- 2e contrôle (aperçu MP4 / rendu, `qa --render`) : format, cadence, durée = composition, volume -16 LUFS, rendu à
  jour ; planche d'images (accroche, milieu, carte de fin) à REGARDER avant d'envoyer le lien ;
- 3e contrôle : Arthur sur l'aperçu MP4 (rien de final sans sa validation).
Épisode complet (`qa --episode`) : réécoute de chaque raccord DANS le MP4 final, volumes teaser / corps, images.

Statuts : OK / ATTENTION (à signaler à Arthur) / ÉCHEC (à corriger avant de montrer la vidéo).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from .media import FFMPEG, probe

OK, WARN, FAIL = "OK", "ATTENTION", "ÉCHEC"


def loudness(media: Path, ss: float = 0.0, t: float | None = None) -> float | None:
    args = [FFMPEG, "-nostats", "-ss", f"{ss:.3f}"] + (["-t", f"{t:.3f}"] if t else []) + \
        ["-i", str(media), "-vn", "-af", "ebur128", "-f", "null", "-"]
    err = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="ignore").stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    return float(m[-1]) if m else None


def _voice_at(wav: Path, t: float) -> bool:
    """La voix est-elle en cours à l'instant t (énergie > 20 % du niveau de parole de la seconde et demie avant) ?"""
    from .tighten import _rms
    r = _rms(wav, max(0.0, t - 1.5), t + 0.04)
    if len(r) < 10:
        return False
    lvl = float(np.percentile(r, 90))
    return lvl > 0 and float(r[-3:].mean()) > 0.2 * lvl


def _tail_silence(wav: Path, t: float) -> float:
    """Durée de silence juste avant t (fin de voix -> coupe)."""
    from .tighten import _rms
    r = _rms(wav, max(0.0, t - 1.5), t)
    if len(r) < 10:
        return 0.0
    lvl = float(np.percentile(r, 90))
    k = len(r)
    while k > 0 and r[k - 1] < 0.2 * lvl:
        k -= 1
    return (len(r) - k) * 0.02


def _new_sound_before_cut(wav: Path, t: float, look: float = 0.35) -> bool:
    """Un NOUVEAU son démarre juste avant la coupe, après un vrai silence (≥ 0,08 s) : le début de la phrase suivante
    est entendu en fin d'extrait (short 1 E22 : « l'entreprise. ‖ É… »)."""
    from .tighten import _rms
    r = _rms(wav, max(0.0, t - 1.5), t)
    if len(r) < 20:
        return False
    lvl = float(np.percentile(r, 90))
    q = r < 0.2 * lvl
    tail = q[-int(look / 0.02):]
    # dans la fenêtre de fin : silence ≥ 4 trames puis son (pas encore de silence final)
    for k in range(len(tail) - 1):
        if tail[:k + 1][-4:].all() and len(tail[:k + 1]) >= 4 and (~tail[k + 1:]).sum() >= 2:
            return True
    return False


def contact_sheet(media: Path, times: list[float], out: Path, width: int = 270) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(times):
        f = out.with_name(f"{out.stem}_{i}.png")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(media), "-frames:v", "1",
                        "-vf", f"scale={width}:-1", str(f)], check=False)
        if f.exists():
            frames.append(f)
    if frames:
        ins = sum((["-i", str(f)] for f in frames), [])
        subprocess.run([FFMPEG, "-v", "error", "-y", *ins, "-filter_complex", f"hstack={len(frames)}" if len(frames) > 1
                        else "null", str(out)], check=False)
        for f in frames:
            f.unlink(missing_ok=True)
    return out


def qa_short(clip: dict, proj: Path, wav: Path, cfg, fmt: str = "9x16", render: Path | None = None,
             work: Path | None = None) -> tuple[str, list[str]]:
    """Contrôle d'un short monté (et de son rendu / aperçu si `render`). Renvoie (statut global, lignes du rapport)."""
    from .config import FORMATS
    from .verify import audit, audit_flags, audit_joins
    rows: list[tuple[str, str]] = []
    segs = clip["segments"]
    work = work or proj
    # 1. ce qu'on entend, raccord par raccord
    # écoute mise en cache : le 2e contrôle (après l'aperçu) réutilise celle du 1er si les coupes n'ont pas changé
    key = json.dumps([[round(float(x["start"]), 3), round(float(x["end"]), 3)] for x in segs])
    cache = work / f"qa_audit_{clip['index']:02d}.json"
    c = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    if c.get("key") == key:
        heard = c["heard"]
    else:
        heard = audit(segs, wav, work / f"qa_audit_{clip['index']:02d}.wav", cfg)
        cache.write_text(json.dumps({"key": key, "heard": heard}, ensure_ascii=False), encoding="utf-8")
    bad = audit_joins(heard)
    rows.append((FAIL if bad else OK, f"raccords à l'écoute : {len(segs) - 1} raccord(s)"
                 + (f", {len(bad)} au milieu d'un mot" if bad else ", aucun mot coupé")))
    euh = [f for f in audit_flags(heard) if "entendu" in f]
    if euh:
        rows.append((WARN, f"hésitations encore entendues : {len(euh)} ({', '.join(euh[:4])})"))
    # 2. débuts et fins de passage (un « passage » = suite de segments enchaînés sans saut dans l'épisode)
    # fin de passage = le morceau qui porte la citation de fin (`end_text`) ; les morceaux internes (« euh » retirés)
    # ne sont pas des fins de phrase
    ends = [k for k, sg in enumerate(segs) if sg.get("end_text")] or [len(segs) - 1]
    if len(segs) - 1 not in ends:
        ends.append(len(segs) - 1)
    cut_in_voice = [f"{float(segs[k]['end']):.2f}" for k in ends if _voice_at(wav, float(segs[k]["end"]))]
    rows.append((FAIL if cut_in_voice else OK, "fins de passage : " + (f"coupe pendant la voix à {', '.join(cut_in_voice)}"
                                                                        if cut_in_voice else "jamais pendant la voix")))
    nxt = [f"{float(segs[k]['end']):.2f}" for k in ends if _new_sound_before_cut(wav, float(segs[k]["end"]))]
    rows.append((FAIL if nxt else OK, "début de la phrase suivante : " + (f"entendu en fin d'extrait à {', '.join(nxt)}"
                                                                          if nxt else "jamais entendu")))
    tight = [f"{float(segs[k]['end']):.2f}" for k in ends if _tail_silence(wav, float(segs[k]["end"])) < 0.1
             and not _voice_at(wav, float(segs[k]["end"]))]
    if tight:
        rows.append((WARN, f"fin serrée (< 0,1 s de respiration, la phrase suivante démarre tout de suite) à {', '.join(tight)}"))
    # 3. sous-titres
    cc = proj / "captions_check.txt"
    if cc.exists():
        lines = [x for x in cc.read_text(encoding="utf-8").splitlines() if x.strip()]
        left = [x for x in lines if x.startswith("RESTE")]
        rows.append((FAIL if left else OK, f"sous-titres : {len(lines) - len(left)} correction(s) d'après l'écoute"
                     + (f", {len(left)} mot(s) entendu(s) non sous-titré(s) : {'; '.join(x[8:] for x in left)}" if left
                        else ", aucun mot entendu oublié")))
    else:
        rows.append((WARN, "sous-titres : double contrôle pas encore fait (relancer build)"))
    # 4. composition
    meta = json.loads((proj / "clip.json").read_text(encoding="utf-8"))
    html = proj / fmt / "index.html"
    from .render import lint
    try:
        _ok, findings = lint(proj / fmt)
        n_err = sum(1 for f in findings if f.get("severity") == "error")
    except Exception as e:  # noqa: BLE001
        n_err = -1
        rows.append((WARN, f"lint impossible : {e}"))
    if n_err >= 0:
        rows.append((FAIL if n_err else OK, f"composition HyperFrames : {n_err} erreur(s)"))
    d = float(meta.get("D_speech", clip.get("duration", 0)))
    sel = cfg.get("selection") or {}
    lo, hi = float(sel.get("min_duration", 20)), float(sel.get("max_duration", 45)) + 12
    rows.append((OK if lo - 5 <= d <= hi else WARN, f"durée : {d:.1f} s de parole + {float(meta.get('D_total', d)) - d:.1f} s de fin"))
    n_leak = html.read_text(encoding="utf-8").count('id="leak-') if html.exists() else 0
    rows.append((OK, f"transitions lumineuses : {n_leak} (seulement aux changements de plan, sur {len(meta.get('junctions', []))} raccord(s))"))
    # 5. rendu / aperçu
    if render and render.exists():
        info = probe(render)
        W, H = FORMATS[fmt]
        stale = html.exists() and render.stat().st_mtime < html.stat().st_mtime
        rows.append((FAIL if stale else OK, f"rendu {render.name} : " + ("PLUS ANCIEN que la composition (à refaire)" if stale
                                                                         else "à jour")))
        good_size = (info["width"], info["height"]) == (W, H)
        rows.append((OK if good_size else FAIL, f"format : {info['width']}×{info['height']} (attendu {W}×{H}), {info['fps']:.0f} i/s"))
        dt = abs(float(info["duration"]) - float(meta.get("D_total", info["duration"])))
        rows.append((OK if dt <= 0.2 else WARN, f"durée du rendu : {info['duration']:.2f} s (composition {float(meta.get('D_total', 0)):.2f} s)"))
        lu = loudness(render, 0, d)
        if lu is not None:
            rows.append((OK if abs(lu + 16) <= 1.5 else WARN, f"volume : {lu:.1f} LUFS (cible -16)"))
        sheet = contact_sheet(render, [min(3.0, d / 4), d * 0.4, d * 0.75, d + 2.0],
                              work / f"qa_images_{clip['index']:02d}.png")
        rows.append((OK, f"planche d'images à regarder : {sheet}"))
    status = FAIL if any(s == FAIL for s, _ in rows) else WARN if any(s == WARN for s, _ in rows) else OK
    return status, [f"- [{s}] {t}" for s, t in rows] + ["", f"Ce qu'on entend (‖ = raccord) : {heard}"]


def qa_episode(ep: Path, mp4: Path, wav_cfg=None, end_len: float = 8.0) -> tuple[str, list[str]]:
    """Épisode complet : réécoute de chaque raccord dans le MP4 final, volumes teaser / corps, images."""
    from .episode import out_time
    from .verify import FILLERS, _norm, verbatim
    rows: list[tuple[str, str]] = []
    edl = json.loads((ep / "work" / "edl.json").read_text(encoding="utf-8"))
    teaser_v = ep / "work" / "episode" / ("teaser_v_apercu.mp4" if "apercu" in mp4.name else "teaser_v.mp4")
    T = float(probe(teaser_v)["duration"]) if teaser_v.exists() else 0.0
    total = float(probe(mp4)["duration"])
    wav = ep / "work" / "qa_episode.wav"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(mp4), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
    R = edl["ranges"]
    pts = [("teaser -> épisode", T)] if T else []
    for i in range(1, len(R)):
        t = out_time(edl["shots"], R[i][0] + 0.05)
        if t is not None:
            pts.append((f"raccord {i}", T + t - 0.05))
    pts.append(("fin de l'épisode", total - end_len))
    for name, t in pts:
        ws = verbatim(wav, max(0.0, t - 8), min(total, t + 6), wav_cfg)
        near = [w["w"] for w in ws if abs(float(w["s"]) - t) < 1.0 and _norm(w["w"]) in FILLERS]
        txt = " ".join(("‖ " if k and float(ws[k - 1]["s"]) < t <= float(w["s"]) else "") + w["w"] for k, w in enumerate(ws))
        rows.append((WARN if near else OK, f"{name} ({int(t // 60)}:{t % 60:04.1f}) : {'« euh » au raccord' if near else 'propre'}"
                     f"\n    on entend : {txt}"))
        if name == "fin de l'épisode" and _voice_at(wav, t):
            rows.append((FAIL, "fin de l'épisode : coupe pendant la voix"))
    if T:
        lt, lb = loudness(mp4, 0, T), loudness(mp4, T, min(600.0, total - T - end_len))
        if lt is not None and lb is not None:
            rows.append((OK if abs(lt - lb) <= 1.5 else WARN, f"volumes : teaser {lt:.1f} LUFS, épisode {lb:.1f} LUFS"))
    sheet = contact_sheet(mp4, [5, T + 2, T + 600, total - end_len - 3, total - 2], ep / "work" / "qa_episode_images.png")
    rows.append((OK, f"planche d'images à regarder (logo en haut à droite, carte de fin) : {sheet}"))
    status = FAIL if any(s == FAIL for s, _ in rows) else WARN if any(s == WARN for s, _ in rows) else OK
    return status, [f"- [{s}] {t}" for s, t in rows]
