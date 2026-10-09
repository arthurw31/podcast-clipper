#!/usr/bin/env python
"""Vérifie que le schéma du process suit le code (règle d'Arthur, 09/10/2026 : « mets à jour le schéma d'architecture à chaque fois
qu'on modifie le process » ; le schéma de référence est celui de GitHub : « Vue d'ensemble » du README). À lancer avant de
commiter toute modification du process ; code de sortie 1 si un oubli est détecté.

Contrôles :
1. chaque commande du CLI figure dans la liste « commandes » de docs/ARCHITECTURE.md ;
2. chaque commande qui est une ÉTAPE du process (PROCESS) figure dans le schéma d'ensemble du README (mermaid) ;
3. le schéma cite les étapes de validation humaine (`titles --pick`, `thumbnail --pick`).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# commandes qui sont une étape du process (les autres : utilitaires, sous-étapes de `polish`, recours)
PROCESS = ["rushes", "transcribe", "propose", "pick", "polish", "qa", "render", "posts", "episode-plan", "episode-render",
           "titles", "thumbnail", "thumbnail-template"]
HUMAN_GATES = ["titles --pick", "thumbnail --pick"]


def cli_commands() -> list[str]:
    out = subprocess.run([sys.executable, "-m", "clipper", "-h"], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"}).stdout
    m = re.search(r"\{([a-z0-9,-]+)\}", out)
    if not m:
        sys.exit("Impossible de lire la liste des commandes (python -m clipper -h)")
    return m.group(1).split(",")


def mentions(text: str, cmd: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(cmd)}(?![\w-])", text) is not None


def section(text: str, start: str) -> str:
    i = text.find(start)
    return text[i:].split("\n## ", 2)[0] if i >= 0 else ""


def main() -> int:
    cmds = cli_commands()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    arch = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    overview = section(readme, "## Vue d'ensemble")
    cli_row = next((l for l in arch.splitlines() if "commandes :" in l), "")
    problems: list[str] = []
    for c in cmds:
        if not mentions(cli_row, c):
            problems.append(f"docs/ARCHITECTURE.md : la commande `{c}` manque dans la liste « commandes » du bloc cli.py")
    for c in PROCESS:
        if c not in cmds:
            problems.append(f"scripts/check_schema.py : `{c}` n'est plus une commande (mettre PROCESS à jour)")
        elif not mentions(overview, c):
            problems.append(f"README.md (Vue d'ensemble) : l'étape `{c}` n'est pas dans le schéma")
    for g in HUMAN_GATES:
        if g not in overview:
            problems.append(f"README.md (Vue d'ensemble) : la validation humaine `{g}` n'est pas dans le schéma")
    if problems:
        print("Le schéma n'est pas à jour avec le process :")
        print("\n".join(f"  - {p}" for p in problems))
        return 1
    print(f"Schéma à jour : {len(cmds)} commandes, {len(PROCESS)} étapes du process, {len(HUMAN_GATES)} validations humaines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
