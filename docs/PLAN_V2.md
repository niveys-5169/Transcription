# Verbatim — analyse de l'existant et plan d'évolution

## Contexte

Verbatim (FastAPI + SQLite + HTML/JS sans framework, ~12 500 lignes, 307 tests, 97 commits du 11 au 16 sept. 2026) transforme un enregistrement en transcription relue, vérifiée par recherche web, publiée dans Obsidian puis compilée dans un Doc Google maître pour NotebookLM. Le README (48 Ko) et `docs/PLAN.md` (6 phases, toutes marquées faites) décrivent une base solide.

Objectif demandé : en faire (1) un éditeur de transcription « niveau Sonix.ai », (2) l'orchestrateur d'une mémoire long terme dans Obsidian, (3) l'alimentation de NotebookLM pour les révisions.

Décisions prises avec l'utilisateur :
- Contenus : cours magistraux **et** réunions/tables rondes → locuteurs nommés nécessaires.
- Priorités Sonix : horodatage au mot + clic sur un mot, locuteurs, édition avancée (rechercher/remplacer, fusion/scission, annuler/rétablir, dictionnaire dans l'UI, vraie forme d'onde).
- Calcul : **uniquement RunPod** (pod GPU). Jeton Hugging Face acceptable → pyannote OK.
- Obsidian : garder aussi chaque transcription **telle quelle** (verbatim), **lire le coffre** pour relier aux notes existantes, **synthèses inter-cours** par thèmes proposés par Claude et validés dans l'UI.
- NotebookLM : **un Google Doc par cours**, Doc maître compilé conservé en option.

---

## État des lieux

### Fait et solide (vérifié dans le code)

| Domaine | Ce qui existe | Où |
|---|---|---|
| Chaîne | 5 tâches indépendantes et rejouables (`TASK_TRANSCRIPTION/PROOFREAD/FACTCHECK/PUBLISH/NOTEBOOKLM`), file mono-thread, cancel/retry, SSE `/api/events` | `app/pipeline.py:819`, `app/server.py` |
| Moteurs | `local` (faster-whisper, confiance dérivée d'`avg_logprob`) et `runpod` (pod HTTP, tronçons 180 s sur silences, pool de pods avec idle timeout) | `app/engines/local.py`, `app/engines/runpod.py`, `app/engines/runpod_pod.py`, `pod_server.py` |
| Relecture | Claude CLI (abonnement) ou API, mode `basic` par règles, repli NIM opt-in ; découpage 6 000 car., rejet des blocs résumés, intertitres insérés par programme | `app/proofread/*` |
| Vérification | Fidélité (règles + Claude) puis fact-check web avec garde-fou « sans recherche = introuvable », corrections par substitution exacte, notes de bas de page `[^vN]`, validation/rejet par correction (`/corrections/{id}/valider|rejeter`) | `app/proofread/verify.py`, `factcheck.py` |
| Éditeur | 3 panneaux, blocs éditables, **champ locuteur + rôle par bloc déjà présent** (`renderSpeakerControls`, propagation au groupe), annotations (note/surlignage/état), versions restaurables, recherche locale/globale, rendu par lots de 250, teinte de confiance, raccourcis espace/←/→/[/]/Ctrl+F/Échap/Ctrl+Entrée | `app/static/app.js:1296`, `:1368`, `:1688` ; `app/db.py` tables `annotations`, `review_versions` |
| Obsidian | Fiche avec frontmatter Dataview/Bases, entités jamais écrasées, MOC en région balisée, glossaire régénéré, republication idempotente (`obsidian_path`) | `app/obsidian/__init__.py`, `notes.py`, `entities.py` |
| NotebookLM | Un seul Doc maître réécrit intégralement (scope `drive.file`), init OAuth depuis l'UI, `--rebuild/--sync/--dry-run`, statut par travail | `app/notebooklm_sync.py` |
| Exports | txt / md / json / aperçu Obsidian ; `to_srt`/`to_vtt` existent encore côté serveur mais retirés de l'UI | `app/exporters.py:56-86` |
| Packaging | Exe Windows PyInstaller + tray + auto-update ; CI build Windows + image pod GHCR | `app/desktop.py`, `app/updates.py`, `.github/workflows/*` |

### Constats et écarts

**Dette immédiate**
- README obsolète : HEAD « Supprime le mode RunPod Serverless » a retiré `handler.py`, mais le README documente encore serverless, bascule et 3 modes de démarrage. `Dockerfile:28` et `pod_server.py:4-13` parlent encore de `handler.py`.
- `docs/PLAN.md` ne reflète plus la cible (il excluait explicitement locuteurs, sous-titres, SRT/VTT).
- Domaine MJPM codé en dur (`domaine: MJPM` dans `app/obsidian/notes.py:141`, dossiers par défaut, lexique unique).

**Écart « Sonix »**
- Aucun horodatage au mot : `word_timestamps` absent (`local.py:97`, `pod_server.py:100`), `Segment` n'a pas de `words` (`app/engines/base.py:14`). Donc pas de karaoké mot à mot ni de clic sur un mot.
- Aucune diarisation : le champ locuteur est purement manuel, bloc par bloc.
- Le découpage en tronçons de 180 s (`runpod_chunk_seconds`) est un héritage de la limite 10 Mo du serverless. Le pod HTTP n'a pas cette limite, et ce découpage rendrait la diarisation incohérente entre tronçons.
- Timeline « décorative » (pas de vraie forme d'onde issue de l'audio), pas de rechercher/remplacer, pas de fusion/scission de bloc, pas d'annuler/rétablir global, pas d'édition des horodatages.
- Langue figée `fr` (`config.py:77`, `<select name="language">` présent mais pas de détection automatique exposée).
- Lexique : pas d'UI d'édition (README « Ce qui n'est pas fait »), seulement `POST /api/lexicon`.
- Exports pro absents de l'UI : SRT/VTT (existent), DOCX/PDF (n'existent pas).

**Écart « mémoire long terme Obsidian »**
- Le coffre n'est **jamais lu** : `app/obsidian/vault.py` n'a que `read/resolve/write_atomic` pour les notes que l'app écrit. Pas de résolution d'alias, pas de lien vers des notes existantes, doublons d'entités possibles (« CDAPH » vs « Cdaph »).
- Seules les **entités nommées** issues du fact-check deviennent des notes ; aucune note de **concept**, aucune **synthèse par thème**, aucune accumulation inter-cours.
- La transcription verbatim (horodatée, locuteurs) n'est pas conservée dans le coffre, seulement la version éditoriale.

**Écart « NotebookLM révisions »**
- Un seul Doc maître : NotebookLM voit un document monolithique sans découpage par cours.
- Aucun matériel de révision généré (points clés, questions/réponses, flashcards) : le Doc contient le cours relu tel quel.
- Pas d'API publique NotebookLM (hypothèse à ce jour) : chaque nouveau Doc devra être ajouté une fois comme source à la main ; l'app doit rendre ce geste trivial (lien direct, statut).

---

## Cible d'architecture (résumé)

```
 média ─► pod RunPod (WhisperX : whisper large-v3 + alignement wav2vec2 + pyannote)
            └─► segments{words[], speaker, confidence}   ← contrat unique, tronçon = fichier entier
                  └─► éditeur : karaoké mot à mot, clic-mot, locuteurs nommés, forme d'onde réelle,
                                rechercher/remplacer, fusion/scission, undo/redo
                        └─► relecture → fidélité → fact-check (inchangés)
                              └─► publication Obsidian : fiche + verbatim + entités + concepts
                                    + synthèses par thème (validées) + MOC
                                    └─► NotebookLM : 1 Doc/cours (+ fiche de révision) + Doc maître optionnel
```

---

## Plan par chantiers

Chaque chantier = une PR livrable seule, tests verts, README mis à jour dans la même PR. Ordre recommandé : 0 → A → C → B → D (A est le socle de qualité ; C est court et rend NotebookLM utile vite ; B est le plus gros ; D ferme).

### Chantier 0 — Hygiène et alignement de la doc (petit)

- Réécrire les sections RunPod du README : pod uniquement (supprimer serverless, bascule, `handler.py`, contrat `/run`), corriger `Dockerfile:28` et l'en-tête de `pod_server.py`.
- Remplacer `docs/PLAN.md` par ce plan (renommé « PLAN v2 »), archiver l'ancien en `docs/PLAN_V1.md`.
- Rendre le domaine paramétrable : nouveau réglage `domain_label` (défaut `MJPM`) utilisé dans `notes.py:141`, les dossiers par défaut et le nom du glossaire. Pas de changement de comportement par défaut.

### Chantier A — Transcription niveau Sonix

**A1. Contrat de données : mots, locuteurs, fichier entier sur le pod**

- `app/engines/base.py` : `Segment` gagne `words: list[Word] | None` (`Word{start,end,text,confidence}`) et `speaker: str | None` ; `shifted()` décale aussi les mots.
- `pod_server.py` : remplacer faster-whisper seul par **WhisperX** (transcription batchée + alignement + diarisation pyannote quand `diarize=true` et `HF_TOKEN` présent). Nouvelle route `POST /transcribe` en **multipart** (fichier WAV/FLAC entier, plus de base64 ni de limite 10 Mo) ; garder l'ancien JSON base64 pour compatibilité un temps. Paramètres : `model`, `language` (`auto` accepté), `initial_prompt`, `diarize`, `min_speakers`, `max_speakers`. Réponse : `segments[{start,end,text,confidence,speaker,words[]}]`, `language`, `speakers[]`.
- `Dockerfile` / `requirements.txt` : épingler `whisperx`, `pyannote.audio`, garder cache HF sur `/runpod-volume` (modèles d'alignement et pyannote y vont aussi). Le workflow `pod-image.yml` reconstruit l'image sans changement.
- `app/engines/runpod.py` + `runpod_pod.py` : envoyer le fichier entier (streaming `httpx`), supprimer le découpage 180 s pour le pod (garder `media.split_wav` uniquement comme repli si le pod répond 413). Transmettre `HF_TOKEN` depuis un nouveau réglage secret `hf_token` (ajout à `SECRET_FIELDS`, `config.py:65`) en plus de l'environnement déjà géré.
- `app/engines/local.py` : activer `word_timestamps=True` (gratuit avec faster-whisper) pour que le moteur local reste cohérent même sans diarisation.
- `app/db.py` : `segments` et `review_blocks` portent `words`/`speaker` (JSON, pas de migration de schéma) ; `ensure_review_blocks` recopie `speaker` ; `update_review_block` recalcule les mots d'un bloc édité par alignement texte simple (les mots inchangés gardent leurs temps, les mots modifiés héritent d'une interpolation) — jamais de réécriture des `segments` bruts.
- Réglages : `diarization_enabled` (défaut `true`), `language` accepte `auto`, `hf_token`.
- Tests : `tests/test_pod_server.py`, `test_runpod_pod_engine.py`, `test_editor_api.py` (mots, locuteurs, décalage, compat sans `words`).

**A2. Éditeur : karaoké, clic-mot, locuteurs nommés, forme d'onde**

- `app/static/app.js` : rendu des blocs au niveau mot (`<span data-t="12.34">`) ; suivi du mot courant sur `timeupdate` (une seule classe active, pas de rerendu) ; clic sur un mot = seek ; double-clic = édition inline du bloc à ce mot. Confiance appliquée au mot quand disponible, au bloc sinon (réutilise `.conf-mid/.conf-low`).
- Locuteurs : regrouper l'affichage par tours de parole (en-tête « SPEAKER_00 » cliquable → renommer partout, réutilise `saveSpeakerField` et sa propagation de groupe), palette de couleurs par locuteur, raccourci pour attribuer le locuteur au bloc actif. Le champ « rôle » existant est conservé.
- Forme d'onde réelle : nouvelle route `GET /api/jobs/{id}/peaks` qui renvoie un profil RMS sous-échantillonné (réutilise `media._rms_profile`, `media.py:238`), mis en cache dans `data/media/{id}.peaks.json` ; rendu `<canvas>` dans la timeline existante, repères de blocs et curseur conservés.
- `index.html` / `styles.css` : styles mots, locuteurs, canvas ; aucun framework ajouté (hypothèse verrouillée du dépôt).
- Recette navigateur mise à jour dans `docs/RECETTE_PHASE6.md` → `docs/RECETTE.md`.

**A3. Édition avancée**

- Annuler/rétablir : pile locale d'opérations (texte de bloc, locuteur, fusion, scission) avec `Ctrl+Z / Ctrl+Y`, persistée par les mêmes routes ; une version `review_versions` est archivée avant chaque fusion/scission (réutilise `db.archive_review_version`).
- Fusion / scission de blocs : `POST /api/jobs/{id}/review-blocks/{block_id}/split` (position mot) et `/merge` (avec le suivant) ; les temps viennent des mots. Les annotations liées au bloc suivent (`block_id`, `range_start/end` recalculés).
- Rechercher / remplacer : extension de la recherche locale existante (`#editor-search-input`) avec champ « remplacer », « remplacer tout », respect de la casse ; chaque remplacement passe par `PUT review-blocks`.
- Dictionnaire personnel dans l'UI : panneau « Lexique » (liste, ajout, suppression, marquage vérifié) sur `GET/POST /api/lexicon` + nouveau `DELETE` ; bouton « Ajouter au lexique » sur une correction validée du fact-check (répond au « pas fait » du README).
- Édition des horodatages d'un bloc (début/fin) avec garde-fous (pas de chevauchement).

**A4. Exports pro**

- Réexposer SRT / VTT dans le menu Exporter (le code existe, `exporters.py:56-86`), avec locuteurs et règle de longueur de ligne.
- Ajouter DOCX (bibliothèque `python-docx`, format : titre, méta, tours de parole horodatés) ; PDF via le même Markdown → HTML → `weasyprint` **ou** laisser PDF de côté si la dépendance Windows pose problème dans PyInstaller (à confirmer au moment de l'implémentation ; DOCX suffit pour Word/Google Docs).
- `render(job, fmt)` reste le point d'entrée unique (`exporters.py:136`).

### Chantier C — NotebookLM : un Doc par cours + matériel de révision

**C1. Un Google Doc par cours**

- `app/notebooklm_sync.py` : nouvelle fonction `sync_course_doc(job)` qui crée (une fois) puis met à jour (`files().update`, même mécanique que `_update_master_doc`, `:269`) un Doc « {date} — {titre} » dans le dossier `notebooklm_drive_folder_id` (créer un sous-dossier « Cours » si vide). `fileId` mémorisé par travail : nouvelle colonne `notebooklm_doc_id` (ajout à `MIGRATIONS`, `db.py:84`).
- Le Doc maître devient optionnel : réglage `notebooklm_master_doc_enabled` (défaut `true` pour ne rien casser) ; `run_publish` et `run_notebooklm_sync` (`pipeline.py:784-816`) appellent le Doc du cours puis, si activé, le Doc maître.
- UI (`#notebooklm-btn`, panneau publication) : lien « Ouvrir le Doc du cours », statut par cours, et une ligne d'aide « Ajouter ce Doc comme source dans NotebookLM (une fois) ». Hypothèse : pas d'API publique NotebookLM pour ajouter une source ; à revérifier avant implémentation, et si une API existe, l'ajouter derrière un réglage.

**C2. Fiche de révision générée**

- Nouveau prompt dans `app/proofread/prompts.py` + fonction `build_revision(job)` : à partir de la version éditoriale (`exporters.editorial_text`), Claude produit un JSON structuré : `points_cles[]`, `definitions[{terme, definition}]`, `questions[{q, r}]` (10–20), `flashcards[{recto, verso}]`, `plan` (titres). Même back-end Claude que la relecture (`app/proofread/backends`), même modèle/effort.
- Stockage : colonne `revision` (JSON) sur `jobs`, régénérable seule via `POST /api/jobs/{id}/revision` (nouvelle tâche `TASK_REVISION`, rejouable comme les autres).
- Sorties : section « Fiche de révision » en tête du Doc du cours (`course_markdown`, `exporters.py:185`) ; onglet « Révision » dans le panneau droit ; note Obsidian « {titre} — Révision » (chantier B) avec les flashcards au format `Q::A` compatible avec le plugin Spaced Repetition d'Obsidian.
- Tests : `tests/test_notebooklm_sync.py` (Doc par cours, pas de doublon, maître optionnel), nouveau `tests/test_revision.py` (parsing JSON tolérant, repli sans back-end = aucune fiche, jamais d'échec de la publication).

### Chantier B — Obsidian comme mémoire long terme

**B1. Transcription verbatim dans le coffre**

- `app/obsidian/notes.py` : `render_verbatim(job)` → note « {date} — {titre} (verbatim) » dans `obsidian_verbatim_folder` (défaut `Formation/Transcriptions/Verbatim`) : frontmatter `type: verbatim`, lien vers la fiche, tours de parole horodatés `**[hh:mm:ss] Locuteur** — texte` issus des `review_blocks` (corrections humaines incluses, brut Whisper jamais réécrit). Lien réciproque dans la fiche (`verbatim: "[[…]]"`). Réglage `obsidian_write_verbatim` (défaut `true`).
- Republication idempotente : chemin mémorisé dans `obsidian_verbatim_path` (migration).

**B2. Lire le coffre pour relier**

- Nouveau module `app/obsidian/index.py` : parcourt le coffre (`.md`), lit titre, `aliases`, `tags`, `type` du frontmatter ; index en cache (`data/vault_index.json`, invalidé par mtime). Jamais d'écriture.
- Résolution d'entités : avant `ensure_entity_notes`, chaque entité est comparée à l'index (normalisation accents/casse, alias) ; si une note existe, on **lie** vers elle au lieu de créer un doublon, en ajoutant l'alias dans le frontmatter de la fiche du cours (jamais dans la note existante).
- Liens de concepts : lors de la relecture structurée (`app/proofread/structure.py`), passer à Claude la liste des titres de notes existantes pertinentes (filtrée par mots du cours, bornée) pour qu'il propose des `[[wikilinks]]` sur les notions déjà présentes dans le coffre ; insertion par le programme, par substitution exacte, uniquement sur la première occurrence (même principe que le fact-check : le modèle propose, le programme dispose).
- Route `GET /api/vault/index?q=` pour le panneau UI et `POST /api/vault/reindex`.

**B3. Concepts atomiques et synthèses par thème (validées)**

- Nouvelle tâche `TASK_KNOWLEDGE` (« Capitaliser ») après publication, rejouable :
  1. Claude extrait de la version éditoriale une liste de **concepts** (`{nom, definition, extrait, source_bloc}`) et propose des **thèmes** en tenant compte de l'index du coffre et des thèmes déjà existants (`obsidian_themes_folder`, défaut `Formation/Synthèses`).
  2. Stockage en base (`knowledge` JSON sur `jobs`, statut `proposed`).
  3. **UI « Mémoire »** (panneau droit) : liste des concepts (cocher/décocher, renommer) et des thèmes (accepter, renommer, fusionner avec un thème existant proposé par l'index). Rien n'est écrit tant que l'utilisateur ne valide pas.
  4. Écriture : notes de concept dans `obsidian_concepts_folder` (créées si absentes, sinon **seulement** une ligne ajoutée dans une région balisée « Sources » de la note existante), synthèse par thème dans une région balisée `<!-- synthese:début/fin -->` : Claude reçoit la synthèse actuelle + le nouveau cours et rend une synthèse mise à jour ; l'ancienne est archivée dans `data/syntheses/{theme}/{date}.md`, et un diff est montré avant écriture. Le MOC reçoit une section « Thèmes ».
- Prompts dédiés dans `prompts.py` ; parsing tolérant ; repli sans back-end = tâche sautée avec message, jamais d'échec de publication.
- Tests : `tests/test_vault_index.py`, `tests/test_knowledge.py` (aucune note existante modifiée hors région balisée, idempotence, validation obligatoire).

**B4. Interroger le coffre (option, plus tard)**
Non planifié maintenant (non retenu par l'utilisateur) ; l'index B2 en est le socle si le besoin vient.

### Chantier D — Qualité, robustesse, livraison

- Tests de contrat front étendus (`tests/test_ui_contract.py`) pour les nouveaux identifiants ; script Playwright optionnel (Chromium déjà présent) pour la recette clavier/mots/locuteurs.
- Performance : rendu mot à mot borné par lot (garder les 250 blocs), `requestAnimationFrame` pour le suivi, peaks mis en cache.
- README réécrit par sections (Transcription, Éditeur, Obsidian, NotebookLM, Réglages) ; suppression des passages historiques (bugs du prototype) vers `docs/HISTORIQUE.md`.
- Build Windows : vérifier que `python-docx` (et `weasyprint` si retenu) passent PyInstaller ; l'image pod est reconstruite par le workflow existant.

---

## Réutilisation explicite

- Propagation de locuteur par groupe : `saveSpeakerField` (`app/static/app.js:1368`).
- Profil d'énergie audio : `media._rms_profile` (`app/media.py:238`) pour la forme d'onde.
- Mise à jour Drive sans doublon : `_update_master_doc` (`app/notebooklm_sync.py:269`) et `create_master_doc` (`:231`).
- Version éditoriale canonique : `exporters.editorial_text` (`app/exporters.py:26`) et `course_markdown` (`:185`).
- Régions balisées : `MOC_START/MOC_END` (`app/obsidian/entities.py:16`) comme modèle pour synthèses et sources de concepts.
- Migrations additives : `MIGRATIONS` (`app/db.py:84`).
- Back-ends Claude et parsing tolérant : `app/proofread/backends/*`, `factcheck.apply_verdicts` (substitution exacte).
- Tâches rejouables : `_RUNNERS` (`app/pipeline.py:819`) et `_Progress`.

## Hypothèses et points à confirmer en cours de route

- WhisperX + pyannote 3.x sur l'image `runpod/base:0.6.2-cuda12.1.0` : à valider au premier build (versions torch/CUDA). Le modèle d'alignement français (`WAV2VEC2_ASR`/`jonatasgrosman/wav2vec2-large-xlsr-53-french`) et pyannote iront sur le volume réseau.
- Pas d'API publique NotebookLM : l'ajout d'une source reste manuel, une fois par cours.
- Le moteur local reste disponible mais secondaire (mots oui, diarisation non).
- PDF : dépendance possiblement lourde sous PyInstaller ; DOCX est la sortie garantie.

## Vérification de bout en bout

1. `pytest` vert à chaque chantier (les moteurs, Claude et Google restent doublés comme aujourd'hui, `tests/conftest.py`).
2. Chantier A : construire l'image pod, créer un pod, importer une réunion à 3 voix ; vérifier segments avec `words` et `speaker`, karaoké mot à mot, clic-mot, renommage d'un locuteur propagé, forme d'onde alignée, fusion/scission/undo, export SRT avec locuteurs, DOCX ouvrable dans Word.
3. Chantier C : publier un cours → un Doc « {date} — {titre} » apparaît dans le dossier Drive avec la fiche de révision en tête ; republier ne crée pas de second Doc ; Doc maître mis à jour si activé ; l'ajouter une fois dans NotebookLM et vérifier la resynchronisation.
4. Chantier B : sur un coffre de test contenant déjà « CDAPH.md » avec alias, publier un cours citant « Cdaph » → lien vers la note existante, pas de doublon ; note verbatim créée et reliée ; lancer « Capitaliser », valider 2 thèmes dans l'UI, vérifier que seules les régions balisées ont changé (`git diff` sur le coffre de test versionné).
5. Recette navigateur (`docs/RECETTE.md`) rejouée sur Windows via l'exe produit par le workflow.
