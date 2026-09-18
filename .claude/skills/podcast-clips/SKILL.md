---
name: podcast-clips
description: Transforme un épisode de podcast (mp4) en shorts/reels/clips LinkedIn montés (9:16, 16:9, 1:1) avec le framework « clipper » de ce dépôt — transcription, sélection des meilleurs extraits par Claude, recadrage vertical, sous-titres, carte de fin, rendu HyperFrames. Pose d'abord les questions qui manquent (marque, invité, côté de l'animateur, nombre de clips, formats, style), crée la fiche marque si elle n'existe pas, puis lance tout le pipeline et livre les MP4 + textes de publication. Déclencher sur « clip », « short », « reel », « découpe mon podcast », « fais des extraits », « /podcast-clips », ou dès qu'un fichier de podcast est déposé dans le dossier.
---

# Podcast → clips (interview guidée puis pipeline complet)

Tu pilotes le framework `clipper` (voir [CLAUDE.md](../../../CLAUDE.md) et [README.md](../../../README.md)).
Objectif : l'utilisateur donne son podcast et ses envies de montage en langage naturel, tu complètes ce
qui manque par quelques questions, puis tu produis les clips sans qu'il touche à la ligne de commande.

## 0. Vérifier l'environnement (une fois par machine)

```bash
python -m clipper doctor
```

Si quelque chose manque (FFmpeg, Node, dépendances Python, clé Pexels, Claude), dis quoi installer et
arrête-toi là. Ne lance jamais une transcription sur une machine qui n'a pas passé le doctor.

## 1. Comprendre la demande, puis poser UNIQUEMENT les questions manquantes

Lis d'abord le message de l'utilisateur et le dossier : un fichier vidéo déposé à la racine ou dans
`brands/<slug>/episodes/` est l'épisode ; `python -m clipper brands` liste les marques existantes.
Déduis tout ce qui peut l'être (nom du podcast dans le nom du fichier, invité dans une description YouTube
collée, etc.). Ensuite, en UNE seule salve (AskUserQuestion, 4 questions max), demande ce qui reste :

1. **Marque / podcast** — une marque existante (`brands/`) ou nouvelle ? Si nouvelle : nom du podcast, et
   a-t-il des clips déjà publiés ou des exemples de style à déposer dans `references/` ? un logo (PNG fond
   transparent) ? une charte (couleurs, police) ?
2. **L'épisode** — invité (nom + entreprise) et **côté de l'animateur dans le plan large** (gauche / droite) ;
   l'épisode commence-t-il par un teaser déjà sous-titré à exclure ?
3. **La commande** — nombre de clips (défaut 6), formats (9:16 seul, ou + 16:9 LinkedIn, ou + 1:1),
   durée cible, thèmes à privilégier ou à éviter.
4. **Le style de montage** — un des presets : `dynamic` (capitales, mots-clés colorés, typewriter, B-roll :
   style « Dans la tête d'un CEO »), `editorial` (sobre, minuscules centrées, montage multi-segments,
   pas de B-roll : style AI Partners), `clean`, `minimal` ; ou « comme la marque X » ; ou « comme ces clips »
   (références). Demande aussi le texte du CTA de fin si la marque est nouvelle (défaut : « L'épisode complet
   sur {podcast} / Lien en bio »).

Ne redemande jamais ce que l'utilisateur a déjà dit. Si tout est clair, ne pose aucune question.

## 2. Mettre en place la marque si elle n'existe pas

```bash
python -m clipper new-brand <slug>
```

Puis édite `brands/<slug>/brand.yaml` (ne garde que ce qui diffère de `config/defaults.yaml`) et
`brands/<slug>/guidelines.md` (brief éditorial : ton, ce qui fait un bon extrait, à éviter, hashtags),
copie logo/polices dans `assets/`, l'épisode dans `episodes/`, les exemples dans `references/`.

Si des clips de référence sont fournis : **analyse-les avant d'écrire brand.yaml** (extraire une planche
contact avec ffmpeg, regarder typo/casse/couleur/position des sous-titres, mots-clés colorés ou non,
typewriter ou non, présence de B-roll, structure du montage — un passage continu ou plusieurs segments —,
carte de fin) et choisis le preset le plus proche, puis ajuste. Exemple complet : `brands/ai-corner/`
(style monteur, multi-segments) et `brands/dans-la-tete-dun-ceo/` (style punchy).

Résume à l'utilisateur en 5 lignes la charte que tu as déduite et demande une validation rapide
avant de lancer les rendus (la transcription peut démarrer pendant qu'il répond).

## 3. Lancer le pipeline

Toujours étape par étape (chaque étape est en cache, on peut reprendre) :

```bash
python -m clipper transcribe --brand <slug> --input <fichier>            # long : lance-le en arrière-plan
python -m clipper select --brand <slug> --input <fichier> --guest "…" --company "…" --host-side left|right --n 6 [--instructions "…"]
python -m clipper build  --brand <slug> --input <fichier>
python -m clipper render --brand <slug> --input <fichier>                # long : arrière-plan
```

- Transcription ≈ 0,4× la durée de l'épisode sur CPU ; rendu ≈ 4–5× la durée de chaque clip et par format.
  Annonce ces délais, lance en arrière-plan, et occupe-toi du reste pendant ce temps.
- Après `select`, **vérifie mot à mot** le début et la fin de chaque extrait (script : mots avant/après
  chaque borne, voir CLAUDE.md règle 1). Un extrait qui coupe une pensée se corrige dans `clips.json`
  puis `build --only N` ; ne livre jamais un clip tronqué.
- Après `build`, `lint` doit être OK ; pour tout changement de style, fais un `npx hyperframes snapshot`
  et regarde les images avant de rendre.

## 4. Livrer

- Envoie les MP4 (`output/<slug>/<épisode>/renders/`) à l'utilisateur avec SendUserFile, en commençant par
  le format principal.
- Donne le chemin du dossier, le récapitulatif des extraits (titre, timecodes, durée) et les textes de
  publication (`summary.md`).
- Propose les retouches possibles : modifier un extrait dans `clips.json`, ouvrir le Studio HyperFrames
  (`python -m clipper preview … --clip N`), changer le preset, désactiver le CTA.

## Garde-fous

- Jamais de rendu sans avoir montré la sélection (titres + timecodes) à l'utilisateur.
- Un clip qui dépasse la durée cible pour finir une idée est correct ; un clip coupé au milieu ne l'est pas.
- Ne modifie pas `config/defaults.yaml` pour un besoin propre à une marque : passe par `brand.yaml`.
