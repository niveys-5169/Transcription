# Refonte « éditeur Sonix » avec publication Obsidian et synchronisation NotebookLM

## Résumé

Créer une application locale centrée sur une bibliothèque de transcriptions et un éditeur média/texte synchronisé. Le lecteur affiche la vidéo originale si le navigateur la supporte, sinon bascule sur le WAV extrait. Les sous-titres, traductions et exports SRT/VTT disparaissent de l'interface.

L'outil conserve et met en avant son avantage spécifique par rapport à Sonix : après validation humaine, une transcription peut être publiée dans Obsidian puis intégrée au document Google Docs maître utilisé par NotebookLM.

## Contrat fonctionnel et API

- Ajouter `GET /api/jobs/{id}/media` : sert le fichier source avec son type MIME ; si le média est absent ou illisible, le client bascule sur le WAV extrait.
- Ajouter `review_blocks` au travail : liste persistée de blocs `{id, start, end, text, speaker?}` initialisée depuis les segments horodatés. `segments` reste la transcription brute immuable.
- Ajouter une table `annotations` : `{id, job_id, block_id, type, color?, content?, status, created_at, updated_at}`.
  - Types v1 : `note`, `highlight`, `review`.
  - États : `a_verifier`, `valide`, `ignore`.
- Ajouter :
  - `GET /api/jobs/{id}/review-blocks`
  - `PUT /api/jobs/{id}/review-blocks/{block_id}`
  - `GET|POST /api/jobs/{id}/annotations`
  - `PATCH|DELETE /api/jobs/{id}/annotations/{annotation_id}`
  - `GET /api/search?q=`
- La recherche globale retourne `{job_id, title, match_text, start, end, match_count}` afin qu'un clic ouvre le travail et se positionne sur le bon passage.
- Les exports TXT, Markdown, JSON, la publication Obsidian et la compilation Google Docs utilisent les `review_blocks` dès qu'ils ont été modifiés ; sinon ils conservent le texte relu existant.
- SRT/VTT restent rétrocompatibles en interne, mais ne sont plus proposés dans l'interface.
- Aucun compte, partage public, collaboration en temps réel, traduction ou sous-titrage n'est implémenté.
- `segments` (et, une fois calculés, `review_blocks`) peuvent porter un score de confiance par mot ou par bloc, dérivé de ce que le moteur de reconnaissance vocale expose déjà (ex. `avg_logprob`/`no_speech_prob` par segment avec `faster-whisper`, ou une confiance par mot si le moteur la fournit). Ce score est purement indicatif : il n'entre jamais dans le texte, ne modifie jamais la transcription, et sert uniquement à l'affichage (voir Phase 3).

## Phase 1 — Socle de données, média et migrations — Codex

- Faire évoluer SQLite sans migration destructive :
  - colonne JSON `review_blocks` dans `jobs` ;
  - table `annotations` et index par `job_id`, `block_id`, `status` ;
  - conservation des anciens travaux et de toutes les colonnes existantes.
- Initialiser les blocs de révision à partir des segments ; pour les travaux existants, effectuer l'initialisation à la première ouverture.
- Ajouter l'accès contrôlé au média source et le repli audio sans jamais exposer un chemin local dans les réponses JSON.
- Faire évoluer la recherche pour inspecter titre, texte relu et blocs de révision avec extrait et horodatage.
- Ajouter les tests de migration, de lecture média, de persistance des blocs, d'annotations, de recherche temporelle et de compatibilité des exports.

**Handoff à Claude :** contrat API documenté avec exemples JSON et suite `pytest` verte.

## Phase 2 — Nouvelle structure visuelle et bibliothèque — Claude

- Remplacer la page actuelle par un layout desktop à trois zones :
  - gauche : bibliothèque, recherche, filtres et import ;
  - centre : espace de travail ;
  - droite : analyse, notes et actions de publication ;
  - mobile : panneaux repliables avec priorité à l'éditeur.
- Réduire l'import à l'action principale « Importer un enregistrement » ; déplacer moteur, modèle, RunPod et options de chaîne dans un panneau fermé par défaut.
- Refaire les cartes de bibliothèque : titre, date, durée, statut, progression, nombre de points à vérifier et état de publication.
- Créer un langage visuel sobre, dense et éditorial : typographie de lecture, actions hiérarchisées, focus accessibles et états clairs.
- Retirer les entrées UI liées aux sous-titres, SRT et VTT.
- Préparer des zones stables pour le lecteur, la timeline, les blocs, la comparaison, les annotations, Obsidian et NotebookLM.

**Handoff à Codex :** interface responsive statique finalisée ; identifiants DOM et états UI documentés.

## Phase 3 — Éditeur synchronisé et interactions de lecture — Codex

- Remplacer les onglets texte/audio par un éditeur central :
  - vidéo source compacte, déplaçable et rétractable ;
  - repli automatique vers le WAV ;
  - barre native interactive indiquant progression, durée, position et repères de blocs ;
  - lecture/pause, ±5 secondes et vitesses 0,75×, 1×, 1,25×, 1,5×, 2×.
- Afficher les `review_blocks` dans une zone de lecture et d'édition :
  - clic sur un bloc ou un horodatage : lecture au bon instant ;
  - suivi visuel du bloc en cours ;
  - édition inline ;
  - `Escape` annule, `Ctrl/Cmd+Enter` enregistre ;
  - statut clair de sauvegarde.
- Ajouter les raccourcis : espace hors édition, flèches ±5 secondes, `[`/`]` pour la vitesse et `Ctrl/Cmd+F` pour la recherche locale.
- Ajouter une comparaison repliable « Version IA » qui montre le texte relu actuel sans modifier le texte brut.
- Après la première modification humaine, faire des blocs de révision la sortie éditoriale canonique.
- Colorer le texte affiché selon le niveau de confiance de la reconnaissance vocale, à la manière de Sonix : un dégradé sobre (texte normal → teinte d'alerte progressive) sur les mots ou segments les moins fiables, pour orienter la relecture sans devoir tout réécouter. Purement indicatif — n'affecte jamais le texte ni son édition, coexiste avec le surlignage manuel des annotations (Phase 4), et disparaît dès qu'un bloc est validé humainement (un bloc modifié n'a plus besoin d'être signalé). Prérequis : le moteur de transcription (`app/engines/*`) doit remonter un score par segment ou par mot, stocké dans `segments`/`review_blocks` — petit ajout ciblé au contrat de données de la Phase 1, à faire en même temps si nécessaire.

**Handoff à Claude :** parcours média → navigation → correction → rechargement démontré et sélecteurs d'intégration stabilisés.

## Phase 4 — Révision, annotations et recherche globale — Claude

- Construire le panneau droit « Analyse » :
  - résumé ;
  - vérification de fidélité ;
  - sources ;
  - notes du bloc actif ;
  - compteur d'éléments à vérifier ;
  - état de publication Obsidian et de synchronisation NotebookLM.
- Ajouter les interactions : note, surlignage à palette limitée, état de révision et liste filtrable avec navigation vers le bon bloc.
- Ajouter la recherche locale : surbrillance, suivant/précédent, compteur et navigation temporelle.
- Ajouter la recherche globale dans la bibliothèque : résultats groupés par transcription, extrait, occurrences et accès au passage exact.
- Ajouter tags locaux et filtres par statut, date et tag.
- Regrouper les actions secondaires dans des menus : exporter, relancer une étape, publier dans Obsidian, synchroniser NotebookLM et supprimer.
- Le CTA principal suit l'état : `Relire`, `Vérifier`, `Publier dans Obsidian` ou `Synchroniser NotebookLM`.

**Handoff à Codex :** tous les états vide, chargement, erreur, petit écran et média indisponible sont vérifiés visuellement.

## Phase 5 — Obsidian et Google Docs / NotebookLM — Codex

- Refondre la sortie Obsidian autour de la transcription révisée :
  - générer le Markdown à partir des blocs humains, avec titre, résumé, tags, métadonnées, statut de vérification et liens vers les sources ;
  - préserver la structure actuelle du coffre, les wikilinks, le MOC et le glossaire ;
  - afficher dans l'interface le chemin de la note publiée, la date de dernière publication et l'action « Ouvrir dans Obsidian ».
- Ajouter un statut de publication distinct et lisible :
  - non configuré ;
  - prêt à publier ;
  - publication en cours ;
  - publié ;
  - erreur détaillée sans perte de transcription.
- Transformer la synchronisation Google Docs en étape visible après Obsidian :
  - régénérer le Markdown intermédiaire du cours depuis la version révisée ;
  - mettre à jour le document Google Docs maître existant, sans créer de doublon ;
  - conserver l'ordre des cours et le sommaire existant ;
  - exposer l'état `NotebookLM non configuré`, `à synchroniser`, `synchronisé`, `erreur`.
- Définir le comportement de publication :
  - le bouton `Publier dans Obsidian` ne contacte que le coffre local ;
  - le bouton `Synchroniser NotebookLM` ne s'active qu'après une publication Obsidian réussie et si Google est configuré ;
  - l'option de chaîne « Publier et synchroniser » exécute Obsidian puis Google Docs ; un échec Google ne défait jamais la publication Obsidian.
- Conserver la configuration Google existante, le scope Drive minimal et le document maître déjà mémorisé ; ne pas automatiser d'authentification ou de consentement Google dans l'éditeur.
- Ajouter les tests : contenu Obsidian venant des blocs révisés, absence de modification du texte brut, publication répétée sans doublon, échec Drive non bloquant, synchronisation du même document maître et indicateurs d'état corrects.

## Phase 6 — Intégration, robustesse et recette — Claude

- Réconcilier les parcours : transcription seule, relecture relancée, vérification externe, édition manuelle, publication Obsidian, synchronisation Google Docs, annulation, reprise et suppression.
- Vérifier que les modifications humaines ne détruisent ni transcription brute, ni rapport de vérification, ni sources, ni annotations.
- Ajouter ou compléter les tests front-end disponibles ; sinon, documenter une recette navigateur reproductible qui complète les tests API.
- Vérifier navigation clavier, focus visible, contrastes et lecteur utilisable sans souris.
- Vérifier les performances pour une transcription longue : rendu progressif des blocs, pas de rerendu complet à chaque événement audio, recherche bornée et listes sans colonnes lourdes.
- Retirer les dernières références visuelles aux sous-titres, SRT et VTT.

## Recette d'acceptation

- Importer une vidéo, ouvrir un bloc et vérifier que la vidéo démarre au bon horodatage.
- Forcer une vidéo non lisible et vérifier le basculement WAV.
- Modifier un bloc, recharger la page et constater sa persistance.
- Créer, modifier et supprimer une note ; modifier le statut de révision ; naviguer depuis le panneau latéral vers le bloc concerné.
- Rechercher une expression localement puis dans la bibliothèque ; chaque résultat ouvre le bon instant.
- Publier une transcription éditée dans Obsidian et vérifier que la note contient les corrections humaines, métadonnées et sources.
- Synchroniser le document Google Docs maître et vérifier que le cours apparaît ou est mis à jour sans doublon.
- Provoquer une erreur Google Docs et vérifier que la note Obsidian reste publiée et que l'erreur est actionnable.
- Télécharger Markdown/TXT/JSON et vérifier que leur contenu correspond à la version révisée.
- Vérifier qu'aucune option de sous-titrage n'est visible.
- Vérifier que les mots ou segments peu fiables sont visuellement distingués dans l'éditeur, et que cette coloration ne modifie jamais le texte ni son export.

## Hypothèses verrouillées

- Cible : expérience proche de Sonix pour la révision individuelle locale, enrichie par une chaîne Obsidian → Google Docs → NotebookLM absente de Sonix.
- Lecture : vidéo source prioritaire ; WAV extrait en repli.
- Timeline : barre interactive native, sans WaveSurfer ni dépendance front-end supplémentaire.
- Notes, surlignages et états de révision sont persistés en SQLite.
- Stack conservé : FastAPI, SQLite, HTML/CSS/JavaScript sans framework.
- Ordre de réalisation : Codex Phase 1, Claude Phase 2, Codex Phase 3, Claude Phase 4, Codex Phase 5, Claude Phase 6.
- La coloration par confiance (Phase 3) est un raffinement optionnel de l'éditeur, pas un prérequis des phases suivantes : si le moteur en place ne remonte aucun score exploitable, l'éditeur reste fonctionnel sans elle.
