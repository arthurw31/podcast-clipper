#!/usr/bin/env python
"""docs/schema.mmd (source Mermaid du schéma d'ensemble) -> docs/schema.svg (image affichée par le README).

Pourquoi une image (09/10/2026) : le schéma Mermaid du README s'affichait chez Arthur avec « Unable to render rich display —
Cannot read properties of undefined (reading 'render') » alors que le même code se dessine sans erreur avec toutes les
versions de Mermaid (10.0 à 12.1) et sur GitHub dans un autre navigateur : c'est l'outil de dessin de GitHub qui ne se
charge pas dans certains navigateurs. Une image SVG déjà dessinée s'affiche partout.

Rendu : Chrome/Edge sans fenêtre (`--headless=new`, déjà requis par HyperFrames) charge une page locale qui importe Mermaid
(version figée), dessine le schéma en texte SVG pur (`htmlLabels: false` : pas de HTML dans l'image, lisible par tous les
visionneurs), puis on récupère le SVG dans le DOM. Fond blanc ajouté (lisible aussi en thème sombre sur GitHub).
L'empreinte de la source est écrite dans l'image : `check_schema.py` signale une image périmée.
"""
from __future__ import annotations

import hashlib
import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC, OUT = ROOT / "docs" / "schema.mmd", ROOT / "docs" / "schema.svg"
MERMAID = "11.4.1"          # version figée : le rendu ne change pas tout seul

PAGE = """<!doctype html><meta charset="utf-8"><body><pre id="src">{src}</pre><pre id="out"></pre>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@{ver}/dist/mermaid.esm.min.mjs";
mermaid.initialize({{startOnLoad: false, securityLevel: "strict", htmlLabels: false,
  flowchart: {{htmlLabels: false, curve: "basis", nodeSpacing: 40, rankSpacing: 45}},
  themeVariables: {{fontFamily: "Arial, Helvetica, sans-serif", fontSize: "15px"}}}});
try {{
  const {{svg}} = await mermaid.render("schema", document.getElementById("src").textContent);
  document.getElementById("out").textContent = svg;
}} catch (e) {{ document.getElementById("out").textContent = "ERREUR " + e; }}
</script>"""


def source_hash(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from clipper.thumbnail import _browser
    src = SRC.read_text(encoding="utf-8")
    browser = _browser()
    if not browser:
        sys.exit("Chrome ou Edge introuvable")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        page = Path(td) / "schema.html"
        page.write_text(PAGE.format(src=html.escape(src), ver=MERMAID), encoding="utf-8")
        res = subprocess.run([browser, "--headless=new", "--disable-gpu", "--virtual-time-budget=30000",
                              f"--user-data-dir={Path(td) / 'profil'}", "--dump-dom", page.as_uri()],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    m = re.search(r'<pre id="out">(.*?)</pre>', res.stdout, re.S)
    svg = html.unescape(m.group(1)) if m else ""
    if not svg.startswith("<svg"):
        sys.exit(f"Rendu impossible : {svg[:300] or res.stderr[-300:]}")
    # fond blanc dans l'attribut style existant (deux attributs style = SVG invalide, image cassée)
    svg, n = re.subn(r'style="max-width: ([\d.]+)px;"', r'style="max-width: \1px; background-color: #ffffff;"', svg, count=1)
    if not n:
        svg = svg.replace("<svg ", '<svg style="background-color: #ffffff;" ', 1)
    # taille réelle (celle du viewBox) : avec width="100%" et sans hauteur, l'image se réduisait à ~100 px dans le README
    vb = re.search(r'viewBox="[\d.-]+ [\d.-]+ ([\d.]+) ([\d.]+)"', svg)
    if vb:
        w, h = (round(float(v)) for v in vb.groups())
        svg = re.sub(r' width="100%"', f' width="{w}" height="{h}"', svg, count=1)
    import xml.etree.ElementTree as ET
    ET.fromstring(svg)                      # SVG valide, sinon GitHub affiche une image cassée
    svg = f"<!-- généré par scripts/render_schema.py depuis docs/schema.mmd ; source-sha256: {source_hash(src)} -->\n" + svg
    OUT.write_text(svg, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)} : {len(svg) // 1024} Ko (Mermaid {MERMAID})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
