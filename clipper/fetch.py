"""Téléchargement des rushs depuis un lien Dropbox partagé, avec reprise automatique.

Pourquoi : dans le navigateur, les gros fichiers (13 Go) d'un dossier Dropbox partagé se coupent au bout d'environ
50 min (constaté deux fois le 06/10/2026, E22 : les 3 caméras arrêtées ensemble vers 70 %) et le navigateur garde
le fichier tronqué sous son nom final, sans « moov atom » -> illisible. Ici : on demande à Dropbox la liste exacte
(nom + taille), on télécharge chaque fichier par requêtes HTTP Range (reprise là où on s'est arrêté, y compris
d'un fichier partiel déjà présent) et on ne déclare un fichier fini que si sa taille est exactement celle annoncée.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests
from rich.console import Console

console = Console()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
CHUNK = 8 * 1024 * 1024


def _dl_url(href: str) -> str:
    """Lien de partage (dl=0) -> lien de téléchargement direct (dl=1)."""
    u = urlparse(href)
    q = {k: v[0] for k, v in parse_qs(u.query).items() if k in ("rlkey",)}
    query = "&".join(f"{k}={v}" for k, v in q.items())
    return f"{u.scheme}://{u.netloc}{u.path}?{query}{'&' if query else ''}dl=1"


def list_dropbox(url: str) -> list[dict]:
    """Fichiers d'un lien Dropbox : [{name, bytes, url}]. Dossier (scl/fo) -> liste ; fichier (scl/fi) -> un élément."""
    s = requests.Session()
    s.headers["User-Agent"] = UA
    u = urlparse(url)
    rlkey = parse_qs(u.query).get("rlkey", [""])[0]
    m = re.match(r"/scl/fo/([^/]+)/([^/?]+)/?$", u.path)
    if not m:  # lien vers un seul fichier
        direct = _dl_url(url)
        r = s.get(direct, headers={"Range": "bytes=0-0"}, stream=True, allow_redirects=True, timeout=60)
        total = int(r.headers.get("Content-Range", "/0").split("/")[-1] or 0)
        r.close()
        return [{"name": unquote(u.path.rstrip("/").split("/")[-1]), "bytes": total, "url": direct}]
    link_key, secure_hash = m.groups()
    s.get(url, timeout=60)  # pose le cookie anti-CSRF « t »
    entries, cursor = [], None
    while True:
        data = {"is_xhr": "true", "t": s.cookies.get("t", ""), "link_key": link_key, "link_type": "c",
                "secure_hash": secure_hash, "sub_path": "", "rlkey": rlkey}
        if cursor:
            data["voucher"] = cursor
        r = s.post("https://www.dropbox.com/list_shared_link_folder_entries", data=data, timeout=60)
        r.raise_for_status()
        js = r.json()
        for e in js.get("entries", []):
            if not e.get("is_dir"):
                entries.append({"name": e["filename"], "bytes": int(e["bytes"]), "url": _dl_url(e["href"])})
        cursor = js.get("next_request_voucher")
        if not js.get("has_more_entries") or not cursor:
            break
    return entries


def download(entry: dict, dest: Path, tries: int = 100) -> bool:
    """Télécharge `entry` dans `dest` en reprenant le fichier partiel ; True si la taille finale est exacte."""
    total = entry["bytes"]
    last_print = 0.0
    for attempt in range(1, tries + 1):
        have = dest.stat().st_size if dest.exists() else 0
        if have == total:
            return True
        if have > total:  # fichier différent du même nom : on repart de zéro
            dest.unlink()
            have = 0
        try:
            # sans User-Agent de navigateur : avec, Dropbox répond par sa page d'aperçu HTML au lieu du fichier
            with requests.get(entry["url"], headers={"Range": f"bytes={have}-"}, stream=True, timeout=(30, 120)) as r:
                if "text/html" in r.headers.get("Content-Type", ""):
                    raise RuntimeError("Dropbox a renvoyé une page web au lieu du fichier")
                if r.status_code == 200 and have:  # pas de reprise côté serveur : recommencer
                    have = 0
                    mode = "wb"
                elif r.status_code in (200, 206):
                    mode = "ab" if have else "wb"
                else:
                    raise RuntimeError(f"HTTP {r.status_code}")
                with open(dest, mode) as fh:
                    for chunk in r.iter_content(CHUNK):
                        fh.write(chunk)
                        have += len(chunk)
                        if time.time() - last_print > 60:
                            console.print(f"  {entry['name']} : {have / 1e9:.1f} / {total / 1e9:.1f} Go ({100 * have / total:.0f} %)")
                            last_print = time.time()
        except Exception as e:  # noqa: BLE001 — coupure réseau / lien expiré : on reprend
            console.print(f"[yellow]  {entry['name']} : coupure ({e}), reprise (essai {attempt})…[/yellow]")
            time.sleep(min(60, 5 * attempt))
    return dest.exists() and dest.stat().st_size == total


def fetch(url: str, dest_dir: Path, skip: list[str] | None = None, jobs: int = 3) -> list[Path]:
    """Télécharge tous les fichiers du lien dans `dest_dir` ; renvoie les fichiers complets."""
    entries = [e for e in list_dropbox(url) if not any(s.lower() in e["name"].lower() for s in (skip or []))]
    if not entries:
        raise SystemExit("Aucun fichier trouvé derrière ce lien (lien expiré ou accès restreint ?)")
    dest_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"{len(entries)} fichier(s), {sum(e['bytes'] for e in entries) / 1e9:.1f} Go -> {dest_dir}")
    for e in entries:
        console.print(f"  · {e['name']}  {e['bytes'] / 1e9:.2f} Go")
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        ok = list(ex.map(lambda e: download(e, dest_dir / e["name"]), entries))
    done = []
    for e, good in zip(entries, ok):
        p = dest_dir / e["name"]
        if good:
            console.print(f"[green]✓ {e['name']} complet ({e['bytes']} octets)[/green]")
            done.append(p)
        else:
            console.print(f"[red]✗ {e['name']} incomplet — relancer la même commande pour reprendre[/red]")
    return done
