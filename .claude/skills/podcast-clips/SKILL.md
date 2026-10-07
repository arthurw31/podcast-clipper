---
name: podcast-clips
description: Transforme un épisode de podcast (mp4, idéalement 4K) en 5 shorts verticaux montés + le post LinkedIn de chacun, avec le framework « clipper » de ce dépôt. Workflow en 5 étapes — l'utilisateur dépose l'épisode dans depot/, Claude transcrit et propose une dizaine de passages (thème, titre, timecodes, extrait), l'utilisateur en choisit 5 et ajoute ses demandes particulières (« il faut absolument le passage où il dit… »), puis Claude monte les 5 shorts (charte de la marque, sous-titres, logos, carte de fin) et rédige les posts LinkedIn. Déclencher sur « clip », « short », « reel », « découpe mon podcast », « fais des extraits », « nouveau podcast », « /podcast-clips », ou dès qu'un épisode est déposé dans depot/.
---

# Podcast → 5 shorts + posts LinkedIn

Tu pilotes le framework `clipper` (voir [CLAUDE.md](../../../CLAUDE.md), [README.md](../../../README.md) et le
schéma [docs/FRAMEWORK.md](../../../docs/FRAMEWORK.md)). L'utilisateur est un membre de l'équipe marketing :
il ne tape aucune commande, tu fais tout et tu lui parles simplement (pas de jargon technique).

Le workflow a **deux moments où l'humain décide** : le choix des passages (étape 3) et la validation des shorts
en aperçu (étape 4). Ne monte rien avant le choix, ne rends rien avant la validation.

## 0. Environnement (une fois par machine)

```bash
python -m clipper doctor
```

Si un point est KO : suis les indications (`-> …`), ou lance `scripts\setup.ps1` (Windows) / `scripts/setup.sh`
(macOS). Ne lance jamais une transcription sur une machine qui n'a pas passé le doctor.

## 1. Récupérer et ranger l'épisode

0. **Lien Dropbox** (cas habituel depuis octobre 2026 : rushs de 13–14 Go par caméra) : ne demande jamais de
   télécharger dans le navigateur — il coupe les gros fichiers au bout d'environ 50 min et garde un fichier tronqué
   illisible (« moov atom not found »). Lance toi-même, en arrière-plan (≈ 40 Go en 1 h 30) :

   ```bash
   python -m clipper fetch "<lien Dropbox>" --dest brands/<marque>/episodes/rushes/<E-numéro>
   ```

   Reprise automatique après coupure (y compris d'un fichier partiel déjà présent : `--dest` vers son dossier),
   fichier déclaré complet seulement à la taille exacte annoncée par Dropbox ; si un fichier reste incomplet, relancer
   la même commande. Puis renomme les caméras (`cam1_<personne>.mp4`, `cam2_large.mp4`…) et l'audio en
   `brands/<marque>/episodes/<E-numéro>_<invite>.wav` (la transcription part de l'audio, sans attendre les vidéos).
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

Puis **retire les « euh »** (jamais coupés par Whisper, donc invisibles dans le texte) :

```bash
python -m clipper tighten --brand <marque> --input <fichier>
```

Seuls les vrais trous (> 0,6 s) sont retirés, pour ne pas rendre le short saccadé ; dis à l'utilisateur ce qui a été
retiré et à quels instants écouter les raccords (tu ne peux pas écouter).

Puis **vérifie chaque coupe mot à mot** (règle absolue : ne jamais couper une pensée) :

```bash
python -m clipper check --brand <marque> --input <fichier>
```

Chaque passage doit commencer au début d'une phrase et finir sur une fin de phrase complète ; jamais de mot du
passage suivant (« après » ne doit pas être entamé). Corrige dans `clips.json` et relance `check`.

## 4. Monter les 5 shorts, les faire valider en aperçu, rédiger les posts

```bash
python -m clipper build   --brand <marque> --input <fichier>          # ≈ 1 min/short en 1080p, 2-3 min en 4K (2 en parallèle)
python -m clipper preview --brand <marque> --input <fichier> --no-open
```

`build` doit finir sur « lint OK ». `preview` lance un aperçu **instantané, sans rendu** pour chaque short
(adresses affichées par la commande, port 3002 pour le n° 1, 3003 pour le n° 2, … — utilise-les telles quelles, avec
leur `?v=…`, sinon le navigateur peut réafficher un ancien short gardé en cache) : ouvre-les dans le navigateur intégré
(Claude Browser `navigate`), place-toi à 3 s pour vérifier logos / bulle / sous-titres, puis **donne toujours à
l'utilisateur la liste des liens cliquables**, un par short, avec le titre de la bulle comme texte du lien :

> 1. [Short 1 : <titre de la bulle>](http://localhost:3002/?v=…#project/9x16)
> 2. [Short 2 : <titre de la bulle>](http://localhost:3003/?v=…#project/9x16)
> …
>
> Sur chaque page : ▶ sous l'image pour lire, icône plein écran juste à droite. Dites-moi ce que vous voulez changer.

(Le panneau du navigateur intégré peut être replié : les liens s'ouvrent aussi dans le navigateur habituel.) Si le navigateur intégré n'est pas disponible, relance `preview` sans `--no-open`
(ouverture dans le navigateur du PC).

Pendant qu'il regarde, rédige les posts (ils ne dépendent pas du rendu) :

```bash
python -m clipper posts --brand <marque> --input <fichier> --guest-role "<Rôle> at <Entreprise>" [--episode-url URL]
```

Les posts suivent `brands/<marque>/posts.md` (méthode « post d'un short » : accroche-thèse, contraste, invité,
développement concret, chute, CTA ; anglais pour AI Corner, sans hashtags). Relis-les : rien d'inventé (chiffre,
exemple, citation absents du short), deux posts ne commencent pas pareil.

Retouches demandées sur l'aperçu : modifie `clips.json` (passages, `hook_title`) ou la config de la marque, puis
`build --only N` ; l'aperçu se recharge tout seul. Recommence jusqu'à ce que l'utilisateur valide **tous** les shorts.
Ne lance jamais le rendu final avant cette validation explicite.

## 5. Rendu final et livraison

```bash
python -m clipper render  --brand <marque> --input <fichier> --formats 9x16   # ≈ 6-8 min/short, 2 en parallèle : arrière-plan
python -m clipper preview --brand <marque> --input <fichier> --stop   # arrête les aperçus
```

- Annonce la durée du rendu (≈ 30–40 min pour 5 shorts) ; l'utilisateur peut faire autre chose.
- Envoie les 5 MP4 (`output/<marque>/<episode>/renders/`) avec SendUserFile, dans l'ordre.
- Colle dans le chat, pour chaque short : le titre, la durée, puis son **post LinkedIn** prêt à copier.
- Donne le chemin du dossier.

## Épisode complet (rushs multicam -> épisode monté + teaser)

Sur demande (« monte l'épisode en entier ») : `python -m clipper episode-plan --brand <m> --input <ep>.mp4 --guest … --company … --host "…"`,
présente `episode_plan.md` (durée, coupes du dérushage, extraits du teaser) et demande validation ; puis
`episode-render … --proxy` (aperçu 540p, ~15 min, à envoyer avec SendUserFile) ; après validation seulement,
`episode-render …` (1080p, ~1 h, arrière-plan). Joindre `episode/description_youtube.md` (titre + chapitres).

## Retouches courantes

| Demande | Action |
| --- | --- |
| « coupe trop tôt / trop tard » | ajuster `segments` dans `clips.json`, `check`, `build --only N` (l'aperçu se recharge) ; après livraison : `render --only N --force` |
| « autre titre dans la bulle » | `hook_title` dans `clips.json`, `build --only N` ; après livraison : `render --only N --force` |
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
