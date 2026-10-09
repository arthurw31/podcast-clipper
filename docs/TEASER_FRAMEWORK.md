# Framework teaser d'épisode — modèle « Dans la tête d'un CEO », charte AI Partners

Teaser d'ouverture d'un épisode complet (16:9, ~40 s). Déduit de l'analyse des 3 teasers de référence de
« Dans la tête d'un CEO » (`brands/dans-la-tete-dun-ceo/references/`, analyse du 08/10/2026), puis adapté à la charte
AI Partners (AI Corner). Demande d'Arthur : « un truc très catchy comme dans les teasers de Dans la tête d'un CEO ».

## 1. Ce que font les références (mesuré)

| | Cleany (A. Bellity) | Gaya (A. Guicheney) | AI Partners (T. Spitz) |
|---|---|---|---|
| Durée | 36,5 s | 44,7 s | 42,0 s |
| Coupes | 18 | 26 | 24 |
| Plan médian | 1,9 s | 1,4 s | 0,8 s |
| Extraits enchaînés | ~10 | ~14 | ~12 |
| Carte de fin | 4 s | 4 s | 4 s |

- **Débit continu** : aucun blanc entre les phrases ; chaque extrait est coupé au ras du dernier mot.
- **Une coupe toutes les ~2 s**, plusieurs par phrase : on ne reste jamais sur un plan.

## 2. Le script (l'ordre compte)

1. **Ouverture — la thèse choc** (4–7 s) : l'invité, une phrase tranchée, contre-intuitive, compréhensible sans
   contexte. *« Personne n'est indispensable dans une boîte. »* / *« Faire des conneries, c'est un très bon moyen de
   progresser. »* / *« Le talent n'existe pas, il n'y a que du travail. »*
2. **Le développement** (3–6 s) : la phrase qui précise ou durcit la thèse.
3. **Le ping-pong** (2–6 s) : une question COURTE de l'animateur, une réponse courte, idéalement **un chiffre**.
   *« Tu fais combien de CA ? — Je fais 11. — 11 quoi ? — Millions. »* / *« Même le CEO ? — Oui. »*
4. **La réaction** (1–2 s) : l'animateur réagit (*« Oh shit ! »*, *« Trop cool »*, *« Bien sûr ! »*, un rire). Elle
   humanise et relance.
5. **L'histoire** (5–8 s) : une anecdote concrète, un moment vécu (*« Ma mère, sa pire angoisse, c'était les réunions
   parents-profs… »*).
6. **La conviction / la vision** (4–6 s) : ce que l'invité défend.
7. **La chute** (2–5 s) : un moment humain ou drôle, ou une phrase forte qui donne envie de la suite.
8. **Carte de fin** (3,5–4 s).

Règles : 8 à 12 extraits, 35 à 45 s au total ; **les deux interlocuteurs parlent** (l'animateur pose, relance,
réagit) ; jamais de mise en contexte ; chaque extrait se comprend seul et finit sur une fin de phrase (contrôle
`verify`) ; « euh », hésitations et blancs retirés.

## 3. Les angles de caméra

- **Gros plan sur celui qui parle** au début de chaque extrait.
- **Plan large** (les deux personnes) au milieu des extraits de plus de ~3 s — il revient très souvent (~1 plan sur 3).
- **Réaction de l'écoutant** (gros plan, ~1 s) : l'animateur qui rit, sourit, hoche la tête.
- **Une coupe toutes les ~2 s** (1,4 à 3 s), posée dans une micro-pause entre deux mots.
- Raccords entre extraits : coupe sèche, ou transition courte (flou / light leak) une fois sur trois.
- **Mouvement** (retour d'Arthur, 08/10/2026 : « des effets de zoom et des transitions parfois, sans que ça fasse too
  much ») : zoom lent alterné avant / arrière de 6 % sur chaque plan ; transition **zoom-flou** (≈ 0,3 s : image
  légèrement floutée, éclaircie et zoomée qui se pose) sur **un changement d'extrait sur trois** seulement.

## 4. Les sous-titres (le cœur du style)

| | Dans la tête d'un CEO | Adaptation AI Partners |
|---|---|---|
| Taille | énorme : ~10 % de la hauteur de l'image par ligne | idem (≈ 100 px en 1080p) |
| Police | Montserrat ExtraBold *Italic* | **Metropolis Bold, droite** ; mot-clé en **Lora italique minuscule** (variante B, 09/10/2026) |
| Casse | CAPITALES | CAPITALES (mot-clé en minuscules) |
| Couleur | blanc + mots-clés **jaunes** | blanc + mots-clés **jaunes #F2E86D** (variante B) |
| Lignes | 1–2, 2e ligne décalée à droite | idem |
| Apparition | mot à mot, glissé depuis la gauche | idem |
| Position | moitié basse, ~60–80 % de la hauteur | idem |
| Mots-clés | chiffres, mots forts (1–3 par phrase) | idem |
| Ombre | ombre portée sombre, nette | ombre **en dégradé** (couches de flou croissant), sans contour |
| Apparition (AIP) | | fondu mot à mot (0,14 s), plus fluide que la machine à écrire |

## 5. Habillage

- **Logo** de l'émission en haut à droite pendant tout le teaser → **logo AI PARTNERS blanc** (même taille que
  dans l'épisode : 20 % de la largeur).
- **Carte invité (seul motion design retenu, Arthur 09/10/2026 : « on va garder que la guest card »)** : à 0,6 s,
  pendant 3,2 s, du côté de l'invité dans le plan large — nom en capitales Metropolis Bold, filet bleu #258AF3 qui
  se trace, rôle en dessous (« VP Global Data & AI · Coty »). Testés puis écartés : chiffre-clé animé (« 3 000 AGENTS
  IA ») et sons (whoosh, montée) — désactivés (`teaser.motion.stats` / `sfx`), réactivables.
- **Carte de fin** : dernière image du teaser **floutée**, logo de l'émission au centre, **nom de l'invité** en
  blanc et **entreprise** en couleur (jaune → **bleu #258AF3**, droit), ~3,5 s. Pas de bouton.

## 6. Exigences de script (retour d'Arthur, 08/10/2026)

- La **thèse d'ouverture** est dite d'une traite (aucune hésitation gardée) — sinon remplacée.
- Le **ping-pong** : une question et SA vraie réponse, courte (2–5 s), qui finit nettement.
- La **chute** est courte (2–5 s), une seule phrase ; une question de l'animateur peut finir le teaser (suspense).
- **Pas de redite** entre deux extraits.
- **Relecture éditoriale finale** : le modèle recompose l'enchaînement à partir des seuls extraits déjà vérifiés à
  l'oreille (texte réellement entendu), pour garantir ces règles.

## 7. Dans l'outil

- `python -m clipper episode-plan … --new-teaser` : script (LLM, `TEASER_PROMPT` = sections 2 et 4 : extraits +
  mots-clés), découpe sur fins de phrase réelles, « euh » et blancs retirés, plans de caméra (section 3), écoute finale.
- Charte : section `teaser:` de `brands/ai-corner/brand.yaml` (sous-titres, logo, carte de fin).
- Aperçu : `python -m clipper preview … --clip 99 --formats 16x9` — MP4 seulement après validation.
