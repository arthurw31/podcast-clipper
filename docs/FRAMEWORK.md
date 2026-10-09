# Comment fonctionne le framework « Podcast → shorts »

Un épisode entier entre, cinq shorts verticaux montés et leurs posts LinkedIn sortent. Claude fait le travail
répétitif (transcrire, repérer, couper, monter, rédiger) ; l'équipe garde les décisions éditoriales :
**quels passages**, **ce qu'il faut absolument inclure**, et la **validation des shorts** en aperçu avant le rendu final.

> Ce qui a été validé (et ce qu'il faut éviter) est résumé dans [BONNES_PRATIQUES.md](BONNES_PRATIQUES.md).

## Le workflow en 5 étapes

```mermaid
flowchart TD
    A["1 · Dépôt<br/>épisode 4K + logo de l'invité<br/>dans le dossier depot/"] --> B["Transcription<br/>Whisper, mot à mot, horodatée"]
    B --> C{{"2 · Claude propose ~10 passages<br/>3 lectures en parallèle<br/>(cas concret · position tranchée · humain)<br/>puis un jury garde les 10 meilleurs"}}
    C --> D["3 · L'équipe choisit 5 passages<br/>+ demandes particulières<br/>« il faut le passage où il dit… »"]
    D --> E["Calage des coupes<br/>début et fin de phrase · contrôle mot à mot"]
    E --> N["4 · Nettoyage à l'écoute (polish)<br/>blancs et « euh » retirés · vraies fins de phrase<br/>+ respiration · aucun mot perdu"]
    N --> F["4 · Montage des 5 shorts<br/>recadrage vertical · logos · bulle-titre<br/>sous-titres vérifiés à l'écoute · carte de fin"]
    F --> Q1["Contrôle 1 (automatique)<br/>raccords · fins · sous-titres complets · durée"]
    Q1 --> P["Aperçus MP4 rapides"]
    P --> Q2["Contrôle 2 (automatique)<br/>format · volume · planche d'images regardée"]
    Q2 --> V["4 · Contrôle 3 : l'équipe regarde les aperçus MP4<br/>et demande ses retouches"]
    V -->|retouche| N
    E --> G["4 · Rédaction des 5 posts LinkedIn<br/>méthode de la marque (posts.md)"]
    V -->|validé| R["5 · Rendu final unique des 5 MP4<br/>1080×1920 + contrôle du fichier final"]
    R --> H["5 · Livraison<br/>5 MP4 + 5 posts prêts à publier"]
    G --> H

    classDef human fill:#258AF3,color:#fff,stroke:#151D53;
    classDef check fill:#E8F1FE,stroke:#258AF3;
    class D,V human;
    class Q1,Q2 check;
```

| Étape | Qui | Durée (PC sans GPU, épisode de 50 min) |
| --- | --- | --- |
| 1. Dépôt + transcription | vous déposez (ou collez le lien Dropbox), Claude télécharge et transcrit | ≈ 20 min (automatique) + téléchargement |
| 2. 10 propositions | Claude | ≈ 8 min |
| 3. Choix + demandes | **vous** | 2 min |
| 4. Nettoyage + montage + contrôles + aperçus + posts | Claude prépare, **vous regardez les aperçus et validez** | ≈ 6–8 min par short (tout compris, en arrière-plan), ≈ 3 min par retouche |
| 5. Rendu final + contrôle + livraison | Claude | ≈ 10 min par short, 2 en parallèle, une seule fois |

**Aperçu ou rendu ?** Un short est d'abord une « page » (vidéo + sous-titres, logos, transitions programmés par
dessus). L'**aperçu MP4** en est une version rapide (image un peu saccadée, 12 images/s) à ouvrir dans votre lecteur
vidéo habituel : idéal pour vérifier le contenu. Le **rendu** final la transforme en MP4 net et fluide image par image
(≈ 10 min par short) : on ne le fait qu'une fois, après votre validation.

**Trois contrôles avant toute livraison.** Chaque retour de l'équipe est devenu une vérification automatique :
aucun mot coupé à un raccord, aucune fin de phrase coupée (et un petit temps après), jamais le début de la phrase
suivante, « euh » retirés sans perdre un mot, sous-titres vérifiés à l'écoute (deux écoutes indépendantes), flash
lumineux seulement aux changements de plan, volume -16 LUFS. Rapport par short : `output/…/qa/`.

## Ce qui se passe à l'intérieur

(Version détaillée pour les curieux et les développeurs : [ARCHITECTURE.md](ARCHITECTURE.md).)

```mermaid
flowchart LR
    subgraph Marque["brands/nom-de-la-marque/ (modifiable)"]
        BY["brand.yaml<br/>charte, formats, durées"]
        GL["guidelines.md<br/>ce qui fait un bon extrait"]
        PM["posts.md<br/>méthode + posts de référence"]
        AS["assets/<br/>logos, polices, animation de fin"]
    end
    EP[("Épisode")] --> T["transcribe<br/>faster-whisper"]
    T --> S["propose / select<br/>Claude × 3 angles + jury"]
    GL --> S
    S --> P["pick + check<br/>choix humain, coupes vérifiées"]
    P --> AN["analysis<br/>plans, visages, fausses coupes"]
    AN --> RF["reframe<br/>cadrage vertical, punch-in"]
    RF --> CO["compose<br/>HyperFrames : sous-titres, bulle,<br/>logos, light leak, carte de fin"]
    BY --> CO
    AS --> CO
    CO --> R["render<br/>MP4 1080×1920"]
    P --> PO["posts<br/>Claude"]
    PM --> PO
```

- **Jamais couper une pensée** : Claude cite les premiers et derniers mots de chaque passage ; le code cale la
  coupe exactement sur ces mots, puis on vérifie mot à mot (`check`).
- **Montage fluide** : seules les vraies coupes de caméra de l'épisode sont gardées (les fausses coupes sont
  détectées par différence d'image) ; un raccord entre deux passages devient un léger zoom.
- **Netteté** : le recadrage vertical est fait par FFmpeg à partir de la source 4K, puis rendu en 1080×1920.

## Modifier le format (tout est dans des fichiers texte)

| Je veux changer… | Fichier | Exemple |
| --- | --- | --- |
| la durée des shorts, le nombre de propositions | `brands/<marque>/brand.yaml` → `selection` | `min_duration: 25`, `max_duration: 45` |
| les formats (vertical, LinkedIn 16:9, carré) | `brand.yaml` → `formats` | `["9x16", "16x9"]` |
| ce que Claude doit chercher / éviter | `brands/<marque>/guidelines.md` | « éviter les passages sur la levée de fonds » |
| le style des posts LinkedIn | `brands/<marque>/posts.md` | coller de nouveaux posts publiés en exemples |
| les logos, la police, l'animation de fin | `brands/<marque>/assets/` | remplacer `outro_anim.mov` |
| le texte du bouton de fin | `brand.yaml` → `outro.cta.text` | « ▶ L'ÉPISODE COMPLET SUR AI CORNER » |
| le style de montage (bulle, sous-titres, coupes) | `config/presets/aip-short.yaml` | taille des sous-titres, durée de la bulle |
| tout le reste (valeurs par défaut commentées) | `config/defaults.yaml` | — |

Le plus simple reste de le demander à Claude dans Claude Code (« les sous-titres plus gros », « shorts de
45 secondes ») : il sait quel fichier modifier.

## Ajouter un autre podcast

`python -m clipper new-brand <nom>` crée `brands/<nom>/` à partir du modèle. Déposez 2 ou 3 shorts déjà publiés
et quelques posts LinkedIn dans `depot/` et demandez à Claude de « caler la charte sur ces exemples ».
