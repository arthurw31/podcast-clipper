"""Lint / check / render des projets HyperFrames via `npx hyperframes`."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from rich.console import Console

from .config import ROOT

console = Console()


def _npx(args: list[str], cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    cmd = ["npx", "hyperframes", *args]
    env = dict(os.environ)
    # le CLI est installé à la racine du framework (node_modules) : npx le trouve en remontant
    env["PATH"] = str(ROOT / "node_modules" / ".bin") + os.pathsep + env.get("PATH", "")
    if not env.get("HYPERFRAMES_BROWSER_PATH"):
        alt = fallback_browser()
        if alt:
            env["HYPERFRAMES_BROWSER_PATH"] = alt
    # aucune fenêtre (Arthur, 08/10/2026 : « arrête d'ouvrir des sessions Chrome, j'ai une pop Chrome souvent ») :
    # pas de console cmd/npx qui clignote ; le Chrome de rendu est déjà invisible (--headless=new)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", shell=(os.name == "nt"), env=env, timeout=timeout, creationflags=flags)


def _bundled_chrome_blocked() -> bool:
    """Le chrome-headless-shell téléchargé par HyperFrames refuse-t-il de s'exécuter ? (Windows : Smart App
    Control / stratégie de contrôle des applications sur un binaire non signé)."""
    if os.name != "nt":
        return False
    base = Path.home() / ".cache" / "hyperframes" / "chrome" / "chrome-headless-shell"
    exes = sorted(base.glob("*/chrome-headless-shell-win64/chrome-headless-shell.exe")) if base.exists() else []
    if not exes:
        return False
    try:
        subprocess.run([str(exes[-1]), "--version"], capture_output=True, timeout=30)
        return False
    except OSError:
        return True
    except subprocess.TimeoutExpired:
        return False


def fallback_browser() -> str | None:
    """Chrome/Edge installés, à utiliser si le navigateur embarqué est bloqué."""
    if not _bundled_chrome_blocked():
        return None
    for c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if Path(c).exists():
            return c
    return None


def lint(proj: Path) -> tuple[bool, list[dict]]:
    res = _npx(["lint", "--json"], proj)
    findings: list[dict] = []
    try:
        data = json.loads(res.stdout)
        findings = [f for f in data.get("findings", []) if isinstance(f, dict)]
        ok = bool(data.get("ok", res.returncode == 0)) and int(data.get("errorCount", 0)) == 0
    except json.JSONDecodeError:
        ok = res.returncode == 0
        if not ok:
            findings = [{"code": "lint", "severity": "error", "message": (res.stdout + res.stderr)[-1500:]}]
    return ok, findings


def render(proj: Path, output: Path, quality: str = "looks", fps: int | None = None, crf: int | None = None,
           frame_format: str = "") -> Path:
    # absolu : npx tourne dans le dossier du projet, un chemin relatif y serait écrit (teaser E22, 07/10/2026)
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    args = ["render", "--output", str(output)]
    args += ["--crf", str(crf)] if crf else ["--quality", quality]
    if fps:
        args += ["--fps", str(fps)]
    if frame_format:
        args += ["--video-frame-format", frame_format]   # png = pas de recompression JPEG des images source
    console.print(f"  rendu {proj.parent.name}/{proj.name} → {output.name} ({quality})…")
    res = _npx(args, proj, timeout=3600)
    if res.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Rendu échoué pour {proj} :\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}")
    return output


def snapshot(proj: Path, at: list[float], out_dir: Path) -> list[Path]:
    """Captures d'images à des instants donnés (contrôle visuel rapide)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    res = _npx(["snapshot", "--at", ",".join(f"{t:.2f}" for t in at), "--output", str(out_dir)], proj, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(res.stdout[-1500:] + res.stderr[-1500:])
    return sorted(out_dir.glob("*.png"))

