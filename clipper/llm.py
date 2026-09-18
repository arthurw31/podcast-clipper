"""Accès au modèle (Claude) — deux backends :

- `anthropic`  : SDK officiel, nécessite ANTHROPIC_API_KEY (dans .env ou l'environnement)
- `claude-cli` : commande `claude -p` (Claude Code), utilise l'abonnement de la machine

`auto` prend le SDK si une clé est présente, sinon le CLI.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console

console = Console()


def _extract_json(text: str) -> dict:
    """Récupère le premier objet JSON d'une réponse (tolère les ```json ... ```)."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"Pas de JSON dans la réponse du modèle :\n{text[:800]}")
    return json.loads(text[start:end + 1])


def pick_backend(preferred: str = "auto") -> str:
    if preferred in ("anthropic", "claude-cli"):
        return preferred
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if shutil.which("claude"):
        return "claude-cli"
    raise RuntimeError(
        "Aucun backend LLM : définissez ANTHROPIC_API_KEY dans .env, ou installez Claude Code (`claude`)."
    )


def ask_json(system: str, user: str, model: str = "claude-sonnet-5", backend: str = "auto",
             max_tokens: int = 8000) -> dict:
    backend = pick_backend(backend)
    console.print(f"[dim]LLM · {backend} · {model}[/dim]")
    if backend == "anthropic":
        return _ask_anthropic(system, user, model, max_tokens)
    return _ask_claude_cli(system, user, model)


def _ask_anthropic(system: str, user: str, model: str, max_tokens: int) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return _extract_json(text)


def _ask_claude_cli(system: str, user: str, model: str) -> dict:
    # alias courts acceptés par le CLI
    alias = {"claude-sonnet-5": "sonnet", "claude-opus-5": "opus", "claude-haiku-4-5-20251001": "haiku"}.get(model, model)
    prompt = f"{system}\n\n---\n\n{user}"
    # cwd neutre pour ne pas charger le contexte d'un projet Claude Code
    with tempfile.TemporaryDirectory() as tmp:
        prompt_file = Path(tmp) / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        cmd = ["claude", "-p", "--output-format", "json", "--model", alias, "--tools", ""]
        with open(prompt_file, "r", encoding="utf-8") as fh:
            res = subprocess.run(cmd, stdin=fh, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", cwd=tmp, shell=(os.name == "nt"))
    if res.returncode != 0:
        raise RuntimeError(f"claude CLI a échoué ({res.returncode}) :\n{res.stderr[:1500]}")
    try:
        payload = json.loads(res.stdout)
        text = payload.get("result", "")
    except json.JSONDecodeError:
        text = res.stdout
    return _extract_json(text)
