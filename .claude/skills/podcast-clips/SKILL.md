---
name: podcast-clips
description: Transforme un épisode de podcast (mp4, idéalement 4K) en 5 shorts verticaux montés + le post LinkedIn de chacun, avec le framework « clipper » de ce dépôt. Workflow en 5 étapes — l'utilisateur dépose l'épisode dans depot/, Claude transcrit et propose une dizaine de passages (thème, titre, timecodes, extrait), l'utilisateur en choisit 5 et ajoute ses demandes particulières (« il faut absolument le passage où il dit… »), puis Claude monte les 5 shorts (charte de la marque, sous-titres, logos, carte de fin) et rédige les posts LinkedIn. Déclencher sur « clip », « short », « reel », « découpe mon podcast », « fais des extraits », « nouveau podcast », « /podcast-clips », ou dès qu'un épisode est déposé dans depot/.
---

# Podcast → 5 shorts + posts LinkedIn

Tu pilotes le framework `clipper` (voir [CLAUDE.md](../../../CLAUDE.md), [README.md](../../../README.md) et le
schéma [docs/FRAMEWORK.md](../../../docs/FRAMEWORK.md)). L'utilisateur est un membre de l'équipe marketing :
il ne tape aucune commande, tu fais tout et tu lui parles simplement (pas de jargon technique).

Le workflow a **deux moments où l'humain décide** (étapes 2 et 3). Ne les saute jamais : ne monte rien avant
que l'utilisateur ait choisi ses passages.

## 0. Environnement (une fois par machine)

```bash
python -m clipper doctor
```

Si un point est KO : suis les indications (`-> …`), ou lance `scripts\setup.ps1` (Windows) / `scripts/setup.sh`
(macOS). Ne lance jamais une transcription sur une machine qui n'a pas passé le doctor.

## 1. Récupérer et ranger l'épisode

1. Regarde `depot/` (sinon les fichiers récents de Téléchargements). Range chaque fichier et dis où tu l'as mis :
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
passage suivant (« après » ne doit pas être entamé). Corrige dans `clips.json` et relance `check`.

## 4. Monter les 5 shorts et rédiger les posts

```bash
python -m clipper build  --brand <marque> --input <fichier>          # projets HyperFrames (lint OK attendu)
python -m clipper posts  --brand <marque> --input <fichier> --guest-role "<Rôle> at <Entreprise>" [--episode-url URL]
python -m clipper render --brand <marque> --input <fichier>          # long : arrière-plan
```

- Le rendu prend ≈ 12 × la durée de chaque short (2 en parallèle) : ≈ 30–40 min pour 5 shorts de 35 s. Annonce-le,
  lance-le en arrière-plan, et pendant ce temps relis les posts.
- Avant le rendu, contrôle visuellement un short : `npx hyperframes snapshot --at 2,10,20 --no-end` dans
  `output/…/clips/clip_01_…/9x16/` (logos centrés en haut, bulle-titre, sous-titres, carte de fin).
- Les posts suivent `brands/<marque>/posts.md` (méthode « post d'un short » : accroche-thèse, contraste, invité,
  développement concret, chute, CTA ; anglais pour AI Corner, sans hashtags). Relis-les : rien d'inventé
  (chiffre, exemple, citation absents du short), deux posts ne commencent pas pareil. Corrige à la main dans
  `output/…/posts/clip_NN_….md` si besoin, ou `posts --only N --force`.

## 5. Livrer

- Envoie les 5 MP4 (`output/<marque>/<episode>/renders/`) avec SendUserFile, dans l'ordre.
- Colle dans le chat, pour chaque short : le titre, la durée, puis son **post LinkedIn** prêt à copier.
- Donne le chemin du dossier et propose les retouches : changer un passage, un titre, un post, refaire un short.

## Retouches courantes

| Demande | Action |
| --- | --- |
| « coupe trop tôt / trop tard » | ajuster `segments` dans `clips.json`, `check`, puis `build --only N` et `render --only N --force` |
| « autre titre dans la bulle » | `hook_title` dans `clips.json`, `build --only N`, `render --only N --force` |
| « c'est saccadé » | ne pas ajouter de coupes : voir `framing` (max_shot_len, min_reframe_len) dans `brand.yaml` |
| « refais le post » | `posts --only N --force` (ajouter une consigne dans `posts.md` si c'est un défaut récurrent) |
| « nouvelle marque / autre podcast » | `python -m clipper new-brand <slug>`, puis brand.yaml, guidelines.md, posts.md (voir README) |

## Garde-fous

- Jamais de montage sans le choix explicite de l'utilisateur (étape 3).
- Un short qui dépasse un peu la durée cible pour finir une idée est correct ; un short coupé au milieu ne l'est pas.
- Ne modifie pas `config/defaults.yaml` pour un besoin propre à une marque : passe par `brands/<marque>/brand.yaml`.
- Un retour de l'utilisateur sur le style qui vaut pour la suite (« moins de coupes », « logos plus grands »)
  se règle dans la config de la marque ou le preset, pas seulement sur ce short — et se note dans CLAUDE.md.
