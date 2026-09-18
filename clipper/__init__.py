"""clipper — clips verticaux automatiques à partir d'un podcast long (HyperFrames)."""
import sys

__version__ = "0.1.0"

# Windows : la console est souvent en cp1252 ; on force l'UTF-8 pour les flèches/accents des logs.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
