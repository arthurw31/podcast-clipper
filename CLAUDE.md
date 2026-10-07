# Podcast Clipper — guide pour Claude Code

Framework Python qui transforme un épisode de podcast (mp4) en shorts/reels montés
(9:16, 16:9, 1:1) via HyperFrames. Lis [README.md](README.md) pour l'usage complet ;
ce fichier résume ce qu'il faut savoir pour **modifier** le projet sans casser les acquis.

## Point d'entrée utilisateur

Arthur dépose ses fichiers (épisodes, clips de référence, logos, posts) dans `depot/` (non versionné) : les ranger
dans `brands/<marque>/episodes|references|assets|assets/guests/` (voir `depot/README.md`). S'il est vide, regarder
les fichiers récents de `~/Downloads`. Depuis E22 (06/10/2026), Arthur envoie surtout un **lien Dropbox** vers les
rushs (3 caméras 1080p de 13–14 Go + 1 WAV) : `python -m clipper fetch "<lien>" --dest brands/<marque>/episodes/rushes/<E>`
(jamais via le navigateur : coupure vers 50 min, fichier tronqué gardé sous son nom final).

Le skill `.claude/skills/podcast-clips/SKILL.md` décrit le **workflow en 5 étapes** voulu par Arthur (05/10/2026) :
dépôt de l'épisode (4K) → `propose` (~10 passages) → l'humain en choisit 5 + demandes particulières (`pick`,
`find`, `check`) → `build` + **`preview`** (aperçu instantané, retouches par `build --only N`) + `posts` → validation
→ `render` une seule fois → livraison des 5 MP4 et des 5 posts. Ne jamais monter avant le choix humain, ne jamais
rendre avant la validation sur l'aperçu. Schéma et « quel fichier modifier » : `docs/FRAMEWORK.md` (destiné à l'équipe marketing, qui
installe via « clone … et installe tout ce qu'il faut » dans Claude Code — voir README).

## Installation sur une machine neuve (« installe tout ce qu'il faut »)

1. Cloner le dépôt si ce n'est pas fait, puis lancer le script : Windows `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`,
   macOS/Linux `bash scripts/setup.sh`. Il installe Python/Node/FFmpeg s'ils manquent (winget / brew), les
   dépendances, crée `.env` et lance `python -m clipper doctor`.
2. Clé Pexels **facultative** (B-roll seulement ; AI Corner n'en utilise pas — le doctor la signale sans bloquer). Si
   une marque a du B-roll : demander la clé (gratuite, pexels.com/api) et la faire coller dans `.env`
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
python -m clipper preview … [--clip all|N] [--stop]   # aperçu instantané (Studio en arrière-plan, ports 3002+), validé AVANT render
python -m clipper new-brand <slug>   # copie brands/_template
python -m clipper fetch "<lien Dropbox>" [--dest dossier] [--skip MIC]   # rushs : reprise auto, taille exacte vérifiée
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
   Arimo Bold (= Arial, embarquée via `fonts.captions_alt`, sinon HyperFrames la remplace par Inter) 68 px + trait
   épaissi 1,6 px + halo sombre marqué (retour de l'équipe, 06/10/2026 : « comme DUST, plus gros, bords ombrés ») sous la bulle, centrés, 1–3 lignes construites mot à mot (`reveal: word`, `reveal_reflow`,
   `word_fade`), même position en écran partagé (`split_center: false`) ; split sans séparateur ; light leak
   aux raccords (`join_transition: leak`) ; 25–45 s ; pas de B-roll ; + carte de fin de **3,5 s** sur l'animation
   officielle AI Partners (`assets/outro_anim.mov`, lignes « montagne » qui se dessinent, recadrée au format et
   accélérée ×2,9 — ×5 sur 2 s jugé trop rapide, 06/10/2026 : `outro.background: video`, `media.prepare_outro_video`) + logo blanc + CTA **centrés** (`outro.layout: center`, voile radial). Bleu #258AF3,
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

## Rushs multicam (E22, 06/10/2026 : l'équipe livre 3 caméras + 1 WAV au lieu de l'épisode monté)

- `clipper/multicam.py` : si `brands/<m>/episodes/<ep>.multicam.json` existe, `compose` découpe chaque passage avec
  `cut_multicam` (gros plan de la personne qui parle, d'après les `turns` de clips.json — la vérité du pipeline ; coupe
  0,15 s avant la prise de parole, plans ≥ 2 s) au lieu de `cut_segment`. `<ep>.mp4` = lien dur vers le plan large
  (probe/analyse), `cams` = {host, guest, wide}, audio = WAV du micro (`audio_delay` mesuré par corrélation : 0,037 s).
  Les caméras sont déjà synchronisées entre elles (même durée à l'image près).
- Le signal bouche des gros plans (`multicam.speaker_turns`) est trop bruité sur 52 min (net pour l'invité, faux
  pour l'animateur) : ne pas s'en servir seul ; les `turns` relus font foi. Le micro est mono (L = R) : pas de
  séparation des voix par canal.
- Rendu 1080p seulement (rushs 1080p) ; coût ≈ 3 min de découpe par minute de short (3 flux H.264 lus).

## Épisode complet depuis les rushs (demande d'Arthur, 07/10/2026 : « monte le podcast en entier + le teaser »)

`python -m clipper episode-plan …` puis `episode-render … --proxy` (aperçu 540p, ~15 min) -> validation -> `episode-render`
(1080p). Fichiers : `clipper/episode.py`, `clipper/diarize.py`, section `teaser:` de brand.yaml. Sorties :
`output/<m>/<ep>/episode_plan.md` (à relire : coupes, teaser, % de plans), `episode/<ep>_episode[_apercu].mp4`,
`episode/description_youtube.md` (titre + chapitres aux temps du montage).
- Style mesuré sur les montages du monteur (E20 MAIF, E21 Mendo — même studio que E22) : plan médian ≈ 10 s ;
  gros plans 76 %, large 14 %, écran partagé (2 gros plans côte à côte, animateur du côté où il est dans le large) 10 % ;
  sur la personne qui parle 97 % du temps ; respiration large/split (~7 s) toutes les 15–24 s dans les longues
  réponses ; ~1 réaction de 1,5 s par minute sur l'écoutant. `build_edl` reproduit ces proportions (E22 : 78/11/10 %).
- Dérushage par le LLM (`PLAN_PROMPT`) : dernière prise de l'intro, au revoir, coupes hors antenne / question reposée
  (citations calées sur les mots) ; jonction de dérushage = changement de plan forcé (jamais de jump cut).
- Qui parle : `diarize` = empreintes vocales WeSpeaker ResNet34 (ONNX, `models/`, téléchargé au 1er usage) + k-moyennes
  à 2 voix, nommées par l'intro de l'animateur ; ~2 min pour 52 min, exact sur les échanges questions/réponses.
  Pas de sherpa-onnx (DLL bloquée par le contrôle des applications Windows) ni speechbrain (pas de torchaudio pour
  torch 2.14). Changements d'orateur recalés sur le plus grand silence voisin (`_refine_boundaries`).
- Rendu du corps : un morceau H.264 par plan (`-frames:v` exact, 24 i/s, `-video_track_timescale 12288`), concat sans
  réencodage ; audio = micro sur les mêmes images (aucune dérive) ; volume normalisé sur tout l'épisode (-16 LUFS :
  E22, le micro de l'invité baisse de 2,5 dB entre 26 et 41 min). Teaser = clip HyperFrames 16:9 (sous-titres de la
  charte) via `teaser_brand` ; fin = animation AI Partners + logo (4 s), comme E20.
- E21 n'a ni teaser ni logo incrusté ; E20 a un teaser (~55 s, 4–5 phrases de l'invité) + sting logo : c'est ce
  modèle qui est reproduit.

## « Euh » et blancs : `tighten` (demande d'Arthur, 07/10/2026 : « enlève les euh, sans couper trop, pas saccadé »)

- Whisper n'écrit jamais les « euh » : ils sont dans les trous entre mots. `python -m clipper tighten` (clipper/tighten.py)
  mesure l'énergie du micro dans chaque trou ≥ 0,5 s, ne retire que les retraits ≥ 0,6 s (en dessous = respiration
  normale, retirer ferait saccadé), coupe dans des creux en gardant ~0,15 s, et crée un nouveau segment (zoom de
  jonction + nouveau bloc de sous-titres). Sauvegarde : `clips_before_tighten.json`. À lancer après `pick`/`check`, avant `build`.
- Limite connue : un « euh » collé au mot suivant (aucun creux entre les deux) n'est pas retiré.
- Vérifier à l'oreille les raccords (l'agent ne peut pas écouter) : le dire à Arthur avec les instants.

## Retours d'Arthur = améliorations du process

Chaque modification demandée par Arthur se généralise : la traduire en réglage de marque/preset ou en code, la noter ici
(avec date et verbatim) et dans la mémoire, pour que les prochains épisodes en profitent sans qu'il ait à le redemander.
Exemples du 07/10/2026 : fin trop rapide -> `outro.duration: 3,5` ; « euh » -> `tighten` ; lien d'aperçu vers un autre
short -> `preview` libère le port ; 16:9 inutile -> `formats: ["9x16"]` ; téléchargement coupé -> `fetch`.

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

- Téléchargements Dropbox : dans le navigateur, les fichiers de 13 Go se coupent vers 50 min (E22 : deux fois, les
  3 caméras à ~70 %) et restent sous leur nom final, illisibles (« moov atom not found »). `clipper/fetch.py` liste
  le dossier via `list_shared_link_folder_entries` (cookie CSRF `t`), puis télécharge en HTTP Range avec reprise.
  Les requêtes de téléchargement partent **sans** User-Agent de navigateur (sinon Dropbox renvoie sa page HTML).
- `claude -p` qui échoue avec un message vide : la cause est dans stdout (ex. « OAuth session expired » ->
  lancer `claude` dans un terminal puis `/login`). `TemporaryDirectory(ignore_cleanup_errors=True)` dans `llm.py` :
  sous Windows le dossier temporaire peut rester verrouillé à la sortie de `claude`.

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
- Passages fournis par l'équipe (timecodes + premiers/derniers mots) : commande `passages --file x.yaml` (calage par
  `snap_to_quotes`), puis compléter `turns` (qui parle : cadrage ET attribution dans les posts) et `hook_title`.
  Whisper peut rater une réplique quand deux personnes parlent en même temps (« C'est exactement ça », E21 44:38) :
  re-transcrire 20 s autour et insérer les mots dans `transcript.json`.
- Premier épisode en 4K pas encore testé (octobre 2026) : surveiller la durée de `build` (analyse des visages
  sur la 4K) ; si c'est trop lent, analyser une copie réduite et ne garder la 4K que pour le recadrage.
- Whisper coupe « c'est » en « c » + « 'est » → `transcribe.merge_fragments`. Il écorche les noms propres
  (« Modry » pour Amaudry, « iPartners ») : `transcribe --guest --company` + `transcribe.vocabulary` sont passés
  en `hotwords` à Whisper, puis `apply_corrections` (table `transcribe.corrections` + rapprochement approximatif
  réservé aux noms ≥ 5 lettres, jamais aux sigles : « Mais » ≠ MAIF). Il oublie souvent les points (d'où `end_text`).
- Le LLM cite parfois les bons mots avec un mauvais timestamp : `snap_to_quotes` cherche la citation près du
  timestamp puis dans tout l'épisode (les mots font foi). La réponse brute est gardée dans `output/…/llm_raw.json`.
- Le master E20 d'AI Corner commence par un teaser déjà sous-titré (0–30 s) : à exclure des sélections
  (consigne dans guidelines.md + `--instructions`).
- Le locuteur actif par heuristique bouche est peu fiable (plans larges 720p) : la vérité vient des tours de
  parole du LLM + `--host-side` (l'animateur change de côté selon l'épisode).
- Pas de GPU sur ce PC : Whisper large-v3-turbo ≈ 0,4× la durée de l'épisode ; rendu HyperFrames ≈ 12× la durée
  du clip (images PNG, CRF 12), 2 rendus en parallèle.
- Backend LLM sans clé API : `claude -p --output-format json --tools ""` dans un cwd temporaire (`llm.py`).

## Vérifier une modification du montage

1. `python -m clipper build … --only 1` puis `npx hyperframes lint --json` dans le dossier format (0 erreur attendue ;
   les avertissements `composition_file_too_large` / `timeline_track_too_dense` sont normaux).
2. `npx hyperframes snapshot --at <instants> --no-end -o <dir>` et regarder les images (visages, sous-titres, PiP, outro).
3. Rendu complet seulement ensuite (`render --only 1 --force`).
