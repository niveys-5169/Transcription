# Vérification du plan v2 et édition en place façon Sonix

## Contexte

Le plan v2 (`docs/PLAN_V2.md`, état dans `docs/PLAN.md`) est annoncé terminé. L'utilisateur demande (1) de vérifier que tout est codé et déployé, (2) de dire ce qui reste à faire, et (3) de reproduire le modèle d'édition de Sonix (vidéo « Introduction to Sonix: How to edit a transcript ») : le texte est un traitement de texte continu, on tape directement dans la transcription, tout est enregistré automatiquement, et le clic sur un mot fait sauter l'audio. Aujourd'hui, un clic sur un bloc « ouvre » un `<textarea>` avec Enregistrer/Annuler (capture d'écran de l'utilisateur), ce qui casse le flux.

## 1. État vérifié (lecture seule)

**Code et déploiement**
- La branche `claude/admiring-lovelace-kencq8` est identique à `origin/main` (aucun commit d'écart). Aucune PR ouverte.
- Tous les chantiers 0, A1-A4, B1-B3, C1-C2, D sont présents dans le code (`app/db.py` mots/locuteurs/scission/fusion/retiming, `app/static/app.js` karaoké, undo/redo, rechercher/remplacer, lexique, exports SRT/VTT/DOCX, `app/obsidian/index.py`, `app/knowledge.py`, `app/revision.py`, `app/notebooklm_sync.py` Doc par cours).
- CI GitHub Actions sur `main` (14ed1f5) : **image du pod RunPod verte** (run 18, après deux échecs corrigés par les deux derniers commits) et **build Windows vert** (run 22, release `latest` mise à jour).
- Aucun TODO/FIXME dans `app/`. `pytest` n'est pas installé dans ce conteneur : la suite (28 fichiers de tests) n'a pas pu être rejouée ici, elle est annoncée verte dans `docs/PLAN.md`.

**Ce qui reste hors dépôt (inchangé, ne peut pas être codé)** : essai réel RunPod à 3 voix, synchro Google Drive/NotebookLM, recette sur coffre Obsidian réel, recette navigateur depuis l'exe Windows (`docs/RECETTE.md`).

**Écarts trouvés par rapport au plan v2**
1. README obsolète : `## Ce qui n'est pas fait` (README.md:887) liste encore « Repérage des locuteurs » et « Acceptation des propositions de lexique dans l'interface », tous deux livrés ; la section « Orientation » parle d'une « forme d'onde décorative » ; « Réviser une transcription » décrit `Échap`/`Ctrl+Entrée`. Les sections historiques (prototype HTML, serverless archivé, README.md:537-800) devaient partir dans `docs/HISTORIQUE.md` (chantier D) et y sont toujours.
2. **Karaoké perdu dès qu'un bloc est modifié** : `renderBlockText` (app.js:1382) ne rend les mots que si `!edited`, alors que le serveur conserve et réaligne `words` (`_retime_words`, db.py:447).
3. **Karaoké perdu après relecture IA** : `review_blocks_from_pairs` (db.py:438) met `words: None`, donc plus de clic-mot ni de suivi mot à mot une fois la relecture faite.
4. **Mots sans espaces avec le pod** : `pod_server.py:114` strip le texte des mots et `renderBlockText` les concatène avec `join("")` ; seul le moteur local (mot précédé d'un espace) s'affiche correctement.
5. Écart Sonix : édition via textarea (le sujet de la demande), scission et horodatage par `window.prompt`, remplacer/remplacer tout non annulable (pas de `pushEditorHistory`), compteur de recherche par bloc, pas de raccourci `Tab` lecture/pause pendant la frappe, pas d'`Entrée` pour créer un nouveau tour de parole, pas de panneau « Raccourcis ».
6. Code mort : `rendered` dans `renderBlockText` (app.js:1389-1393), `renderComparisonPanel` (app.js:1963).

## 2. Approche retenue : édition en place (contenteditable + autosave)

Principe Sonix : chaque bloc est **toujours** éditable, sans mode. Pas de bouton Enregistrer/Annuler. Les mots restent des `<span class="word">` pour le karaoké et le clic-mot ; la frappe modifie le DOM, l'extraction du texte se fait par `textContent`, l'enregistrement est différé (debounce) et le serveur réaligne les mots (`_retime_words` existant, aucune nouvelle route).

### Front — `app/static/app.js`

**Rendu (`renderBlockText`, app.js:1379)**
- Toujours `<p class="block-text" contenteditable="true" data-block-id spellcheck="false">`. Retirer la branche `textarea`/`block-actions` de `renderBlocks` (app.js:1505-1511) et `state.editingBlockId`-driven rerender.
- Rendre les mots dès que `block.words` existe, **modifié ou non** (corrige l'écart 2). Découper `block.text` en `text.split(/(\s+)/)` : si le nombre de jetons non blancs égale `words.length` (garanti après tout `_retime_words`), chaque jeton devient un `<span class="word" data-time data-index>` et les blancs deviennent des nœuds texte (corrige l'écart 4, et garantit `textContent === block.text` pour les annotations et le menu contextuel). Sinon, repli : mots joints par un espace.
- Sans `words` : texte brut échappé dans le même `<p>` (mêmes gestionnaires).
- Surlignages d'annotation et correspondances de recherche : calculés en plages de caractères sur `block.text`, puis appliqués comme classes sur les mots qui chevauchent (`annotation-highlight-<couleur>`, `editor-match`) au lieu de `<mark>` ; pour un bloc sans mots, garder les `<mark>` actuels. Supprimer le code mort `rendered`.
- Nouveau `renderBlock(blockId)` : remplace uniquement le `.block` concerné (innerHTML via la même fabrique que `renderBlocks`), pour ne jamais perdre le caret des autres blocs. `renderBlocks` reste pour les changements de liste (scission, fusion, restauration, changement de travail).

**Saisie et autosave**
- `input` sur `.block-text` : marquer le bloc `dirty`, planifier `scheduleBlockSave(blockId)` (debounce 700 ms). Statut discret dans le bloc (`.block-save-status` réutilisé : « Enregistrement… », « Enregistré », erreur) et indicateur global dans `.blocks-header` (« Toutes les modifications sont enregistrées » / « Enregistrement… »).
- `saveBlockNow(blockId)` : texte = `textContent` normalisé (`\s+` → espace, trim). Si vide → ne pas enregistrer, statut « Le texte du bloc est obligatoire ». Sinon `PUT /api/jobs/{id}/review-blocks/{block}` avec `{text}` (existant). Réponse → `Object.assign(block, updated)` (texte + mots réalignés). Si le bloc **n'a pas le focus**, `renderBlock(blockId)` pour rétablir les spans mots ; s'il l'a, mémoriser `needsRerender` et rerendre au `blur`.
- Historique : un enregistrement pousse une entrée undo/redo (snapshots via `editableBlockSnapshot`, comme aujourd'hui). Coalescer : si la dernière entrée concerne le même bloc et date de moins de 5 s, la mettre à jour plutôt que d'en empiler une nouvelle.
- `flushPendingSaves()` : appelé avant scission, fusion, undo/redo, remplacer, changement de travail, restauration de version et `beforeunload`. Remplace la vérification `state.editingBlockId` partout.
- `paste` : `insertText` texte brut uniquement. Bloquer le glisser-déposer HTML.

**Clavier dans un bloc (Sonix)**
- Clic sur un mot : le caret se place naturellement **et** `seekTo(word.start)` (audio « cousu » au texte). Le clic-mot n'utilise plus `stopPropagation` ni `preventDefault`. Pas de seek si une sélection existe.
- `Tab` : lecture/pause sans quitter le bloc (raccourci phare de Sonix). `Échap` : annuler la saisie non enregistrée (restaure `block.text` depuis l'état et blur). `Ctrl/Cmd+Entrée` : forcer l'enregistrement immédiat.
- `Entrée` : **scinder au caret** = nouveau tour de parole (Sonix « hit Enter »). Position du caret → offset caractères → index du premier mot de droite → `POST .../split {word_index}` existant, puis focus au début du bloc droit. `Shift+Entrée` ignoré (pas de saut de ligne dans un bloc).
- `Retour arrière` au tout début d'un bloc avec un bloc précédent : fusionner avec le précédent (`POST .../{previous}/merge`), caret placé à la jointure.
- `Ctrl/Cmd+Z / Y` restent natifs dans le bloc (undo du navigateur sur le texte en cours) ; hors bloc, pile applicative inchangée. `isEditableTarget` couvre déjà `[contenteditable]`.
- Le bouton **Scinder** utilise le caret s'il est dans ce bloc, sinon l'invite actuelle. **Horodatage** : inchangé (invite), hors périmètre de cette PR.
- Menu « Raccourcis » (`?` hors saisie, et un bouton dans `.blocks-header`) : une `<dialog>` listant les touches. Contenu statique.

**Rechercher / remplacer**
- `replaceEditorMatches` pousse une entrée d'historique (snapshot avant/après) pour rendre le remplacement annulable.
- Compteur : nombre d'occurrences (somme des matches par bloc) au lieu du nombre de blocs ; navigation inchangée (par bloc).

**Karaoké** : `updateActiveWordFromTime` (app.js:1213) inchangé ; il fonctionne désormais sur les blocs modifiés et relus.

### Serveur — `app/db.py`
- `review_blocks_from_pairs` (db.py:418) : quand les segments sources ont des `words`, construire `words` du bloc relu par `_retime_words({"words": mots_sources_concaténés, "start", "end"}, texte_relu)` (les mots inchangés gardent leurs temps, les autres sont interpolés) au lieu de `None` (corrige l'écart 3). Vérifier que `ensure_review_blocks` n'écrase pas des `review_blocks` existants.
- `split_review_block` : accepter aussi un bloc sans `words` en scindant par index de jeton (`re.findall(r"\S+")`) avec temps interpolés, pour que `Entrée` marche sur tout bloc. `PUT` inchangé.

### `pod_server.py`
- Ne plus strip le texte des mots au point de perdre l'espace : garder `word.get("word")` tel quel (WhisperX met l'espace devant) — ou, plus simple et robuste, laisser le strip et compter sur le nouveau rendu à partir de `block.text`. Choix : **laisser le pod tel quel**, le rendu côté front devient la source de vérité des espaces (aucune reconstruction d'image nécessaire).

### `app/static/index.html` / `styles.css`
- Retirer tout HTML lié à `block-edit` ; ajouter l'indicateur global d'enregistrement, le bouton/`<dialog>` Raccourcis.
- `.block-text[contenteditable]` : `outline: none`, focus discret (bordure gauche accent), `caret-color: var(--accent)`, `white-space: pre-wrap` conservé ; `.word.editor-match`, `.word.annotation-highlight-*` (fond), `.block.is-saving/.is-error`.

### Tests et docs
- `tests/test_ui_contract.py` : `contenteditable="true"` présent, plus de `block-edit`/`data-action="save"`, présence de `flushPendingSaves`, `scheduleBlockSave`, id du dialog raccourcis.
- `tests/test_editor_api.py` : `review_blocks_from_pairs` conserve des mots réalignés ; scission d'un bloc sans mots par index de jeton.
- `README.md` : réécrire « Réviser une transcription » (édition en place, autosave, Tab/Entrée/Retour/Échap, clic-mot), corriger « Orientation » (forme d'onde réelle), supprimer de « Ce qui n'est pas fait » les deux points livrés, déplacer les sections historiques (537-580 et 581-800) dans `docs/HISTORIQUE.md`. `docs/RECETTE.md` : étapes édition en place. `docs/PLAN.md` : ajouter une ligne « A5 — Édition en place » et les écarts corrigés.

## Fichiers touchés
- `app/static/app.js` (renderBlockText, renderBlocks, nouveau renderBlock, initBlocksList, initKeyboardShortcuts, saveBlockEdit→saveBlockNow/scheduleBlockSave/flushPendingSaves, splitBlock, replaceEditorMatches, updateEditorMatches)
- `app/static/index.html`, `app/static/styles.css`
- `app/db.py` (review_blocks_from_pairs, split_review_block)
- `tests/test_ui_contract.py`, `tests/test_editor_api.py`
- `README.md`, `docs/HISTORIQUE.md`, `docs/RECETTE.md`, `docs/PLAN.md`

## Réutilisation
- `_retime_words` (db.py:447) pour tout réalignement ; `archive_review_version` avant scission/fusion (déjà appelé).
- `editableBlockSnapshot` / `pushEditorHistory` / `restoreEditableBlockSnapshot` (app.js:1436-1461) pour l'historique.
- Routes existantes `PUT review-blocks`, `POST split`, `POST merge` : aucune nouvelle route.
- `updateActiveWordFromTime`, `seekTo`, `playPause` existants.

## Vérification
1. `pip install -r requirements-dev.txt` puis `python -m pytest -p no:cacheprovider` et `node --check app/static/app.js`.
2. Lancer `python run.py`, ouvrir un travail avec mots (moteur local suffit) via Playwright/Chromium préinstallé :
   - cliquer dans un mot : caret placé, lecteur déplacé ; taper : statut « Enregistrement… » puis « Enregistré » sans clic ; recharger : texte conservé et mots toujours cliquables/karaoké ; le badge « modifié » reste.
   - `Tab` bascule lecture/pause pendant la frappe ; `Entrée` crée un nouveau bloc au caret ; `Retour arrière` en début de bloc fusionne ; `Échap` annule la saisie en cours.
   - Remplacer tout puis `Ctrl+Z` hors bloc : annulé.
   - Un travail relu par l'IA garde le suivi mot à mot ; un bloc issu du pod (mots strippés) s'affiche avec ses espaces.
   - Annotation surlignée sur une plage : la plage reste juste après édition d'un autre mot du bloc.
3. Rejouer `docs/RECETTE.md` (section éditeur) dans le navigateur.
