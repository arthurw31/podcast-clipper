"""Rédaction des textes de publication de chaque clip (post LinkedIn + description courte).

Entrées : clips.json, transcript.json, le brief `brands/<slug>/posts.md` (règles + posts publiés
servant de référence de ton) et la section « Publication » de guidelines.md.
Sortie  : output/<brand>/<ep>/posts/clip_NN_<titre>.md (un fichier par clip), les champs
`linkedin_post` / `short_description` dans clips.json, et le récap dans summary.md.

Un seul appel LLM pour tous les clips de l'épisode : le modèle voit l'ensemble et varie les
angles / structures d'un post à l'autre (pas six posts « Nouvel épisode »).
"""
from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from .config import Brand
from .llm import ask_json
from .transcribe import words_between

console = Console()

SYSTEM_PROMPT = """Tu es le community manager d'un podcast. Pour chaque clip vidéo (extrait monté d'un épisode)
tu rédiges le texte qui l'accompagne sur les réseaux :

1. `linkedin_post` : le post LinkedIn complet, prêt à coller, qui suit STRICTEMENT le brief de la marque
   (langue, voix, ton, longueur, emojis, hashtags ou non, structure, CTA). Les posts publiés fournis en exemple
   fixent le ton : imite leur rythme et leur structure sans jamais les recopier.
2. `short_description` : la description courte (1–3 lignes, dans la même langue que le post) pour Reels / Shorts /
   TikTok — l'idée du clip en une phrase + l'invité et son entreprise, puis les hashtags si le brief en prévoit.

Règles absolues :
- Le post porte L'IDÉE DU CLIP (ce que dit l'invité dans cet extrait précis), reformulée avec ses mots-clés,
  pas un résumé générique de l'épisode.
- Ne jamais inventer un chiffre, un nom de client, une citation ou un fait absent de la transcription du clip.
  Une citation entre guillemets doit être mot pour mot dans la transcription.
- Varie les structures entre les clips d'un même épisode (accroches, angles, variantes du brief) ; deux posts ne
  doivent pas commencer de la même façon. Si le brief définit une variante « nouvel épisode », ne l'utilise
  qu'une fois, pour le clip n° 1.
- Si une URL d'épisode est fournie, elle apparaît en clair dans le CTA ; sinon applique la consigne du brief
  (« lien en commentaire » ou équivalent). Ne fabrique jamais d'URL.
- Les sauts de ligne du post sont des `\\n` dans la chaîne JSON.
- Réponds UNIQUEMENT avec un objet JSON valide de la forme :
{"posts": [{"index": 1, "variant": "A", "linkedin_post": "…", "short_description": "…"}]}"""


def _clip_text(transcript: dict, clip: dict) -> str:
    """Transcription du clip, segment par segment (les segments viennent d'endroits différents de l'épisode)."""
    parts = []
    for sg in clip.get("segments") or [{"start": clip["start"], "end": clip["end"]}]:
        words = words_between(transcript, float(sg["start"]), float(sg["end"]))
        parts.append(" ".join(w["w"] for w in words).strip())
    return "\n[…]\n".join(p for p in parts if p)


def _publication_rules(guidelines: str) -> str:
    """Section « ## Publication » de guidelines.md (hashtags, longueur…), si elle existe."""
    if "## Publication" not in guidelines:
        return ""
    sect = guidelines.split("## Publication", 1)[1]
    return sect.split("\n## ", 1)[0].strip()


def write_posts(brand: Brand, ep: Path, clips: dict, transcript: dict, episode_url: str = "",
                only: set[int] | None = None, force: bool = False) -> dict:
    cfg = brand.cfg
    pub = cfg.get("publication") or {}
    if not pub.get("enabled", True):
        console.print("[dim]publication.enabled: false → pas de posts[/dim]")
        return clips
    posts_dir = ep / "posts"
    posts_dir.mkdir(parents=True, exist_ok=True)

    targets = [c for c in clips["clips"] if not only or c["index"] in only]
    todo = [c for c in targets if force or not c.get("linkedin_post")]
    if not todo:
        console.print(f"[dim]Posts en cache : {posts_dir}[/dim]")
        _write_files(posts_dir, targets, clips)
        return clips

    brief = brand.posts_brief or "(aucun brief : langue du podcast, ton professionnel, 100–150 mots, CTA vers l'épisode complet)"
    episode_url = episode_url or str(pub.get("episode_url") or "")
    language = str(pub.get("language") or "") or str(cfg.get("language") or transcript.get("language") or "fr")
    guest, company = clips.get("guest", ""), clips.get("company", "")
    clips_txt = []
    for c in todo:
        clips_txt.append(
            f"### Clip {c['index']} — {c.get('title', '')}\n"
            f"- accroche : {c.get('hook_title', '')}\n"
            f"- pourquoi cet extrait : {c.get('why', '')}\n"
            f"- durée : {c.get('duration', 0):.0f}s\n"
            f"- transcription :\n{_clip_text(transcript, c)}\n"
        )
    user = f"""## Brief posts de la marque « {cfg.name} »
{brief}

## Consignes « Publication » des guidelines éditoriales
{_publication_rules(brand.guidelines) or "(aucune)"}

## Contexte
- Podcast : {cfg.get('podcast_name') or cfg.name} · Animateur·ice : {pub.get('host_name') or 'voir brief'}
- Invité : {guest or 'inconnu'} · Rôle : {clips.get('guest_role') or pub.get('guest_role') or 'inconnu'} · Entreprise : {company or 'inconnue'}
- Langue attendue du post : {language} (le brief prime s'il impose une autre langue)
- URL de l'épisode complet : {episode_url or 'non fournie'}
- Plateformes où écouter : {pub.get('channels') or 'voir brief'}
- Nombre de clips : {len(todo)} — un post distinct par clip, structures variées.

## Les clips
{chr(10).join(clips_txt)}
"""
    data = ask_json(SYSTEM_PROMPT, user, model=str(pub.get("llm_model") or cfg.selection.llm_model),
                    backend=str(pub.get("llm_backend") or cfg.selection.llm_backend), max_tokens=8000)
    (ep / "llm_posts_raw.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    by_index = {}
    for p in data.get("posts", []) or []:
        try:
            by_index[int(p.get("index"))] = p
        except (TypeError, ValueError):
            continue
    for c in todo:
        p = by_index.get(c["index"])
        if not p:
            console.print(f"[yellow]Clip {c['index']} : pas de post dans la réponse du modèle[/yellow]")
            continue
        c["linkedin_post"] = str(p.get("linkedin_post", "")).strip()
        c["short_description"] = str(p.get("short_description", "")).strip()
        c["post_variant"] = str(p.get("variant", ""))
    (ep / "clips.json").write_text(json.dumps(clips, ensure_ascii=False, indent=1), encoding="utf-8")
    _write_files(posts_dir, targets, clips)
    console.print(f"[green]{len([c for c in todo if c.get('linkedin_post')])} post(s) rédigé(s)[/green] → {posts_dir}")
    return clips


def _write_files(posts_dir: Path, clips_list: list[dict], clips: dict) -> None:
    from .compose import slugify

    for c in clips_list:
        if not c.get("linkedin_post"):
            continue
        name = f"clip_{c['index']:02d}_{slugify(c.get('title', ''))}.md"
        body = [f"# Clip #{c['index']} · {c.get('title', '')}", "",
                f"Invité : {clips.get('guest', '')} ({clips.get('company', '')}) · durée {c.get('duration', 0):.0f}s",
                "", "## Post LinkedIn", "", c["linkedin_post"], "",
                "## Description courte (Reels / Shorts / TikTok)", "", c.get("short_description", ""), ""]
        (posts_dir / name).write_text("\n".join(body), encoding="utf-8")
