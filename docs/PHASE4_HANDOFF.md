# Phase 4 → Phase 5 : révision, annotations et recherche

La phase 4 est raccordée dans l'interface statique existante. Elle ne change
pas le contrat serveur : les endpoints d'annotations et de recherche globale
livrés en phase 1 sont désormais utilisés par `app/static/app.js`.

## Ce qui est livré

- Le panneau `#annotations-zone` permet d'ajouter une note, un élément « à
  vérifier » ou un surlignage à palette limitée au `state.activeBlockId`.
  Les annotations sont persistées via `POST/PATCH/DELETE
  /api/jobs/{id}/annotations`.
- La liste `#annotation-list` se filtre par état, permet de modifier l'état
  et de supprimer un élément. Son horodatage ramène au bloc et positionne le
  lecteur au bon instant.
- La recherche locale `#editor-search-input` met les correspondances en
  évidence, affiche le compteur `#editor-search-count` et navigue avec
  `#editor-search-prev` / `#editor-search-next`. Le raccourci `Ctrl/Cmd+F`
  existant continue de lui donner le focus.
- La recherche de la bibliothèque appelle `GET /api/search?q=` et affiche
  `#global-search-results`. Chaque résultat ouvre le travail puis cherche
  l'horodatage retourné par l'API.
- `#status-filter`, `#date-filter` et `#tag-filter` filtrent les cartes de
  bibliothèque. Les tags sont volontairement locaux au navigateur
  (`localStorage`, clé `transcription-tags:<job_id>`), conformément au
  périmètre « tags locaux » : ils n'affectent ni SQLite ni les exports.
- Le CTA primaire du panneau Publication suit le prochain état utile
  (relire, vérifier, publier). Les actions moins fréquentes sont regroupées
  dans `#secondary-actions-menu`.

Les résumés, la vérification de fidélité, les sources, le compteur des
éléments à vérifier et le statut Obsidian existaient déjà : ils sont préservés
et cohabitent avec le nouveau panneau.

## Points d'intégration pour la phase 5

- Les actions de publication restent `#publish-btn`, `#obsidian-link` et
  `#notebooklm-btn`. Ce dernier est toujours désactivé : la phase 5 doit le
  rendre disponible seulement après publication Obsidian réussie et
  configuration Google valide.
- L'éditeur continue de considérer `review_blocks` comme la version humaine
  dès la première modification. Les exports et publications serveur ne les
  consomment pas encore : c'est le travail central de la phase 5.
- Ne pas changer `.block[data-block-id]`, `#blocks-list` ni
  `state.activeBlockId` sans mettre à jour annotations et recherche locale.

## Vérification effectuée

- `node --check app/static/app.js`
- `.venv\\Scripts\\python.exe -m pytest tests/test_editor_api.py tests/test_api.py -q --basetemp .pytest-tmp-phase4 -p no:cacheprovider`
  : **41 tests passés**.
