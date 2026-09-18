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
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", shell=(os.name == "nt"), env=env, timeout=timeout)


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


def render(proj: Path, output: Path, quality: str = "looks", fps: int | None = None) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    args = ["render", "--output", str(output), "--quality", quality]
    if fps:
        args += ["--fps", str(fps)]
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


def preview_cmd(proj: Path) -> str:
    return f'cd "{proj}" && npx hyperframes preview --background'
