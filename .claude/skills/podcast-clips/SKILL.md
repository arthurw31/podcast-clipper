---
name: podcast-clips
description: Transforme un épisode de podcast (mp4, idéalement 4K) en 5 shorts verticaux montés + le post LinkedIn de chacun, avec le framework « clipper » de ce dépôt. Workflow en 5 étapes — l'utilisateur dépose l'épisode dans depot/, Claude transcrit et propose une dizaine de passages (thème, titre, timecodes, extrait), l'utilisateur en choisit 5 et ajoute ses demandes particulières (« il faut absolument le passage où il dit… »), puis Claude monte les 5 shorts (charte de la marque, sous-titres, logos, carte de fin) et rédige les posts LinkedIn. Déclencher sur « clip », « short », « reel », « découpe mon podcast », « fais des extraits », « nouveau podcast », « /podcast-clips », ou dès qu'un épisode est déposé dans depot/.
---

# Podcast → 5 shorts + posts LinkedIn

Tu pilotes le framework `clipper` (voir [CLAUDE.md](../../../CLAUDE.md), [README.md](../../../README.md) et le
schéma [docs/FRAMEWORK.md](../../../docs/FRAMEWORK.md)). **Lis d'abord [docs/BONNES_PRATIQUES.md](../../../docs/BONNES_PRATIQUES.md)** :
ce qui a été validé sur E22 (le résultat de référence) et ce qu'il ne faut plus jamais faire. L'utilisateur est un membre de l'équipe marketing :
il ne tape aucune commande, tu fais tout et tu lui parles simplement (pas de jargon technique).

Le workflow a **deux moments où l'humain décide** : le choix des passages (étape 3) et la validation des shorts
en aperçu MP4 (étape 4). Ne monte rien avant le choix, ne rends rien avant la validation. Entre les deux, chaque
vidéo passe **trois contrôles** : automatique sur le montage (`qa`), automatique sur l'aperçu (`qa --render` +
planche d'images que tu regardes), puis l'humain.

## 0. Environnement (une fois par machine)

```bash
python -m clipper doctor
```

Si un point est KO : suis les indications (`-> …`), ou lance `scripts\setup.ps1` (Windows) / `scripts/setup.sh`
(macOS). Ne lance jamais une transcription sur une machine qui n'a pas passé le doctor.

## 1. Récupérer et ranger l'épisode

0. **Rushs déposés dans `depot/`** (cas habituel depuis octobre 2026 : 3 caméras de 13–14 Go + l'audio WAV) :

   ```bash
   python -m clipper rushes --brand <marque> --episode <E-numéro> --guest "Prénom Nom" --company "Entreprise" --dry-run
   ```

   Regarde la planche `rushes_<E>.jpg` (invité / plan large / animateur bien attribués ?), puis relance sans
   `--dry-run` (ajoute `--host <morceau du nom>` si l'animateur est mal reconnu). La commande range et renomme les
   fichiers, mesure le décalage du micro et écrit `<E>_<invité>_<entreprise>.multicam.json` ; elle donne le
   `--input` et le `--host-side` à utiliser ensuite. Un fichier illisible (téléchargement tronqué) est signalé : le
   faire redéposer.

   **Lien Dropbox** (si les rushs n'ont pas été déposés) : ne demande jamais de
   télécharger dans le navigateur — il coupe les gros fichiers au bout d'environ 50 min et garde un fichier tronqué
   illisible (« moov atom not found »). Lance toi-même, en arrière-plan (≈ 40 Go en 1 h 30) :

   ```bash
   python -m clipper fetch "<lien Dropbox>" --dest depot
   ```

   Reprise automatique après coupure (y compris d'un fichier partiel déjà présent : `--dest` vers son dossier),
   fichier déclaré complet seulement à la taille exacte annoncée par Dropbox ; si un fichier reste incomplet, relancer
   la même commande. Puis range-les avec `rushes` comme ci-dessus.
1. Regarde `depot/` (sinon les fichiers récents de Téléchargements). Vérifie toujours qu'une vidéo s'ouvre
   (`ffprobe`) : « moov atom not found » = téléchargement tronqué -> `fetch` (avec le lien) pour la compléter. Range chaque fichier et dis où tu l'as mis :
   épisode → `brands/<marque>/episodes/<E-numéro>_<invite>_<entreprise>.mp4` ; logo de l'entreprise invitée →
   `brands/<marque>/assets/guests/<entreprise>.svg|png` ; clips de référence → `references/` ; logos/polices → `assets/`.
2. Préfère toujours la **version 4K** de l'épisode si elle existe (bien plus net en vertical). Vérifie avec
   `ffprobe` : 3840×2160 attendu ; si c'est du 1080p, signale-le en une phrase et continue.
3. Demande en UNE salve (AskUserQuestion) uniquement ce qui manque :
   - la marque (`python -m clipper brands` ; par défaut `ai-corner`) ;
   - l'invité : prénom nom, **rôle**, entreprise, et son **logo** (SVG ou PNG transparent) s'il n'est pas déposé ;
   - le côté de l'animateur dans le plan large (gauche / droite) — regarde une image de l'épisode pour le déduire
     toi-même (`ffmpeg -ss 600 -i … -frames:v 1`) avant de demander ;
   - le lien de l'épisode complet (YouTube) s'il existe déjà — sinon les posts finiront par « Link in the comments ».
4. Lance la transcription **en arrière-plan** (≈ 20 min pour 50 min d'épisode sur CPU) :

```bash
python -m clipper transcribe --brand <marque> --input <fichier> --guest "Prénom Nom" --company "Entreprise"
```

   `--guest` / `--company` sont donnés à Whisper (avec le lexique de la marque, `transcribe.vocabulary`) pour qu'il
   écrive correctement les noms propres ; les quasi-homonymes restants sont corrigés automatiquement
   (`transcribe.corrections`). Jette quand même un œil aux noms dans les 10 propositions ; si un terme revient mal
   écrit, ajoute-le au lexique de la marque dans `brand.yaml`.

## 1 bis. Les passages sont déjà choisis (fournis par l'équipe)

Si l'utilisateur colle une liste de passages (timecodes + « De … jusqu'à … »), saute les étapes 2 et 3 :
écris un fichier `output/<marque>/<episode>/passages.yaml` puis lance `passages` :

```yaml
- start: "00:18:17:09"          # HH:MM:SS(:images à 24 i/s) ou MM:SS ; « ~ » accepté
  end: "00:18:50:19"
  start_text: "Et donc, il y a un aspect humain qui est énorme"
  end_text: "ils ont pas le temps et l'envie de le faire"
  hook_title: "Pourquoi les équipes n'adoptent pas l'IA ?"   # à écrire s'il n'est pas fourni (style de la marque)
  turns: [{at: "00:18:17", speaker: guest}]                  # qui parle : host (animateur) / guest (invité)
```

```bash
python -m clipper passages --brand <marque> --input <fichier> --file output/<marque>/<episode>/passages.yaml --guest "…" --company "…" --guest-role "<Rôle> at <Entreprise>" --host-side left|right
python -m clipper check    --brand <marque> --input <fichier>
```

- **Qui parle** : les `turns` servent au cadrage ET à l'attribution dans les posts (sans eux, le post attribue tout à
  l'invité). Vérifie-le sur des images de l'épisode (`ffmpeg -ss <t> -i … -frames:v 1`, la source montre en général
  celui qui parle) et complète `turns` dans `clips.json`.
- Si la fin citée tombe au milieu d'une phrase, prolonge jusqu'à la fin de la phrase (règle absolue) et dis-le.
- Si une phrase citée est introuvable, Whisper l'a peut-être ratée (deux personnes qui parlent en même temps) :
  re-transcris 20 s autour (faster-whisper, `word_timestamps=True`) et insère les mots manquants dans
  `transcript.json` à leurs horaires, puis coupe juste avant que l'autre personne enchaîne.

Puis reprends à l'étape 4.

## 2. Proposer une dizaine de passages

```bash
python -m clipper propose --brand <marque> --input <fichier> --guest "…" --company "…" --host-side left|right --n 10 [--instructions "…"]
```

Trois lectures de l'épisode en parallèle (une par angle éditorial de la marque), puis un « jury » garde les 10
meilleurs, variés. Sortie : `output/<marque>/<episode>/candidates.md` (+ `candidates.json`).

Présente-les à l'utilisateur dans le chat, numérotés, chacun en 3 lignes : **le titre de la bulle** (la question),
le thème + timecodes + durée, et une phrase-clé de ce qu'on entend. Puis demande :
« Lesquels gardez-vous ? (5 numéros, ex. 1, 3, 4, 7, 9) — et y a-t-il un passage précis que vous voulez
absolument, ou quelque chose à éviter ? »

## 3. Choix de l'utilisateur et demandes particulières

```bash
python -m clipper pick --brand <marque> --input <fichier> --ids 1,3,4,7,9
```

Pour chaque demande particulière :
- « il faut absolument le passage où il dit … » → `python -m clipper find --brand … --input … "mots de la phrase"`
  donne le timecode et le contexte. Intègre ce passage au short le plus proche par le thème (comme accroche ou
  conclusion), ou remplace un candidat — en éditant `segments` dans `output/…/clips.json` (début = début d'une
  phrase, fin = fin d'une phrase) ; dis à l'utilisateur ce que tu as fait.
- « pas ce sujet / pas ce chiffre » → retire ou recoupe le segment concerné.
- un changement de titre de bulle → `hook_title` dans `clips.json`.

Puis **vérifie chaque coupe mot à mot** (règle absolue : ne jamais couper une pensée) :

```bash
python -m clipper check --brand <marque> --input <fichier>
```

Chaque passage doit commencer au début d'une phrase et finir sur une fin de phrase complète ; jamais de mot du
passage suivant. Corrige dans `clips.json` et relance `check`.

## 4. Nettoyer, monter et contrôler — `polish` (une seule commande, contrôles à chaque étape)

```bash
python -m clipper polish --brand <marque> --input <fichier> --only 1,2,3,4,5      # arrière-plan, ≈ 6–8 min/short
```

`polish` enchaîne, pour chaque short, dans UN seul processus (Whisper chargé une fois) :

1. `tighten` — blancs et « euh » entre les mots (trous > 0,6 s ; en dessous = respiration, on garde) ;
2. `verify --fix` — contrôle « à l'oreille » : vraies fins de phrase (ponctuation stable + vraie pause dans le son),
   bords recalés sur la voix, « euh » isolés retirés, et **chaque fin attend la fin de la voix + ~0,35 s de
   respiration, sans jamais entendre le début de la phrase suivante** (`verify.pad_end`) ;
3. `fillers` — « euh » collés aux mots (voyelles tenues détectées dans le son) ; toutes les coupes d'un passage
   validées en une écoute, sinon une par une : jamais un mot perdu, jamais un raccord dans un mot ;
4. `build` — montage ; les **sous-titres sont contrôlés à l'écoute** : le montage est écouté 2 fois, tout mot entendu
   les 2 fois est sous-titré, les bouts de phrase non entendus près d'un raccord sont retirés
   (`clips/<clip>/captions_check.txt`) ; flash lumineux seulement quand l'angle de caméra change ;
5. `qa` — **1er contrôle** (rapport `output/<marque>/<episode>/qa/clip_NN.md`) : raccords à l'écoute, fins jamais
   pendant la voix, début de la phrase suivante jamais entendu, sous-titres complets, composition valide, durée ;
   un ÉCHEC arrête la chaîne (à corriger, puis relancer `polish --only N`) ;
6. aperçus **MP4** (12 i/s, brouillon, 2 à la fois ; ≈ 2–3 min/short) dans `output/…/apercus/` ;
7. `qa --render` — **2e contrôle** sur l'aperçu : format, durée, volume -16 LUFS, et une **planche d'images**
   (`qa/qa_images_NN.png`) que tu DOIS regarder (Read) avant d'envoyer le lien : visages cadrés, logos, bulle,
   sous-titres, carte de fin.

Les étapes 1–3 déjà faites sont sautées (`checks` dans `clips.json`) : relancer `polish` après une retouche ne
recoupe rien deux fois. `--redo` repart des passages d'origine (`clips_before_tighten.json`).

Puis le **3e contrôle, humain** : envoie à l'utilisateur les liens cliquables vers les MP4 (jamais seulement le
Studio HyperFrames, qui bugue chez Arthur), un par short, avec le titre de la bulle, plus ce que le rapport `qa`
signale (ATTENTION) et les instants à écouter de près :

> 1. [Short 1 : <titre de la bulle>](output/<marque>/<episode>/apercus/clip_01_…_9x16_apercu.mp4)
> 2. [Short 2 : <titre de la bulle>](output/<marque>/<episode>/apercus/clip_02_…_9x16_apercu.mp4)
> …
>
> Aperçus rapides (image un peu saccadée, normal) : dites-moi ce que vous voulez changer.

Pendant qu'il regarde, rédige les posts (ils ne dépendent pas du rendu) :

```bash
python -m clipper posts --brand <marque> --input <fichier> --guest-role "<Rôle> at <Entreprise>" [--episode-url URL]
```

Les posts suivent `brands/<marque>/posts.md` (méthode « post d'un short » : accroche-thèse, contraste, invité,
développement concret, chute, CTA ; anglais pour AI Corner, sans hashtags). Relis-les : rien d'inventé (chiffre,
exemple, citation absents du short), deux posts ne commencent pas pareil.

Retouches : modifie `clips.json` (passages, `hook_title`) ou la config de la marque, puis `polish --only N`. **Chaque
retour qui vaut pour la suite devient une règle durable** (code ou config + CLAUDE.md + mémoire), avec la date et
la phrase de l'utilisateur. Recommence jusqu'à ce que l'utilisateur valide **tous** les shorts. Jamais de rendu
final avant cette validation explicite.

## 5. Rendu final, dernier contrôle et livraison

```bash
python -m clipper render --brand <marque> --input <fichier> --formats 9x16 --only N    # ≈ 10 min/short, 2 en parallèle : arrière-plan
python -m clipper qa     --brand <marque> --input <fichier> --only N --render          # contrôle du MP4 final + planche d'images
```

- Ne réécrase jamais un MP4 déjà livré : renomme l'ancien (`…_v1_<date>.mp4`) avant le rendu (un fichier ouvert
  dans le lecteur de l'utilisateur bloque l'écriture).
- `qa --render` doit être OK (ou ATTENTION expliquée) ; regarde la planche d'images du MP4 final.
- Envoie les MP4 (`output/<marque>/<episode>/renders/`) avec SendUserFile, dans l'ordre ; colle pour chaque short le
  titre, la durée et son **post LinkedIn** prêt à copier ; donne le chemin du dossier.

## Épisode complet (rushs multicam -> épisode monté + teaser)

Sur demande (« monte l'épisode en entier ») : `python -m clipper episode-plan --brand <m> --input <ep>.mp4 --guest … --company … --host "…"`,
présente `episode_plan.md` (durée, coupes du dérushage, extraits du teaser) et demande validation ; puis
`episode-render … --proxy` (aperçu 540p, ~15 min) : il enchaîne le contrôle qualité (`qa/episode_apercu.md` : raccords
réécoutés, celui qui parle à l'image, volumes). Lis le rapport : ÉCHEC ou point à corriger -> retouche (plan, coupes, teaser) et
nouvel aperçu, jusqu'à un contrôle propre ; ALORS seulement envoie l'aperçu (SendUserFile) ; après validation seulement,
`episode-render …` (1080p, ~1 h, arrière-plan ; pour juger seulement teaser + raccord + volumes :
`episode-render … --proxy --minutes 2`, ~5 min). Puis **`qa --episode`** : réécoute de chaque raccord DANS le MP4
final (aucun « euh », aucun mot coupé, fin jamais pendant la voix), volumes teaser / épisode à ±1,5 LUFS, planche
d'images (logo en haut à droite, carte de fin) à regarder. Joindre `episode/description_youtube.md` (titre +
chapitres). Teaser et corps sont rendus séparément : retoucher le teaser ne refait pas le corps (~15 min au lieu d'1 h).

## Titre et miniature de l'épisode (rushs multicam)

Le titre de l'épisode est validé par l'équipe marketing AVANT les miniatures. À lancer après `transcribe`, en parallèle des
shorts (aucune attente mutuelle) :

1. `python -m clipper titles --brand <marque> --input <E>.mp4` : 5 titres contrôlés, en TEXTE (chacun prouvé par un passage
   de l'épisode, format `AI Corner E<n> | …`, brief `brands/<marque>/titles.md`). Donne les 5 titres à Arthur pour l'équipe
   marketing, avec le lien vers `titres/propositions.md`. Aucune miniature à ce stade.
2. L'équipe répond par un numéro (ou corrige une ligne) : `titles … --pick N [--lines "ligne 1 | *ligne 2"] --by <prénom>
   --note "<remarque>"`. Toute remarque de fond devient une ligne « À éviter » ou une règle dans `titles.md`.
3. `python -m clipper thumbnail --brand <marque> --input <E>.mp4` : planche numérotée de 4 miniatures avec le titre validé (photos
   où les deux sourient en se regardant). Regarde `miniatures/planche.jpg` avant de l'envoyer à Arthur pour l'équipe.
4. L'équipe répond par le numéro de sa préférée : `python -m clipper thumbnail … --pick N --by <prénom>` -> `miniatures/
   miniature_finale.jpg` (+ `_HD.png`) : le livrable à téléverser sur YouTube avec le titre validé.

`thumbnail` s'arrête s'il n'y a pas de titre validé ; la description YouTube de l'épisode reprend le même titre.
Si tu modifies ce process, mets à jour le schéma de GitHub (`docs/schema.mmd` puis `python scripts/render_schema.py`,
docs/ARCHITECTURE.md) : voir CLAUDE.md, « Règle de maintenance », et lance `python scripts/check_schema.py`.

## Retouches courantes

| Demande | Action |
| --- | --- |
| « coupe trop tôt / trop tard » | ajuster `segments` dans `clips.json`, `check`, `polish --only N` ; après livraison : renommer l'ancien MP4, `render --only N`, `qa --render --only N` |
| « autre titre dans la bulle » | `hook_title` dans `clips.json`, `polish --only N` ; après livraison : idem |
| « il reste des euh » | `polish --only N --redo` ; si un « euh » précis reste, le signaler (instant) : seuil de `fillers.held_vowels` |
| « il manque des mots dans les sous-titres » | lire `clips/<clip>/captions_check.txt` (RESTE = écart) ; `captions.double_check` doit être actif |
| « c'est saccadé » | ne pas ajouter de coupes : voir `framing` (max_shot_len, min_reframe_len) dans `brand.yaml` |
| « refais le post » | `posts --only N --force` (ajouter une consigne dans `posts.md` si c'est un défaut récurrent) |
| « nouvelle marque / autre podcast » | `python -m clipper new-brand <slug>`, puis brand.yaml, guidelines.md, posts.md (voir README) |

## Garde-fous

- Jamais de montage sans le choix explicite de l'utilisateur (étape 3), jamais de rendu final sans sa validation
  sur l'aperçu (étape 4).
- Un short qui dépasse un peu la durée cible pour finir une idée est correct ; un short coupé au milieu ne l'est pas.
- Ne modifie pas `config/defaults.yaml` pour un besoin propre à une marque : passe par `brands/<marque>/brand.yaml`.
- Un retour de l'utilisateur sur le style qui vaut pour la suite (« moins de coupes », « logos plus grands »)
  se règle dans la config de la marque ou le preset, pas seulement sur ce short — et se note dans CLAUDE.md.
