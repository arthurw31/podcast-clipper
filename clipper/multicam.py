"""Montage multicam automatique : rushs (gros plans + plan large + micro) -> un « master » monté comme un épisode.

Cas E22 (06/10/2026) : l'équipe livre les rushs bruts au lieu de l'épisode monté — une caméra par personne en gros
plan, un plan large, un WAV mono (les deux voix mélangées). Les rushs sont déjà synchronisés (même durée, écart
caméra/micro < 1 image), donc pas de recalage.

Qui parle : le micro est mono, on regarde donc la bouche de chaque personne sur SON gros plan (net, plein cadre,
bien plus fiable que sur un plan large). Activité de bouche (YuNet + imagette normalisée, comme `analysis`)
mesurée mot par mot de la transcription : chaque mot est attribué à la personne dont la bouche bouge le plus,
lissé sur quelques mots, puis les tours trop courts (« oui », « exactement ») sont absorbés. Le master passe sur le
gros plan de la personne qui parle, coupe juste avant le premier mot du tour, avec des plans d'au moins `min_shot` s.
"""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from rich.console import Console

from .analysis import _detector, _mouth_patch
from .media import FFMPEG, probe, run

console = Console()


def mouth_series(video: Path, fps: float = 6.0, width: int = 640, start: float = 0.0, dur: float = 0.0) -> np.ndarray:
    """Activité de bouche du visage principal, une valeur par image échantillonnée (NaN si pas de visage)."""
    info = probe(video)
    h = int(round(info["height"] * width / info["width"] / 2)) * 2
    cmd = [FFMPEG, "-v", "error", *(["-ss", f"{start}", "-t", f"{dur}"] if dur else []), "-i", str(video), "-an", "-vf", f"fps={fps},scale={width}:{h}", "-f", "rawvideo",
           "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=width * h * 3 * 4)
    det = _detector(width, h)
    out, prev = [], None
    size = width * h * 3
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(h, width, 3)
        _, faces = det.detect(frame)
        patch = None
        if faces is not None and len(faces):
            f = max(faces, key=lambda f: f[2] * f[3])  # le plus grand visage = la personne filmée en gros plan
            patch = _mouth_patch(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), f[4:14].reshape(5, 2))
        out.append(float(np.abs(patch - prev).mean()) if patch is not None and prev is not None else np.nan)
        prev = patch
    proc.wait()
    return np.array(out, dtype=np.float32)


def _norm(a: np.ndarray) -> np.ndarray:
    """Centre-réduit de façon robuste (chaque caméra a son cadrage et sa netteté)."""
    med = np.nanmedian(a)
    mad = np.nanmedian(np.abs(a - med)) + 1e-6
    return (a - med) / mad


def speaker_turns(words: list[dict], act: dict[str, np.ndarray], fps: float, smooth_words: int = 7,
                  min_turn: float = 1.6) -> list[dict]:
    """Tours de parole [{start, end, speaker}] à partir des mots (transcript : clés s/e/w) et de l'activité de bouche de chaque personne."""
    names = list(act)
    norm = {k: _norm(v) for k, v in act.items()}
    score = []
    for w in words:
        i0, i1 = int(w["s"] * fps), max(int(w["s"] * fps) + 1, int(np.ceil(w["e"] * fps)) + 1)
        vals = [np.nanmean(norm[k][i0:i1]) if i0 < len(norm[k]) and np.isfinite(norm[k][i0:i1]).any() else 0.0
                for k in names]
        score.append(vals)
    s = np.nan_to_num(np.array(score))
    # lissage sur quelques mots : une personne parle rarement un seul mot au milieu de l'autre
    k = np.ones(smooth_words) / smooth_words
    sm = np.stack([np.convolve(s[:, j], k, mode="same") for j in range(len(names))], axis=1)
    lab = sm.argmax(1)
    turns: list[dict] = []
    for w, l in zip(words, lab):
        if turns and turns[-1]["speaker"] == names[l]:
            turns[-1]["end"] = w["e"]
        else:
            turns.append({"start": w["s"], "end": w["e"], "speaker": names[l]})
    # tours trop courts absorbés par le précédent (réactions brèves : le plan reste sur celui qui parlait)
    merged: list[dict] = []
    for t in turns:
        if merged and (t["end"] - t["start"] < min_turn or merged[-1]["speaker"] == t["speaker"]):
            merged[-1]["end"] = t["end"]
        else:
            merged.append(dict(t))
    return merged


def edit_list(turns: list[dict], cams: dict[str, int], duration: float, lead: float = 0.15,
              min_shot: float = 2.0) -> list[tuple[float, int]]:
    """[(instant, index de caméra)] : coupe `lead` s avant le premier mot de chaque tour, plans >= min_shot."""
    edl: list[tuple[float, int]] = [(0.0, cams[turns[0]["speaker"]] if turns else 0)]
    for t in turns[1:]:
        at = max(0.0, round(t["start"] - lead, 3))
        cam = cams[t["speaker"]]
        if cam == edl[-1][1]:
            continue
        if at - edl[-1][0] < min_shot:  # plan précédent trop court : on reste dessus (pas de flash)
            continue
        if at < duration:
            edl.append((at, cam))
    return edl


def render_master(videos: list[Path], audio: Path, edl: list[tuple[float, int]], dst: Path, work: Path,
                  audio_delay: float = 0.0, crf: int = 14, preset: str = "fast") -> Path:
    """Une passe FFmpeg : streamselect piloté par sendcmd (aucune découpe intermédiaire), audio du micro."""
    work.mkdir(parents=True, exist_ok=True)
    cmds = work / "multicam_cmds.txt"
    cmds.write_text("".join(f"{t:.3f} streamselect@cam map {c};\n" for t, c in edl[1:]), encoding="utf-8")
    cpath = str(cmds).replace("\\", "/").replace(":", "\\:")  # syntaxe des filtres FFmpeg
    n = len(videos)
    # sendcmd AVANT streamselect : la commande s'applique dès l'image de l'instant de coupe
    graph = (f"[0:v]sendcmd=f='{cpath}'[c0];[c0]{''.join(f'[{i}:v]' for i in range(1, n))}"
             f"streamselect@cam=inputs={n}:map={edl[0][1]},format=yuv420p[v];"
             f"[{n}:a]adelay={int(round(audio_delay * 1000))}:all=1,aresample=48000[a]")
    script = work / "multicam_graph.txt"
    script.write_text(graph, encoding="utf-8")
    inputs = [x for v in videos for x in ("-i", str(v))]
    run([FFMPEG, "-y", "-v", "error", *inputs, "-i", str(audio), "-/filter_complex", str(script),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-g", "48",
         "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", str(dst)])
    return dst


def build_master(cams: dict[str, Path], audio: Path, words: list[dict], dst: Path, work: Path,
                 audio_delay: float = 0.0, fps: float = 6.0, min_shot: float = 2.0) -> dict:
    """cams = {"host": gros plan animateur, "guest": gros plan invité}. Écrit `dst` + turns/edl dans `work`."""
    work.mkdir(parents=True, exist_ok=True)
    cache = work / "multicam_mouth.npz"
    if cache.exists():
        z = np.load(cache)
        act = {k: z[k] for k in cams}
    else:
        console.print("  activité des bouches sur les gros plans (en parallèle)…")
        with ThreadPoolExecutor(max_workers=len(cams)) as ex:
            res = dict(zip(cams, ex.map(lambda p: mouth_series(p, fps), cams.values())))
        act = res
        np.savez(cache, **act)
    turns = speaker_turns(words, act, fps)
    names = list(cams)
    edl = edit_list(turns, {k: i for i, k in enumerate(names)}, probe(audio)["duration"], min_shot=min_shot)
    (work / "multicam.json").write_text(json.dumps({"cams": {k: str(v) for k, v in cams.items()}, "turns": turns,
                                                    "edl": edl}, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print(f"  {len(turns)} tours de parole, {len(edl)} plans")
    return {"turns": turns, "edl": edl, "names": names}


# ---------------------------------------------------------------------------------------------------------------
# Découpe multicam d'un passage (utilisée par compose à la place de cut_segment)
#
# Le signal de bouche seul n'est pas assez fiable sur 52 min (E22 : net pour l'invité, bruité pour l'animateur),
# alors que chaque short a déjà des tours de parole relus (`turns` de clips.json, la vérité de tout le pipeline).
# L'épisode « source » est donc un simple lien vers le plan large (probe, images fixes) accompagné d'un fichier
# `<épisode>.multicam.json` ; seuls les passages des shorts sont montés, à partir des gros plans.
# ---------------------------------------------------------------------------------------------------------------

def load_spec(source: Path) -> dict | None:
    """`<source sans extension>.multicam.json` : {"cams": {"host": …, "guest": …, "wide": …}, "audio": …,
    "audio_delay": s (le micro est en avance de… sur les caméras), "lead": s, "min_shot": s}. Chemins relatifs au json."""
    side = source.with_suffix(".multicam.json")
    if not side.exists():
        return None
    spec = json.loads(side.read_text(encoding="utf-8"))
    base = side.parent
    spec["cams"] = {k: (base / v).resolve() for k, v in spec["cams"].items()}
    spec["audio"] = (base / spec["audio"]).resolve() if spec.get("audio") else None
    return spec


def window_edl(turns: list[dict], start: float, duration: float, names: list[str], lead: float = 0.15,
               min_shot: float = 2.0) -> list[tuple[float, int]]:
    """Plans [(t relatif, index caméra)] d'une fenêtre, d'après les tours de parole absolus [{at, speaker}]."""
    def cam(spk: str) -> int:
        if spk == "split":          # écran partagé : flux composé en plus des caméras (voir cut_multicam)
            return len(names)
        return names.index(spk) if spk in names else names.index("wide") if "wide" in names else 0
    ts = sorted(turns, key=lambda t: float(t["at"]))
    before = [t for t in ts if float(t["at"]) <= start + lead]
    first = before[-1]["speaker"] if before else (ts[0]["speaker"] if ts else "guest")
    edl = [(0.0, cam(first))]
    for t in ts:
        at = round(float(t["at"]) - start - lead, 3)
        if at <= 0 or at >= duration:
            continue
        c = cam(t["speaker"])
        if c == edl[-1][1] or (at - edl[-1][0] < min_shot and not t.get("force")):  # force : réaction voulue
            continue
        edl.append((at, c))
    return edl


def cut_multicam(spec: dict, dst: Path, start: float, duration: float, turns: list[dict], height: int | None = None,
                 normalize_audio: bool = True, fps: int | None = None) -> Path:
    """Équivalent de media.cut_segment pour des rushs multicam : gros plan de la personne qui parle + micro.

    `turns` : tours de parole [{at, speaker}] ou plans imposés `cams` [{at, speaker: host|guest|wide|split, force}].
    « split » = les deux gros plans côte à côte dans une image 16:9 (animateur à gauche, invité à droite, chacun recadré
    sur son visage, pixels natifs) : le cadrage vertical y voit deux visages et les empile (haut / bas), comme les
    shorts du monteur (retour d'Arthur, 07/10/2026 : « l'angle avec les deux personnes pour montrer que l'autre écoute »)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    names = list(spec["cams"])
    edl = window_edl(turns, start, duration, names, float(spec.get("lead", 0.15)), float(spec.get("min_shot", 2.0)))
    n = len(names)
    use_split = any(c == n for _, c in edl)
    cmds = dst.with_suffix(".cmds.txt")
    cmds.write_text("".join(f"{t:.3f} streamselect@cam map {c};\n" for t, c in edl[1:]) or "0.0 streamselect@cam map "
                    f"{edl[0][1]};\n", encoding="utf-8")
    cpath = str(cmds).replace("\\", "/").replace(":", "\\:")
    vf = [f"scale=-2:{height}"] if height else []
    vf += [f"fps={fps}"] if fps else []
    vf.append("format=yuv420p")
    af = "loudnorm=I=-16:TP=-1.5:LRA=11" if normalize_audio else "anull"
    pre, vin = [], [f"[{i}:v]" for i in range(n)]
    if use_split:
        from .episode import _face_x
        at = start + duration / 2
        half = {}
        for k in ("host", "guest"):
            i = names.index(k)
            cx = _face_x(spec["cams"][k], at)
            x = min(max(cx * 1920 - 480, 0), 960)           # fenêtre de 960 px de large centrée sur le visage
            pre.append(f"[{i}:v]split=2[m{i}][s{i}]")
            pre.append(f"[s{i}]crop=960:1080:{x:.0f}:0[c{k}]")
            vin[i] = f"[m{i}]"
        pre.append("[chost][cguest]hstack=2,setsar=1[split]")
        vin.append("[split]")
    graph = ";".join(pre + [f"{vin[0]}sendcmd=f='{cpath}'[c0]",
                             f"[c0]{''.join(vin[1:])}streamselect@cam=inputs={len(vin)}:map={edl[0][1]},{','.join(vf)}[v]",
                             f"[{n}:a]{af}[a]"])
    script = dst.with_suffix(".graph.txt")
    script.write_text(graph, encoding="utf-8")
    inputs = [x for p in spec["cams"].values() for x in ("-ss", f"{start:.3f}", "-i", str(p))]
    audio = spec.get("audio") or spec["cams"][names[0]]
    a_start = max(0.0, start - float(spec.get("audio_delay", 0.0)))
    run([FFMPEG, "-y", "-v", "error", *inputs, "-ss", f"{a_start:.3f}", "-i", str(audio), "-t", f"{duration:.3f}",
         "-/filter_complex", str(script), "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "10",   # intermédiaire quasi sans perte, comme cut_segment
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(dst)])
    cmds.unlink(missing_ok=True)
    script.unlink(missing_ok=True)
    return dst


def short_cams(segments: list[dict], turns: list[dict], words: list[dict], every: tuple[float, float] = (6.5, 9.5),
               length: tuple[float, float] = (2.4, 3.2), first_after: float = 11.5, tail_guard: float = 1.0,
               seed: int = 1, calm=None) -> list[dict]:
    """Plans d'un short : gros plan de celui qui parle, et de temps en temps (toutes les ~8–12 s, 2,6–3,6 s) l'écran
    partagé qui montre aussi l'autre en train d'écouter. Jamais avant `first_after` s (accroche), jamais collé à un
    raccord (`tail_guard` avant la fin d'un morceau) ; chaque bascule posée dans une pause entre deux mots."""
    import random
    rng = random.Random(seed)
    ts = sorted(turns, key=lambda t: float(t["at"]))

    def spk_at(t: float) -> str:
        cur = ts[0]["speaker"] if ts else "guest"
        for x in ts:
            if float(x["at"]) <= t + 0.2:
                cur = x["speaker"]
        return cur

    def snap(t: float, lo: float, hi: float) -> float:
        best, gap = t, 0.0
        for a, b in zip(words, words[1:]):
            mid = (a["e"] + b["s"]) / 2
            if lo <= mid <= hi and abs(mid - t) <= 0.8 and b["s"] - a["e"] > gap:
                best, gap = mid, b["s"] - a["e"]
        return best

    cams, splits, rel = [], [], 0.0
    nxt = first_after + rng.uniform(0.0, 1.5)
    for sg in segments:
        s0, s1, d = float(sg["start"]), float(sg["end"]), float(sg["duration"])
        cams.append({"at": round(s0, 3), "speaker": spk_at(s0)})
        cams += [{"at": float(t["at"]), "speaker": t["speaker"]} for t in ts if s0 < float(t["at"]) < s1]
        while nxt < rel + d - tail_guard - length[0]:
            a_rel = max(nxt, rel + 0.3)   # juste après un raccord : l'écran partagé masque le jump cut
            ln = rng.uniform(*length)
            if a_rel + ln > rel + d - tail_guard:
                break
            a = snap(s0 + (a_rel - rel), s0 + 0.2, s1 - tail_guard - ln) if a_rel - rel > 1.0 else s0 + 0.15
            b = snap(a + ln, a + 1.8, s1 - tail_guard)
            if b - a < 1.8:   # pas de pause nette où couper : on réessaie plus loin
                nxt += 1.0
                continue
            # celui qui écoute doit être calme à l'image (Arthur, 09/10/2026 : « enlève le passage où Thomas se gratte
            # l'oreille ») : sinon on essaie un peu plus loin (et pas d'écran partagé s'il ne se calme jamais)
            if calm is not None and not calm(a, b, "guest" if spk_at(a) == "host" else "host"):
                nxt += 1.0
                continue
            splits.append((a, b))
            cams += [{"at": round(a, 3), "speaker": "split", "force": True},
                     {"at": round(b, 3), "speaker": spk_at(b), "force": True}]
            nxt = a_rel + (b - a) + rng.uniform(*every)
        rel += d
    # un changement d'orateur pendant un écran partagé n'interrompt pas l'écran partagé
    cams = [c for c in cams if c["speaker"] == "split" or c.get("force") or not any(a < c["at"] < b for a, b in splits)]
    cams.sort(key=lambda c: c["at"])
    return cams


def listener_motion(cam: Path, a: float, b: float) -> float:
    """Mouvement maximal (différence moyenne entre images à 6 i/s, vignettes 96×54) dans la caméra de celui qui écoute.
    E22 : immobile à l'écoute 0,6–1,9 ; se gratte l'oreille 6,95."""
    import subprocess

    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a:.2f}", "-t", f"{max(0.2, b - a):.2f}", "-i", str(cam),
                        "-vf", "fps=6,scale=96:54,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    f = np.frombuffer(r, np.uint8)
    if f.size < 2 * 96 * 54:
        return 0.0
    f = f[: f.size // (96 * 54) * 96 * 54].reshape(-1, 54, 96).astype(float)
    return float(np.abs(np.diff(f, axis=0)).mean(axis=(1, 2)).max())
