# Dépôt

Déposez ici tout ce que vous donnez à Claude (épisodes, clips de référence, logos, posts publiés…).
Claude les range ensuite au bon endroit :

- épisode de podcast → `brands/<marque>/episodes/`
- clips de référence (montages existants) → `brands/<marque>/references/`
- logos, polices, musiques de la marque → `brands/<marque>/assets/` (logos d'invités : `assets/guests/`)
- posts LinkedIn déjà publiés → `brands/<marque>/posts.md`

Rushs volumineux sur Dropbox (plusieurs caméras de 13 Go…) : ne les téléchargez pas vous-même, envoyez
simplement le lien à Claude. Il les récupère avec `python -m clipper fetch "<lien>"` (reprise automatique après
coupure, taille de chaque fichier vérifiée) — le navigateur, lui, coupe les gros fichiers en cours de route.

Le contenu de ce dossier n'est pas versionné.
