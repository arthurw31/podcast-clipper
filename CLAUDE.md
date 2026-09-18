# Podcast Clipper — guide pour Claude Code

Framework Python qui transforme un épisode de podcast (mp4) en shorts/reels montés
(9:16, 16:9, 1:1) via HyperFrames. Lis [README.md](README.md) pour l'usage complet ;
ce fichier résume ce qu'il faut savoir pour **modifier** le projet sans casser les acquis.

## Point d'entrée utilisateur

Le skill `.claude/skills/podcast-clips/SKILL.md` décrit l'interview à mener (marque, invité, côté animateur,
nombre de clips, formats, style) puis le pipeline. Pour toute demande « fais-moi des clips », suis ce skill.

## Installation sur une machine neuve (« installe tout ce qu'il faut »)

1. Cloner le dépôt si ce n'est pas fait, puis lancer le script : Windows `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`,
   macOS/Linux `bash scripts/setup.sh`. Il installe Python/Node/FFmpeg s'ils manquent (winget / brew), les
   dépendances, crée `.env` et lance `python -m clipper doctor`.
2. Demander à l'utilisateur sa clé Pexels (gratuite, pexels.com/api) et lui faire coller dans `.env`
   (ne jamais l'écrire dans un fichier versionné ni la demander en clair dans le chat si un gestionnaire
   de secrets est disponible). Sans clé, le B-roll est simplement désactivé (`broll.enabled: false`).
3. La sélection des extraits utilise `claude -p` (l'abonnement Claude Code de l'utilisateur) si aucune
   `ANTHROPIC_API_KEY` n'est définie : vérifier que `claude --version` répond.
4. Relancer `python -m clipper doctor` jusqu'à « Tout est prêt », puis suivre le skill `podcast-clips`.
   Le modèle Whisper (~1,6 Go) se télécharge au premier `transcribe`.

## Commandes

```bash
python -m clipper doctor                # vérifie l'installation
python -m clipper run --brand <slug> --input <fichier-dans-brands/slug/episodes/> --guest "…" --company "…" --host-side left|right
python -m clipper transcribe|select|build|posts|render|preview … --brand <slug> --input …   # étapes séparées, cache dans output/
python -m clipper posts --brand <slug> --input … [--episode-url URL] [--guest-role "…"] [--only N] [--force]   # post LinkedIn + description par clip
python -m clipper build|render … --jobs N          # parallélisme (défaut : build.jobs / render.jobs = auto)
python -m clipper new-brand <slug>   # copie brands/_template
npx hyperframes lint|check|snapshot --at 3,10 --no-end -o <dir>   # dans output/<brand>/<ep>/clips/<clip>/<format>/
```

Sous Windows : le package force stdout en UTF-8 (`clipper/__init__.py`) ; dans un script ad hoc,
exporter `PYTHONIOENCODING=utf-8` avant tout `print` contenant des accents ou des flèches.

## Structure

- `brands/<slug>/` : **un dossier par podcasteur/marque** — `brand.yaml` (surcharge de `config/defaults.yaml`),
  `guidelines.md` (brief éditorial injecté tel quel dans le prompt LLM), `posts.md` (brief des posts LinkedIn +
  posts déjà publiés, injecté tel quel dans `clipper/posts.py`), `assets/` (logo, fonts, musique),
  `episodes/` (sources), `references/` (clips existants pour caler le style).
- `config/defaults.yaml` : toutes les options, commentées. `config/presets/` : styles de montage.
  Fusion : defaults ← preset ← brand.yaml ← `format_overrides[<format>]`.
- `clipper/` : `transcribe` (faster-whisper) → `select_clips` (Claude) → `analysis` (plans, visages YuNet, locuteur)
  → `reframe` (plan de caméras) → `captions` → `broll` (Pexels) → `compose` (Jinja → HyperFrames) → `posts`
  (Claude : post LinkedIn + description courte par clip, un seul appel pour l'épisode) → `render`.
- `clipper/templates/clip.html.j2` : LA composition HyperFrames. Respecte le contrat du skill `hyperframes-core`
  (voir « Pièges » ci-dessous) ; valider avec `npx hyperframes lint --json` après toute modification.
- `output/<brand>/<episode>/` : généré, jamais versionné. `clips.json` est éditable à la main puis `build --only N`.

## Règles produit (demandes explicites d'Arthur — ne pas régresser)

1. **Jamais couper une pensée.** Un extrait commence au début d'une phrase et finit sur une fin de phrase
   complète. Mécanisme : le LLM reçoit le transcript **phrase par phrase avec `[début → fin]`** et doit citer
   `start_text` / `end_text` (mots exacts) ; `select_clips.snap_to_quotes` cale les timecodes sur ces mots,
   prolonge si la fin tombe sur un connecteur, et `_snap` borne la marge audio au silence réellement disponible
   (`tail_silence`) pour ne jamais entendre le début de la phrase suivante. Mieux vaut dépasser `max_duration`
   de quelques secondes (tolérance 12 s) qu'une idée tronquée.
2. **Carte de fin avec CTA** vers l'épisode complet, **actif par défaut sur toutes les marques**
   (`outro.cta` dans defaults.yaml), désactivable par marque via `outro.cta.enabled: false`.
3. Le B-roll ne couvre jamais un visage : `compose._pip_box` cherche un espace libre sur toute la durée de
   l'insert ; sinon l'insert passe en plein écran. En écran partagé, les sous-titres se centrent sur la séparation.
4. Chartes : DLTDC = Montserrat ExtraBold Italic, jaune #FFE14D, typewriter (preset `dynamic`).
   AI Corner = style du **monteur AI Partners** (clips de référence dans `brands/ai-corner/references/`) :
   preset `editorial` — sous-titres Metropolis Bold en minuscules, centrés, en bas, sans mot-clé coloré ni
   typewriter, tirets de dialogue ; coupes franches ; pas de B-roll ; carte de fin = cover de la charte
   (fond noir + motif « mountain lines » + logo blanc à gauche + CTA). Bleu #258AF3, **jamais d'italique**.
5. **Clips multi-segments** (`selection.max_segments` > 1) : un clip = accroche + développement + conclusion
   pris à des endroits différents de l'épisode ; `clip["segments"]` (liste ordonnée), `compose` découpe chaque
   segment, les concatène (`media.concat_segments`) et remappe mots/tours de parole/B-roll en temps relatif
   (`abs_to_rel`). `montage.join_transition: cut|flash`.
6. **Un post LinkedIn par clip** (`clipper/posts.py`, commande `posts`, lancée par `run`) : rédigé dans le ton de
   `brands/<slug>/posts.md` (règles + posts réellement publiés par la marque, à ne jamais recopier), porte l'idée
   du clip, n'invente rien qui ne soit dans la transcription de l'extrait, alterne les variantes (idée / preview /
   « nouvel épisode » une seule fois). AI Corner : posts **en anglais**, voix de la page AI Partners, sans hashtags,
   CTA YouTube AI PARTNERS / Spotify / Ausha. Sortie : `output/…/posts/clip_NN_<titre>.md` + champs
   `linkedin_post` / `short_description` dans `clips.json` + `summary.md`.

## Parallélisme (pattern « split → parallèle → agrégation »)

- `select` : si `selection.angles` liste ≥ 2 angles, `select_clips` lance une passe LLM **par angle** en parallèle
  (`ThreadPoolExecutor`, chaque `claude -p` est un process), tague chaque candidat `angle`, cale tous les candidats
  (`snap_to_quotes`), écarte les recouvrements (`_dedupe`, > 40 % du plus court) puis un **jury** (`_jury`, appel
  court sans transcription) choisit et ordonne les n finalistes. `jury: false` = tri par score. Angles définis par
  marque dans `brand.yaml` ; vide = une passe unique (comportement d'origine).
- `build` : `ProcessPoolExecutor` (un processus par clip, `_build_one` dans cli.py : arguments simples, `Brand`
  rechargée dans le fils). **`clipper/__main__.py` garde `if __name__ == "__main__"`** (spawn Windows) — ne pas retirer.
- `render` : `ThreadPoolExecutor` sur des `npx hyperframes render` indépendants. `render.jobs` / `build.jobs` :
  `auto` = cœurs/4 (mesuré : 2 rendus en parallèle ≈ 1,6× plus vite sur 8 cœurs ; plus = contention).
- `posts` reste un appel unique par épisode (le modèle doit voir tous les clips pour varier les structures).
- Ne pas paralléliser `transcribe` (Whisper occupe déjà les cœurs) ni découper le transcript par tranches pour la
  sélection : les clips multi-segments ont besoin de l'épisode entier.

## Pièges connus (déjà résolus — ne pas réintroduire)

- OpenCV ne lit pas les chemins accentués sous Windows → YuNet est chargé depuis un buffer (`analysis._detector`).
- HyperFrames refuse deux `index*.html` racine dans un même dossier → **un dossier projet par format**
  (`clips/<clip>/9x16/`, `16x9/`), `assets/` en lien symbolique/jonction vers `clips/<clip>/assets/`.
- `hyperframes lint` prend un DOSSIER, pas `-c`. `render` : `--output`, `--quality draft|looks|delivery`.
- Contrat HyperFrames : un `<video>` timé ne doit jamais avoir d'ancêtre timé (les wrappers `.slot`/`.cam`/`.pip`
  restent non timés et sont animés par la timeline) ; chaque `<audio>` a un `id` ; une seule timeline GSAP
  `window.__timelines["main"]` ; pas de `transform` CSS initial sur un élément tweené en `x/y/scale`.
- Deux `<video>` identiques (src/start/durée) déclenchent `duplicate_media_discovery_risk` → la caméra du bas
  en écran partagé a `data-media-start` décalé de 0,001 s.
- Whisper coupe « c'est » en « c » + « 'est » → `transcribe.merge_fragments`. Il écrit « iPartners » pour
  « AI Partners » ; il oublie souvent les points sur des passages entiers (d'où les citations `end_text`).
- Le LLM cite parfois les bons mots avec un mauvais timestamp : `snap_to_quotes` cherche la citation près du
  timestamp puis dans tout l'épisode (les mots font foi). La réponse brute est gardée dans `output/…/llm_raw.json`.
- Le master E20 d'AI Corner commence par un teaser déjà sous-titré (0–30 s) : à exclure des sélections
  (consigne dans guidelines.md + `--instructions`).
- Le locuteur actif par heuristique bouche est peu fiable (plans larges 720p) : la vérité vient des tours de
  parole du LLM + `--host-side` (l'animateur change de côté selon l'épisode).
- Pas de GPU sur ce PC : Whisper large-v3-turbo ≈ 0,4× temps réel ; rendu HyperFrames ≈ 4–5× la durée du clip.
- Backend LLM sans clé API : `claude -p --output-format json --tools ""` dans un cwd temporaire (`llm.py`).

## Vérifier une modification du montage

1. `python -m clipper build … --only 1` puis `npx hyperframes lint --json` dans le dossier format (0 erreur attendue ;
   les avertissements `composition_file_too_large` / `timeline_track_too_dense` sont normaux).
2. `npx hyperframes snapshot --at <instants> --no-end -o <dir>` et regarder les images (visages, sous-titres, PiP, outro).
3. Rendu complet seulement ensuite (`render --only 1 --force`).
