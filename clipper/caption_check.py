"""Double contrôle des sous-titres : chaque mot entendu dans le montage doit être sous-titré.

Retour d'Arthur (08/10/2026, short 1 de E22) : « dans les sous-titres il oublie quelques mots des fois, faudrait une
sorte de boucle de double check ». Deux causes mesurées sur ce short :
- la transcription NORMALE « nettoie » la parole : « c'est », « en fait », « Et », « Donc »… dits mais pas écrits ;
- un mot à cheval sur une coupe (« euh » retiré juste dedans) n'était gardé que s'il tenait entier dans un morceau
  (« redéployer », « humaine » disparaissaient) — corrigé dans `compose` (mot gardé du côté où il est le plus entendu).

Boucle : l'audio du montage (assets/source.mp4, exactement ce qu'on entend) est écouté DEUX fois en verbatim, avec un
découpage différent (le 2e décalé de 0,7 s de silence) ; un mot n'est retenu que si les deux écoutes l'entendent
(Whisper invente parfois un mot sur une seule écoute). Puis alignement avec les sous-titres :
- mot entendu absent des sous-titres -> ajouté à l'instant où on l'entend ;
- passage où l'écoute entend plus de mots -> texte de l'écoute, orthographe des sous-titres gardée pour les mots
  communs (noms propres corrigés par `transcribe.apply_corrections`) ;
- mot sous-titré jamais entendu au tout début / à la toute fin -> retiré (« Et » de la phrase suivante, non entendu) ;
- au milieu, un mot sous-titré non entendu est gardé (l'écoute a pu le rater).
"""
from __future__ import annotations

import difflib
import json
import subprocess
from pathlib import Path

import numpy as np

from .media import FFMPEG
from .transcribe import merge_fragments
from .verify import FILLERS, HALLU, PROMPT, _model, _norm


def _listen(audio: np.ndarray, pad: float, cfg=None) -> list[dict]:
    if pad:
        audio = np.concatenate([np.zeros(int(pad * 16000), np.float32), audio])
    segs, _ = _model(cfg).transcribe(audio, language="fr", word_timestamps=True, initial_prompt=PROMPT,
                                     vad_filter=False, condition_on_previous_text=False, beam_size=5)
    out = []
    for s in segs:
        for w in s.words or []:
            txt = w.word.strip()
            if txt and not HALLU.search(txt):
                out.append({"w": txt, "s": round(max(0.0, w.start - pad), 3), "e": round(max(0.0, w.end - pad), 3),
                            "p": float(w.probability)})
    words = merge_fragments(out)
    return [dict(w, n=_norm(w["w"])) for w in words if _norm(w["w"]) and _norm(w["w"]) not in FILLERS]


def heard_words(media: Path, dur: float, cfg=None, cache: Path | None = None) -> list[dict]:
    """Mots entendus par les DEUX écoutes (temps relatifs au montage)."""
    key = f"{Path(media).stat().st_size}:{Path(media).stat().st_mtime_ns}:{dur:.3f}"
    if cache and cache.exists():
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("key") == key:
            return c["words"]
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(media), "-t", f"{dur:.3f}", "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True).stdout
    audio = np.frombuffer(raw, np.float32)
    a, b = _listen(audio, 0.0, cfg), _listen(audio, 0.7, cfg)
    sm = difflib.SequenceMatcher(None, [w["n"] for w in a], [w["n"] for w in b], autojunk=False)
    both = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            both += a[i1:i2]
        elif op == "replace" and i2 - i1 == j2 - j1:   # même nombre de mots : on garde ceux qui se ressemblent
            both += [x for x, y in zip(a[i1:i2], b[j1:j2])
                     if difflib.SequenceMatcher(None, x["n"], y["n"]).ratio() >= 0.75]
    if cache:
        cache.write_text(json.dumps({"key": key, "words": both}, ensure_ascii=False), encoding="utf-8")
    return both


def reconcile(caps: list[dict], heard: list[dict], bounds: list[float] | None = None,
              edge: float = 0.4) -> tuple[list[dict], list[str]]:
    """Sous-titres (mots {w, s, e}) corrigés d'après ce qui est entendu ; renvoie (mots, notes).
    Ordre = celui de l'écoute ; horaires = ceux de l'écoute (mesurés sur le montage lui-même, alors que la
    transcription normale est en avance de ~0,2 s) ; orthographe des sous-titres gardée pour les mots communs.
    `bounds` : débuts/fins des morceaux du montage — un mot sous-titré non entendu tout près d'un raccord (bout de la
    phrase suivante à peine audible) est retiré."""
    if not heard:
        return caps, ["écoute vide : sous-titres inchangés"]
    bounds = bounds or []
    ca = [_norm(w["w"]) for w in caps]
    he = [w["n"] for w in heard]
    t_first, t_last = heard[0]["s"], heard[-1]["e"]
    out, notes = [], []
    timed = lambda c, h: {"w": c["w"], "s": h["s"], "e": h["e"]}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ca, he, autojunk=False).get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            out += [timed(c, h) for c, h in zip(caps[i1:i2], heard[j1:j2])]
        elif op == "insert":
            add = [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in heard[j1:j2]]
            out += add
            notes.append(f"+ « {' '.join(w['w'] for w in add)} » à {add[0]['s']:.2f} s")
        elif op == "delete":
            for w in caps[i1:i2]:
                if w["s"] < t_first - 0.05 or w["s"] > t_last - 0.05 or any(abs(w["s"] - x) <= edge for x in bounds):
                    notes.append(f"- « {w['w']} » à {w['s']:.2f} s (pas entendu)")
                else:
                    out.append(dict(w))
        else:                                                     # l'écoute entend plus (ou moins) de mots
            block = []
            for h in heard[j1:j2]:
                same = next((c for c in caps[i1:i2] if difflib.SequenceMatcher(None, _norm(c["w"]), h["n"]).ratio() >= 0.8), None)
                block.append({"w": same["w"] if same else h["w"], "s": h["s"], "e": h["e"]})
            notes.append(f"~ « {' '.join(c['w'] for c in caps[i1:i2])} » -> « {' '.join(x['w'] for x in block)} »"
                         f" à {block[0]['s']:.2f} s")
            out += block
    for k in range(1, len(out)):                                  # horaires croissants (mots gardés sans écoute)
        if out[k]["s"] < out[k - 1]["s"]:
            out[k]["s"] = out[k - 1]["s"]
        out[k]["e"] = max(out[k]["e"], out[k]["s"] + 0.05)
    return out, notes


def missing(caps: list[dict], heard: list[dict]) -> list[str]:
    """Contrôle final : mots entendus (deux écoutes) toujours absents des sous-titres."""
    ca = [_norm(w["w"]) for w in caps]
    he = [w["n"] for w in heard]
    miss = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ca, he, autojunk=False).get_opcodes():
        if op == "insert" or (op == "replace" and j2 - j1 > i2 - i1):
            miss.append(" ".join(w["w"] for w in heard[j1:j2]))
    return miss
