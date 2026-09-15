# Phase 3 → Phase 4 : ce qui est livré et ce qui reste

Contexte : `PLAN.md` découpe la refonte en phases alternées Codex/Claude.
La Phase 3 (éditeur synchronisé, normalement Codex) a en réalité été faite
par Claude, sur la branche `claude/determined-maxwell-y1uli5`
([PR #25](https://github.com/niveys-5169/Transcription/pull/25), pas
encore mergée au moment de l'écriture). Ce document est le handoff attendu
en fin de phase (même rôle que `PHASE2_HANDOFF.md`) pour que la Phase 4
(révision, annotations, recherche globale, Claude) puisse démarrer sans
redécouvrir ce qui a changé.

Si la PR #25 n'est pas encore mergée quand vous démarrez, partez de la
branche `claude/determined-maxwell-y1uli5` (ou de son état une fois
mergée dans `main`) plutôt que de `main` seul.

## Ce qui est fait et testé

Dans `#shell-workspace` (`app/static/index.html`), l'éditeur central
remplace entièrement les anciens onglets `#panel-clean/raw/segments`
(supprimés) :

- **Lecteur synchronisé** (`#player-zone`) : vidéo source prioritaire
  (`GET /api/jobs/{id}/media`), repli automatique sur le WAV extrait
  (`GET /api/jobs/{id}/audio`, déjà existant) si le média source est
  absent ou illisible ; déplaçable (`#player-drag-handle`, glisser-
  déposer, persistance de position, désactivé sous 960px) et réductible
  (`#player-collapse-btn`) ; lecture/pause, ±5 s, vitesses
  0,75×/1×/1,25×/1,5×/2×.
- **Timeline** (`#timeline-zone`) : barre native interactive (clic/glisser
  pour se positionner, repères de blocs), sans dépendance externe.
- **Blocs de révision** (`#blocks-zone` → `#blocks-list`) : un bloc par
  `.block[data-block-id]`, clic sur l'horodatage (`.block-time`) pour
  lire à cet instant, suivi visuel du bloc en cours de lecture
  (`.is-active-block`), édition inline au clic sur le texte
  (`.block-text[data-action="edit"]`), `Escape` annule, `Ctrl/Cmd+Enter`
  enregistre via `PUT /api/jobs/{id}/review-blocks/{block_id}` (déjà
  fourni par la Phase 1), indicateur d'état de sauvegarde
  (`.block-save-status`).
- **Raccourcis clavier** : espace (lecture/pause), flèches (±5 s), `[`/`]`
  (vitesse), tous désactivés pendant l'édition d'un bloc ou dans tout
  champ de saisie. `Ctrl/Cmd+F` active et met le focus sur
  `#editor-search-input` (actuellement `disabled` par défaut — voir « Ce
  qui reste » ci-dessous).
- **Comparaison « Version IA »** (`#comparison-zone` → `#comparison-body`,
  élément `<details>` natif) : affiche `clean_text` en lecture seule,
  jamais modifiable.
- **Canonicité éditoriale** : dès qu'un bloc diffère du segment brut de
  même index (`isBlockEdited()`/`anyBlockEdited()` dans `app.js`), les
  blocs deviennent le texte utilisé par `currentText()` (copie, export
  local) — comparaison purement textuelle, aucun flag serveur dédié.
  **Ceci reste local à l'éditeur** : les exports serveur
  (TXT/MD/JSON/Obsidian, `app/exporters.py`) n'ont pas été modifiés et
  continuent d'ignorer les `review_blocks` — c'est explicitement le
  travail de la Phase 5.
- **Coloration par confiance** (façon Sonix) : `review_blocks[].confidence`
  (`float | null`, dérivé de `avg_logprob`) colore le texte peu fiable
  (`.conf-mid`/`.conf-low`), disparaît dès qu'un bloc est édité. Couvre
  le moteur local **et** RunPod (serverless + pod de secours) — voir
  « Point d'attention » plus bas pour la limite connue sur RunPod.

Suite de tests : `pytest` complet vert au moment du commit
(`tests/test_editor_api.py`, `tests/test_local_engine.py` [nouveau],
`tests/test_handler.py`, `tests/test_pod_server.py` mis à jour pour le
nouveau champ `confidence`). Aucun test frontend automatisé (convention
du dépôt) — vérification manuelle faite en navigateur headless
(sélection d'un job, bascule vidéo→WAV, lecture/seek, édition + rechargement,
raccourcis, coloration).

## Zones et contrats stables pour la Phase 4

Dans `#shell-workspace` :

- `#blocks-list` — ne pas changer la structure `.block` /
  `data-block-id` / `.block-time` / `.block-text` sans mettre à jour
  `renderBlocks()`, `initBlocksList()` et `updateActiveBlockFromTime()`
  dans `app.js`.
- `#editor-search-input` — présent et visible mais `disabled` ; seul le
  raccourci `Ctrl/Cmd+F` l'active/le focus pour l'instant. La Phase 4
  doit y câbler le filtrage réel + surbrillance + suivant/précédent +
  compteur (recherche locale, voir `PLAN.md` Phase 4).
- `#blocks-edited-flag` — visible dès qu'un bloc diffère de son segment
  d'origine ; peut servir de signal pour la Phase 4 sans le dupliquer
  (réutiliser `anyBlockEdited()`).
- `#comparison-zone`/`#comparison-body` — strictement en lecture seule,
  ne pas y ajouter d'édition.

Dans `#shell-analysis` :

- `#annotations-zone` reste un placeholder texte intact
  (« Arrivent avec l'éditeur synchronisé… ») — c'est le seam explicite
  laissé pour la Phase 4. Les endpoints sont déjà prêts depuis la
  Phase 1 : `GET/POST /api/jobs/{id}/annotations`,
  `PATCH/DELETE /api/jobs/{id}/annotations/{id}` (voir
  `app/server.py`, `app/db.py`). `block_id` d'une annotation correspond
  à l'`id` d'un `review_block` (`"segment-{n}"`).
- `#publish-zone` (`#proofread-btn`, `#factcheck-btn`, `#publish-btn`,
  `#obsidian-link`, `#notebooklm-btn`) : inchangé, non concerné par la
  Phase 4.

Fonctions JS (`app/static/app.js`) réutilisables comme modèle pour les
annotations : le listener délégué unique sur `#blocks-list`
(`initBlocksList()`, voir aussi les listeners délégués existants pour
`.finding-expand` et `[data-pending-action]`) plutôt qu'un listener par
élément ; `state.blocks`/`state.activeBlockId` déjà maintenus par
l'éditeur, consultables sans re-fetch pour associer une annotation au bon
bloc actif.

## Point d'attention : coloration par confiance sur RunPod

`handler.py` et `pod_server.py` (racine du dépôt, code des workers
RunPod) ont été modifiés pour renvoyer `confidence` par segment, mais
RunPod ne reconstruit pas son image Docker automatiquement depuis ce
dépôt (voir `app/engines/runpod_pod.py`). Tant que l'utilisateur n'a pas
reconstruit et republié l'image vers son registre, les transcriptions
faites via RunPod garderont `confidence: null` (dégradation silencieuse,
pas de coloration) — ce n'est pas un bug à corriger côté Phase 4.

## Ce qui n'a pas changé

Sélection d'un travail, vérification de fidélité, sources, réglages,
export (sans SRT/VTT), suppression, recherche globale (`GET /api/search`,
toujours non branchée côté UI — Phase 4) : identifiants et comportements
inchangés depuis `PHASE2_HANDOFF.md`.
