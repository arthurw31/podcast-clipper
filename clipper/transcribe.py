"""Transcription mot à mot avec faster-whisper (local, CPU ou GPU).

Sortie : transcript.json
{
  "language": "fr",
  "duration": 3600.2,
  "segments": [ {"id": 0, "start": 1.2, "end": 4.8, "text": "…",
                 "words": [{"w": "Bonjour", "s": 1.2, "e": 1.6, "p": 0.98}, …]} ]
}
"""
from __future__ import annotations

import difflib
import json
import re
import time
from pathlib import Path

from rich.console import Console

from .config import Cfg
from .media import extract_audio_wav, probe

console = Console()


def _device_and_compute(cfg: Cfg) -> tuple[str, str]:
    device = cfg.transcribe.get("device", "auto")
    compute = cfg.transcribe.get("compute_type", "auto")
    if device == "auto":
        try:
            import torch  # noqa

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


def vocabulary(cfg: Cfg, names: list[str] | None = None) -> list[str]:
    """Noms propres et termes à faire reconnaître par Whisper : invité + entreprise (passés à la commande)
    + lexique de la marque (`transcribe.vocabulary` dans brand.yaml)."""
    out: list[str] = []
    for v in [*(names or []), *(cfg.transcribe.get("vocabulary") or [])]:
        v = str(v).strip()
        if v and v not in out:
            out.append(v)
    return out


def apply_corrections(data: dict, cfg: Cfg, names: list[str] | None = None) -> int:
    """Corrige les noms propres mal transcrits, mot par mot (horaires inchangés) :
    1. la table `transcribe.corrections` de la marque (ex. « iPartners » -> « AI Partners ») ;
    2. les mots à majuscule très proches d'un nom du vocabulaire (« Amodry » -> « Amaudry », « Mondo » -> « Mendo »).
    Idempotent : appliqué après la transcription et à chaque relecture du cache."""
    table = {str(k): str(v) for k, v in (cfg.transcribe.get("corrections") or {}).items()}
    # rapprochement approximatif seulement pour les noms (≥ 5 lettres, pas les sigles) : « Mais » ne doit
    # jamais devenir « MAIF », ni « Comme » « COMEX » — les sigles passent par la table `corrections`
    targets = []
    for v in vocabulary(cfg, names):
        for tok in re.findall(r"[A-ZÀ-Ÿ][\wÀ-ÿ'-]{4,}", v):
            if not tok.isupper():
                targets.append(tok)
    n = 0
    for seg in data.get("segments", []):
        changed = False
        for w in seg.get("words", []):
            m = re.match(r"^([^\wÀ-ÿ]*)([\wÀ-ÿ'’-]+)([^\wÀ-ÿ]*)$", w["w"])
            if not m:
                continue
            pre, core, post = m.groups()
            new = table.get(core)
            if new is None and targets and core[:1].isupper() and not core.isupper() and len(core) >= 5                     and core not in targets:
                best = max(targets, key=lambda t: difflib.SequenceMatcher(None, core.lower(), t.lower()).ratio())
                if difflib.SequenceMatcher(None, core.lower(), best.lower()).ratio() >= 0.7:
                    new = best
            if new and new != core:
                w["w"] = pre + new + post
                n += 1
                changed = True
        if changed:
            seg["text"] = " ".join(w["w"] for w in seg["words"])
    return n


def transcribe(video: Path, out_json: Path, cfg: Cfg, work_dir: Path, force: bool = False,
               names: list[str] | None = None) -> dict:
    if out_json.exists() and not force:
        console.print(f"[dim]Transcription en cache : {out_json.name}[/dim]")
        data = json.loads(out_json.read_text(encoding="utf-8"))
        if apply_corrections(data, cfg, names):
            out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return data

    from faster_whisper import WhisperModel

    device, compute = _device_and_compute(cfg)
    model_name = cfg.transcribe.get("model", "large-v3-turbo")
    console.print(f"[bold]Transcription[/bold] · modèle {model_name} · {device}/{compute}")

    wav = extract_audio_wav(video, work_dir / "audio16k.wav")
    info = probe(video)

    t0 = time.time()
    model = WhisperModel(model_name, device=device, compute_type=compute)
    vocab = vocabulary(cfg, names)
    if vocab:
        console.print(f"  vocabulaire donné à Whisper : {', '.join(vocab)}")
    segments_iter, tinfo = model.transcribe(
        str(wav),
        language=cfg.get("language") or None,
        beam_size=int(cfg.transcribe.get("beam_size", 5)),
        word_timestamps=True,
        vad_filter=bool(cfg.transcribe.get("vad", True)),
        vad_parameters={"min_silence_duration_ms": 400},
        condition_on_previous_text=False,
        # indices d'orthographe pour les noms propres, appliqués à chaque fenêtre de 30 s
        hotwords=", ".join(vocab) if vocab else None,
    )

    segments = []
    last_pct = -1
    for i, seg in enumerate(segments_iter):
        words = []
        for w in seg.words or []:
            txt = w.word.strip()
            if not txt:
                continue
            words.append({"w": txt, "s": round(w.start, 3), "e": round(w.end, 3), "p": round(w.probability, 3)})
        words = merge_fragments(words)
        if not words:
            continue
        segments.append({
            "id": i,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": words,
        })
        pct = int(100 * seg.end / max(info["duration"], 1))
        if pct // 5 != last_pct // 5:
            console.print(f"  … {pct:3d}%  ({seg.end/60:5.1f} min)  {time.time()-t0:5.0f}s", highlight=False)
            last_pct = pct

    data = {
        "language": tinfo.language,
        "duration": info["duration"],
        "model": model_name,
        "segments": segments,
    }
    fixed = apply_corrections(data, cfg, names)
    if fixed:
        console.print(f"  {fixed} nom(s) propre(s) corrigé(s) automatiquement")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print(f"[green]Transcription terminée[/green] · {len(segments)} segments · {time.time()-t0:.0f}s")
    return data


def merge_fragments(words: list[dict]) -> list[dict]:
    """Whisper coupe parfois « c'est » en « c » + « 'est » ou « parents-prof » en deux :
    on recolle les fragments qui commencent par une apostrophe ou un trait d'union."""
    out: list[dict] = []
    for w in words:
        txt = w["w"]
        if out and (txt[:1] in "'’-" or out[-1]["w"][-1:] in "'’-") and len(out[-1]["w"]) <= 12:
            prev = out[-1]
            prev["w"] = prev["w"] + txt
            prev["e"] = max(prev["e"], w["e"])
            prev["p"] = min(prev["p"], w["p"])
            continue
        out.append(dict(w))
    return out


def all_words(transcript: dict) -> list[dict]:
    return [w for seg in transcript["segments"] for w in seg["words"]]


def words_between(transcript: dict, start: float, end: float) -> list[dict]:
    return [w for w in all_words(transcript) if w["s"] >= start - 0.05 and w["e"] <= end + 0.05]


SENTENCE_END = ".?!…"
# mots qui ne peuvent pas clore une pensée (on ne coupe jamais juste après)
CONNECTORS = {"et", "ou", "mais", "donc", "car", "que", "qui", "qu", "de", "du", "des", "à", "au", "aux", "en", "le", "la",
              "les", "un", "une", "d", "l", "parce", "c'est-à-dire", "puis", "alors", "quand", "si", "comme", "pour", "sur",
              "dans", "avec", "sans", "par", "chez", "vers", "où", "dont", "ce", "cette", "ces", "se", "ne", "y", "on", "je",
              "tu", "il", "elle", "nous", "vous", "ils", "elles", "c'est", "est", "sont", "a", "ont", "très", "plus", "moins"}


def _is_connector(word: str) -> bool:
    core = word.rstrip(".?!…,;:").lower().replace("’", "'")
    if "'" in core:  # « d'un », « qu'il » -> on juge le dernier morceau
        core = core.rsplit("'", 1)[-1]
    return core in CONNECTORS


def is_terminal(sent: dict) -> bool:
    """Vrai si la phrase se termine sur une fin nette (ponctuation finale) ou une vraie pause après un mot plein."""
    return sent.get("terminal", "none") in ("hard", "soft")


def sentences(transcript: dict, max_pause: float = 0.6, max_len: float = 30.0) -> list[dict]:
    """Découpe le transcript en phrases. Chaque phrase porte `terminal` :
    - "hard" : se termine sur . ? ! (et pas sur un connecteur)
    - "soft" : pause > max_pause après un mot plein (Whisper oublie souvent le point)
    - "none" : coupe technique (phrase trop longue, à une virgule)

    C'est l'unité de base de la sélection : un extrait commence au début d'une phrase et finit
    à la fin d'une phrase "hard" ou "soft", jamais au milieu.
    """
    out: list[dict] = []
    cur: list[dict] = []

    def flush(kind: str) -> None:
        out.append({"start": cur[0]["s"], "end": cur[-1]["e"], "text": " ".join(x["w"] for x in cur),
                    "words": list(cur), "terminal": kind})

    for w in all_words(transcript):
        if cur:
            gap = w["s"] - cur[-1]["e"]
            last = cur[-1]["w"]
            connector = _is_connector(last)
            if last[-1:] in SENTENCE_END and not connector:
                flush("hard"); cur = []
            elif gap > max_pause and not connector:
                flush("soft"); cur = []
            elif w["e"] - cur[0]["s"] > max_len and last[-1:] in ",;:" and not connector:
                flush("none"); cur = []
        cur.append(w)
    if cur:
        flush("hard" if cur[-1]["w"][-1:] in SENTENCE_END else "soft")
    return out


def _ts(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:04.1f}"


def to_timed_text(transcript: dict) -> str:
    """Transcript pour le LLM : une ligne par PHRASE `[début → fin] texte`.

    Le modèle choisit un extrait en donnant `start` = début de la 1re phrase et `end` = FIN (→) de la dernière.
    """
    return "\n".join(f"[{_ts(p['start'])} → {_ts(p['end'])}] {p['text']}" for p in sentences(transcript))
