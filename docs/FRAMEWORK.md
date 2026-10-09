# Guide pratique : délais, aperçus et quel fichier modifier

Le process complet (rushs -> shorts, épisode + teaser, titre et miniature) est décrit par le **schéma du
[README](../README.md#vue-densemble--des-rushs-du-tournage-à-tous-les-livrables)** : c'est la seule référence, tenue à
jour à chaque changement. Cette page ne refait pas le schéma ; elle répond aux questions pratiques de l'équipe.

> Ce qui a été validé (et ce qu'il faut éviter) : [BONNES_PRATIQUES.md](BONNES_PRATIQUES.md). Fonctionnement technique :
> [ARCHITECTURE.md](ARCHITECTURE.md).

## Combien de temps ? (PC sans GPU, épisode de 50 min)

| Étape | Qui | Durée |
| --- | --- | --- |
| Dépôt + transcription | vous déposez les 3 rushs MP4 et l'audio WAV dans `depot/`, Claude les range, les synchronise et transcrit | ≈ 1 min + ≈ 20 min (automatique) |
| 10 passages proposés | Claude | ≈ 8 min |
| Choix des 5 passages + demandes | **vous** | 2 min |
| Nettoyage, montage, contrôle qualité, aperçus, posts | Claude prépare, **vous regardez les aperçus et validez** | ≈ 6–8 min par short, ≈ 3 min par retouche |
| Rendu final des shorts | Claude | ≈ 10 min par short, 2 en parallèle, une seule fois |
| 5 titres proposés -> choix | Claude, puis **vous** | quelques minutes |
| 4 miniatures avec le titre choisi -> choix | Claude, puis **vous** | ≈ 5 min |
| Outro des shorts (avec la miniature choisie) | Claude | ≈ 3 min + ≈ 1 min par short |
| Épisode complet : aperçu 540p + contrôle | Claude | ≈ 15 min |
| Épisode complet : rendu 1080p | Claude, après **votre** validation | ≈ 1 h |

## Aperçu ou rendu ?

Une vidéo est d'abord une « page » (images + sous-titres, logos, transitions programmés par dessus). L'**aperçu MP4**
en est une version rapide (un peu saccadée, 12 images/s) à ouvrir dans votre lecteur vidéo habituel : idéal pour
vérifier le contenu. Le **rendu** final la transforme en MP4 net et fluide (≈ 10 min par short) : on ne le fait
qu'une fois, après votre validation. Avant qu'un aperçu vous soit montré, le contrôle qualité automatique est passé
(aucun mot coupé à un raccord, fins de phrase complètes, « euh » retirés sans perdre un mot, sous-titres vérifiés à
l'écoute, volume -16 LUFS) ; tant qu'il trouve un défaut, Claude corrige et recommence. Rapports : `output/…/qa/`.

## Modifier le format (tout est dans des fichiers texte)

| Je veux changer… | Fichier | Exemple |
| --- | --- | --- |
| la durée des shorts, le nombre de propositions | `brands/<marque>/brand.yaml` → `selection` | `min_duration: 25`, `max_duration: 45` |
| les formats (vertical, LinkedIn 16:9, carré) | `brand.yaml` → `formats` | `["9x16", "16x9"]` |
| ce que Claude doit chercher / éviter | `brands/<marque>/guidelines.md` | « éviter les passages sur la levée de fonds » |
| le style des posts LinkedIn | `brands/<marque>/posts.md` | coller de nouveaux posts publiés en exemples |
| le style des titres d'épisode | `brands/<marque>/titles.md` | ajouter un titre refusé dans « À éviter » |
| l'outro des shorts (texte, bouton, durée) | `brand.yaml` → `short_outro` | `cta: "Regarder l'épisode — lien en description"` |
| le gabarit des miniatures | réimport du design Canva (`thumbnail-template`) | nouveau design exporté en PPTX + PNG |
| les logos, la police, l'animation de fin | `brands/<marque>/assets/` | remplacer `outro_anim.mov` |
| le style de montage (bulle, sous-titres, coupes) | `config/presets/aip-short.yaml` | taille des sous-titres, durée de la bulle |
| tout le reste (valeurs par défaut commentées) | `config/defaults.yaml` | — |

Le plus simple reste de le demander à Claude dans Claude Code (« les sous-titres plus gros », « shorts de
45 secondes ») : il sait quel fichier modifier.

## Ajouter un autre podcast

`python -m clipper new-brand <nom>` crée `brands/<nom>/` à partir du modèle. Déposez 2 ou 3 shorts déjà publiés
et quelques posts LinkedIn dans `depot/` et demandez à Claude de « caler la charte sur ces exemples ».
