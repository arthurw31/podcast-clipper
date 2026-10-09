# Architecture technique

Ce document explique **comment le framework fonctionne sous le capot** : quels programmes tournent, dans quel
ordre, quels fichiers ils produisent. Pour **utiliser** l'outil, voir le guide [FRAMEWORK.md](FRAMEWORK.md).

## 1. Les briques

```mermaid
flowchart TB
    subgraph CC["Claude Code (app desktop)"]
        SK["Skill podcast-clips<br/>.claude/skills/podcast-clips/SKILL.md<br/>pilote les étapes, parle à l'utilisateur"]
    end

    subgraph PY["Package Python clipper/ (python -m clipper …)"]
        CLI["cli.py<br/>commandes : rushes, fetch, transcribe, titles, propose, pick, polish, qa,<br/>build, preview, render, posts, episode-plan, episode-render,<br/>thumbnail, thumbnail-template, doctor"]
        CFG["config.py<br/>fusion de la configuration"]
        TR["transcribe.py"]
        SEL["select_clips.py"]
        LLM["llm.py"]
        AN["analysis.py"]
        RF["reframe.py"]
        CAP["captions.py"]
        CO["compose.py + templates/clip.html.j2"]
        MED["media.py"]
        REN["render.py"]
        PO["posts.py"]
        MC["multicam.py<br/>rushs 3 caméras"]
        IG["ingest.py<br/>rushs déposés : rôles, synchro"]
        CHK["verify.py · fillers.py<br/>caption_check.py · qa.py"]
        EPI["episode.py · diarize.py<br/>épisode complet + teaser"]
        TH["thumbnail.py · canva_template.py<br/>miniatures"]
    end

    subgraph EXT["Outils installés sur le PC"]
        WH["faster-whisper<br/>(modèle large-v3-turbo)"]
        CL["commande claude -p<br/>(abonnement Claude Pro)"]
        CV["OpenCV + YuNet<br/>(détection de visages)"]
        FF["FFmpeg"]
        HF["HyperFrames CLI (Node)<br/>+ Chrome headless"]
    end

    SK -->|lance| CLI
    CLI --> CFG
    CLI --> TR & SEL & CO & REN & PO
    TR --> WH
    SEL --> LLM
    PO --> LLM
    LLM --> CL
    CLI --> CHK & EPI & TH & IG
    IG --> CV & FF
    CO --> AN & RF & CAP & MED & MC
    EPI --> FF & HF
    TH --> LLM & CV
    AN --> CV
    AN --> FF
    MED --> FF
    REN --> HF
```

| Brique | Rôle |
| --- | --- |
| **Skill** `podcast-clips` | Mode d'emploi lu par Claude Code : enchaîne les commandes, pose les questions, vérifie, livre. Ne contient pas de code. |
| `cli.py` | Point d'entrée `python -m clipper <commande>`. Chaque étape lit et écrit des fichiers dans `output/` (reprise possible à tout moment). |
| `config.py` | Fusionne `config/defaults.yaml` ← `config/presets/<preset>.yaml` ← `brands/<marque>/brand.yaml` ← surcharges par format. |
| `transcribe.py` | Whisper sur CPU : texte mot à mot avec horodatage, découpé en phrases. |
| `select_clips.py` + `llm.py` | Envoie la transcription (phrase par phrase, avec horaires) et `guidelines.md` à Claude ; 3 lectures en parallèle (une par angle) puis un jury. Cale chaque passage sur les mots cités. |
| `analysis.py` | Détecte les changements de plan et les visages (position, taille, qui parle). |
| `reframe.py` | Calcule le plan de caméras vertical : quel cadrage à quel moment (gros plan, écran partagé, punch-in). |
| `captions.py` | Regroupe les mots en blocs de sous-titres (3 lignes, mot à mot). |
| `compose.py` | Assemble les passages, lisse les coupes, recadre avec FFmpeg, écrit la composition HTML HyperFrames (sous-titres, bulle-titre, logos, light leak, carte de fin). |
| `media.py` | Toutes les opérations FFmpeg : découpe, concaténation, recadrage Lanczos, animation de fin. |
| `render.py` | Rendu de la composition en MP4 par HyperFrames (Chrome headless invisible), lint, captures. `preview --mp4` produit l'aperçu brouillon (12 i/s) validé par l'équipe ; `preview --open` lance le Studio pour une retouche fine. |
| `ingest.py` | Commande `rushes` : prend les 3 rushs + le WAV déposés dans `depot/`, écarte un fichier tronqué, reconnaît le plan large (2 visages) et l'animateur (empreinte de visage SFace comparée à `assets/host_face.jpg`), mesure le décalage du micro par corrélation avec le son des caméras, range et renomme d'après l'invité, écrit `multicam.json` (avec le côté de l'animateur dans le plan large). |
| `multicam.py` | Rushs 3 caméras : gros plan de celui qui parle, écran partagé quand l'autre écoute (et seulement s'il est calme), son du micro. |
| `verify.py`, `fillers.py`, `caption_check.py`, `qa.py` | Contrôles « à l'oreille » : vraies fins de phrase, « euh » retirés sans perdre un mot, sous-titres écoutés deux fois, rapport OK / ATTENTION / ÉCHEC avant chaque aperçu et après chaque rendu. |
| `episode.py`, `diarize.py` | Épisode complet : dérushage par Claude, qui parle (empreintes vocales), liste de plans façon monteur, teaser scripté, chapitres ; corps rendu en FFmpeg, teaser en HyperFrames. |
| `titles.py` | Titre de l'épisode (un seul, pour YouTube et la vignette) : Claude écrit ~12 candidats d'après `brands/<m>/titles.md` (relevé des 16 titres publiés), chacun adossé à un passage réel de l'épisode ; contrôles automatiques (longueur, chiffres et noms présents dans la transcription, pas de nom d'invité, pas de copie d'un titre publié ou refusé) ; jury qui garde 5 titres de formules variées ; planche « chaîne YouTube » ; `--pick N` = validation par l'équipe marketing. |
| `thumbnail.py`, `canva_template.py` | Miniatures YouTube : photos où les deux sourient en se regardant (détecteur d'expressions + jury visuel Claude), détourage, composition sur le gabarit Canva importé du PPTX de l'équipe. |
| `posts.py` | Rédige le post LinkedIn de chaque short avec `posts.md` (méthode + exemples). |

## 2. Le parcours d'un épisode, fichier par fichier

```mermaid
flowchart TD
    EP[("brands/marque/episodes/<br/>rushs 3 caméras + micro (multicam.json)<br/>ou épisode monté .mp4")]

    EP -->|transcribe<br/>faster-whisper| T["transcript.json<br/>mots + horaires"]
    T -->|propose<br/>Claude × 3 angles + jury| CA["candidates.json / .md<br/>~10 passages"]
    CA -->|pick<br/>choix humain| CJ["clips.json<br/>5 shorts : passages, titre, tours de parole"]
    CJ -->|find / check<br/>ajustements à la main| CJ

    CJ -->|polish : tighten, verify, fillers,<br/>réactions vides, mots-clés| B
    subgraph B["build — un dossier par short : clips/clip_NN_titre/"]
        direction TB
        S1["assets/source.mp4<br/>passages découpés et assemblés, plan par plan<br/>dans les rushs (FFmpeg, quasi sans perte)"]
        S2["analysis.json<br/>plans + visages"]
        S3["plan de caméras<br/>(reframe + lissage des coupes)"]
        S4["assets/reframed_9x16.mp4<br/>recadrage vertical FFmpeg Lanczos"]
        S5["assets/outro_9x16.mp4<br/>animation de fin accélérée"]
        S6["9x16/index.html<br/>composition HyperFrames"]
        S1 --> S2 --> S3 --> S4 --> S6
        S5 --> S6
    end

    B -->|qa puis preview --mp4| PV["Aperçu MP4 (apercus/)<br/>+ qa --render (planche d'images)<br/>validation par l'équipe"]
    PV -->|retouche : clips.json puis polish --only N| CJ
    PV -->|validé : render + qa --render<br/>HyperFrames + Chrome| R["renders/clip_NN_titre_9x16.mp4<br/>1080×1920"]
    CJ -->|posts<br/>Claude + posts.md| P["posts/clip_NN_titre.md<br/>post LinkedIn + description courte"]
    R --> OUT["Livraison : 5 MP4 + 5 posts<br/>+ summary.md"]
    P --> OUT
```

L'épisode complet (`episode-plan` → `episode_plan.md` à relire → `episode-render --proxy` → `episode-render`, sorties
dans `episode/`) et les miniatures (`thumbnail` → `miniatures/`) partent de la même transcription : voir le schéma
d'ensemble du [README](../README.md).

Tout est rangé dans `output/<marque>/<episode>/` (jamais versionné). Chaque fichier intermédiaire est
lisible et modifiable : on peut corriger `clips.json` à la main puis relancer seulement `build` et `render`
pour le short concerné (`--only N`).

## 3. Ce qui se passe pendant le montage d'un short

```mermaid
sequenceDiagram
    autonumber
    participant SK as Skill (Claude Code)
    participant CO as compose.py
    participant FF as FFmpeg
    participant AN as analysis.py
    participant HF as HyperFrames

    SK->>CO: build --only N
    CO->>FF: découpe chaque passage dans les rushs (gros plan / écran partagé), assemble
    CO->>AN: plans et visages sur l'assemblage
    AN-->>CO: changements de plan, positions des visages
    CO->>CO: plan de caméras + lissage<br/>(fausses coupes fusionnées, aucun zoom pour AI Corner)
    CO->>FF: applique le plan : crop + agrandissement Lanczos → reframed_9x16.mp4
    CO->>FF: animation de fin AI Partners recadrée (carte de fin de 7 s)
    CO->>CO: sous-titres, bulle-titre, barre de logos → index.html
    CO->>HF: lint (contrôle de la composition)
    SK->>HF: qa puis preview --mp4 (aperçu brouillon 12 i/s)
    HF-->>SK: aperçu MP4 validé par l'utilisateur
    SK->>HF: render (une seule fois)
    HF-->>SK: MP4 1080×1920 (CRF 12)
```

## 4. Les garde-fous intégrés

| Règle | Où c'est codé |
| --- | --- |
| Ne jamais couper une pensée : coupes calées sur les premiers / derniers mots cités, marge audio limitée au silence disponible | `select_clips.snap_to_quotes`, `_snap` ; contrôle `clipper check` |
| Pas de saccades : les fausses coupes sont détectées par différence d'image et fusionnées ; raccord entre passages = coupe nette (AI Corner : aucun zoom, `junction_punch: 1.0`) | `compose._smooth_plan`, `_frame_diff`, `_punch_junctions` |
| Netteté : recadrage par FFmpeg depuis la 4K, intermédiaires CRF 10, images PNG, rendu CRF 12 | `media.reframe_video`, `cut_segment`, `config/defaults.yaml` → `render` |
| Posts sans invention : seule la transcription du short est fournie à Claude, consigne de ne rien ajouter | `posts.py` |
| Navigateur bloqué par Windows : repli automatique sur Chrome / Edge installés | `render.fallback_browser` |
| Installation vérifiée | `python -m clipper doctor` |

## 5. Où modifier quoi (pour un développeur)

- **Le look** (bulle, sous-titres, logos, carte de fin) : `clipper/templates/clip.html.j2` + les options de
  `config/presets/aip-short.yaml`. Valider avec `npx hyperframes lint` dans le dossier d'un short.
- **La sélection** : le prompt système dans `select_clips.py`, les angles dans `brand.yaml` → `selection.angles`.
- **Les posts** : le prompt dans `posts.py`, la méthode dans `brands/<marque>/posts.md`.
- **Le cadrage** : `reframe.py` (plan de caméras) et `compose._smooth_plan` (lissage).
- Les règles produit à ne pas casser et les pièges connus sont listés dans [CLAUDE.md](../CLAUDE.md).
