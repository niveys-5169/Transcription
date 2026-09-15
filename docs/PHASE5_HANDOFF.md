# Phase 5 → Phase 6 : publication Obsidian et NotebookLM

La phase 5 rend les corrections humaines des `review_blocks` canoniques pour
les sorties, sans altérer `segments` ni `raw_text`.

## Livré

- `app.exporters.editorial_text()` est l'unique règle de sélection : dès
  qu'un bloc diffère du segment brut correspondant, TXT, Markdown, JSON et
  la fiche Obsidian utilisent la concaténation des blocs. Sinon ils gardent
  `clean_text` (et donc sa structure IA). Le JSON conserve aussi les blocs
  sous `blocs_revision` et toujours le brut séparément.
- La publication Obsidian réécrit le même fichier à partir de cette source
  éditoriale. `obsidian_published_at` est mémorisé et affiché dans le panneau
  Publication, avec le lien existant « Ouvrir dans Obsidian ».
- La migration SQLite ajoute `obsidian_published_at`, `notebooklm_status`,
  `notebooklm_synced_at` et `notebooklm_error`, sans toucher aux travaux
  existants.
- `POST /api/jobs/{id}/notebooklm-sync` ne s'autorise qu'après une note
  Obsidian publiée et une configuration Google valide. Il enfile
  `TASK_NOTEBOOKLM`, qui régénère le Markdown intermédiaire et met à jour le
  même `notebooklm_master_doc_id`.
- Le panneau Publication active `#notebooklm-btn` seulement quand ces
  préconditions sont remplies et affiche les états non configuré, à
  synchroniser, en cours, synchronisé ou erreur détaillée.
- La chaîne automatique ne contacte Google qu'après le succès d'Obsidian.
  Un échec Drive laisse la publication acquise et est affiché comme action
  de reprise ; un échec Obsidian ne déclenche pas Google.

## Points de recette phase 6

- Modifier un bloc, republier, puis vérifier TXT/MD/JSON, note Obsidian et
  Doc maître : tous doivent contenir la correction, tandis que le brut reste
  inchangé.
- Vérifier qu'une publication répétée ne crée ni deuxième note Obsidian ni
  deuxième fichier de cours ni deuxième Google Doc.
- Avec Google non configuré, le bouton reste désactivé ; avec une erreur
  Drive, Obsidian reste publié et le message est actionnable.
- Contrôler les petits écrans et l'accessibilité du nouveau statut de
  publication (`#publication-status`).

## Vérification effectuée

- `node --check app/static/app.js`
- `pytest tests/test_exporters.py tests/test_obsidian.py
  tests/test_notebooklm_sync.py tests/test_editor_api.py
  tests/test_pipeline_stages.py -q` : **57 tests passés**.
