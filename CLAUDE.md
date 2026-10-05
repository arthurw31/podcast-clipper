# Podcast Clipper — guide pour Claude Code

Framework Python qui transforme un épisode de podcast (mp4) en shorts/reels montés
(9:16, 16:9, 1:1) via HyperFrames. Lis [README.md](README.md) pour l'usage complet ;
ce fichier résume ce qu'il faut savoir pour **modifier** le projet sans casser les acquis.

## Point d'entrée utilisateur

Arthur dépose ses fichiers (épisodes, clips de référence, logos, posts) dans `depot/` (non versionné) : les ranger
dans `brands/<marque>/episodes|references|assets|assets/guests/` (voir `depot/README.md`). S'il est vide, regarder
les fichiers récents de `~/Downloads`.

Le skill `.claude/skills/podcast-clips/SKILL.md` décrit le **workflow en 5 étapes** voulu par Arthur (05/10/2026) :
dépôt de l'épisode (4K) → `propose` (~10 passages) → l'humain en choisit 5 + demandes particulières (`pick`,
`find`, `check`) → `build` + `posts` + `render` → livraison des 5 MP4 et des 5 posts. Ne jamais monter avant le
choix humain. Schéma et « quel fichier modifier » : `docs/FRAMEWORK.md` (destiné à l'équipe marketing, qui
installe via « clone … et installe tout ce qu'il faut » dans Claude Code — voir README).

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
python -m clipper propose|pick|find|check … --brand <slug> --input …   # workflow 10 propositions -> 5 choisies (skill)
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
   AI Corner = **shorts du monteur AI Partners** (5 shorts de référence dans `brands/ai-corner/references/dust/`,
   demande d'Arthur du 05/10/2026 : « refaire exactement le même montage ») : preset `aip-short` — **barre de
   logos centrée** en haut (`guest_logo.bar`) : logo de l'entreprise invitée (`assets/guests/<entreprise>.svg|png`)
   + logo AI PARTNERS blanc, même hauteur, même ligne, agrandis jusqu'à `bar_max_width` (marges PNG rognées) ; titre-question (`hook_title`) dans une bulle blanche translucide (`hook.style: pill`,
   Fira Sans Condensed Bold, noir) pendant l'accroche (`hook.duration: auto`, 7,5–11,5 s) ; sous-titres blancs
   type Arial Bold sous la bulle, centrés, 1–3 lignes construites mot à mot (`reveal: word`, `reveal_reflow`,
   `word_fade`), même position en écran partagé (`split_center: false`) ; split sans séparateur ; light leak
   aux raccords (`join_transition: leak`) ; 25–45 s ; pas de B-roll ; + carte de fin de **2 s** sur l'animation
   officielle AI Partners (`assets/outro_anim.mov`, lignes « montagne » qui se dessinent, recadrée au format et
   accélérée ×5 : `outro.background: video`, `media.prepare_outro_video`) + logo blanc + CTA **centrés** (`outro.layout: center`, voile radial). Bleu #258AF3,
   **jamais d'italique**.
   L'ancien style LinkedIn 16:9 (`references/*.mp4`) reste disponible : preset `editorial`.
5. **Clips multi-segments** (`selection.max_segments` > 1) : un clip = accroche + développement + conclusion
   pris à des endroits différents de l'épisode ; `clip["segments"]` (liste ordonnée), `compose` découpe chaque
   segment, les concatène (`media.concat_segments`) et remappe mots/tours de parole/B-roll en temps relatif
   (`abs_to_rel`). `montage.join_transition: cut|flash`.
6. **Un post LinkedIn par clip** (`clipper/posts.py`, commande `posts`, lancée par `run`) : rédigé dans le ton de
   `brands/<slug>/posts.md` (règles + posts réellement publiés par la marque, à ne jamais recopier), porte l'idée
   du clip, n'invente rien qui ne soit dans la transcription de l'extrait. AI Corner : **méthode « post d'un short »**
   déduite des 4 posts publiés pour les shorts Dust (accroche-thèse en 1 ligne — citation, constat ou question —,
   contraste, invité « Prénom Nom, Rôle at Entreprise », développement concret, chute en 1 ligne, CTA 🎬/🎙️ + lien) ;
   anglais, voix de la page AI Partners, sans hashtags. Les formats « nouvel épisode / preview » seulement sur demande. Sortie : `output/…/posts/clip_NN_<titre>.md` + champs
   `linkedin_post` / `short_description` dans `clips.json` + `summary.md`.

## Fluidité des coupes (demande d'Arthur, 05/10/2026 : « moins de cuts, pas trop couper, plus fluide »)

- La détection de plans (`analysis`) voit de FAUSSES coupes dans des plans continus du montage source ; chacune
  recadrait le visage → saccades. `compose._smooth_plan` fusionne toute coupe détectée où la différence d'image
  de part et d'autre est faible (`_frame_diff` < `framing.false_cut_diff`, 15 ; vraie coupe ≈ 50–70, fausse ≈ 1–6).
  Flash < `min_flash` (0,5 s) : fusionné avec un voisin du même plan source seulement ; s'il est en toute fin de
  clip, la carte de fin démarre plus tôt et le masque (`outro_start`).
- Jonction de segments sur la même caméra = jump cut → `_punch_junctions` resserre le 2e morceau
  (`junction_punch: 1.15` dans aip-short), tenu jusqu'à la vraie coupe suivante.
- Peu de coupes ajoutées : `max_shot_len: 10` (AI Corner). Les vraies coupes de la source restent.
- Sous-titres : nouveau bloc à chaque jonction (`group_words(breaks=junctions)`).

## Qualité d'image (demande d'Arthur, 05/10/2026 : « il faut que ce soit en 1080p »)

- Un cadre 9:16 dans un master 1920×1080 ne fait que 608 px de large : agrandi ×1,8 (×2 en punch-in). Pour éviter
  d'empiler les pertes : `cut_segment` encode en CRF 10 (intermédiaire quasi sans perte) ; `media.reframe_video`
  applique le plan de caméras avec FFmpeg (crop + Lanczos + `render.sharpen`) en une vidéo au format de sortie
  (`assets/reframed_<fmt>.mp4`), jouée 1:1 par HyperFrames (`plan_render` = un seul plan plein cadre) ; rendu final
  `--crf 12` et `--video-frame-format png` (sinon images source en JPEG). Désactivé si `framing.slow_zoom` (le
  zoom lent reste animé par la timeline). Rendu ≈ 12× la durée du clip avec PNG.
- `-filter_complex_script` n'existe plus dans FFmpeg 9 : utiliser `-/filter_complex <fichier>`.
- La vraie limite reste la source : AI Partners a accès aux épisodes en 4K (octobre 2026) — toujours partir de la
  4K. `cut_segment` garde jusqu'à `render.max_source_height` (2160) lignes ; un cadre 9:16 y fait 1215 px de large.

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

- Windows (Smart App Control / contrôle des applications) peut bloquer le `chrome-headless-shell` téléchargé par
  HyperFrames (`spawn UNKNOWN`, doctor « Chrome failed ») : ne pas toucher au réglage de sécurité ;
  `render._npx` bascule automatiquement sur Chrome/Edge installés (`fallback_browser`) et `.env` peut fixer
  `HYPERFRAMES_BROWSER_PATH`. Pour un `npx hyperframes snapshot` lancé à la main, exporter cette variable.

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
