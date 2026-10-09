# Bonnes pratiques validées — AI Corner (épisode E22, 05 → 09/10/2026)

Ce qu'Arthur et l'équipe ont validé sur E22 (teaser, épisode complet, short 1) : **la référence pour les prochains
épisodes**. Chaque règle vient d'un retour réel (date + phrase d'Arthur) et est déjà appliquée par l'outil — ce guide
dit *quoi* et *pourquoi* ; le *comment* (code, réglages) est dans [CLAUDE.md](../CLAUDE.md), le pas à pas dans le skill
[podcast-clips](../.claude/skills/podcast-clips/SKILL.md), le teaser dans [TEASER_FRAMEWORK.md](TEASER_FRAMEWORK.md).

Résultat jugé « franchement plutôt bien » par Arthur (09/10/2026) : `output/ai-corner/e22-nicolas-comestaz-coty/episode/`
(`E22_teaser_final.mp4`, `E22_nicolas-comestaz_coty_episode.mp4`) et `renders/clip_01_…_9x16.mp4` (style à reprendre).

## 1. Le son et les coupes (valable pour TOUT : shorts, teaser, épisode)

| Règle | Pourquoi / retour |
|---|---|
| Jamais couper une pensée : un extrait commence au début d'une phrase et finit sur une fin de phrase | « des fois ça coupe avant qu'il ait terminé sa phrase » (07/10) |
| Jamais couper pendant que quelqu'un parle, et laisser **un petit temps (~0,35 s)** après la dernière syllabe | « laisse même un mini temps à la fin pour que ça ne fasse pas effet coupé » (08/10) |
| … mais jamais au point d'entendre le **début de la phrase suivante** | trouvé par le contrôle qualité (« l'entreprise. ‖ É… », 08/10) |
| … et **pas de blanc trop long** non plus : au plus ~0,35 s de silence après la voix en fin d'extrait | « après "top-down avec du leadership" il y a un petit blanc qui n'est pas ouf » (09/10, 1,4 s) |
| Retirer les **« euh »**, y compris ceux collés aux mots, sans jamais perdre un mot | « l'invité le fait beaucoup, ce n'est pas assez dynamique » (08/10) |
| **Aucune réaction vide** (« super intéressant », « ah ouais », « exactement »…) | « ça n'apporte rien » (09/10) |
| Une reprise après une coupe de dérushage démarre sur un mot propre (pas de « euh, pour, pour… », pas de long blanc) | « vérifie bien plusieurs fois qu'il n'y a pas de euh laissé au montage » (08/10) |
| Dans l'épisode complet, les « euh » à l'intérieur des réponses restent (conversation naturelle) | style du monteur |
| Volume : chaque partie normalisée à **-16 LUFS** (teaser = épisode) | « le teaser avait un volume trop fort » (08/10) |

## 2. Les sous-titres (shorts + teaser)

- **Variante B adoucie** (choisie par l'équipe, 09/10) : capitales **Metropolis Bold** (pas ExtraBold), un peu
  espacées, sans contour ; **mot-clé en Lora italique, minuscules, jaune #F2E86D** ; apparition mot à mot en fondu.
- **Ombre en dégradé** autour des lettres (halo flou progressif), jamais de contour épais ni d'ombre nette (08/10).
- **Tous les mots entendus sont sous-titrés** — double écoute du montage, « il oublie quelques mots » (08/10).
- Mots-clés : ~1 tous les 6–10 mots, mots porteurs de sens (chiffres, concepts), jamais un petit mot.
- À surveiller : le jaune sur une chemise blanche est un peu moins lisible (l'ombre aide).

## 3. Les shorts (9:16)

- Montage du monteur AI Partners (shorts DUST) : barre de logos invité + AI PARTNERS, bulle-titre pendant l'accroche,
  25–45 s, carte de fin AI Partners de **7 s** (« trop rapide, x2 », 08/10).
- Multicam : gros plan de celui qui parle ; **écran partagé** (invité en haut, animateur en bas) de temps en temps
  pour montrer que l'autre écoute — **seulement si celui qui écoute est calme** (« enlève le passage où Thomas se
  gratte l'oreille », 09/10).
- **Aucun zoom** (« pour les shorts, ne pas mettre de zoom », 08/10).
- Flash lumineux de transition **seulement quand l'angle de caméra change** (08/10).

## 4. Le teaser d'ouverture (16:9, ~40–45 s)

- Script façon « Dans la tête d'un CEO » : thèse sans hésitation → développement → vrai ping-pong question/réponse →
  histoire → conviction → chute courte ; les deux interlocuteurs parlent ; aucune redite.
- Coupes toutes les ~2 s (gros plan / large / réaction), zoom lent et zoom-flou dosés (1 transition sur 3).
- **Musique très basse** (« Driving Momentum », composée par Arthur ; ~22 dB sous les voix) — « qu'on entende surtout
  les invités » (08/10).
- **Un seul élément de motion design : la carte invité** (nom, filet bleu #258AF3 qui se trace, rôle, du côté de
  l'invité, 3 s au début). Testés puis écartés : chiffre-clé animé, sons (whoosh, montée) — « on va garder que la
  guest card » (09/10). Jamais : glitch, citation plein écran, mur de logos.
- Logo AI PARTNERS en haut à droite (20 % de la largeur).
- **Fin du teaser = l'animation du logo AI Partners** (lignes « montagne » + logo blanc, 7 s), la même qu'à la fin des
  shorts — règle permanente jusqu'à nouvel ordre (09/10, retour de Mohamed : « remplacer cette partie avec
  l'animation ») ; plus de carte floutée avec l'invité.

## 5. L'épisode complet (16:9)

- Style mesuré sur les épisodes montés par le monteur : gros plans ~76 %, plan large ~14 %, écran partagé ~10 %,
  plan médian ~10 s.
- **On voit toujours celui qui parle** : soit son gros plan, soit un plan avec les deux (large ou écran partagé) ;
  jamais l'autre seul pendant qu'il parle, même 1 s (09/10 : « des fois l'invité parle et la caméra est sur Thomas »).
  Pas de plan de réaction sur celui qui écoute seul. Contrôlé par `qa --episode` (tours de parole + empreinte vocale).
- Dérushage : dernière prise de l'intro, coupes hors antenne / question reposée ; chaque raccord change de plan.
- Teaser au début, **logo en haut à droite** (20 %), fin = animation AI Partners + logo de **8 s**.
- Teaser et corps rendus séparément : retoucher le teaser ne refait pas le corps (~15 min au lieu d'1 h).

## 6. La méthode de travail (validée)

1. **Rien de final sans validation sur un aperçu.** Les aperçus sont des **MP4** (le Studio HyperFrames bugue chez
   Arthur), rapides (12 images/s), avec un **lien cliquable** vers chaque fichier.
2. **Trois contrôles** avant toute livraison : automatique sur le montage (`qa`), automatique sur l'aperçu / le MP4
   final (`qa --render`, `qa --episode`, planche d'images regardée), puis Arthur.
3. Chemin d'un short : `pick` → `polish --only N` → liens MP4 → validation → `render` → `qa --render`.
4. Jamais réécraser un MP4 livré : l'ancien est renommé (`_v1_<date>`).
5. **Chaque retour d'Arthur devient une règle durable** (code ou réglage + CLAUDE.md + mémoire), datée, avec sa phrase.
6. Aucune fenêtre ne s'ouvre chez Arthur (pas de Chrome, pas de console) ; tout est vérifié par images et par écoute.
7. Plusieurs sessions Claude peuvent travailler en parallèle sur le dépôt : ne committer que ses propres fichiers,
   jamais de modèle d'IA dans le dépôt (public).
