# Podcast Clipper — guide pour Claude Code

Framework Python qui transforme un épisode de podcast (mp4) en shorts/reels montés
(9:16, 16:9, 1:1) via HyperFrames. Lis [README.md](README.md) pour l'usage complet ;
ce fichier résume ce qu'il faut savoir pour **modifier** le projet sans casser les acquis.

> **Bonnes pratiques validées** (son, coupes, sous-titres, shorts, teaser, épisode, méthode) : [docs/BONNES_PRATIQUES.md](docs/BONNES_PRATIQUES.md)
> — résumé de tous les retours d'Arthur sur E22, à respecter sur chaque nouvel épisode.
>
> **Reprise de session** : l'état du travail en cours (épisode, ce qui est validé, ce qui reste) est dans
> [docs/REPRISE.md](docs/REPRISE.md) — à lire en premier dans une nouvelle session.

## Règle de maintenance : le schéma suit le process (Arthur, 09/10/2026 : « mets à jour le schéma d'architecture à chaque fois
qu'on modifie le process » ; précisé le même jour : « le schéma, c'est celui de GitHub, on met à jour que celui-là »)

Le SEUL schéma à tenir à jour est celui de GitHub, affiché par la « Vue d'ensemble » du [README](README.md). La page partagée
publiée en plus (artifact, docs/schema.html) a été supprimée : ne pas la recréer. Le schéma est une IMAGE (`docs/schema.svg`)
générée depuis la source Mermaid [docs/schema.mmd](docs/schema.mmd) : le même schéma en Mermaid direct affichait chez Arthur
« Unable to render rich display — Cannot read properties of undefined (reading 'render') » (09/10/2026), alors qu'il se
dessinait sans erreur avec Mermaid 10.0 à 12.1 et sur GitHub dans un autre navigateur. Dans les libellés, jamais de ligne
qui commence par « + » après `<br/>` : en image, elle est prise pour une puce et effacée.
À CHAQUE modification du process (nouvelle commande, étape ajoutée, retirée ou déplacée, nouvelle validation humaine, nouveau
livrable), dans le MÊME commit :
1. `docs/schema.mmd` (les étapes citent leur commande), puis `python scripts/render_schema.py` (Chrome sans fenêtre,
   Mermaid 11.4.1 figé, ~15 s) et REGARDER l'image ;
2. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (liste « commandes » du bloc cli.py, tableau des briques, parcours des fichiers) ;
3. lancer `python scripts/check_schema.py` : il échoue si une commande du CLI manque dans ARCHITECTURE, si une étape du
   process (liste `PROCESS` du script) ou une validation humaine manque dans le schéma, ou si l'image n'a pas été
   régénérée depuis la source (empreinte). Une nouvelle étape du process s'ajoute aussi à `PROCESS` dans le script ;
4. pousser sur GitHub (c'est ce que l'équipe lit) après avoir vérifié `git show --stat` (seulement les fichiers de la
   modification, aucun modèle .onnx), puis regarder le rendu sur github.com.
Puis noter la demande et sa date dans la section concernée de ce fichier.

## Point d'entrée utilisateur

Arthur dépose ses fichiers (épisodes, clips de référence, logos, posts) dans `depot/` (non versionné) : les ranger
dans `brands/<marque>/episodes|references|assets|assets/guests/` (voir `depot/README.md`). S'il est vide, regarder
les fichiers récents de `~/Downloads`. Depuis E22, l'équipe livre des **rushs** : 3 caméras 1080p de 13–14 Go
(gros plan invité, plan large, gros plan animateur) + 1 WAV du micro, **déposés dans `depot/`** (Arthur, 09/10/2026 :
« on dépose les rushs MP4 et l'audio dans le dossier depot ») -> `python -m clipper rushes --brand <m> --episode E23
--guest "Prénom Nom" --company X` (voir « Rushs déposés » plus bas). Si des rushs restent sur Dropbox :
`python -m clipper fetch "<lien>"` (jamais via le navigateur : coupure vers 50 min, fichier tronqué gardé sous son nom).

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
python -m clipper polish … --only N   # APRÈS pick : tighten -> verify -> fillers -> réactions -> mots-clés -> build -> qa -> aperçu MP4 -> qa
python -m clipper qa … --only N [--render] | --episode   # contrôle qualité (rapport output/…/qa/), à relancer après tout rendu
python -m clipper fillers … --only N   # retire les « euh » collés aux mots (inclus dans polish)
python -m clipper preview … [--clip all|N] [--stop] [--mp4]   # aperçu (Studio ports 3002+, ou MP4 brouillon --mp4), validé AVANT render
python -m clipper new-brand <slug>   # copie brands/_template
python -m clipper rushes --brand <m> --episode E23 --guest "…" --company "…" [--dry-run] [--host <fichier>]   # rushs de depot/
python -m clipper fetch "<lien Dropbox>" [--dest dossier] [--skip MIC]   # rushs restés sur Dropbox : reprise auto, taille vérifiée
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
   aux raccords (`join_transition: leak`) **seulement quand l'angle de caméra change** (Arthur, 08/10/2026 : « les flashs
   lumineux de transition, que quand tu changes d'angle de caméra » : `montage.transition_min_diff` 25, différence
   d'image de part et d'autre du raccord — même caméra 0,5–7, autre caméra ≈ 50 ; même caméra = coupe nette) ; 25–45 s ; pas de B-roll ; + carte de fin de **7 s** (3,5 s jusqu'au 08/10/2026 : « trop rapide, x2 ») sur l'animation
   officielle AI Partners (`assets/outro_anim.mov`, lignes « montagne » qui se dessinent, recadrée au format et
   accélérée ×1,4 depuis le 08/10 — ×5 sur 2 s puis ×2,9 sur 3,5 s jugés trop rapides : `outro.background: video`, `media.prepare_outro_video`) + logo blanc + CTA **centrés** (`outro.layout: center`, voile radial). Bleu #258AF3,
   **jamais d'italique** — SAUF le mot-clé des sous-titres en serif italique (variante B choisie par l'équipe le 09/10/2026).
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

## Titre de l'épisode : `titles` (Arthur, 09/10/2026 : « l'équipe m'a retourné que les titres n'étaient pas ouf ; réfléchis à un
process pour les rendre mieux, et une étape où quelqu'un du marketing valide un titre parmi 5 propositions »)

- **Cause** : le titre YouTube sortait du prompt de dérushage (`"youtube_title": "<titre YouTube>"`, aucun brief) et la vignette
  avait son propre générateur ; E22 : `IA en entreprise chez Coty : 3000 agents, Microsoft Copilot et la vraie transformation`
  (liste de mots-clés, pas de préfixe) et des vignettes qui énonçaient des faits internes (« Chez Coty il y a 3000 agents »).
- **Un seul générateur** (`clipper/titles.py`, brief `brands/ai-corner/titles.md` = relevé des 16 titres publiés E06–E21,
  captures d'Arthur) : format YouTube `AI Corner E<n> | <titre>` (sans nom d'invité depuis E16) ; vignette = le MÊME titre en 2
  lignes ≤ 34 caractères, bandeau sur la ligne du sujet (2e ligne dans 12 cas sur 16) ; 5 formules (`comment`, `tension`,
  `promesse`, `enjeu`, `constat`) ; le titre pose LA question du spectateur, l'invité est la preuve ; ton sobre.
- **Process** : `python -m clipper titles --brand <m> --input <E>.mp4` (après `transcribe`, en parallèle de `propose`) :
  Claude Opus écrit ~12 candidats avec `evidence` (horodatage + citation EXACTE de l'épisode) -> `check` : 2 lignes ≤ 34
  (échec > 40), titre complet ≤ 80 (échec > 95), chiffres et noms propres présents dans la transcription, citation retrouvée
  d'un seul tenant (`find_evidence`), aucun nom d'invité, aucun mot sensationnel, pas de copie (> 0,85) d'un titre publié ni
  d'un titre refusé (rubrique « À éviter » de titles.md, lue par `refused`) ; ATTENTION : proche (> 0,72), deux-points,
  aucun mot du métier -> jury (Claude, sans transcription) : 5 titres, ≥ 4 formules, une phrase d'explication pour
  l'équipe -> `titres/propositions.jpg` (planche « chaîne YouTube » : vignette réelle + titre, 6e case = dernier épisode
  publié pour comparer), `propositions.md` (justification, passage à l'horodatage, contrôles), `propositions.json`.
- **Enchaînement voulu par Arthur (09/10/2026)** : l'agent propose 5 titres -> l'équipe marketing en choisit un -> Claude
  fait les miniatures avec ce titre, en planche numérotée -> l'équipe choisit sa miniature préférée (`thumbnail --pick N` ->
  `miniatures/miniature_finale.jpg` + `_HD.png` + `miniature_choisie.json`) = le livrable à téléverser sur YouTube.
- **Validation par l'équipe marketing** : `titles --pick N [--lines "l1 | *l2"] [--youtube "…"] [--by Prénom] [--note "…"]` ->
  `titres/titre_valide.json` (avec les propositions écartées et la remarque : matière pour compléter titles.md) ; la
  description YouTube (`episode_title`, `episode-plan`) reprend ce titre ; **`thumbnail` refuse de tourner sans titre
  validé** (`--title "l1 | l2"` pour passer outre) et ne génère plus de titres : ses variantes ne diffèrent que par les photos.
- Les contrôles attrapent les règles dures, pas le style : « Pourquoi Coty a / 10 fois le même agent ? » (refusé) est bloqué
  seulement parce qu'il figure dans « À éviter ». Le style repose sur le brief, le jury et la validation humaine.
- `titles --import-file x.json` : titres déjà écrits (même format que `candidates`) contrôlés et présentés sans appeler
  Claude (limite d'abonnement atteinte le 09/10 : propositions E22 écrites dans la session à partir de la transcription).
- Chaque remarque de l'équipe sur un titre -> ligne dans « À éviter » ou règle dans titles.md (puis noter ici).

## Miniatures YouTube : `thumbnail` (demande d'Arthur, 09/10/2026 : « automatise la création de miniatures, même style que les nôtres »)

- Références : `brands/ai-corner/references/thumbnails/` (+ README d'analyse). Gabarit : animateur à gauche / invité à
  droite détourés depuis LEUR caméra, studio flouté recoloré bleu, 2 icônes d'app 3D au centre (AI Partners / entreprise
  invitée `assets/guests/<entreprise>.svg|png`), titre 2 lignes Metropolis ExtraBold, une ligne sur bandeau bleu arrondi
  (le titre vient de `titles` : voir la section précédente ; depuis le 09/10 ombre douce sous la ligne hors bandeau,
  `title.shadow` dans template.json, 0 pour la retirer).
- `python -m clipper thumbnail --brand ai-corner --input E22_nicolas-comestaz_coty.mp4 [--n 4] [--title "l1 | *l2"]
  [--host-frame s] [--guest-frame s]` -> `output/…/miniatures/` (`variante_N.jpg` 1280×720, `_HD.png`, `planche.jpg`,
  `titres.md`). Invité/entreprise lus dans clips.json. ~5 min (détourage ≈ 45 s par photo, CPU).
- `clipper/thumbnail.py` : images clés toutes les 8 s -> note YuNet (netteté, regard vers le centre, sourire) ->
  **jury visuel** (Claude regarde les planches `work/thumbnail/candidats_*.jpg` : `llm.ask_json(images=…)`, le CLI lit les
  images avec l'outil Read) -> détourage BiRefNet-portrait en onnxruntime direct (`models/birefnet_portrait.onnx`, ~1 Go ;
  **`rembg` inutilisable** : il importe numba, DLL bloquée par le contrôle des applications, comme `resvg-py`) -> logos
  SVG rendus par Chrome headless -> composition PIL (réglages : `STYLE`, surchargeables par `thumbnail.style` dans brand.yaml).
- Retour d'Arthur (09/10/2026) : « le logo de chaque invité un peu caché derrière la tête et l'épaule, plus en 3D,
  tournés l'un vers l'autre comme les deux invités qui se regardent » -> calques fond -> icônes -> personnes ; `tile_x`
  place chaque icône d'après le détourage (`tile_overlap` 0,16 de sa largeur cachée) ; `tile_3d` = vraie perspective
  (rotation `yaw` ±36°, `tile_pitch` 10°, épaisseur `tile_depth` 0,2, focale 1,7× le côté), tranche visible côté
  extérieur, logo décalé de 6 % vers le côté visible.
- 2e retour (09/10/2026) : « encore plus en 3D, presque deux cubes » + « le bleu doit vraiment entourer le texte blanc,
  un peu short en bas » -> face avant tournée modérément (±26°, pitch 6°) et épaisseur de cube extrudée vers le BAS
  et l'EXTÉRIEUR (`tile_depth` 0,22 : le dessous reste visible même si le côté passe derrière la personne ; une
  perspective forte déformait l'icône en losange) ; bandeau calé sur l'encre réelle du texte (jambages g/q compris),
  marge égale tout autour.
- **Gabarit Canva** (09/10/2026 : l'équipe marketing fait les miniatures sur Canva, export PPTX + PNG dans
  `references/thumbnails/canva/`) : `python -m clipper thumbnail-template --brand ai-corner --pptx … --png …`
  (`clipper/canva_template.py`) range les VRAIS calques dans `assets/thumbnail/` (fond studio bleu, 2 cadres flous,
  cubes vides animateur/invité, logo AI Partners, bandeau) + `template.json` (boîtes en px à 96 ppp = EMU/9525,
  rotations, miroir `flipH` — Thomas est retourné vers l'invité —, police Montserrat Bold 125 px sur le bandeau /
  ExtraBold 108 px hors bandeau, lignes de base et visages mesurés sur le PNG). Dès que `template.json` existe,
  `thumbnail` compose dessus (`compose_template`) : nos photos détourées posées d'après les visages du design (pas
  de découpe aux formes Canva : elles coupaient une épaule), logo de l'invité dans la boîte du logo (87 % de large).
  Ordre des 10 calques attendu (design dupliqué) ; un autre design = refaire l'import. Contrôle : la reconstruction
  du design d'origine depuis le gabarit diffère de son PNG de 3/255 en moyenne.
- Choix des photos (Arthur, 09/10/2026 : « il faut que les deux se regardent avec une expression souriante ») :
  images toutes les **3 s** (à 8 s, aucune image de Thomas souriant), expression mesurée par **FER+** (ONNX,
  `models/emotion_ferplus.onnx`, probabilité « joie » : souriant 1,0 / neutre 0,0 — l'ancien indice largeur de bouche
  prenait une bouche ouverte en pleine phrase pour un sourire), classement = joie d'abord + regard vers le centre ;
  planches recadrées sur les VISAGES (16 candidats, 4×4) ; jury `JURY_PROMPT` : vrai sourire tourné vers l'autre.
  E22 : Thomas 40:06, Nicolas 48:33. Si `claude -p` est indisponible (limite hebdo atteinte le 09/10), l'agent regarde
  les planches et écrit lui-même `work/thumbnail/jury_v2.json` ({"host": [n°…], "guest": [n°…]}).
- `cv2.imread` ne lit pas les chemins accentués (« Création… ») : `score_frame` lit avec `np.fromfile` + `imdecode`.
- Nommage des épisodes : **d'après l'INVITÉ**, `E<n>_<prenom-nom-invite>_<entreprise>` (E20 `thierry-champeroux_maif`,
  E21 `quentin-amaudry_mendo`, E22 `nicolas-comestaz_coty`) — jamais d'après Thomas Spitz, l'animateur (CEO AI
  Partners, présent dans tous les épisodes). E22 était nommé « thomas-spitz » : renommé le 09/10/2026 (demande
  d'Arthur, « pour être cohérent avec ce qu'on faisait avant ») — sources, dossier `output/…/e22-nicolas-comestaz-coty`,
  fichiers et chemins dans les JSON ; jonctions `assets/` des projets recréées (cible absolue).

## Rushs déposés : `rushes` (Arthur, 09/10/2026 : « on dépose les rushs MP4 et l'audio dans le dossier depot »)

- `clipper/ingest.py` : 3 vidéos + 1 audio lisibles dans `depot/` (un fichier tronqué — `ffprobe` en échec — est
  ignoré et signalé : les anciens téléchargements partiels d'E22 de 9,3 Go y sont encore). Plan large = la caméra qui
  montre 2 visages ; animateur = gros plan dont l'empreinte de visage (SFace, ONNX, `models/face_recognition_sface.onnx`,
  alignement sur les 5 repères YuNet) ressemble le plus à `assets/host_face.jpg` (Thomas : 0,80 contre 0,20 pour
  l'invité sur E22) ; sans photo de référence : couleurs comparées au plan large + `framing.host_side`.
- **Le côté de l'animateur change d'un épisode à l'autre** (E21 à droite, E22 à GAUCHE alors que brand.yaml dit
  `host_side: right`) : `rushes` le mesure dans le plan large et l'écrit dans `multicam.json` (`host_side`) -> passer
  `--host-side` en conséquence.
- Décalage du micro : corrélation (FFT) du son témoin du plan large avec le WAV, 90 s à ~10 min ; convention de
  `multicam.json` : son caméra(t) = micro(t - `audio_delay`). E22 mesuré à **-0,037 / -0,042 s** sur les 3 caméras et à
  3 moments, alors que le fichier E22 contient +0,037 (signe probablement inversé à la mesure manuelle du 06/10 : le
  son des rendus E22 serait ~74 ms en retard sur l'image) — extraits de test `apercus/test_synchro_{A_actuel,B_mesure}.mp4`,
  à trancher par Arthur avant de corriger E22.
- Rangement : `episodes/rushes/<E>/{invite_<prénom>,large,animateur_<prénom>}.mp4`, `episodes/<E>_<invité>_<entreprise>.wav`,
  `<base>.mp4` = lien dur vers le plan large, `<base>.multicam.json` ; planche `episodes/rushes_<E>.jpg` à REGARDER
  (`--host <morceau de nom>` corrige l'animateur). Fichiers déplacés, jamais copiés. `--dry-run` : identifie sans déplacer.

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
  sur la personne qui parle 97 % du temps ; respiration large/split (~7 s) toutes les 12–19 s dans les longues
  réponses. `build_edl` reproduit ces proportions (E22 : 74/13/13 %, médiane 9 s).
- **On voit TOUJOURS celui qui parle** (Arthur, 09/10/2026 : « des fois l'invité parle et la caméra est centrée sur
  Thomas, et inversement — quand une personne parle, soit un plan avec que lui, soit un plan avec les deux, mais il
  faut qu'on voie toujours celui qui parle ») : `episode.enforce_speaker` (fin de `build_edl`) — dans un gros plan,
  toute réplique de l'autre de plus de 0,4 s (`speaker_runs`) devient son gros plan (≥ 2,5 s) ou un écran partagé
  (≥ 2 s) ; gros plan restant < 2 s -> écran partagé ; répété 3 fois (ping-pong rapide). Les plans de **réaction**
  sur l'écoutant seul sont **supprimés** (`reaction_rate: 0`) : ils montraient l'autre 1,5 s en pleine phrase.
  E22 avant : 41 passages, 54 s hors champ (réactions, « bonjour Thomas », « Pardon », ping-pong 21:48 « Qui est
  globale ? » / « Qui est globale. Ok. » fondu dans un seul plan par le nettoyage des plans < 2 s) ; après : 0.
  `qa --episode` le contrôle deux fois : `speaker_visible` (tours de parole, ÉCHEC) et `voice_matches_shot`
  (empreinte vocale WeSpeaker de chaque gros plan comparée aux deux voix, INDÉPENDANT de la diarisation -> ATTENTION
  avec l'instant, à regarder : c'est lui qui attrape une erreur de « qui parle »).
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
- Retours d'Arthur sur le 1er montage (07/10/2026) : « teaser plus dynamique, enlever les euh, qu'on voie les deux
  interlocuteurs, des passages impactants » -> `make_teaser` (TEASER_PROMPT, appel dédié avec Opus :
  `episode.teaser_model`) : 6–9 extraits de 2,5–8 s, alternance animateur/invité, début et fin de phrase ;
  `teaser_clip` : `tighten` serré + réaction de 1 s de l'autre (`clip["cams"]`, lu par `cut_multicam`, `force`).
  2e retour : « phrases un peu plus longues, que chacun parle, des plans des 3 caméras » -> 5–7 extraits de 5–12 s
  en alternance ; dans chaque extrait, tour à tour un plan large de 2 s ou la réaction de l'autre (1,2 s).
  « Logo plus gros en haut à droite » -> `episode_logo` (0,20 de la largeur, comme E20 ≈ 0,22) dans chaque plan
  du corps + même réglage pour le teaser (`teaser.format_overrides.16x9.logo`).
- Raccords de dérushage (retour d'Arthur, 08/10/2026 : « vérifie bien plusieurs fois qu'il n'y a pas de problème de
  coupure, de euh laissé au montage ») : `episode.clean_join_starts` (appelé par `episode-plan` après `keep_ranges`)
  — reprise dans un son -> reculée au creux qui le précède ; voix entre la reprise et le 1er mot de la transcription
  NORMALE (qui omet toujours les hésitations) = hésitation -> reprise 0,28 s avant l'attaque de ce mot (signal le plus
  fiable : le verbatim, lui, entend ou non le « euh » selon la fenêtre) ; « euh » / mot répété en tête (écouté 2 fois, contextes
  différents : le verbatim varie) -> blocs de voix sautés, coupe au point le plus silencieux ; blanc > 1,2 s au
  raccord -> reprise 0,28 s avant la voix. E22 : « Euh, pour, pour finir » -> « Pour finir » (2924,50) ; 3 s de blanc
  après la question reposée + « Euh… ben, je… » non transcrit -> reprise sur « Je pense » (2469,95), 1,1 s de pause.
  Le contrôle final se fait sur le MP4 rendu (audio extrait, réécoute de chaque raccord) : c'est lui qui a trouvé ce
  « ben, je… ». Avant tout rendu final : réécouter chaque raccord (`verify.audit`, 10 s de
  part et d'autre). Les « euh » DANS les réponses restent (montage du monteur : conversation naturelle).
- Fin de l'épisode : `episode_end.duration` (brand.yaml), 8 s depuis le 08/10/2026 (équipe : « trop rapide, x2 sur la
  longueur de l'animation ») ; même retour pour les shorts : `outro.duration` 3,5 -> 7 s. `end_card` lit le logo PNG
  avec `-loop 1` (sinon une seule image, transparente avant le fondu : logo invisible — bug présent jusqu'au 08/10).
- L'épisode entier n'est PAS rendu par HyperFrames (≈ 12× la durée = 9–10 h) : corps en FFmpeg, teaser en
  HyperFrames. Toujours montrer l'aperçu (Studio pour le teaser : `preview --clip 99 --formats 16x9`, 540p pour le
  corps) et attendre la confirmation d'Arthur avant tout MP4 final.
- E21 n'a ni teaser ni logo incrusté ; E20 a un teaser (~55 s, 4–5 phrases de l'invité) + sting logo : c'est ce
  modèle qui est reproduit.

## Shorts multicam : gros plan + écran partagé (retour de l'équipe via Arthur, 07/10/2026)

« Plus avec les 3 angles : le gros plan la plupart du temps sur celui qui parle, mais aussi de temps en temps l'angle avec
les deux personnes (comme les shorts du monteur : un en haut, un en bas) pour montrer que l'autre écoute. »
- `multicam.short_cams` génère les plans d'un short (`clip["cams"]` si fourni à la main) : gros plan de celui qui parle,
  écran partagé de 2,4–3,2 s toutes les ~6,5–9,5 s (`brand.yaml: multicam.split_every / split_len / first_after`),
  jamais avant la fin de l'accroche (la bulle-titre cacherait le visage du bas), posé sur une pause entre deux mots,
  de préférence juste après un raccord (le split masque le jump cut).
- `cut_multicam` compose un flux « split » = les deux gros plans côte à côte (960 px de large chacun, centrés sur le
  visage, pixels natifs) ; `compose` force alors `framing.wide_shot_mode: split`, `split_half: true`
  (`reframe.half_crop` : le crop reste DANS la moitié de chaque personne) et `min_face_motion: 0`. Invité en haut,
  animateur en bas (`host_side: left`). Limite : la tête de celui du haut touche la barre de logos (le gros plan
  n'a pas de marge au-dessus) — comme sur les shorts de référence.
- Écran partagé seulement si celui qui écoute est CALME à l'image (Arthur, 09/10/2026 : « enlève le passage où Thomas
  se gratte l'oreille ») : `multicam.listener_motion` (différence d'image max à 6 i/s dans sa caméra) ≤
  `multicam.max_listener_motion` (3,0 ; immobile 0,6–1,9, se gratter l'oreille 6,95) ; sinon `short_cams` essaie plus
  loin, et pas d'écran partagé s'il ne se calme jamais.
- Le plan large des rushs n'est PAS utilisé en vertical (recadré, on ne verrait qu'une personne).
- Pour un short existant : `build --only N --force` (le cache de découpe dépend des plans).

## Teaser façon « Dans la tête d'un CEO » (08/10/2026) — `docs/TEASER_FRAMEWORK.md`

- Script (`TEASER_PROMPT`, rôles these → developpement → pingpong → reaction → histoire → conviction → chute, mots-clés
  par extrait), coupes toutes les ~2 s (gros plan / large / réaction, `teaser_clip`), sous-titres géants Metropolis
  ExtraBold blanc + mots-clés #258AF3 (section `teaser:` de brand.yaml), fin = **animation AI Partners + logo** (7 s, comme les
  shorts — règle permanente d'Arthur, 09/10/2026, retour de Mohamed ; avant : carte floutée + invité + entreprise).
- Mots-clés en expressions (« 3000 agents ») : `captions.group_words` les découpe en mots (petits mots exclus).
- Exigences : thèse sans hésitation, ping-pong indissociable (question + SA réponse), chute ≤ 6 s ; extraits
  écartés -> réserve courte et propre ; puis `editorial_order` (LLM, `EDITOR_PROMPT`) recompose l'ordre final à
  partir des seuls extraits VÉRIFIÉS (texte réellement entendu) : Q/R cohérentes, aucune redite, fin forte. Sans ça,
  E22 avait un ping-pong absurde (« top-down ? » / « 3 000 agents ») et une redite ouverture/fin.
- Hésitations : `verify._isolated_hole` — on ne retire un « euh »/« enfin » que s'il forme un bloc de voix entouré
  de vrais silences dans le son ; sinon il reste (E22 : couper sur les horodatages avait emporté « plus de »).
  Raccord signalé au milieu d'un mot par l'écoute (`audit_joins`) -> retrait annulé, réécoute.
- Dynamique dosée : `montage.dynamic_fx` (compose) — zoom lent alterné 6 % sur chaque plan + `fx_in: zoomblur`
  (filtre flou + luminosité, 0,32 s, template) sur 1 changement d'extrait sur 3 (`fx_every`). Sous-titres : ombre
  en DÉGRADÉ (retour d'Arthur, 08/10/2026 : « fais un effet plus dégradé sur l'ombre autour ») — 6 couches de flou
  croissant (2 → 90 px) et d'opacité décroissante, contour fin (`stroke_width: 3`, noir 40 %), plus d'ombre portée
  nette ni de contour épais ; garder ce principe pour tout réglage de contraste des sous-titres.

## Workflow « double / triple check » (demande d'Arthur, 08/10/2026 : « intègre vraiment le workflow en mode double
triple check, optimise-le pour la rapidité sans réduire la qualité »)

- Chemin normal d'un short après `pick` : **`polish --only N`** = `tighten` -> `verify --fix` -> `fillers` -> `build`
  (sous-titres contrôlés à l'écoute) -> **`qa`** (1er contrôle) -> aperçu MP4 -> **`qa --render`** (2e contrôle +
  planche d'images à REGARDER) -> liens à Arthur (3e contrôle, humain) -> `render` -> `qa --render` sur le final.
  Épisode complet : `qa --episode` (réécoute de chaque raccord dans le MP4 final, volumes, images).
- `clipper/qa.py` : statuts OK / ATTENTION / ÉCHEC ; un ÉCHEC arrête `polish` avant l'aperçu (`--anyway` pour forcer).
  Contrôles : raccords à l'écoute (mot coupé), fin pendant la voix (`_voice_at`), **début de la phrase suivante entendu
  en fin d'extrait** (`_new_sound_before_cut`), respiration de fin, sous-titres (`captions_check.txt`), lint, durée,
  transitions ; rendu : à jour, format, durée, -16 LUFS ±1,5, planche d'images.
- Rapidité sans perte : un seul processus (Whisper chargé une fois), étapes déjà faites sautées (`clip["checks"]`,
  posé aussi par `tighten` / `verify --fix` / `fillers` lancés seuls ; `--redo` repart de `clips_before_tighten.json`),
  `fillers` valide toutes les coupes d'un passage en UNE transcription (une par une seulement si un mot manque —
  Whisper traite toujours des blocs de 30 s, des fenêtres plus courtes ne coûtent pas moins), 2 aperçus MP4 en
  parallèle. Whisper utilise déjà 4 threads = 4 cœurs physiques : rien à gagner de ce côté.
- `pad_end` corrigé le 08/10 (trouvé par `qa`) : si la coupe tombe déjà dans un vrai silence (≥ 0,08 s), la phrase est
  finie et on ne dépasse JAMAIS le son suivant (short 1 E22 : « l'entreprise. ‖ É(t) » entendu) ; un silence plus court
  est une occlusion dans un mot (« bien-t-ôt », 0,06 s) -> on va jusqu'à la vraie fin de la voix. Teaser E22 : 5 fins
  reculées de 0,1–0,35 s ; épisode : fins de partie 2406,19 -> 2405,99 et 2819,02 -> 2818,67 (début du hors antenne).

## Motion design discret du teaser (test du 09/10/2026 : « ajoute du motion design aux couleurs d'AI Partners, quelque
chose de subtil, que ça ne fasse pas trop »)

- Section `motion:` (defaults : désactivée ; `teaser.motion` d'AI Corner : activée), `compose._motion_ctx` + template :
  1. carte invité à 0,6 s pendant 3,2 s, **du côté de l'invité dans le plan large** (`framing.host_side` -> côté
     opposé) : nom en capitales Metropolis Bold, filet bleu #258AF3 qui se trace, rôle en Metropolis Regular
     (`clip["guest_role"]` dans teaser_clip.json) ;
  2. chiffre-clé qui se compte (0 -> valeur en 0,9 s) en haut à droite sous le logo, au moment où le mot est entendu
     (`clip["stats"]` : [{match, value, prefix, label}], ex. « PLUS DE / 3 000 / AGENTS IA ») ;
  3. sons Pixabay (`assets/sfx/`, licence dans CREDITS.md) : whoosh court sous chaque zoom-flou (gain 0,25 ≈ -31 dB),
     fin d'une montée (riser) sur les 2,4 s avant la carte de fin (0,15 ≈ -30 dB) — ≈ 13 dB sous les voix.
- **Décision d'Arthur (09/10/2026, après le test) : « on va garder que la guest card »** -> `stats` et `sfx` désactivés
  dans `teaser.motion` (code conservé, réactivable) ; seule la carte invité reste. Noté dans docs/TEASER_FRAMEWORK.md.
- Jamais : glitch, citation plein écran, mur de logos, plus d'un élément graphique en plus des sous-titres et du logo.
- Nombres découpés par Whisper (« 3 » « 000 ») recollés dans `caption_check._listen` (sinon « DE 3 3000 agents »).

## Aperçus en MP4 (retour d'Arthur, 08/10/2026 : « les aperçus dans HyperFrames buguent à chaque fois »)

Le Studio HyperFrames ne suffit pas pour valider : donner à Arthur un MP4 brouillon à ouvrir dans son lecteur
(`preview --mp4 --clip N` -> `output/…/apercus/clip_NN_…_<fmt>_apercu.mp4` ; épisode : `episode-render --proxy`, avec
`--minutes 2` pour juger teaser + raccord + volumes). Le lien Studio peut être donné en plus. Lien cliquable vers le
fichier dans la réponse. Toujours un aperçu avant le rendu final.
- Vitesse (Arthur : « fais le genre en 240p pour que ce soit rapide sur la phase d'itération ») : HyperFrames ne rend
  jamais plus petit que la composition (`--resolution` ne fait qu'agrandir) et réduire après coup ne gagne rien ; le
  coût est par image -> aperçu à 12 i/s (`preview --mp4 --fps 12`, défaut) en brouillon : short de 49 s en 2 min 30.

## Fins d'extraits : jamais pendant que quelqu'un parle + respiration (retour d'Arthur, 08/10/2026)

« Faut jamais que tu coupes avant que quelqu'un ait fini sa phrase, laisse même un mini temps à la fin pour que ça ne
fasse pas effet coupé — et ça vaut pour toutes les formes de vidéo : teaser, short, podcast entier. »
- `verify.pad_end(wav, t)` : la voix est finie au premier silence ≥ 0,12 s (< 20 % du niveau de parole — le souffle
  du micro de l'invité monte à ~10 %) ; fin = cet instant + 0,35 s, sans jamais atteindre la voix suivante (- 0,08 s) ;
  ne raccourcit jamais ; voix qui continue > 1,2 s = phrase pas finie -> inchangé.
- Blanc trop long en fin d'extrait (Arthur, 09/10/2026 : « après "top-down avec du leadership" il y a un petit blanc
  qui n'est pas ouf » — 1,4 s) : `verify.trim_tail` ramène tout silence > 0,5 s après la dernière voix à 0,35 s
  (`episode-plan` sur chaque fin d'extrait du teaser) ; `qa` signale un blanc de fin > 0,6 s.
- Appelé partout : `trim_edges` (fin de chaque short et de chaque extrait du teaser), `episode.clean_join_starts`
  (fin de chaque partie du montage complet et fin de l'épisode).
- Cas E22 : teaser « …en productivité » coupé sur la dernière syllabe (« -té » jusqu'à 1294,94, coupe à 1294,85) ;
  fin de l'épisode coupée dans « À bien-tôt » (3058,97 -> 3059,42).

## Volume et musique du teaser (retour d'Arthur, 08/10/2026)

- « Le teaser avait un volume trop fort par rapport au reste » : teaser rendu à -17 LUFS, micro brut du corps à -37.
  `episode.assemble` normalise désormais CHAQUE partie séparément à -16 LUFS avant de les enchaîner.
- Musique d'ambiance sous le teaser : `teaser.audio.music` (brand.yaml, fichier dans `assets/music/`), « vraiment en
  léger, qu'on entende surtout les invités » : `music_volume: 0.08` pour une piste à -16 LUFS (≈ -22 dB sous les
  voix) ; elle s'éteint pendant la carte de fin. E22 : « Driving Momentum ».
- Teaser et corps sont rendus SÉPARÉMENT (`renders/teaser.mp4` ; morceaux du corps en cache dans
  `work/episode/pieces_*`) : une retouche du teaser = nouveau rendu du teaser (~10 min) + réassemblage (~5 min,
  sans réencoder le corps). Supprimer/renommer `renders/teaser.mp4` pour forcer son nouveau rendu.

## Contrôle « à l'oreille » : `verify` (retour d'Arthur, 07/10/2026 : « des euh qui restent, des phrases coupées trop
tôt, un bégaiement au début — rajoute une vérification »)

- La transcription normale nettoie le texte : « euh », bégaiements, mots coupés y sont invisibles. `clipper/verify.py`
  retranscrit chaque passage en VERBATIM (Whisper + `initial_prompt` plein d'hésitations, 0,4 s avant / 0,6 s après)
  et repère : fillers (`FILLERS`), « enfin » entre virgules (`HEDGES`), mots répétés, mot à cheval sur le début ou
  la fin. Sur E22 il a retrouvé exactement ce qu'Arthur avait entendu.
- Correction (`fix_segment`, `check_and_fix`, 2 passes) : coupes posées au point le plus SILENCIEUX du micro
  (`_quiet`) — les horodatages verbatim d'un « euh » bougent de ±0,1 s d'une passe à l'autre ; voisins pris dans
  l'ordre des mots (un « euh » mal horodaté avait emporté « refonte ») ; morceaux d'un même passage jamais chevauchants.
- Garde-fou : un passage qui garde un mot coupé (« euh » collé aux mots, aucun silence) -> short : coupe d'origine
  (`on_fail="restore"`) ; teaser : extrait retiré et remplacé par la réserve du LLM (`backup`), enchaînement final en
  alternance animateur / invité (`teaser_clip`).
- Shorts : `python -m clipper verify … --fix` après `tighten`, avant `build`. Coût ≈ 1,5× la durée contrôlée (CPU).
- `trim_edges` (3e retour, 07/10/2026 : « il commence sur la fin d'un mot, coupe la demi-seconde du début ») : un bord
  qui démarre par ≥ 0,2 s sans parole soutenue (blanc, souffle, fin de mot trop faible pour le seuil mais audible) est
  recalé 0,1 s avant la parole (fin : 0,15 s après) — mesuré sur l'énergie du micro, appliqué après les 2 passes.
- Fins de phrase (4e retour, 07/10/2026 : « des fois ça coupe avant qu'il ait terminé sa phrase ») : 2 extraits du
  teaser finissaient sur une virgule (« …de loin le leader, ‖ en tout cas, nous… »). Le verbatim seul NE SUFFIT PAS
  pour décider où une phrase finit : sa ponctuation et ses « euh » changent d'une transcription à l'autre (« venir. »
  puis « venir, et »). Méthode retenue (`verify.is_boundary` / `sentence_bounds` / `snap_extract`) : une frontière =
  ponctuation finale dans la transcription NORMALE (stable) + vraie pause mesurée dans le son (`pause_after` ≥ 0,25 s,
  silence = < 25 % du niveau de parole voisin, fenêtre élargie car les horodatages normaux sont décalés de 0,2–0,3 s)
  + mot suivant qui ne prolonge pas (et/donc/parce que… si pause < 0,5 s) ; changement d'orateur = frontière dès
  0,08 s de blanc. Extrait prolongé jusqu'à la prochaine frontière (≤ 8 s) ou ramené à la précédente, sinon écarté
  (teaser) / coupe d'origine (short). Cas de référence E22 : « venir ‖ et » non, « leader ‖ En tout cas » non,
  « l'autre ‖ Donc » oui, « productivité ‖ Le » oui, « réinternaliser ‖ Mais » oui, « consommateurs ‖ (1,6 s) donc » oui.
  Le verbatim (une seule transcription par extrait) ne sert qu'à retirer les « euh » à l'intérieur.
- Écoute finale (`verify.audit`) : l'audio est assemblé exactement comme au montage puis retranscrit d'un bloc ->
  « ce qu'on entend », ‖ à chaque raccord (`teaser_audit.txt`, `episode_plan.md`, sortie de `verify`) ; `audit_flags`
  signale un « euh » entendu ou un raccord au milieu d'un mot. C'est ce texte qu'on montre à Arthur.
- Whisper hallucine « Sous-titrage ST' 501 » sur le silence de fin : filtré (`HALLU`).

## « Euh » collés aux mots : `fillers` (retour d'Arthur, 08/10/2026, short 1 E22 : « faut que tu enlèves les euhhh,
l'invité le fait beaucoup donc c'est pas assez dynamique »)

- Ni `tighten` (trous entre mots) ni `verify` (verbatim) ne les voyaient : Whisper rattache le « euh » au mot voisin,
  qui dure alors anormalement longtemps (« humaine » 3,2 s, « qu'il » 1,7 s) et le verbatim ne l'écrit pas.
- `clipper/fillers.py` : un « euh » = VOYELLE TENUE (voix au spectre stable ≥ 0,2 s, flux spectral < 40e centile du
  passage). Chaque coupe (bords au point le plus silencieux à ±0,06 s) est VALIDÉE par la transcription normale du
  passage coupé : un mot dit une seule fois ne doit jamais disparaître (un mot répété peut perdre une occurrence) ;
  puis écoute (`audit`) : raccord au milieu d'un mot -> coupe annulée. Hésitation en début de passage : on démarre
  après ; deux « euh » collés : une seule coupe. Morceaux gardés ≥ 0,25 s.
- Commande : `python -m clipper fillers --only N` (sauvegarde `clips_before_fillers.json`), après `verify --fix`,
  avant `build`. Short 1 E22 : 41,8 -> 37,1 s, 4,7 s d'hésitations retirées, tous les mots gardés.

## Sous-titres « variante B » + réactions vides (retours d'Arthur, 09/10/2026)

- « On a choisi la variante B : bold capitals, mot-clé en serif italique minuscule jaune ; adoucis un peu la partie
  bold capital pour que ce soit plus fluide. » -> section `captions:` de brand.yaml (shorts ET teaser) : Metropolis
  **Bold** 700 (pas ExtraBold), capitales, `letter_spacing 0.025em`, aucun trait, ombre en dégradé, fondu mot à mot
  (`reveal: word`, 0,14–0,16 s) ; mot-clé = `fonts.keyword` Lora Italic 600, minuscules, ×1,14, #F2E86D
  (`captions.keyword_style`, appliqué par le template + `captions.py` pour la casse). Remplace le style DUST (Arimo)
  et le bleu des mots-clés du teaser. Variantes testées : `apercus/E22_teaser_sous-titres_{A,B,actuel}_apercu.mp4`.
- Mots-clés des shorts : choisis par le LLM s'il n'y en a pas (`captions.auto_keywords`, `caption_check.pick_keywords`,
  appelé par `polish`) : ~1 tous les 6–10 mots, mots porteurs de sens, copiés exactement.
- « Thomas dit "super intéressant", il faudrait pas ça, ça n'apporte rien » -> **jamais de réaction vide** (liste
  `fillers.REACTIONS` : super intéressant, ah ouais, exactement, d'accord, c'est clair…) : `fillers.drop_reactions`
  retire une réaction prononcée seule entre deux pauses (shorts : étape de `polish` ; teaser : `episode-plan`, et
  extrait entier retiré) ; `TEASER_PROMPT` l'interdit (le rôle « reaction » doit relancer avec du fond). E22 : le
  « Super intéressant. » du teaser (13:10) retiré.

## Sous-titres : double contrôle à l'écoute (retour d'Arthur, 08/10/2026 : « dans les sous-titres il oublie quelques
mots des fois, faudrait une sorte de boucle de double check »)

- Causes mesurées (short 1 E22) : la transcription NORMALE « nettoie » (« c'est », « en fait » ×3, « Et », « Donc »
  dits mais pas écrits) ; un mot à cheval sur une coupe n'était gardé que s'il tenait entier dans un morceau
  (« redéployer », « humaine » perdus après `fillers`) ; à l'inverse un bout de la phrase suivante (« Et puis ») non
  entendu était sous-titré.
- `compose` : mot gardé dans le morceau où on l'entend le plus ; puis `clipper/caption_check.py` (`captions.double_check`,
  défaut true) : l'audio du montage (assets/source.mp4) est écouté 2 fois en verbatim (2e écoute décalée de 0,7 s) ;
  seuls les mots entendus les DEUX fois comptent. Alignement : mot entendu absent -> ajouté ; ordre et horaires = ceux
  de l'écoute (la transcription normale est en avance de ~0,2 s) ; orthographe des sous-titres gardée pour les mots
  communs (noms propres) ; mot sous-titré non entendu au début, à la fin ou à ±0,4 s d'un raccord -> retiré. Contrôle
  final `missing` : rapport `clips/<clip>/captions_check.txt` (+ ajouts, - retraits, RESTE = écart non résolu).
- Coût ≈ 2 écoutes de la durée du clip (~1 min pour 40 s), mises en cache (`heard_words.json`).

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
Exemples du 07/10/2026 : fin trop rapide -> `outro.duration: 3,5` (puis 7 s le 08/10) ; « euh » -> `tighten` ; lien d'aperçu vers un autre
short -> `preview` libère le port ; 16:9 inutile -> `formats: ["9x16"]` ; téléchargement coupé -> `fetch`.

## Fluidité des coupes (demande d'Arthur, 05/10/2026 : « moins de cuts, pas trop couper, plus fluide »)

- La détection de plans (`analysis`) voit de FAUSSES coupes dans des plans continus du montage source ; chacune
  recadrait le visage → saccades. `compose._smooth_plan` fusionne toute coupe détectée où la différence d'image
  de part et d'autre est faible (`_frame_diff` < `framing.false_cut_diff`, 15 ; vraie coupe ≈ 50–70, fausse ≈ 1–6).
  Flash < `min_flash` (0,5 s) : fusionné avec un voisin du même plan source seulement ; s'il est en toute fin de
  clip, la carte de fin démarre plus tôt et le masque (`outro_start`).
- Jonction de segments sur la même caméra = jump cut → `_punch_junctions` resserre le 2e morceau
  (`junction_punch: 1.15` dans aip-short), tenu jusqu'à la vraie coupe suivante. **AI Corner : AUCUN zoom dans les
  shorts** (Arthur, 08/10/2026 : « pour les shorts, ne pas mettre de zoom finalement ») : `framing.junction_punch: 1.0`
  et `framing.synthetic_punch: 1.0` (punch-in alterné sur les plans fixes longs, `reframe`) dans brand.yaml ; le
  teaser garde 1.15 / 1.18 + zoom lent (section `teaser.framing`). Les raccords sur la même caméra restent des coupes
  nettes (l'écran partagé est placé de préférence juste après un raccord pour les masquer).
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

- `build` ne supprime QUE l'ancienne version d'un short de clips.json (même numéro, autre titre) — avant le 09/10/2026
  il supprimait aussi le teaser (99) et ses variantes, en plein rendu. Vidéo redécoupée (`recut`) -> `analysis.json`
  refait : sinon les visages de l'ancienne découpe cadrent la nouvelle (écran partagé vide en haut, short 1 E22).

- Téléchargements Dropbox : dans le navigateur, les fichiers de 13 Go se coupent vers 50 min (E22 : deux fois, les
  3 caméras à ~70 %) et restent sous leur nom final, illisibles (« moov atom not found »). `clipper/fetch.py` liste
  le dossier via `list_shared_link_folder_entries` (cookie CSRF `t`), puis télécharge en HTTP Range avec reprise.
  Les requêtes de téléchargement partent **sans** User-Agent de navigateur (sinon Dropbox renvoie sa page HTML).
- `claude -p` qui échoue avec un message vide : la cause est dans stdout (ex. « OAuth session expired » ->
  lancer `claude` dans un terminal puis `/login`). `TemporaryDirectory(ignore_cleanup_errors=True)` dans `llm.py` :
  sous Windows le dossier temporaire peut rester verrouillé à la sortie de `claude`.

- Aucune fenêtre chez Arthur (08/10/2026 : « arrête d'ouvrir des sessions Chrome, j'ai une pop Chrome souvent ») :
  `render._npx` lance npx avec `CREATE_NO_WINDOW` (pas de console qui clignote) ; le Chrome de rendu est invisible
  (`--headless=new`) ; `preview` n'ouvre plus le navigateur par défaut (`--open` pour le Studio) ; on ne laisse plus
  tourner de serveur Studio (`preview --stop`) puisque les aperçus sont des MP4. Ne pas utiliser le navigateur de
  l'utilisateur pour vérifier une vidéo : planches d'images (`qa`) et `npx hyperframes snapshot`.
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
