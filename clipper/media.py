"""Utilitaires FFmpeg / FFprobe."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def run(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, check=check, capture_output=capture, text=True, encoding="utf-8", errors="replace"
    )


def probe(path: Path) -> dict:
    out = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]).stdout
    info = json.loads(out)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    fps = 30.0
    if video and video.get("r_frame_rate"):
        n, d = video["r_frame_rate"].split("/")
        fps = float(n) / float(d) if float(d) else 30.0
    return {
        "duration": float(info["format"].get("duration", 0)),
        "width": int(video["width"]) if video else 0,
        "height": int(video["height"]) if video else 0,
        "fps": fps,
        "has_audio": audio is not None,
    }


def extract_audio_wav(src: Path, dst: Path, sr: int = 16000) -> Path:
    """Piste audio mono 16 kHz pour Whisper."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)])
    return dst


def cut_segment(src: Path, dst: Path, start: float, duration: float, height: int | None = None,
                normalize_audio: bool = True, fps: int | None = None) -> Path:
    """Découpe un segment ré-encodé (précis à l'image) avec audio normalisé."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = []
    if height:
        vf.append(f"scale=-2:{height}")
    if fps:
        vf.append(f"fps={fps}")
    af = "loudnorm=I=-16:TP=-1.5:LRA=11" if normalize_audio else "anull"
    cmd = [FFMPEG, "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
           "-map", "0:v:0", "-map", "0:a:0?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-af", af, "-movflags", "+faststart"]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd.append(str(dst))
    run(cmd)
    return dst


def concat_segments(parts: list[Path], dst: Path) -> Path:
    """Concatène des segments encodés à l'identique (demuxer concat, sans ré-encodage)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    lst = dst.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
         "-movflags", "+faststart", str(dst)])
    lst.unlink(missing_ok=True)
    return dst


def extract_frame(src: Path, at: float, dst: Path, width: int | None = None) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y", "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", str(src), "-frames:v", "1"]
    if width:
        cmd += ["-vf", f"scale={width}:-2"]
    cmd += ["-q:v", "2", str(dst)]
    run(cmd)
    return dst


def detect_scene_cuts(src: Path, threshold: float = 0.32, start: float = 0.0, duration: float | None = None) -> list[float]:
    """Instants de coupe (en secondes, relatifs à `start`) via le filtre `scene` de ffmpeg."""
    cmd = [FFMPEG, "-v", "info", "-nostats", "-ss", f"{start:.3f}", "-i", str(src)]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vf", f"scale=640:-2,select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"]
    res = run(cmd, check=False)
    cuts = []
    for m in re.finditer(r"pts_time:\s*([0-9.]+)", res.stderr):
        cuts.append(float(m.group(1)))
    return sorted(set(round(c, 3) for c in cuts))


def transcode_broll(src: Path, dst: Path, duration: float, width: int = 1080, height: int | None = None) -> Path:
    """Ré-encode un B-roll (Pexels) : coupe à `duration`, sans audio, H.264 léger."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    scale = f"scale={width}:-2" if not height else f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-t", f"{duration + 0.5:.3f}", "-an",
         "-vf", scale, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(dst)])
    return dst


def make_blurred_still(src_frame: Path, dst: Path, blur: int = 22, darken: float = 0.18) -> Path:
    """Image floutée/assombrie (fond de la carte de fin)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run([FFMPEG, "-y", "-v", "error", "-i", str(src_frame),
         "-vf", f"gblur=sigma={blur},eq=brightness=-{darken:.2f}", "-q:v", "3", str(dst)])
    return dst
