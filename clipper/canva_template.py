"""Gabarit de miniature importé d'un export PowerPoint (PPTX) de Canva.

Demande d'Arthur (09/10/2026) : l'équipe marketing fait les miniatures sur Canva ; l'export PPTX garde chaque élément
en calque séparé (fond, cadres flous, cubes vides, logos, personnes détourées, bandeau) + les textes (police, taille).
`import_pptx` range ces calques dans `brands/<m>/assets/thumbnail/` avec leurs positions (template.json) ;
`compose_template` refait la miniature à l'identique avec les photos et le logo d'un nouvel épisode.

Ordre des calques attendu (celui du design de référence, dupliqué par l'équipe à chaque épisode) :
fond, cadre flou, cube animateur, logo animateur, animateur, cadre flou, cube invité, logo invité, invité, bandeau ;
puis les 2 lignes de titre. 1 px = 9525 EMU (96 ppp) : le design fait 2400×1334.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

EMU = 9525
ROLES = ["background", "decor", "host_cube", "host_logo", "host_person", "decor", "guest_cube", "guest_logo",
         "guest_person", "pill"]
WEIGHTS = {"ultra-bold": "ExtraBold", "extra-bold": "ExtraBold", "extrabold": "ExtraBold", "semi-bold": "SemiBold",
           "semibold": "SemiBold", "black": "Black", "bold": "Bold", "medium": "Medium", "regular": "Regular"}


def _font_spec(typeface: str) -> tuple[str, str]:
    """« Montserrat Ultra-Bold » -> ("Montserrat", "ExtraBold")."""
    low = typeface.lower()
    for k, v in WEIGHTS.items():
        if low.endswith(" " + k):
            return typeface[: -len(k) - 1].strip(), v
    return typeface, "Regular"


def font(brand_assets: Path, family: str, weight: str, size: float) -> ImageFont.FreeTypeFont:
    for name in (f"{family}-VF.ttf", f"{family}-{weight}.ttf"):
        p = brand_assets / "fonts" / name
        if p.exists():
            f = ImageFont.truetype(str(p), max(8, round(size)))
            if name.endswith("-VF.ttf"):
                f.set_variation_by_name(weight)
            return f
    raise FileNotFoundError(f"Police {family} introuvable dans {brand_assets / 'fonts'} (ajouter {family}-VF.ttf)")


def _box(xfrm: str) -> list[float]:
    off = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"', xfrm)
    ext = re.search(r'<a:ext cx="(\d+)" cy="(\d+)"', xfrm)
    return [int(off.group(1)) / EMU, int(off.group(2)) / EMU, int(ext.group(1)) / EMU, int(ext.group(2)) / EMU]


def import_pptx(pptx: Path, out_dir: Path, png: Path | None, brand_assets: Path) -> dict:
    """Range les calques du PPTX dans out_dir et écrit template.json. `png` = export PNG du même design (sert à
    mesurer la place des visages et des lignes de titre telles que Canva les affiche)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(pptx)
    pres = z.read("ppt/presentation.xml").decode("utf-8")
    sw, sh = (int(v) / EMU for v in re.search(r'<p:sldSz cx="(\d+)" cy="(\d+)"', pres).groups())
    slide = z.read("ppt/slides/slide1.xml").decode("utf-8", "replace")
    rels = dict(re.findall(r'Id="([^"]+)"[^>]*Target="\.\./media/([^"]+)"',
                           z.read("ppt/slides/_rels/slide1.xml.rels").decode("utf-8")))
    layers, texts = [], []
    for sp in re.findall(r"<p:sp>.*?</p:sp>", slide, re.S):
        xfrm = re.search(r"<a:xfrm[^>]*>.*?</a:xfrm>", sp, re.S).group(0)
        rot = float(re.search(r'rot="(-?\d+(?:\.\d+)?)"', xfrm).group(1)) if 'rot="' in xfrm else 0.0
        rot = rot / 60000 if abs(rot) > 360 else rot          # OOXML : 1/60000 de degré ; Canva écrit des degrés
        rot = rot - 360 if rot > 180 else rot
        box = _box(xfrm)
        emb = re.search(r'r:embed="([^"]+)"', sp)
        if emb:
            fr = re.search(r'<a:fillRect l="(-?\d+)" t="(-?\d+)" r="(-?\d+)" b="(-?\d+)"', sp)
            l, t, r, b = (int(v) / 100000 for v in fr.groups()) if fr else (0, 0, 0, 0)
            flip_h, flip_v = 'flipH="true"' in xfrm, 'flipV="true"' in xfrm
            if flip_h:                 # miroir : les marges gauche/droite s'échangent (Thomas retourné vers l'invité)
                l, r = r, l
            if flip_v:
                t, b = b, t
            x, y, w, h = box
            img = [x + l * w, y + t * h, w * (1 - l - r), h * (1 - t - b)]    # image étirée au-delà de la forme
            layers.append({"box": box, "img": img, "rot": rot, "flip_h": flip_h, "flip_v": flip_v,
                           "media": rels[emb.group(1)]})
        elif "<a:t>" in sp:
            run = re.search(r'sz="(\d+)".*?<a:latin typeface="([^"]+)"', sp, re.S)
            color = re.search(r'<a:srgbClr val="([0-9A-Fa-f]{6})"', sp)
            text = "".join(re.findall(r"<a:t>(.*?)</a:t>", sp, re.S))
            family, weight = _font_spec(run.group(2))
            texts.append({"text": text, "box": box, "family": family, "weight": weight,
                          "size": int(run.group(1)) / 100 * 96 / 72, "color": "#" + (color.group(1) if color else "FFFFFF")})
    if len(layers) != len(ROLES):
        raise ValueError(f"{len(layers)} images dans le PPTX, {len(ROLES)} attendues ({', '.join(ROLES)}) : "
                         "le design n'a pas la structure de référence")
    decor = 0
    for lay, role in zip(layers, ROLES):
        if role == "decor":
            decor += 1
            role = f"decor_{decor}"
        lay["role"] = role
        lay["file"] = f"{role}.png"
        (out_dir / lay["file"]).write_bytes(z.read(f"ppt/media/{lay.pop('media')}"))

    by = {l["role"]: l for l in layers}
    # logo de l'invité : place réellement occupée par l'encre dans sa boîte (le PNG Canva a des marges)
    gl = Image.open(out_dir / by["guest_logo"]["file"]).convert("RGBA")
    ink = gl.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox() or (0, 0, gl.width, gl.height)
    tpl = {"size": [round(sw), round(sh)], "layers": layers,
           "guest_logo_fill": [(ink[2] - ink[0]) / gl.width, 0.62]}

    # bandeau : couleurs du dégradé (gauche, milieu, droite) prises dans son image
    pill_img = np.asarray(Image.open(out_dir / by["pill"]["file"]).convert("RGBA"))
    pill_box, pill_rect = by["pill"]["box"], by["pill"]["img"]
    # seule la partie de l'image dans la forme est visible : échantillons pris dans cette partie
    row = pill_img[int((pill_box[1] + pill_box[3] / 2 - pill_rect[1]) / pill_rect[3] * pill_img.shape[0])]
    def at(fx):
        x = pill_box[0] + fx * pill_box[2]
        return row[min(pill_img.shape[1] - 1, int((x - pill_rect[0]) / pill_rect[2] * pill_img.shape[1])), :3].tolist()
    cols = [at(k) for k in (0.02, 0.5, 0.98)]

    # lignes de titre : laquelle est sur le bandeau, ligne de base mesurée sur le PNG
    texts.sort(key=lambda t: t["box"][1])
    ref = np.asarray(Image.open(png).convert("RGB")).astype(int) if png else None
    if ref is not None and ref.shape[1] != round(sw):
        ref = np.asarray(Image.open(png).convert("RGB").resize((round(sw), round(sh)))).astype(int)
    for t in texts:
        f = font(brand_assets, t["family"], t["weight"], t["size"])
        asc, desc = f.getmetrics()
        top_off = f.getbbox(t["text"])[1]
        x, y, w, h = t["box"]
        ink_top = None
        if ref is not None:          # plus longue suite de lignes « blanches » dans la boîte = la ligne de texte
            white = ref[int(y):int(y + h * 1.2), max(0, int(x)):int(x + w)].min(axis=2) > 235
            on = white.sum(1) > 15
            runs, start = [], None
            for k, v in enumerate(list(on) + [False]):
                if v and start is None:
                    start = k
                elif not v and start is not None:
                    runs.append((start, k)); start = None
            if runs:
                ink_top = int(y) + max(runs, key=lambda r: r[1] - r[0])[0]
        t["baseline"] = (ink_top - top_off + asc) if ink_top is not None else y + asc * 1.15
        t["ink_w"] = f.getlength(t["text"])
    pc = pill_box[1] + pill_box[3] / 2
    pill_t = min(texts, key=lambda t: abs(t["baseline"] - t["size"] * 0.33 - pc))   # ligne centrée sur le bandeau
    for t in texts:
        t["on_pill"] = t is pill_t
    pill = {"colors": cols, "height": pill_box[3] / pill_t["size"],
            "center_from_baseline": (pill_t["baseline"] - (pill_box[1] + pill_box[3] / 2)) / pill_t["size"],
            "pad_x": (pill_box[2] - pill_t["ink_w"]) / 2 / pill_t["size"], "radius": 0.2,
            "max_w": pill_box[2] / sw if pill_box[2] / sw > 0.75 else 0.9}
    tpl["title"] = {"pill_style": {k: pill_t[k] for k in ("family", "weight", "size", "color")},
                    "plain_style": {k: t[k] for t in texts if not t["on_pill"] for k in ("family", "weight", "size", "color")}
                    or {k: pill_t[k] for k in ("family", "weight", "size", "color")},
                    "baselines": [t["baseline"] for t in texts], "pill_line": texts.index(pill_t),
                    "examples": [t["text"] for t in texts]}
    tpl["pill"] = pill

    # visages : centre et hauteur relatifs (YuNet sur le PNG), animateur = le plus à gauche
    if png:
        import cv2

        from .analysis import _detector
        a = cv2.cvtColor(np.asarray(Image.open(png).convert("RGB").resize((round(sw), round(sh)))), cv2.COLOR_RGB2BGR)
        _, faces = _detector(a.shape[1], a.shape[0]).detect(a)
        faces = sorted([f for f in (faces if faces is not None else [])], key=lambda f: f[0])
        if len(faces) >= 2:
            for role, f in (("host", faces[0]), ("guest", faces[-1])):
                tpl[f"{role}_face"] = [float((f[0] + f[2] / 2) / sw), float((f[1] + f[3] / 2) / sh), float(f[3] / sh)]
    tpl["source"] = pptx.name
    (out_dir / "template.json").write_text(json.dumps(tpl, ensure_ascii=False, indent=1), encoding="utf-8")
    return tpl


def _place(canvas: Image.Image, img: Image.Image, rect, clip=None, rot: float = 0.0, flip_h: bool = False,
           flip_v: bool = False):
    """Colle `img` étirée dans rect [x, y, w, h], en miroir si demandé, tournée de `rot` degrés (sens horaire,
    comme PowerPoint), découpée à `clip` [x, y, w, h] (la forme Canva)."""
    from PIL import ImageOps
    x, y, w, h = rect
    im = img.convert("RGBA").resize((max(1, round(w)), max(1, round(h))), Image.Resampling.LANCZOS)
    if flip_h:
        im = ImageOps.mirror(im)
    if flip_v:
        im = ImageOps.flip(im)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    if rot:
        im = im.rotate(-rot, Image.Resampling.BICUBIC, expand=True)
        x, y = x + w / 2 - im.width / 2, y + h / 2 - im.height / 2
    ix, iy = round(x), round(y)
    sx, sy = max(0, -ix), max(0, -iy)                   # partie hors cadre (à gauche / en haut) retirée
    ex, ey = min(im.width, canvas.width - ix), min(im.height, canvas.height - iy)
    if ex > sx and ey > sy:
        layer.alpha_composite(im.crop((sx, sy, ex, ey)), (max(0, ix), max(0, iy)))
    if clip:
        m = Image.new("L", canvas.size, 0)
        ImageDraw.Draw(m).rectangle([clip[0], clip[1], clip[0] + clip[2], clip[1] + clip[3]], fill=255)
        layer.putalpha(Image.fromarray(np.minimum(np.asarray(layer.getchannel("A")), np.asarray(m))))
    canvas.alpha_composite(layer)


def _pill(size: tuple[int, int], colors) -> Image.Image:
    w, h = size
    t = np.linspace(0, 1, w)[None, :, None]
    c0, c1, c2 = (np.array(c, np.float32) for c in colors)
    row = np.where(t < 0.5, c0 + (c1 - c0) * (t / 0.5), c1 + (c2 - c1) * ((t - 0.5) / 0.5))
    return Image.fromarray(np.repeat(row, h, axis=0).astype(np.uint8), "RGB").convert("RGBA")


def compose_template(tpl: dict, tpl_dir: Path, host, guest, guest_logo: Image.Image | None,
                     lines: list[str], highlight: int, brand) -> Image.Image:
    """host / guest = (personne détourée RGBA, mesures du visage) ; guest_logo = logo de l'entreprise invitée."""
    from .thumbnail import grade_person, place_person
    W, H = tpl["size"]
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    people = {"host_person": host, "guest_person": guest}
    for lay in tpl["layers"]:
        role = lay["role"]
        if role == "pill":
            continue
        if role in people:
            img, face = people[role]
            key = role.split("_")[0]
            cx, cy, fh = tpl.get(f"{key}_face", [0.18 if key == "host" else 0.81, 0.35, 0.35])
            person = place_person((W, H), grade_person(img), face, (cx, cy), fh)
            # pas de découpe à la forme Canva : elle était faite pour SA photo et couperait net une épaule
            canvas.alpha_composite(person)
            continue
        if role == "guest_logo":
            if guest_logo is None:
                continue
            bx, by_, bw, bh = lay["box"]
            fw, fh_ = tpl["guest_logo_fill"]
            lg = guest_logo.copy()
            k = min(bw * fw / lg.width, bh * fh_ / lg.height)
            lw, lh = lg.width * k, lg.height * k
            _place(canvas, lg, [bx + (bw - lw) / 2, by_ + (bh - lh) / 2, lw, lh], rot=lay["rot"])
            continue
        _place(canvas, Image.open(tpl_dir / lay["file"]), lay["img"], clip=lay["box"], rot=lay["rot"],
               flip_h=lay.get("flip_h", False), flip_v=lay.get("flip_v", False))
    draw_title(canvas, tpl, lines, highlight, brand.assets_dir)
    return canvas.convert("RGB")


def draw_title(canvas: Image.Image, tpl: dict, lines: list[str], highlight: int, assets: Path):
    W, H = canvas.size
    ti, pill = tpl["title"], tpl["pill"]
    styles = [ti["pill_style"] if i == highlight else ti["plain_style"] for i in range(len(lines))]
    max_w = pill["max_w"] * W - 2 * pill["pad_x"] * ti["pill_style"]["size"]
    scale = 1.0
    for _ in range(30):
        fonts = [font(assets, s["family"], s["weight"], s["size"] * scale) for s in styles]
        if max(f.getlength(t) for f, t in zip(fonts, lines)) <= max_w:
            break
        scale *= 0.96
    base = list(ti["baselines"])
    if highlight != ti["pill_line"] and len(base) == 2:
        # bandeau sur la 2e ligne : il doit passer sous les jambages de la 1re avec un vrai espace, et le bloc
        # garde le même bas que dans le design (bas du texte d'origine)
        s1 = styles[1]["size"] * scale
        desc0 = fonts[0].getmetrics()[1]
        pill_up = (pill["center_from_baseline"] + pill["height"] / 2) * s1     # haut du bandeau au-dessus de sa ligne de base
        b1 = base[1] + 0.06 * s1
        b0 = min(base[0], b1 - pill_up - desc0 - 0.12 * s1)
        base = [b0, b1]
    d = ImageDraw.Draw(canvas)
    for i, (text, f, s) in enumerate(zip(lines, fonts, styles)):
        b = base[min(i, len(base) - 1)]
        if i == highlight:
            l, _, r, _ = d.textbbox((W / 2, b), text, font=f, anchor="ms")
            size = s["size"] * scale
            ph = pill["height"] * size
            cy = b - pill["center_from_baseline"] * size
            px = pill["pad_x"] * size
            box = [round(l - px), round(cy - ph / 2), round(r + px), round(cy + ph / 2)]
            p = _pill((box[2] - box[0], box[3] - box[1]), pill["colors"])
            m = Image.new("L", p.size, 0)
            ImageDraw.Draw(m).rounded_rectangle([0, 0, p.width - 1, p.height - 1], round(pill["radius"] * ph), fill=255)
            canvas.paste(p, (box[0], box[1]), m)
        elif ti.get("shadow", 0.67):
            # ombre douce sous la ligne hors bandeau : le texte blanc se perdait sur une chemise claire (E22) ; invisible
            # sur fond sombre, comme sur la vignette DUST de référence
            sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            ImageDraw.Draw(sh).text((W / 2, b + 5), text, font=f, fill=(0, 0, 0, int(255 * ti.get("shadow", 0.67))), anchor="ms")
            canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(7)))
        d.text((W / 2, b), text, font=f, fill=s["color"], anchor="ms")
