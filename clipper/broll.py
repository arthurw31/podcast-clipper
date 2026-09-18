"""B-roll via l'API Pexels (vidéos ou photos), avec cache local.

Clé : PEXELS_API_KEY dans .env (ou variable d'environnement).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import requests
from rich.console import Console

from .config import CACHE_DIR
from .media import transcode_broll

console = Console()
API = "https://api.pexels.com"


def _headers() -> dict:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        raise RuntimeError("PEXELS_API_KEY manquante (.env)")
    return {"Authorization": key}


def _cache_path(kind: str, query: str, orientation: str) -> Path:
    h = hashlib.sha1(f"{kind}|{query}|{orientation}".encode()).hexdigest()[:12]
    ext = "mp4" if kind == "video" else "jpg"
    return CACHE_DIR / "pexels" / f"{h}.{ext}"


def _download(url: str, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return dst


def search_video(query: str, orientation: str = "portrait", min_duration: float = 3.0) -> dict | None:
    r = requests.get(f"{API}/videos/search", headers=_headers(), timeout=30,
                     params={"query": query, "orientation": orientation, "size": "medium", "per_page": 8})
    r.raise_for_status()
    for v in r.json().get("videos", []):
        if v.get("duration", 0) < min_duration:
            continue
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
        if not files:
            continue
        # fichier le plus proche de 1080 px de large (assez net, pas trop lourd)
        best = min(files, key=lambda f: abs(int(f["width"]) - 1080))
        return {"id": v["id"], "url": best["link"], "width": best["width"], "height": best["height"],
                "credit": v.get("user", {}).get("name", ""), "page": v.get("url", "")}
    return None


def search_photo(query: str, orientation: str = "portrait") -> dict | None:
    r = requests.get(f"{API}/v1/search", headers=_headers(), timeout=30,
                     params={"query": query, "orientation": orientation, "per_page": 5})
    r.raise_for_status()
    for p in r.json().get("photos", []):
        return {"id": p["id"], "url": p["src"].get("large2x") or p["src"]["large"],
                "credit": p.get("photographer", ""), "page": p.get("url", "")}
    return None


def fetch_broll(query: str, kind: str, duration: float, dst_dir: Path, name: str,
                orientation: str = "landscape", width: int = 1080) -> dict | None:
    """Télécharge (ou reprend du cache) un B-roll et le prépare dans dst_dir/name.ext.

    Retourne {"file": "broll/name.mp4", "kind": "video"|"photo", "credit": …} ou None.
    """
    if not query:
        return None
    try:
        if kind == "video":
            cache = _cache_path("video", query, orientation)
            meta_file = cache.with_suffix(".json")
            if not cache.exists():
                hit = search_video(query, orientation, min_duration=duration)
                if not hit:
                    console.print(f"  [yellow]Pexels : aucune vidéo pour « {query} »[/yellow]")
                    return _fallback_photo(query, duration, dst_dir, name, orientation)
                _download(hit["url"], cache)
                meta_file.write_text(json.dumps(hit), encoding="utf-8")
            meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
            out = dst_dir / f"{name}.mp4"
            transcode_broll(cache, out, duration, width=width)
            return {"file": out.name, "kind": "video", "credit": meta.get("credit", ""), "page": meta.get("page", ""), "query": query}
        return _fallback_photo(query, duration, dst_dir, name, orientation)
    except Exception as e:  # noqa: BLE001
        console.print(f"  [yellow]Pexels : erreur pour « {query} » : {e}[/yellow]")
        return None


def _fallback_photo(query: str, duration: float, dst_dir: Path, name: str, orientation: str) -> dict | None:
    cache = _cache_path("photo", query, orientation)
    meta_file = cache.with_suffix(".json")
    if not cache.exists():
        hit = search_photo(query, orientation)
        if not hit:
            console.print(f"  [yellow]Pexels : aucune photo pour « {query} »[/yellow]")
            return None
        _download(hit["url"], cache)
        meta_file.write_text(json.dumps(hit), encoding="utf-8")
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    dst_dir.mkdir(parents=True, exist_ok=True)
    out = dst_dir / f"{name}.jpg"
    out.write_bytes(cache.read_bytes())
    return {"file": out.name, "kind": "photo", "credit": meta.get("credit", ""), "page": meta.get("page", ""), "query": query}
