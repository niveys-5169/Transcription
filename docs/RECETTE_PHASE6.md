# Recette navigateur — phase 6

Cette recette complète les tests API : le front est volontairement sans
framework ni runner JavaScript, donc les interactions média et clavier sont
à vérifier dans un navigateur avant livraison.

## États et parcours

1. Importer un média puis, pendant le traitement, vérifier le bouton
   **Annuler**. Une fois annulé ou en erreur, vérifier **Reprendre** et que
   l'état revient à la transcription sans effacer les données déjà produites.
2. Ouvrir un travail transcrit, relire un bloc, recharger la page et vérifier
   que la correction, le brut, les annotations, les sources et le rapport de
   vérification restent distincts.
3. Créer une note, un surlignage et un élément « à vérifier » ; modifier son
   statut, le supprimer, puis naviguer depuis la liste vers son bloc.
4. Rechercher localement puis globalement ; les boutons suivant/précédent et
   un résultat de bibliothèque doivent positionner le lecteur au passage.
5. Débrancher ou renommer temporairement le média source : le lecteur doit
   utiliser le WAV ; rendre le WAV indisponible doit afficher « Média
   indisponible » sans rendre l'éditeur inutilisable.
6. Publier après une correction humaine, vérifier le contenu Obsidian, puis
   synchroniser NotebookLM. Avec Google non configuré, le bouton est désactivé;
   avec une erreur Drive, Obsidian reste publié et le statut propose une
   reprise.

## Clavier, petit écran et volume

1. Naviguer au clavier : tabulation avec focus visible, espace, flèches,
   `[`/`]`, `Ctrl/Cmd+F`, puis `Escape` et `Ctrl/Cmd+Enter` en édition.
2. Sous 960 px, vérifier les panneaux Bibliothèque / Éditeur / Analyse et que
   le lecteur reste dans le flux de page.
3. Ouvrir une transcription de plus de 250 blocs : la liste affiche le lot
   initial et « Afficher les blocs suivants ». La lecture d'un bloc plus loin
   doit charger son lot sans rerendre tous les blocs à chaque événement audio.
4. Confirmer qu'aucun contrôle SRT ou VTT n'est visible dans l'interface.

## Contrôles automatisés effectués

- `node --check app/static/app.js`
- 92 tests ciblant API, pipeline, exports, Obsidian, NotebookLM et contrat
  structurel du front (`tests/test_ui_contract.py`) passent.
- L'état vide a été contrôlé visuellement dans un navigateur local : les
  éléments marqués `hidden` ne sont plus rendus par les règles CSS.
