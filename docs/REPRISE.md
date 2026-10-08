# Reprise de session — état au 08/10/2026

À lire par une nouvelle session Claude Code qui reprend le projet (changement de compte Claude : même PC, mêmes
dossiers). Les règles durables sont dans [CLAUDE.md](../CLAUDE.md) ; ce fichier dit **où on en est** et **ce qui reste**.

## Contexte en 5 lignes

- Projet : framework `clipper` qui transforme un podcast en shorts verticaux + posts LinkedIn, et désormais en
  **épisode complet monté** + **teaser** à partir des **rushs multicam** (3 caméras + 1 WAV).
- Utilisateur : Arthur (AI Partners), non-développeur ; il parle en français, veut des explications simples, des liens
  cliquables vers les aperçus, et que **chaque retour devienne une règle durable** (code/config + CLAUDE.md).
- Marque en cours : `ai-corner` (podcast AI Corner d'AI Partners). Épisode en cours : **E22**, Thomas Spitz (CEO AI
  Partners, animateur, à gauche dans le plan large) reçoit **Nicolas Comestaz**, VP Global Data & AI CoE chez **Coty**.
- Règle d'or : **ne jamais produire un MP4 final sans validation d'Arthur sur l'aperçu** (HyperFrames Studio pour
  shorts/teaser, aperçu 540p pour l'épisode complet).
- Économie de tokens : Arthur conçoit avec Opus et fait tourner avec Sonnet.

## Fichiers de E22

| Quoi | Où |
|---|---|
| Rushs (40 Go, non versionnés) | `brands/ai-corner/episodes/rushes/E22/cam1_nicolas.mp4`, `cam2_large.mp4`, `cam3_thomas.mp4` |
| Audio micro | `brands/ai-corner/episodes/E22_thomas-spitz.wav` |
| « Épisode » (lien dur vers le plan large) + description des caméras | `E22_thomas-spitz.mp4` + `E22_thomas-spitz.multicam.json` |
| Tout le travail généré | `output/ai-corner/e22-thomas-spitz/` (transcript, clips.json, teaser_*, episode_plan.*, work/) |

Commande de base : `python -m clipper <commande> --brand ai-corner --input E22_thomas-spitz.mp4`
(+ `--guest "Nicolas Comestaz" --company Coty --host "Thomas Spitz (CEO AI Partners)"` pour `episode-*`).

## Où on en est

1. **5 shorts E22** : rendus et livrés le 07/10 (`output/.../renders/clip_0N_*_9x16.mp4`) avec leurs posts LinkedIn
   (`output/.../posts/`). Ils sont dans l'ancien style (gros plan seul).
2. **Short 1 refait** avec la nouvelle règle « gros plan + écran partagé vertical de temps en temps » (retour de l'équipe)
   et la nouvelle vérification des coupes : monté, **aperçu validé « pas mal, garde ça de côté »** par Arthur, **pas
   encore rendu en MP4**. En attente du retour de l'équipe pour refaire les shorts 2 à 5 de la même façon.
3. **Épisode complet** : aperçu 540p fait le 07/10 (`episode/E22_thomas-spitz_episode_apercu.mp4`), Arthur a dit « bien
   pour le montage global » ; depuis : logo agrandi en haut à droite (validé sur image), teaser refait. **Rendu final
   1080p pas encore lancé** (`episode-render` sans `--proxy`, ~1 h) — attendre le feu vert.
4. **Teaser** : refait sur le modèle des teasers « Dans la tête d'un CEO » (framework :
   [docs/TEASER_FRAMEWORK.md](TEASER_FRAMEWORK.md)). Dernière version = aperçu HyperFrames (clip 99, 16:9), **en attente
   de l'avis d'Arthur** (il demandait : thèse sans hésitation, vrai ping-pong, chute courte, sous-titres plus contrastés,
   zooms et transitions dosés — tout cela est fait). Les MP4 `E22_teaser*.mp4` dans `episode/` sont des versions
   ANTÉRIEURES (v3 = style précédent).

## Pour relancer les aperçus (les serveurs ne survivent pas au changement de session)

```bash
python -m clipper preview --brand ai-corner --input E22_thomas-spitz.mp4 --clip 99 --formats 16x9 --no-open   # teaser (port 3100)
python -m clipper preview --brand ai-corner --input E22_thomas-spitz.mp4 --clip 1 --no-open                   # short 1 (port 3002)
```

Donner à Arthur les liens affichés (avec leur `?v=…`). MP4 du teaser après validation : rendu HyperFrames du dossier
`output/ai-corner/e22-thomas-spitz/clips/clip_99_teaser/16x9` vers un NOUVEAU nom (un ancien MP4 ouvert dans le lecteur
d'Arthur bloque l'écriture : « EPERM rename »). Épisode final : `python -m clipper episode-render … ` (sans `--proxy`).

## Ce qui reste à faire (dans l'ordre probable)

1. Avis d'Arthur sur le teaser -> retouches éventuelles -> MP4 du teaser.
2. Feu vert -> rendu final 1080p de l'épisode complet (teaser + épisode + fin), puis `episode/description_youtube.md`
   (titre + chapitres) à lui donner.
3. Retour de l'équipe sur le short 1 -> refaire les shorts 2 à 5 (`verify --only N --fix` en partant de
   `clips_before_tighten.json`, puis `build --only N --force`), aperçus, puis rendu.
4. Les anciens fichiers `E22_teaser.mp4` / `_v2` / `_v3` peuvent être supprimés par Arthur (pas par Claude).

## Points techniques à connaître (détails dans CLAUDE.md)

- Nouvelles commandes : `fetch` (Dropbox), `tighten`, `verify` (contrôle « à l'oreille »), `episode-plan`,
  `episode-render`. Nouveaux modules : `multicam.py`, `diarize.py`, `episode.py`, `verify.py`, `fetch.py`, `tighten.py`.
- `claude -p` (utilisé par l'outil pour choisir les extraits et écrire les posts) a sa propre connexion : si elle a
  expiré (« OAuth session expired »), lancer `claude auth login` dans un terminal et valider dans le navigateur
  (avec le NOUVEAU compte). Le teaser utilise Opus via ce canal (`episode.teaser_model`).
- Le PC n'a pas de GPU : transcription ≈ 0,4× la durée, rendu HyperFrames ≈ 12× la durée du clip. Garder le PC branché.
- Windows bloque certaines DLL (sherpa-onnx) : ne pas toucher au réglage de sécurité.
