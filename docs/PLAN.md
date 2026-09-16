# Verbatim — plan v2, état au 16 septembre 2026

## But produit

Verbatim transforme un enregistrement en transcription relue, vérifiée,
publiée dans Obsidian et utilisable dans NotebookLM. La cible est triple :

1. un éditeur de transcription au niveau Sonix ;
2. une mémoire long terme reliée à un coffre Obsidian ;
3. un cours et une fiche de révision par source NotebookLM.

Les contenus visés sont les cours comme les réunions : les locuteurs nommés
et les horodatages au mot sont donc des données de premier rang. Le calcul GPU
est réalisé sur un pod RunPod ; le moteur local reste un repli pratique.

## État de livraison

`main` contient les livraisons ci-dessous (PR #38 à #49) ; les compléments v2
sont prêts localement. La suite de tests locale est verte. Les validations nécessitant des services
réels (RunPod, Google Drive, NotebookLM et un coffre utilisateur) restent à
exécuter manuellement avant une release.

| Chantier | État | Livré |
|---|---|---|
| 0 — Hygiène | Terminé | Documentation pod-only, plan v2, domaine configurable. |
| A1 — Contrat audio | Terminé | Mots, locuteurs, WhisperX/pyannote sur le pod, multipart, `HF_TOKEN`, langue `auto`. |
| A2 — Éditeur mot à mot | Terminé | Karaoké, clic-mot, locuteurs colorés et renommables, vraie forme d'onde RMS. |
| A3 — Édition avancée | Terminé | Fusion/scission, annotations déplacées, rechercher/remplacer, undo/redo, lexique UI et édition sécurisée des temps. |
| A4 — Exports pro | Terminé | SRT/VTT avec locuteurs et lignes bornées ; DOCX avec métadonnées et tours de parole. PDF est explicitement hors périmètre. |
| C1 — Un Doc par cours | Terminé | Création/mise à jour idempotente du Doc de cours, Doc maître optionnel, lien dans l'UI. |
| C2 — Fiche de révision | Terminé | Génération JSON, tâche rejouable, affichage, insertion dans le Doc de cours et note Obsidian Spaced Repetition. |
| B1 — Verbatim Obsidian | Terminé | Note horodatée, locuteurs, liens réciproques et republication idempotente. |
| B2 — Index du coffre | Terminé | Index titre/alias/tag/type, rapprochement d'entités, API de recherche/réindexage et wikilinks contrôlés. |
| B3 — Mémoire validée | Terminé | Propositions Claude, sélection/renommage, écriture balisée, synthèses, archive et MOC Thèmes. |
| D — Qualité/livraison | Terminé en local | Recette v2, rendu borné, suivi par animation frame, cache des peaks et inclusion DOCX dans PyInstaller. |

## Fait dans `main`

### 0 — Hygiène et configuration

- Documentation et image RunPod alignées sur l'usage par pod HTTP.
- `domain_label` rend les noms de domaine et les chemins Obsidian configurables
  sans changer le défaut `MJPM`.
- Les réglages sensibles (`HF_TOKEN`, clés API) restent masqués par l'API.

### A1/A2 — Transcription et éditeur Sonix

- Le contrat `Segment` comporte désormais `words[]` et `speaker` ; décalage,
  persistance JSON et compatibilité avec les anciens segments sont couverts.
- Le pod accepte un fichier complet en multipart et produit des segments
  WhisperX alignés, avec diarisation pyannote quand elle est activée et que le
  jeton Hugging Face est disponible. Le moteur local demande aussi les temps
  par mot.
- L'éditeur rend les mots individuellement, suit le mot lu sans rerendu et
  permet de cliquer un mot pour déplacer le lecteur.
- Les tours de parole sont visuellement regroupés, colorés et renommables ;
  la propagation existante de locuteur est conservée.
- `GET /api/jobs/{id}/peaks` met en cache une forme d'onde RMS et la timeline
  la dessine sur canvas.

### C1/C2 — NotebookLM et révision

- Chaque travail peut avoir son Google Doc « date — titre », réutilisé à chaque
  synchronisation ; le Doc maître est un réglage optionnel.
- Une fiche de révision structurée est générée via le même back-end Claude que
  la relecture : points clés, définitions, questions/réponses, flashcards et
  plan. Elle est stockée en JSON, régénérable et présente dans le Doc de cours
  ainsi que dans le panneau Révision.
- NotebookLM ne reçoit pas encore automatiquement une source : l'ajout du Doc
  par cours reste manuel, une seule fois.

### B1/B2/B3 — Mémoire Obsidian

- La publication produit une fiche et, par défaut, une note verbatim reliée,
  avec tours de parole et horodatages des blocs relus.
- Le coffre est indexé en lecture seule (titre, alias, tags, type) et mis en
  cache dans `data/vault_index.json`. Les entités résolues via un alias sont
  liées à la note existante au lieu de générer un doublon.
- `GET /api/vault/index?q=` consulte l'index et `POST /api/vault/reindex` le
  reconstruit.
- La tâche « Capitaliser » propose concepts et thèmes ; rien n'est écrit avant
  sélection et renommage explicites dans le panneau Mémoire.
- Les notes de concept ne changent que leur région Sources. Les synthèses de
  thème sont comparées avant validation, écrites dans une région balisée,
  archivées dans `data/syntheses/{theme}/{date}.md` et ajoutées au MOC.

## Livraison restante hors dépôt

Le code du plan v2 est terminé. Les seules étapes restantes exigent des
services ou un environnement utilisateur et ne peuvent pas être simulées par
la suite locale : essai RunPod réel (GPU/diarisation), synchronisation Google
Drive/NotebookLM, recette sur coffre Obsidian réel et build Windows en CI.
La procédure reproductible est dans [RECETTE.md](RECETTE.md).

PDF est volontairement hors périmètre : DOCX est l'export bureautique garanti
et évite d'introduire une dépendance de rendu lourde dans l'exécutable Windows.

## Validation de bout en bout à faire avant release

1. Construire l'image pod et traiter une réunion de trois voix : mots,
   diarisation, clic-mot, renommage, forme d'onde et exports.
2. Publier un cours avec Google : un Doc de cours est créé puis réutilisé, la
   fiche de révision est en tête et le Doc maître est mis à jour si activé.
3. Utiliser un coffre de test avec `CDAPH.md` et l'alias `Cdaph` : aucune note
   en double, verbatim relié, mémoire validée et seules les régions balisées
   modifiées.
4. Rejouer la recette navigateur et la suite `pytest` depuis l'exécutable
   Windows produit par CI.

## Décisions conservées

- RunPod est le moteur GPU cible ; le moteur local reste disponible, avec mots
  mais sans diarisation.
- Aucune dépendance de framework front : HTML/CSS/JavaScript natifs.
- Les modèles proposent ; le programme et l'utilisateur gardent la décision
  d'écrire, de corriger ou de publier.
