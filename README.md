# Podcast Clipper

Transforme un épisode de podcast (fichier vidéo long) en **shorts / reels / clips LinkedIn montés**,
prêts à publier : sélection des meilleurs extraits par Claude, recadrage vertical qui suit celui qui parle,
sous-titres animés à la charte de votre podcast, logo, B-roll, carte de fin avec appel à l'action, et les
textes de publication qui vont avec.

Chaque podcast / marque a son propre dossier (charte graphique, consignes éditoriales, logo, police, style de
montage) : le même épisode peut donc être monté « façon TikTok punchy » pour une marque et « sobre LinkedIn »
pour une autre.

Deux façons de l'utiliser :
- **guidée**, dans Claude Code : vous déposez votre vidéo, vous dites ce que vous voulez, Claude pose les
  questions manquantes et fait tout ;
- **en ligne de commande**, étape par étape.

---

## 1. Ce qu'il faut sur votre machine

| Outil | Pourquoi | Installation |
| --- | --- | --- |
| **Python 3.11+** | le pipeline | [python.org](https://www.python.org/downloads/) (cocher « Add to PATH ») |
| **Node.js 22+** | le moteur de rendu HyperFrames | [nodejs.org](https://nodejs.org) |
| **FFmpeg** | découpe / encodage vidéo | Windows : `winget install Gyan.FFmpeg` · macOS : `brew install ffmpeg` |
| **Git** | récupérer le projet | [git-scm.com](https://git-scm.com) |
| **Claude Code** (app desktop ou CLI) | l'assistant qui pilote le pipeline et choisit les extraits | [claude.com/claude-code](https://claude.com/claude-code) |
| Clé API **Pexels** (gratuite) | images / vidéos d'illustration (B-roll) | [pexels.com/api](https://www.pexels.com/api/) |

Facultatif : une clé `ANTHROPIC_API_KEY` (sinon la sélection des extraits passe par votre abonnement Claude Code,
via la commande `claude`). Un GPU NVIDIA accélère fortement la transcription mais n'est pas nécessaire.

Ordres de grandeur sans GPU (PC portable) : transcription ≈ 0,4× la durée de l'épisode (18 min pour 45 min),
sélection ≈ 5 min, rendu ≈ 4–5 min par clip et par format.

## 2. Installation

```bash
git clone https://github.com/arthurw31/podcast-clipper.git
cd podcast-clipper
pip install -r requirements.txt
npm install
```

Copiez `.env.example` en `.env` et renseignez au minimum `PEXELS_API_KEY`. Puis :

```bash
python -m clipper doctor
```

La commande vérifie tout (FFmpeg, Node, HyperFrames, librairies Python, modèle de détection de visages,
clés, marques) et indique quoi corriger. Le modèle de transcription Whisper (~1,6 Go) se télécharge tout
seul au premier lancement.

## 3. Premier clip en 5 minutes (mode guidé)

1. Ouvrez le dossier du projet dans **Claude Code**.
2. Déposez votre épisode (mp4/mov) dans `brands/<votre-marque>/episodes/` — ou à la racine si la marque
   n'existe pas encore.
3. Écrivez par exemple :

   > Fais-moi 6 clips verticaux + LinkedIn de cet épisode. Invité : Thierry Champéroux, MAIF.
   > L'animateur est à droite. Style sobre, comme les clips dans references/.

4. Claude (skill `podcast-clips`) complète ce qui manque par quelques questions, crée la fiche marque si
   besoin, vous montre la sélection (titres + timecodes), puis lance transcription → sélection → montage →
   rendu et vous envoie les MP4 et les textes de publication.

Vous pouvez aussi taper `/podcast-clips` pour lancer l'interview directement.

## 4. Créer la fiche de votre podcast (marque)

```bash
python -m clipper new-brand mon-podcast
```

crée `brands/mon-podcast/` :

```
brands/mon-podcast/
  brand.yaml       ← charte : couleurs, police, logo, sous-titres, B-roll, recadrage, formats, carte de fin
  guidelines.md    ← consignes éditoriales (ton, ce qui fait un bon extrait, à éviter, hashtags)
  assets/          ← logo.png (fond transparent), fonts/*.ttf, musique éventuelle
  episodes/        ← vos épisodes sources
  references/      ← clips déjà publiés ou exemples du style voulu
```

`brand.yaml` ne contient que ce qui diffère des valeurs par défaut ([config/defaults.yaml](config/defaults.yaml),
entièrement commenté). Le plus rapide : choisir un **preset de montage** et ajuster.

| Preset | Style | Exemple |
| --- | --- | --- |
| `dynamic` | capitales, mots-clés colorés, révélation « machine à écrire », coupes rythmées, zoom lent, B-roll en fenêtre | `brands/dans-la-tete-dun-ceo/` |
| `editorial` | sous-titres sobres en minuscules centrés en bas, montage multi-segments (accroche + développement + conclusion), pas de B-roll | `brands/ai-corner/` |
| `clean` | sobre, mot à mot, B-roll plein écran | |
| `minimal` | recadrage + sous-titres uniquement | |

Si vous avez des clips de référence, déposez-les dans `references/` et demandez à Claude de « caler la charte
sur ces clips » : il en déduit la typo, les couleurs, la position des sous-titres, le type de montage.

Options les plus utiles de `brand.yaml` :

- `formats` : `9x16` (TikTok / Reels / Shorts), `16x9` (LinkedIn / YouTube), `1x1`
- `selection` : nombre de clips, durées min / max / cible, `max_segments` (1 = un passage continu ;
  3–4 = montage de plusieurs passages de l'épisode)
- `captions` : taille, casse, italique, couleur des mots-clés, position, `reveal` (`typewriter` / `word` / `instant`)
- `logo` : image dans `assets/logo.png`, position, taille (sinon un logo texte est généré)
- `outro` : carte de fin — fond flouté ou image de charte, logo, invité, **CTA** vers l'épisode complet
  (actif par défaut, `outro.cta.enabled: false` pour le retirer)
- `broll` : illustrations Pexels en fenêtre ou plein écran, nombre max par clip
- `framing` : recadrage vertical des plans larges (`speaker` suit celui qui parle, `split` écran partagé),
  `host_side` (côté de l'animateur)
- `format_overrides` : ajuster une option pour un format précis (ex. logo désactivé en 16:9)

## 5. Ligne de commande

```bash
# tout d'un coup
python -m clipper run --brand mon-podcast --input episode-12.mp4 \
    --guest "Prénom Nom" --company "Entreprise" --host-side right --n 6

# ou étape par étape (chaque étape est mise en cache et reprend là où elle s'est arrêtée)
python -m clipper transcribe --brand mon-podcast --input episode-12.mp4
python -m clipper select     --brand mon-podcast --input episode-12.mp4 --guest "…" --company "…" --host-side right --n 6
python -m clipper build      --brand mon-podcast --input episode-12.mp4 [--only 1,3] [--formats 9x16,16x9]
python -m clipper render     --brand mon-podcast --input episode-12.mp4 [--only 1] [--quality draft|looks|delivery]
python -m clipper preview    --brand mon-podcast --input episode-12.mp4 --clip 1     # ouvre l'éditeur HyperFrames
```

- `--input` : un chemin, ou simplement le nom d'un fichier déposé dans `brands/<marque>/episodes/`.
- `--host-side left|right` : côté de l'animateur dans le plan large (peut être fixé dans `brand.yaml`).
- `select … --instructions "…"` : consignes ponctuelles (« évite les passages sur la levée de fonds »).
- `select … --ranges 120-160,900-940` : sélection manuelle par timecodes, sans LLM.
- `select … --force` : refaire la sélection.

## 6. Ce que vous obtenez

```
output/<marque>/<episode>/
  renders/                 ← les MP4 finaux : clip_01_<titre>_9x16.mp4, clip_01_<titre>_16x9.mp4, …
  summary.md               ← récap des extraits + textes de publication (description, question, hashtags)
  clips.json               ← la sélection, éditable à la main (timecodes, segments, mots-clés, B-roll)
  transcript.json          ← transcription mot à mot
  clips/clip_01_<titre>/   ← projet HyperFrames de chaque clip (retouche manuelle possible)
```

**Corriger un clip** : modifiez `clips.json` (ou demandez à Claude), puis
`python -m clipper build … --only N` et `python -m clipper render … --only N --force`.
Pour une retouche fine, `preview` ouvre le Studio HyperFrames (timeline, déplacement des éléments).

## 7. Ce que garantit le montage

- **Jamais de pensée coupée** : chaque extrait commence au début d'une phrase et finit sur une fin de phrase
  complète (le LLM cite les premiers et derniers mots exacts ; le code cale les timecodes dessus et n'attrape
  jamais le début de la phrase suivante). Un extrait peut dépasser la durée cible de quelques secondes pour finir l'idée.
- **Recadrage vertical intelligent** : gros plans centrés sur le visage, plans larges recadrés sur celui qui
  parle (d'après les tours de parole) ou en écran partagé.
- **Le B-roll ne couvre jamais un visage** (placement automatique, sinon plein écran).
- **Carte de fin** avec logo, invité/entreprise et CTA vers l'épisode complet, aux couleurs de la marque.

## 8. Problèmes fréquents

| Symptôme | Cause / solution |
| --- | --- |
| `python -m clipper doctor` en erreur | suivez les flèches `->` : outil manquant, `pip install -r requirements.txt`, `.env` incomplet |
| Sélection lente ou erreur `claude CLI` | sans `ANTHROPIC_API_KEY`, la sélection utilise Claude Code : vérifiez que `claude` fonctionne dans un terminal |
| Rendu très lent / échec mémoire | fermez les applis gourmandes ; `--quality draft` pour tester ; `npx hyperframes doctor` |
| Sous-titres doublés | le master contient déjà des sous-titres (teaser au début de l'épisode) : excluez ce passage via `--instructions` |
| Mauvaise personne cadrée sur le plan large | vérifiez `--host-side` (l'animateur change parfois de côté selon l'épisode) |
| Mots mal transcrits (noms propres) | corrigez dans `transcript.json` avant `select`, ou dans `clips.json` avant `build` |

## 9. Sous le capot

`clipper/` : `transcribe` (faster-whisper, mots horodatés) → `select_clips` (Claude : extraits, mots-clés,
tours de parole, B-roll, posts) → `analysis` (coupes, visages YuNet, locuteur) → `reframe` (plan de caméras
par format) → `captions` → `broll` (Pexels) → `compose` (assemblage des segments, composition
[HyperFrames](https://hyperframes.heygen.com) via `templates/clip.html.j2`) → `render` (MP4).
Pour modifier le code, lisez [CLAUDE.md](CLAUDE.md) : règles produit, contrat HyperFrames et pièges connus.
