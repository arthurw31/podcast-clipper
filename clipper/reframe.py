"""Plan de recadrage : analyse + format de sortie -> liste de « caméras » timées.

Chaque entrée du plan devient un <video> HyperFrames avec son propre crop :
{"t0": 0.0, "t1": 3.4, "layout": "single" | "split" | "center" | "full",
 "cams": [{"slot": "full" | "top" | "bottom", "crop": {"x":…, "y":…, "w":…, "h":…}}]}
Le crop est en pixels source ; le slot désigne la zone du canvas de sortie.
"""
from __future__ import annotations

from .config import FORMATS, Cfg

MAX_UPSCALE = 2.2


def _slots(fmt: str) -> dict[str, tuple[int, int, int, int]]:
    """slot -> (x, y, w, h) sur le canvas de sortie."""
    W, H = FORMATS[fmt]
    return {
        "full": (0, 0, W, H),
        "top": (0, 0, W, H // 2),
        "bottom": (0, H // 2, W, H - H // 2),
    }


def _crop_for(slot_wh: tuple[int, int], sw: int, sh: int, cx: float, cy: float, fh: float,
              face_frac: float, headroom: float, zoom: float) -> dict:
    """Crop (pixels source) d'aspect = slot, centré sur (cx, cy) fractions, visage ~face_frac du crop."""
    sw_slot, sh_slot = slot_wh
    aspect = sw_slot / sh_slot
    face_px = max(fh * sh, 8)
    want_h = face_px / face_frac / max(zoom, 0.5)
    min_h = sh_slot / MAX_UPSCALE
    crop_h = min(sh, max(want_h, min_h))
    crop_w = crop_h * aspect
    if crop_w > sw:
        crop_w = sw
        crop_h = crop_w / aspect
    x = cx * sw - crop_w / 2
    y = cy * sh - headroom * crop_h
    x = min(max(0.0, x), sw - crop_w)
    y = min(max(0.0, y), sh - crop_h)
    return {"x": round(x, 1), "y": round(y, 1), "w": round(crop_w, 1), "h": round(crop_h, 1)}


def _center_crop(slot_wh: tuple[int, int], sw: int, sh: int) -> dict:
    aspect = slot_wh[0] / slot_wh[1]
    crop_h = sh
    crop_w = crop_h * aspect
    if crop_w > sw:
        crop_w, crop_h = sw, sw / aspect
    return {"x": round((sw - crop_w) / 2, 1), "y": round((sh - crop_h) / 2, 1), "w": round(crop_w, 1), "h": round(crop_h, 1)}


def _sentence_breaks(words: list[dict], t0: float, t1: float) -> list[float]:
    """Instants de respiration (fin de phrase / pause) dans [t0, t1] pour couper proprement."""
    pts = []
    prev = None
    for w in words:
        if w["e"] < t0 or w["s"] > t1:
            prev = w
            continue
        if prev is not None:
            gap = w["s"] - prev["e"]
            if gap > 0.25 or prev["w"][-1:] in ".?!…":
                pts.append(round((prev["e"] + w["s"]) / 2, 3))
        prev = w
    return [p for p in pts if t0 + 1.0 < p < t1 - 1.0]


def _split_long(t0: float, t1: float, max_len: float, breaks: list[float]) -> list[tuple[float, float]]:
    """Découpe [t0,t1] en morceaux ≤ max_len, en préférant les pauses de la voix."""
    out = []
    cur = t0
    while t1 - cur > max_len * 1.15:
        target = cur + max_len * 0.8
        cands = [b for b in breaks if cur + max_len * 0.45 <= b <= cur + max_len]
        cut = min(cands, key=lambda b: abs(b - target)) if cands else cur + max_len * 0.85
        out.append((cur, cut))
        cur = cut
    out.append((cur, t1))
    return out


def _turn_segments(turns: list[dict], t0: float, t1: float, host_side: str) -> list[dict] | None:
    """Segments de locuteur d'après les tours de parole (LLM) et le côté de l'animateur.

    Retourne [{"t0", "t1", "side": "left"|"right"}] ou None si l'info manque.
    """
    if not turns or host_side not in ("left", "right"):
        return None
    other = "right" if host_side == "left" else "left"
    segs = []
    cur_side = None
    cur_t = t0
    for t in turns:
        at = float(t["at"])
        side = host_side if t["speaker"] == "host" else other
        if at <= t0:
            cur_side = side
            continue
        if at >= t1:
            break
        if cur_side is not None and at - cur_t > 0.05:
            segs.append({"t0": cur_t, "t1": at, "side": cur_side})
        cur_side = side
        cur_t = at
    if cur_side is None:
        # aucun tour avant t0 : on prend le premier connu
        cur_side = host_side if turns[0]["speaker"] == "host" else other
    if t1 - cur_t > 0.05:
        segs.append({"t0": cur_t, "t1": t1, "side": cur_side})
    # segments trop courts (< 1 s) absorbés par le précédent
    out: list[dict] = []
    for sg in segs:
        if out and sg["t1"] - sg["t0"] < 1.0:
            out[-1]["t1"] = sg["t1"]
        else:
            out.append(sg)
    return out or None


def build_plan(analysis: dict, fmt: str, cfg: Cfg, words: list[dict], turns: list[dict] | None = None,
               host_side: str = "") -> list[dict]:
    W, H = FORMATS[fmt]
    sw, sh = analysis["width"], analysis["height"]
    fr = cfg.framing
    slots = _slots(fmt)
    plan: list[dict] = []
    src_aspect = sw / sh
    out_aspect = W / H

    # Format proche de la source (ex. 16:9 -> 16:9) : pas de recadrage, juste le zoom lent.
    if abs(src_aspect - out_aspect) < 0.05:
        for shot in analysis["shots"]:
            plan.append({"t0": shot["t0"], "t1": shot["t1"], "layout": "full",
                         "cams": [{"slot": "full", "crop": {"x": 0, "y": 0, "w": sw, "h": sh},
                                   "faces": [{"cx": p["cx"], "cy": p["cy"], "fh": p["fh"]} for p in shot["persons"]]}]})
        return _apply_zoom(plan, cfg)

    full_wh = slots["full"][2:]
    half_wh = slots["top"][2:]
    wide_mode = fr.wide_shot_mode
    fallback = fr.speaker_fallback

    def single_cam(p: dict, zoom: float = 1.0) -> dict:
        return {"slot": "full", "face": {"cx": p["cx"], "cy": p["cy"], "fh": p["fh"]},
                "crop": _crop_for(full_wh, sw, sh, p["cx"], p["cy"], p["fh"], 0.20, float(fr.headroom), float(fr.face_zoom) * zoom)}

    hs = host_side or fr.get("host_side", "")

    def split_cams(persons: list[dict]) -> list[dict]:
        # écran partagé : l'invité en haut, l'animateur en bas (si le côté est connu)
        ps = sorted(persons, key=lambda p: p["cx"])[:2]
        if hs == "left":
            ps = ps[::-1]
        return [{"slot": "top", "face": {"cx": ps[0]["cx"], "cy": ps[0]["cy"], "fh": ps[0]["fh"]},
                 "crop": _crop_for(half_wh, sw, sh, ps[0]["cx"], ps[0]["cy"], ps[0]["fh"], 0.26, 0.45, float(fr.face_zoom))},
                {"slot": "bottom", "face": {"cx": ps[1]["cx"], "cy": ps[1]["cy"], "fh": ps[1]["fh"]},
                 "crop": _crop_for(half_wh, sw, sh, ps[1]["cx"], ps[1]["cy"], ps[1]["fh"], 0.26, 0.45, float(fr.face_zoom))}]

    for shot in analysis["shots"]:
        persons = shot["persons"]
        t0, t1 = shot["t0"], shot["t1"]
        if len(persons) == 0:
            plan.append({"t0": t0, "t1": t1, "layout": "center", "cams": [{"slot": "full", "crop": _center_crop(full_wh, sw, sh)}]})
            continue
        if len(persons) == 1 or fr.single_mode == "center" and len(persons) == 1:
            p = persons[0]
            pieces = _split_long(t0, t1, float(fr.max_shot_len), _sentence_breaks(words, t0, t1)) if fr.synthetic_cuts else [(t0, t1)]
            for i, (a, b) in enumerate(pieces):
                # sur un plan fixe long : alternance cadrage normal / punch-in
                zoom = 1.0 if i % 2 == 0 else 1.18
                plan.append({"t0": a, "t1": b, "layout": "single", "cams": [single_cam(p, zoom)]})
            continue

        # plan large (≥ 2 visages)
        if wide_mode == "center":
            plan.append({"t0": t0, "t1": t1, "layout": "center", "cams": [{"slot": "full", "crop": _center_crop(full_wh, sw, sh)}]})
            continue
        if wide_mode == "split":
            plan.append({"t0": t0, "t1": t1, "layout": "split", "cams": split_cams(persons)})
            continue

        # mode speaker : suit le locuteur actif ; segments incertains -> fallback
        by_id = {p["id"]: p for p in persons}
        by_side = {"left": min(persons, key=lambda p: p["cx"]), "right": max(persons, key=lambda p: p["cx"])}
        turn_segs = _turn_segments(turns or [], t0, t1, host_side or fr.get("host_side", ""))
        if turn_segs:
            for sg in turn_segs:
                pieces = _split_long(sg["t0"], sg["t1"], float(fr.max_shot_len), _sentence_breaks(words, sg["t0"], sg["t1"])) if fr.synthetic_cuts else [(sg["t0"], sg["t1"])]
                for i, (a, b) in enumerate(pieces):
                    if i % 2 == 1:
                        plan.append({"t0": a, "t1": b, "layout": "split", "cams": split_cams(persons)})
                    else:
                        plan.append({"t0": a, "t1": b, "layout": "single", "cams": [single_cam(by_side[sg["side"]])]})
            continue
        for seg in shot["segments"]:
            sp = seg.get("speaker")
            conf = seg.get("confidence", 0)
            if sp is None or sp not in by_id or conf < float(fr.speaker_min_confidence):
                if fallback == "split":
                    plan.append({"t0": seg["t0"], "t1": seg["t1"], "layout": "split", "cams": split_cams(persons)})
                else:
                    plan.append({"t0": seg["t0"], "t1": seg["t1"], "layout": "center", "cams": [{"slot": "full", "crop": _center_crop(full_wh, sw, sh)}]})
                continue
            pieces = _split_long(seg["t0"], seg["t1"], float(fr.max_shot_len), _sentence_breaks(words, seg["t0"], seg["t1"])) if fr.synthetic_cuts else [(seg["t0"], seg["t1"])]
            for i, (a, b) in enumerate(pieces):
                # alternance locuteur / écran partagé pour garder du rythme
                if i % 2 == 1 and len(persons) >= 2:
                    plan.append({"t0": a, "t1": b, "layout": "split", "cams": split_cams(persons)})
                else:
                    plan.append({"t0": a, "t1": b, "layout": "single", "cams": [single_cam(by_id[sp])]})

    # nettoyage : fusion des entrées identiques consécutives, arrondis
    cleaned: list[dict] = []
    for e in plan:
        if cleaned and cleaned[-1]["layout"] == e["layout"] and cleaned[-1]["cams"] == e["cams"] and e["layout"] != "single":
            cleaned[-1]["t1"] = e["t1"]
        else:
            cleaned.append(e)
    return _apply_zoom(cleaned, cfg)


def _apply_zoom(plan: list[dict], cfg: Cfg) -> list[dict]:
    fr = cfg.framing
    amt = float(fr.slow_zoom_amount) if fr.slow_zoom else 0.0
    for i, e in enumerate(plan):
        e["t0"] = round(e["t0"], 3)
        e["t1"] = round(e["t1"], 3)
        e["id"] = i + 1
        # alterne zoom-in / zoom-out pour éviter la monotonie
        e["zoom_from"], e["zoom_to"] = (1.0, 1.0 + amt) if i % 2 == 0 else (1.0 + amt, 1.0)
    return plan
