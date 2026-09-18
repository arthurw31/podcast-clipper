"""Chargement et fusion de la configuration : defaults -> preset -> brand.

La configuration est un simple dict (accès par attribut via `Cfg`) pour rester
souple : chaque marque ne surcharge que ce dont elle a besoin.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
BRANDS_DIR = ROOT / "brands"
OUTPUT_DIR = ROOT / "output"
CACHE_DIR = ROOT / "cache"
MODELS_DIR = ROOT / "models"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

FORMATS: dict[str, tuple[int, int]] = {
    "9x16": (1080, 1920),
    "16x9": (1920, 1080),
    "1x1": (1080, 1080),
}

load_dotenv(ROOT / ".env")


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Cfg(dict):
    """dict avec accès par attribut, récursif (cfg.captions.font_size)."""

    def __getattr__(self, item: str) -> Any:
        try:
            v = self[item]
        except KeyError as e:
            raise AttributeError(item) from e
        return Cfg(v) if isinstance(v, dict) else v

    def get_path(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Brand:
    """Une marque = un dossier brands/<slug>/ (brand.yaml, guidelines.md, assets/)."""

    def __init__(self, slug: str):
        self.slug = slug
        self.dir = BRANDS_DIR / slug
        if not self.dir.exists():
            raise FileNotFoundError(
                f"Marque introuvable : {self.dir}\n"
                f"Créez-la avec : python -m clipper new-brand {slug}"
            )
        self.assets_dir = self.dir / "assets"
        self.template_assets_dir = BRANDS_DIR / "_template" / "assets"

        defaults = _load_yaml(CONFIG_DIR / "defaults.yaml")
        brand = _load_yaml(self.dir / "brand.yaml")
        preset_name = (brand.get("montage") or {}).get("preset") or "dynamic"
        preset = _load_yaml(CONFIG_DIR / "presets" / f"{preset_name}.yaml")
        merged = deep_merge(deep_merge(defaults, preset), brand)
        merged.setdefault("montage", {})["preset"] = preset_name
        self.cfg = Cfg(merged)

        gl = self.dir / "guidelines.md"
        self.guidelines = gl.read_text(encoding="utf-8") if gl.exists() else ""
        pb = self.dir / "posts.md"   # brief + posts publiés servant de référence pour les textes de publication
        self.posts_brief = pb.read_text(encoding="utf-8") if pb.exists() else ""

    # -- assets -------------------------------------------------------------
    def asset(self, rel: str | None) -> Path | None:
        """Résout un asset : d'abord dans la marque, sinon dans _template."""
        if not rel:
            return None
        for base in (self.assets_dir, self.template_assets_dir):
            p = base / rel
            if p.exists():
                return p
        return None

    def font_path(self, key: str) -> Path | None:
        return self.asset(self.cfg.fonts.get(key))

    @property
    def logo_path(self) -> Path | None:
        return self.asset(self.cfg.logo.get("file"))

    @property
    def music_path(self) -> Path | None:
        return self.asset(self.cfg.audio.get("music"))


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def list_brands() -> list[str]:
    return sorted(p.name for p in BRANDS_DIR.iterdir() if p.is_dir() and not p.name.startswith("_"))
