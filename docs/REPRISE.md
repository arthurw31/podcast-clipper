# Reprise de session — état au 08/10/2026 (fin d'après-midi)

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

## Où on en est (08/10/2026, fin de journée)

1. **5 shorts E22** livrés le 07/10 dans l'ancien style (gros plan seul) : `renders/clip_0N_*_9x16.mp4` + posts.
2. **Short 1 refait (nouveau style), RENDU en 1080p** après `polish` + `qa` : `renders/clip_01_…_9x16.mp4`
   (≈ 44 s : 36,8 s de parole + fin AI Partners de 7 s). « Euh » collés retirés (`fillers`), sous-titres doublement
   contrôlés à l'écoute, fins avec respiration sans jamais entendre la phrase suivante, flash seulement aux changements
   de plan. Anciennes versions gardées : `…_v1_0710.mp4`, `…_v2_sans-double-check.mp4`.
3. **Teaser + épisode complet** : un MP4 1080p a été rendu le 08/10 (`episode/E22_teaser_final.mp4`,
   `episode/E22_thomas-spitz_episode.mp4`) MAIS depuis : fins d'extraits corrigées (`pad_end`), musique « Driving
   Momentum » (composée par Arthur) sous le teaser, volumes teaser / épisode égalisés (-16 LUFS par partie), fin de
   l'épisode sur « À bientôt » entier, raccords de dérushage recalés. Aperçus MP4 envoyés à Arthur
   (`apercus/E22_teaser_apercu.mp4`, `episode/E22_thomas-spitz_episode_apercu_2min.mp4`) ; **en attente du retour de
   ses collègues**, puis : renommer `renders/teaser.mp4`, `episode-render` (1080p : ~15 min, seuls le teaser et
   quelques plans sont refaits), `qa --episode`.

## Aperçus

Toujours en MP4 (le Studio HyperFrames bugue chez Arthur) : `preview --mp4 --clip N` (12 i/s, ~2–3 min) ou via
`polish` ; épisode : `episode-render … --proxy [--minutes 2]`. Donner des liens cliquables vers les fichiers.

## Ce qui reste à faire (dans l'ordre probable)

1. Retour des collègues d'Arthur sur le teaser et l'épisode -> retouches -> rendu final -> `qa --episode`.
2. Shorts 2 à 5 dans le nouveau style : `python -m clipper polish --brand ai-corner --input E22_thomas-spitz.mp4
   --only 2,3,4,5 --redo` (repart des passages d'origine), regarder les planches `qa/`, envoyer les aperçus MP4,
   validation, renommer les MP4 du 07/10, `render --only N`, `qa --render --only N`.
3. Pistes de la recherche d'outils (08/10) mises de côté par Arthur (« on reste sur notre workflow ») : Silero VAD
   (déjà installé), alignement CTC français, ducking de la musique, détection de parole simultanée (pyannote ONNX).
4. Les anciens fichiers `E22_teaser.mp4` / `_v2` / `_v3` peuvent être supprimés par Arthur (pas par Claude).

## Points techniques à connaître (détails dans CLAUDE.md)

- Chemin normal d'un short : `polish` puis `render` puis `qa --render` (skill + CLAUDE.md, « Workflow double /
  triple check »). Commandes : `fetch`, `tighten`, `verify`, `fillers`, `polish`, `qa`, `episode-plan`, `episode-render`. Modules : `multicam.py`, `diarize.py`, `episode.py`, `verify.py`, `fetch.py`, `tighten.py`, `fillers.py`,
  `caption_check.py`, `qa.py`.
- `claude -p` (utilisé par l'outil pour choisir les extraits et écrire les posts) a sa propre connexion : si elle a
  expiré (« OAuth session expired »), lancer `claude auth login` dans un terminal et valider dans le navigateur
  (avec le NOUVEAU compte). Le teaser utilise Opus via ce canal (`episode.teaser_model`).
- Le PC n'a pas de GPU : transcription ≈ 0,4× la durée, rendu HyperFrames ≈ 12× la durée du clip. Garder le PC branché.
- Windows bloque certaines DLL (sherpa-onnx) : ne pas toucher au réglage de sécurité.
