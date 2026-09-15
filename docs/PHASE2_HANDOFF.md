# Phase 2 → Phase 3 : identifiants DOM et zones stables

Contexte : `PLAN.md` (fourni par l'utilisateur, non versionné) découpe la
refonte en phases alternées Codex/Claude. Ce document est le handoff attendu
en fin de phase 2 (« interface responsive statique finalisée ; identifiants
DOM et états UI documentés ») pour que la phase 3 (éditeur synchronisé,
Codex) puisse câbler sans deviner la structure.

## Gabarit à trois zones

`app/static/index.html` expose désormais un `<div class="app-shell">` en
grille CSS à trois colonnes (voir `app/static/styles.css`) :

- `#shell-library` (`data-shell-panel="library"`) — import + bibliothèque.
- `#shell-workspace` (`data-shell-panel="workspace"`) — lecteur + texte.
- `#shell-analysis` (`data-shell-panel="analysis"`) — vérification, sources,
  notes, publication.

Sous 960px, un seul panneau est visible à la fois (classe
`.is-shell-active`), piloté par `.shell-tab[data-shell="…"]` et
`initShellTabs()` dans `app.js`. Ne pas dupliquer ce mécanisme : basculer un
panneau se fait en déclenchant un clic sur le bouton correspondant (voir
`selectJob()` qui active `workspace` à la sélection d'un travail).

## Zones stables préparées pour la phase 3

Dans `#shell-workspace` :

- `#player-zone` — conteneur du lecteur.
  - `#media-player` (`<video>`, `hidden` par défaut) : à afficher et
    alimenter en priorité quand le navigateur peut lire le média source
    (`GET /api/jobs/{id}/media`, ajouté en phase 1).
  - `#audio-player` (`<audio>`, déjà fonctionnel) : repli sur le WAV extrait
    (`GET /api/jobs/{id}/audio`) — bascule automatique si `#media-player`
    échoue à charger (`error` event) ou si le type MIME renvoyé par
    `/media` n'est pas lisible.
  - `#timeline-zone` (`hidden`) : réservé à la barre interactive avec
    repères de blocs. Actuellement un `div` vide.
- `#blocks-zone` et `#comparison-zone` (`hidden`) : réservés à l'éditeur de
  `review_blocks` et à la comparaison repliable « Version IA ». Le texte
  relu actuel reste affiché en attendant via les onglets existants
  (`#panel-clean`, `#panel-raw`, `#panel-segments`) — à remplacer par
  l'éditeur, pas à conserver en parallèle.

Dans `#shell-analysis` :

- `#annotations-zone` : placeholder texte, à remplacer par la vraie UI de
  notes/surlignage (phase 4, pas 3).
- `#publish-zone` : contient déjà `#proofread-btn`, `#factcheck-btn`,
  `#publish-btn`, `#obsidian-link` (fonctionnels) et `#notebooklm-btn`
  (`disabled`, réservé phase 5).

## Contrat de données déjà disponible (phase 1)

- `GET /api/jobs` et le flux SSE `GET /api/events` renvoient maintenant,
  par travail : `pending_review_count` (annotations `status="a_verifier"`)
  et `publication_status` (`"publie"` | `"pret"` | `"non_disponible"`,
  calculé par `app/server.py::_publication_status`). Utilisés par les
  cartes de bibliothèque (`renderJobs()` / `jobFlags()` dans `app.js`) —
  toute modification de leurs valeurs possibles doit mettre à jour ces deux
  fonctions.
- `GET/PUT /api/jobs/{id}/review-blocks`, `GET/POST /api/jobs/{id}/annotations`,
  `PATCH/DELETE /api/jobs/{id}/annotations/{id}` : pas encore consommés par
  le front — c'est le travail de la phase 3 (blocs) et 4 (annotations).

## Ce qui n'a pas changé

Tout ce qui fonctionnait déjà (sélection d'un travail, onglets texte,
vérification de fidélité, sources, réglages, export sans SRT/VTT,
suppression, recherche) reste branché sur les mêmes identifiants qu'avant
la refonte visuelle — seule leur position dans le DOM a changé. Les entrées
SRT/VTT ont été retirées de l'interface (menu de téléchargement) ; les
routes serveur restent en place pour compatibilité interne.
