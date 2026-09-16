# Recette navigateur — Verbatim v2

## Éditeur et audio

1. Importer une réunion à plusieurs voix ; vérifier le mot actif pendant la
   lecture, le clic sur un mot et le déplacement de la tête de lecture.
2. Renommer un locuteur : tous ses tours changent de nom et de couleur.
3. Vérifier la forme d’onde RMS, les marqueurs de blocs et le chargement par
   lots sur plus de 250 blocs.
4. Modifier un bloc, fusionner puis scinder ; utiliser `Ctrl/Cmd+Z` et
   `Ctrl/Cmd+Y` après chaque opération.
5. Rechercher/remplacer, modifier un horodatage sans chevauchement et vérifier
   que le refus est explicite en cas de collision.

## Mémoire et sorties

1. Ajouter, vérifier puis supprimer un terme utilisateur du Lexique ; valider
   une correction avec « Valider + lexique ».
2. Publier dans un coffre de test contenant `CDAPH.md` et l’alias `Cdaph` :
   aucune entité en double, verbatim relié, fiche Révision avec cartes `Q::A`.
3. Réexécuter une relecture avec une note de coffre pertinente : seuls les
   wikilinks vers des titres indexés et des citations uniques sont insérés.
4. Télécharger SRT, VTT et DOCX : locuteur, horodatages et tours de parole
   sont présents ; ouvrir le DOCX dans Word.

## Commandes de contrôle

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .pytest-tmp
node --check app/static/app.js
```

Les tests automatisés ne remplacent pas les contrôles nécessitant un pod
RunPod, un compte Google ou un coffre utilisateur réel.
