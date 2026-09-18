"""Sous-titres : regroupe les mots en blocs de 1-2 lignes, surligne les mots-clés,
prépare le timing typewriter (caractère par caractère) pour le template.

Sortie (par clip) : liste de groupes
{"id": 3, "start": 4.12, "end": 6.40,
 "lines": [ [ {"text": "JE SUIS LE", "chars": [{"c": "J", "t": 4.12}, …], "hl": false}, … ], […] ]}
Les temps sont relatifs au début du clip.
"""
from __future__ import annotations

import re
import unicodedata

from .config import Cfg


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s)


def _is_highlight(word: str, keywords: set[str]) -> bool:
    n = _norm(word)
    if not n or len(n) < 2:
        return False
    if n in keywords:
        return True
    # tolérance aux flexions : "galère" ~ "galères"
    return any(len(k) >= 5 and (n.startswith(k) or k.startswith(n)) and abs(len(n) - len(k)) <= 2 for k in keywords)


def _split_lines(words: list[dict], max_chars: int, max_lines: int) -> list[list[dict]]:
    """Coupe une liste de mots en ≤ max_lines lignes équilibrées."""
    text_len = sum(len(w["w"]) for w in words) + len(words) - 1
    if text_len <= max_chars or max_lines == 1 or len(words) == 1:
        return [words]
    # cherche le point de coupe qui équilibre le mieux les 2 lignes
    best, best_score = 1, 1e9
    for i in range(1, len(words)):
        l1 = sum(len(w["w"]) for w in words[:i]) + i - 1
        l2 = sum(len(w["w"]) for w in words[i:]) + len(words) - i - 1
        score = abs(l1 - l2) + (0 if words[i - 1]["w"][-1:] in ",;:.?!" else 1.5)
        if l1 <= max_chars * 1.15 and l2 <= max_chars * 1.15 and score < best_score:
            best, best_score = i, score
    return [words[:best], words[best:]]


def _speaker_at(turns: list[dict] | None, t: float) -> str | None:
    if not turns:
        return None
    cur = None
    for tr in turns:
        if float(tr["at"]) <= t + 0.05:
            cur = tr.get("speaker")
        else:
            break
    return cur


def group_words(words: list[dict], clip_start: float, clip_end: float, cfg: Cfg, keywords: list[str],
                turns_rel: list[dict] | None = None) -> list[dict]:
    """`words` : mots avec temps relatifs au clip (s/e) OU absolus (clip_start/clip_end servent alors à recaler).
    `turns_rel` : tours de parole en temps relatifs ([{"at", "speaker"}]) pour les tirets de dialogue."""
    cap = cfg.captions
    kw = {_norm(k) for k in keywords if _norm(k)} if cap.get("highlight_keywords", True) else set()
    max_chars = int(cap.max_chars_per_line)
    max_lines = int(cap.max_lines)
    max_words = int(cap.max_words_per_group)
    budget = max_chars * max_lines
    pause_break = float(cap.pause_break)
    D = clip_end - clip_start

    # mots du clip, temps relatifs
    ws = []
    for w in words:
        if w["e"] <= clip_start or w["s"] >= clip_end:
            continue
        s = max(0.0, w["s"] - clip_start)
        e = min(D, w["e"] - clip_start)
        if e <= s:
            e = s + 0.08
        ws.append({"w": w["w"], "s": round(s, 3), "e": round(e, 3)})

    for w in ws:
        w["spk"] = _speaker_at(turns_rel, w["s"])

    groups: list[list[dict]] = []
    cur: list[dict] = []
    cur_len = 0
    for w in ws:
        gap = (w["s"] - cur[-1]["e"]) if cur else 0.0
        ends_sentence = bool(cur) and cur[-1]["w"][-1:] in ".?!…"
        too_long = cur_len + len(w["w"]) + 1 > budget or len(cur) >= max_words
        speaker_change = bool(cur) and w["spk"] != cur[-1]["spk"]
        if cur and (too_long or gap > pause_break or ends_sentence or speaker_change):
            groups.append(cur)
            cur, cur_len = [], 0
        cur.append(w)
        cur_len += len(w["w"]) + 1
    if cur:
        groups.append(cur)

    # dialogue : une réplique très courte (« Ouais. ») rejoint la réplique suivante de l'autre locuteur
    dialogue_flags: list[bool] = [False] * len(groups)
    if cap.get("dialogue_dashes", False) and turns_rel:
        merged: list[list[dict]] = []
        flags: list[bool] = []
        i = 0
        while i < len(groups):
            g = groups[i]
            nxt = groups[i + 1] if i + 1 < len(groups) else None
            short = (g[-1]["e"] - g[0]["s"]) <= 1.6 and len(g) <= 3
            if nxt and short and nxt[0]["spk"] != g[-1]["spk"] and nxt[0]["s"] - g[-1]["e"] < 1.0 \
                    and sum(len(x["w"]) for x in nxt) + len(nxt) <= max_chars * 1.1:
                merged.append(g + nxt)
                flags.append(True)
                i += 2
                continue
            merged.append(g)
            flags.append(False)
            i += 1
        groups, dialogue_flags = merged, flags

    out = []
    min_step = float(cap.typewriter_min_char_step)
    for gi, g in enumerate(groups):
        start = g[0]["s"]
        # le groupe reste affiché jusqu'au suivant (ou fin du clip)
        end = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else min(D, g[-1]["e"] + 1.2)
        end = max(end, start + float(cap.min_group_duration))
        lines = []
        if dialogue_flags[gi]:
            # une ligne par locuteur, chacune préfixée d'un tiret
            split_at = next(k for k in range(1, len(g)) if g[k]["spk"] != g[k - 1]["spk"])
            raw_lines = [g[:split_at], g[split_at:]]
        else:
            raw_lines = _split_lines(g, max_chars, max_lines)
        for li, line in enumerate(raw_lines):
            lw = []
            for wi, w in enumerate(line):
                text = w["w"].upper() if cap.uppercase else w["w"]
                if dialogue_flags[gi] and wi == 0:
                    text = "- " + text
                n = len(text)
                dur = max(w["e"] - w["s"], min_step * n)
                chars = [{"c": ch, "t": round(w["s"] + dur * (i / max(n, 1)), 3)} for i, ch in enumerate(text)]
                lw.append({"text": text, "s": w["s"], "e": w["e"], "hl": _is_highlight(w["w"], kw), "chars": chars})
            lines.append(lw)
        out.append({"id": gi + 1, "start": round(start, 3), "end": round(min(end, D), 3), "lines": lines})
    return out
