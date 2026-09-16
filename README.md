# Verbatim — éditeur de transcription

Application locale qui transforme un enregistrement — vidéo ou audio — en une
**transcription révisée, vérifiée et publiable dans Obsidian**. Verbatim est
un outil d'édition : le média, le texte horodaté et les décisions de révision
restent au centre de l'expérience.

On dépose le fichier, on clique une fois, et l'application enchaîne quatre
étapes : extraction audio, transcription, relecture, puis vérification par
recherche web des noms propres, titres de rapport, statistiques et
références juridiques cités — pas une simple relecture de plausibilité. Ce
qui n'a pas pu être confirmé reste visible dans le texte, avec un appel de
note : rien n'est lissé en une version fluide mais faussement définitive.
L'utilisateur relit ensuite les blocs synchronisés au média, annote les
passages à revoir, puis publie le résultat dans un coffre Obsidian avec ses
fiches d'entités et son glossaire. Une publication peut enfin mettre à jour
un unique document Google Docs, source de NotebookLM.

Tout tourne sur votre machine. Rien n'est envoyé sur Internet, sauf si vous
activez explicitement le GPU RunPod, la relecture et la vérification par
Claude, ou la synchronisation Google Docs — voir
[Accès à Claude](#accès-à-claude--cli-ou-clé-api).

```
  1. TRANSCRIPTION        2. RELECTURE          3. VÉRIFICATION        4. PUBLICATION
  un calcul               une lecture           une recherche          une fiche
  ┌───────────┐  ffmpeg  ┌──────────┐  Claude   ┌──────────────┐      ┌──────────┐
  │vidéo/audio│─────────►│texte brut│──────────►│texte relu +  │─────►│  coffre  │
  │n'importe  │ Whisper  │+segments │ fidélité  │vérification  │Claude│ Obsidian │
  │quel format│          │datés     │           │web (fait)    │      │          │
  └───────────┘          └──────────┘           └──────────────┘      └──────────┘
  blocs horodatés         version IA + édition     appels de note       Obsidian → Google Docs
  immuables               humaine canonique        sur l'incertain      → NotebookLM
```

**Les quatre étapes sont indépendantes.** Chacune part de ce que la
précédente a produit, plus tard si vous voulez, et peut être relancée autant
de fois que nécessaire sans jamais refaire tourner celles d'avant. Décocher
une étape dans les options avancées du dépôt arrête la chaîne à la
précédente — un travail « transcrit », « relu » ou « vérifié » est déjà un
état exploitable, pas une étape de passage.

## Orientation produit et UI

La direction retenue est l'**éditeur professionnel** : une interface sombre,
calme et dense, conçue pour relire de longues transcriptions sans perdre le
contexte audio. Toute évolution de l'interface doit préserver cette
hiérarchie, plutôt que de transformer l'application en tableau de bord.

- **À gauche : projets.** Import, recherche, filtres et bibliothèque de
  transcriptions.
- **Au centre : éditeur.** Titre du média, commandes, forme d'onde / timeline
  synchronisée et blocs de texte éditables.
- **À droite : révision.** Résumé, vérification, sources, notes, tags et
  publication ; ces éléments accompagnent l'édition, ils ne doivent pas
  concurrencer le texte.
- **Identité visuelle.** Thème sombre permanent, surfaces discrètes et accent
  violet réservé à la progression, au bloc actif et aux actions principales.
  Les états de confiance, avertissement et erreur conservent leur couleur
  sémantique.
- **Navigation média.** La timeline est une forme d'onde décorative avec une
  progression lumineuse, un curseur précis et des repères de blocs. Le clic
  sur un horodatage ou la timeline garde la synchronisation texte / média.

Les fonctionnalités, raccourcis clavier, données locales et APIs existantes
restent prioritaires sur tout changement esthétique.

## Démarrer

**Windows** — double-cliquez sur `lancer.bat`.
**macOS / Linux** — `./lancer.sh`.

Le premier lancement crée un environnement Python isolé et installe les
dépendances (quelques minutes). Les suivants démarrent en quelques secondes.
Le navigateur s'ouvre sur <http://127.0.0.1:8765>.

Il faut Python 3.10 ou plus récent. Rien d'autre : **ffmpeg est installé
automatiquement** avec les dépendances, il n'y a pas d'installation système à
faire.

<details>
<summary>Lancement manuel</summary>

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-app.txt   # Windows : .venv\Scripts\pip
.venv/bin/python run.py
```

Options : `--port 9000`, `--host 0.0.0.0` (accès depuis le réseau local),
`--no-browser`, `--reload` (développement). Si le port demandé est occupé,
le lanceur choisit automatiquement le premier port libre suivant et affiche
l'URL réellement utilisée.
</details>

## Application Windows (sans installer Python)

Pour un usage courant sur une machine où l'on ne veut pas installer Python ni
garder une fenêtre de terminal ouverte, `packaging/build.bat` produit un
exécutable autonome : `dist/Transcription/Transcription.exe`.

- Pas de fenêtre console : une icône dans la barre système propose
  « Ouvrir », « Voir les logs » et « Quitter ».
- Les logs et les données (`data/`) vivent dans
  `%LOCALAPPDATA%\Transcription`, pas dans le dossier de l'exe.
- Les réglages et clés API vivent dans
  `%LOCALAPPDATA%\Transcription\config.json`. La base des transcriptions,
  les médias et les jetons Google sont au même endroit : reconstruire ou
  remplacer l'exécutable ne les modifie jamais.
- Relancer l'exe pendant qu'il tourne déjà rouvre simplement le navigateur
  sur l'instance existante, plutôt que d'en démarrer une seconde.

Le build se fait dans un venv dédié (`.venv-build`), avec le même
interpréteur Python que le reste du projet — voir `packaging/build.bat`.

### Compilation automatique

Chaque push sur la branche `main` lance aussi une compilation Windows dans
l’onglet **Actions** de GitHub. Une fois le workflow **Application Windows**
terminé, téléchargez l’artefact **Transcription-Windows** : il contient le
dossier complet, avec `Transcription.exe`. Les artefacts sont conservés
30 jours. Le même workflow peut être lancé manuellement depuis GitHub avec
**Run workflow**.

L’exécutable vérifie automatiquement cette « dernière version » à son
démarrage. Si elle est plus récente, le bouton **Mise à jour disponible**
télécharge le paquet, ferme l’application, remplace le dossier complet puis
redémarre. Pendant le remplacement, une fenêtre **Installation de la mise à
jour** affiche chaque étape et conserve les erreurs éventuelles dans
`%LOCALAPPDATA%\Transcription\update.log`. Il faut donc toujours conserver le
dossier `Transcription` entier, pas seulement le fichier `.exe`.

Le téléchargement se fait en arrière-plan : le bouton indique sa progression
et le journal est créé dès le premier essai réseau. Une connexion qui ne
répond pas échoue après 20 secondes au lieu de laisser l’application bloquée.

Sous Windows, Verbatim ouvre sa fenêtre dédiée avec Google Chrome en priorité ;
Microsoft Edge reste uniquement une solution de secours si Chrome est absent.

## Réviser une transcription

L'interface est organisée en trois panneaux : la **bibliothèque** à gauche,
l'**éditeur synchronisé** au centre et l'**analyse / publication** à droite.
Sur petit écran, ces panneaux deviennent des vues repliables qui donnent la
priorité à l'éditeur.

- Le lecteur essaie d'abord la vidéo source, puis bascule automatiquement sur
  le WAV extrait. Il propose lecture/pause, sauts de 5 secondes, vitesses,
  timeline et repères de blocs.
- Cliquer un horodatage positionne le lecteur ; le bloc en cours est suivi
  visuellement. Un bloc se modifie en ligne : `Échap` annule et
  `Ctrl/Cmd+Entrée` enregistre.
- Après relecture, l'éditeur affiche la version IA. Le bouton **Brut** de
  chaque passage révèle la source Whisper et permet de réécouter exactement
  son intervalle horodaté.
- Les segments peu fiables peuvent être teintés discrètement lorsque le
  moteur fournit une confiance. La teinte disparaît après une correction
  humaine et ne change jamais le texte ni ses exports.
- Une recherche locale met les correspondances en évidence ; la recherche de
  bibliothèque ouvre directement le travail et son horodatage. Les listes
  très longues sont rendues par lots de 250 blocs.
- Notes, surlignages et états « à vérifier / validé / ignoré » sont liés à un
  bloc ; les surlignages peuvent viser une expression précise. Une nouvelle
  relecture archive la version et ses annotations, restaurables depuis
  **Historique**. Les tags de bibliothèque restent locaux au
  navigateur.
- Un travail peut être annulé, puis repris depuis le fichier d'origine ; les
  actions impossibles ou déjà en cours restent explicitement désactivées.

La transcription brute et ses segments ne sont jamais réécrits. Dès qu'un
bloc est corrigé, la version des blocs devient la source utilisée par TXT,
Markdown, JSON, Obsidian et Google Docs.

Le détail de la livraison est conservé dans les
[hand-offs des phases 4 et 5](docs/PHASE4_HANDOFF.md),
[la recette de la phase 6](docs/RECETTE_PHASE6.md) et le
[plan réalisé](docs/PLAN.md).

## Pourquoi des étapes séparées

Transcrire, relire, vérifier et publier sont quatre métiers différents, et
les mélanger coûte cher.

**Transcrire est un calcul.** On donne de l'audio, on récupère les mots
prononcés et leurs horodatages. Whisper fait ça, que ce soit sur votre
processeur ou sur un GPU loué. Le résultat est vérifiable, reproductible, et
c'est tout ce qu'on lui demande — d'où le mot « pure » : le worker RunPod ne
fait rien d'autre, ne reformule rien, ne corrige rien.

**Relire est une lecture.** Il faut comprendre le propos pour savoir qu'« a
priori » n'était pas « appris ou rit », rétablir la ponctuation, décider où
commence une nouvelle idée. C'est du travail sur du texte.

**Vérifier est une recherche.** Un nom propre, un titre de rapport, une
statistique plausibles à l'oreille peuvent être faux — la relecture, aussi
soignée soit-elle, ne peut pas le savoir sans aller voir ailleurs. C'est un
travail différent, qui suppose d'interroger le monde extérieur, pas
seulement de relire.

**Publier est une mise en forme.** Écrire une fiche, la classer, la relier
au reste d'un second brain — encore un métier à part, qui n'a aucune raison
de bloquer les trois précédents.

Aucune de ces étapes n'a de raison de se produire au même moment que les
autres. Les séparer donne plusieurs choses :

- **Le texte brut arrive tout de suite** et ne dépend de rien d'autre. Pas de
  clé, pas de réseau, pas d'attente supplémentaire. Les segments horodatés
  restent conservés, immuables, pour être comparés à la version éditée.
- **Chaque étape suivante se rejoue, seule.** Pas satisfait du découpage ?
  Envie d'essayer sans les intertitres, avec un effort plus élevé, ou de
  relancer seulement la vérification web sur un texte relu à la main
  entre-temps ? On relance l'étape voulue seule : quelques secondes ou
  minutes d'appels, au lieu de tout refaire depuis l'audio.
- **Un échec à une étape ne détruit rien.** Abonnement non connecté, quota
  atteint, panne réseau, coffre introuvable : le travail retombe à l'état
  précédent, ce qu'il avait déjà produit reste là, et le bouton de l'étape
  concernée attend.

Concrètement : le bouton **« Importer et transcrire »** enchaîne les quatre étapes avec
les réglages par défaut. Dans les options avancées, décocher une étape
arrête la chaîne à la précédente — transcrire seulement, ou s'arrêter après
la relecture, ou vérifier sans publier.

## Accès à Claude : CLI ou clé API

La relecture, le sommaire et la vérification par recherche web passent tous
par Claude. Deux façons d'y accéder, réglables dans **Réglages → Accès à
Claude** :

| Back-end | Facturation | Ce qu'il faut |
|---|---|---|
| **CLI `claude`** (par défaut) | Sur votre abonnement Claude — pas de compte API séparé | [Claude Code](https://claude.com/claude-code) installé, puis `claude setup-token` une fois sur cette machine |
| **Clé API Anthropic** | À l'usage, sur un compte API | Une clé `sk-ant-…`, dans les réglages |

Le CLI est le choix par défaut : c'est lui qui permet d'utiliser
l'abonnement plutôt que de payer un compte API séparé. Chaque appel est un
processus `claude -p` jetable, isolé de votre configuration personnelle
(pas de `CLAUDE.md`, pas de skills, pas de serveurs MCP) et sans la moindre
possibilité d'attendre une confirmation.

Sans back-end disponible, l'application bascule d'elle-même sur la relecture
simple, et saute la vérification par recherche web avec un message
explicite : **le texte n'est jamais perdu.**

## Les choix à faire

### Le moteur de transcription

| Moteur | Où ça calcule | Quand le choisir |
|---|---|---|
| **Ordinateur** | Votre processeur (ou votre GPU s'il y en a un) | Par défaut. Gratuit, hors ligne. |
| **RunPod** | Un GPU loué à la seconde | Cours longs en `large-v3`, quand le processeur devient trop lent |

Le moteur navigateur (WASM) du prototype a disparu : il était le plus lent des
trois et n'existait que pour éviter d'installer quoi que ce soit — ce que le
lanceur fait maintenant tout seul.

### Le modèle Whisper

`tiny` → `base` → `small` → `medium` → `large-v3`, du plus rapide au plus
précis. `large-v3` pour un vrai cours ; `tiny` pour vérifier en trente
secondes que le fichier est bien lu.

### La relecture

| Mode | Ce qu'il fait | Ce qu'il suppose |
|---|---|---|
| **Complète (Claude)** | Ponctuation, orthographe, suppression des hésitations, correction des erreurs de reconnaissance d'après le contexte, paragraphes, titre, résumé, intertitres | Un back-end Claude disponible (CLI ou clé) |
| **Simple** | Hésitations, bégaiements, ponctuation, majuscules, paragraphes — par règles, sans rien « comprendre » | Rien, fonctionne hors ligne |
| **Aucune** | Le texte de Whisper tel quel, juste regroupé en paragraphes | Rien |

Sans back-end disponible, l'application bascule d'elle-même sur la relecture
simple. Si un appel échoue en cours de route, elle fait de même : **le texte
n'est jamais perdu.**

### La vérification de fidélité

Une relecture réussie est invisible — c'est bien le problème. Rien ne
distingue, à la lecture, un texte fidèle d'un texte où une date a changé ou
une phrase a disparu. Chaque passage est donc comparé à sa version brute, et
ce qui cloche est listé dans le panneau **Vérification**, horodaté.

Deux niveaux, complémentaires :

- **Des règles**, gratuites, hors ligne, toujours actives — y compris en
  relecture simple. Elles voient ce qui est objectif : un nombre prononcé et
  absent du texte relu, un sigle disparu (gravité relevée s'il figure au
  [lexique MJPM](#le-lexique-mjpm)), un passage qui a perdu 40 % de sa
  longueur, une graphie proche d'un terme du lexique sans lui être
  identique. « 1 000 » relu en « 1000 » n'est pas une perte ; « vingt » relu
  en « 20 » non plus — seul le sens de la disparition compte.
- **Une lecture par Claude**, qui repère ce qu'aucune règle ne voit : un sens
  qui glisse, une nuance perdue, une phrase ajoutée. Elle ignore délibérément
  la ponctuation, les majuscules et le retrait des hésitations, qui sont
  précisément le travail attendu.

C'est une vérification de **fidélité** : elle dit si le texte relu rend
fidèlement ce qui a été dit, pas si ce qui a été dit est exact — un nom
propre mal reconnu à l'oral, puis « corrigé » par la relecture d'après le
contexte, y passera inaperçu puisque rien n'a été perdu. C'est le rôle de
l'étape suivante.

Les points relevés partent aussi dans le `.md`, le `.json` et la fiche
Obsidian. Décochable au dépôt : la vérification par Claude double
approximativement le temps de la relecture, puisqu'elle relit les deux
versions.

Une vérification automatique reste une aide, pas une garantie. Sur un passage
décisif, l'audio fait foi — les blocs horodatés et le lecteur synchronisé sont là
pour ça.

## La vérification externe (recherche web)

C'est l'étape 3, et le cœur de ce que cette application ajoute à une simple
transcription relue : une recherche web ciblée sur ce que la vérification de
fidélité ne peut pas voir — un nom propre, un titre de rapport, une
statistique, une référence juridique plausibles mais faux.

Deux passes :

1. **Repérage.** Un appel identifie, dans le texte relu, ce qui se prête à
   vérification — pas les notions générales du cours, seulement les faits
   précis.
2. **Vérification, une affirmation à la fois.** Un appel par affirmation, avec
   l'outil de recherche web activé. Le prompt est explicite : répondre sans
   avoir cherché est une faute, « je n'ai pas trouvé » est une réponse
   valable, et le modèle ne doit jamais deviner une graphie plausible.

**Le garde-fou est mécanique, pas seulement prompté.** Si la réponse ne porte
la trace d'aucune recherche web effective — aucun appel constaté, aucune
source citée — le programme requalifie le verdict de force en
« introuvable », quoi qu'ait écrit le modèle. Un verdict rendu de mémoire est
ainsi structurellement impossible.

**Une correction n'est appliquée que par substitution exacte, faite par le
programme**, et seulement si le verdict est « corrige », la confiance
« haute » et au moins une source citée. Dans tous les autres cas — infirmé,
introuvable, ambigu, confiance basse, quota d'abonnement atteint — **le texte
transcrit reste caractère pour caractère identique**, avec un appel de note
ajouté :

```markdown
Le rapport Dupont[^v1] est notre référence sur le sujet.

[^v1]: **« rapport Dupont »** — introuvable. Aucune source ne mentionne ce
    rapport sur le sujet (2 recherches). Confiance : basse.
```

C'est le principe directeur de toute cette étape : **le modèle propose, le
programme dispose.** L'incertitude résiduelle reste visible dans le texte,
plutôt que lissée en une version fluide mais faussement définitive — utile
pour n'importe quel usage, indispensable si le document doit servir de
référence citable.

Les points non confirmés partent aussi dans le panneau **Sources**, distinct
du panneau **Vérification** (fidélité). Un [lexique du domaine](#le-lexique-mjpm)
répond sans recherche pour les termes déjà vérifiés. Décochable au dépôt : le
coût n'est pas un critère de conception ici, mais un cours d'une heure fait
un appel par affirmation repérée, et l'abonnement Claude a ses propres
limites d'usage.

## Le lexique MJPM

Un glossaire du domaine (mandataires judiciaires à la protection des
majeurs), livré non vérifié — mesures de protection, acteurs, actes,
prestations, textes de référence, avec leurs sigles et variantes.
`app/lexicon/mjpm.json`.

Il n'est jamais opposé comme référence sur la foi de sa seule rédaction :
`python -m app.lexicon verify` passe chaque entrée non vérifiée par la même
recherche web que l'étape 3, et ne la marque vérifiée que si elle est
confirmée. Une entrée non vérifiée continue à amorcer la reconnaissance
vocale (le pire risque y est un mot de vocabulaire inutile), mais n'est
jamais recopiée dans une fiche d'entité du coffre tant qu'elle ne l'est pas.

Cinq points d'usage : amorce de vocabulaire pour Whisper (`initial_prompt`),
bloc de référence dans le prompt de relecture, gravité relevée dans la
vérification de fidélité, résolution sans recherche à l'étape 3, note
« Glossaire MJPM » tenue à jour dans le coffre. Un terme confirmé par
recherche web pendant un fact-check peut être ajouté au lexique de
l'utilisateur (`POST /api/lexicon`) — jamais au fichier livré avec le dépôt.

## Le coffre Obsidian

Étape 4 : une fiche écrite directement dans un coffre Obsidian existant,
chemin configuré dans **Réglages → Coffre Obsidian**. Vide, cette étape reste
inactive — tout le reste de l'application fonctionne normalement sans coffre
configuré.

Le bouton **Parcourir…**, à côté du champ, ouvre l'arborescence des dossiers
de la machine pour choisir le coffre sans avoir à taper son chemin à la
main. Le rangement à l'intérieur du coffre se choisit dans un menu déroulant
(« Formation / MJPM » par défaut, « à la racine du coffre », ou
« Personnalisé » pour choisir chaque sous-dossier) ; le nom des fiches, de
même (« Date — Titre » par défaut). C'est ce que voit `GET /api/browse` — en
lecture seule, il ne renvoie que des noms de dossiers, jamais un contenu de
fichier — mais lancé avec `--host 0.0.0.0`, l'arborescence des dossiers de la
machine devient visible à quiconque atteint le port sur le réseau.

La fiche porte un frontmatter YAML (lisible par Dataview et par les Bases
d'Obsidian : `statut_verification`, `points_incertains`, métadonnées du
travail, wikilinks vers les entités), un encart de tête
(`> [!warning]`/`> [!success]` selon qu'il reste des points incertains), et
les notes de bas de page posées par l'étape 3.

**Second brain.** Chaque entité confirmée (nom propre, organisme, référence)
devient un `[[wikilink]]` ; sa fiche est créée si elle n'existe pas, avec ce
que la recherche web en a appris — et **jamais modifiée si elle existe déjà**
: rien de ce que vous y avez écrit à la main ne peut être écrasé, les
backlinks d'Obsidian font le reste. Une note d'index (MOC) reçoit une ligne
par travail, dans une région balisée : republier met la ligne à jour plutôt
que de la dupliquer. Republier un travail déjà publié réécrit la même fiche
(son chemin est mémorisé), n'en crée pas une seconde.

Chaque publication conserve aussi une note **verbatim** distincte, avec les
tours de parole et leurs horodatages, reliée dans les deux sens à la fiche.
Avant de créer une entité, Verbatim lit un index en cache du coffre (titres,
alias, tags et type), sans jamais écrire dans les notes existantes : « Cdaph »
peut ainsi pointer vers une note `CDAPH` déjà présente. `GET /api/vault/index`
permet de consulter cet index et `POST /api/vault/reindex` de le reconstruire.

Le panneau **Mémoire** peut demander à Claude des propositions de concepts et
de thèmes transversaux. Elles restent à l'état `proposed` et sont affichées
pour contrôle : cette étape n'écrit encore aucune note dans le coffre.

Les dossiers par défaut (`Formation/Transcriptions`, `Formation/MJPM/…`)
sont des conjectures, tous modifiables dans les réglages — la première
publication dira si la convention tombe juste pour votre coffre.

## NotebookLM : un Doc maître Google

La synchronisation NotebookLM intervient **après une publication Obsidian
réussie**. La version éditoriale canonique est alors régénérée dans
`data/cours/`, puis tous les cours sont compilés dans **un seul** Google Doc,
dont le contenu est intégralement remplacé — jamais un nouveau document,
jamais un ajout au précédent. Ce Doc peut être ajouté une fois comme source
dans NotebookLM ; Google et NotebookLM en assurent ensuite la mise à jour.

Dans l'interface, l'état est explicite : non configuré, à synchroniser,
synchronisé ou en erreur. Le bouton **Synchroniser NotebookLM** permet de
relancer cette dernière étape sans republier le cours. Sans
`NOTEBOOKLM_SYNC_ENABLED`, elle reste inactive : tout le reste de
l'application fonctionne localement et la suite de tests ne fait aucun appel
réseau.

**Mise en place, une fois :**

1. Dans [Google Cloud Console](https://console.cloud.google.com/), créez un
   projet (ou réutilisez-en un), activez l'**API Google Drive**, puis créez
   des identifiants OAuth de type **Application de bureau**. Téléchargez le
   fichier JSON et déposez-le sous `data/google_credentials.json` (chemin
   par défaut, modifiable — voir plus bas).
2. Le scope demandé est le plus étroit possible,
   `https://www.googleapis.com/auth/drive.file` : l'application ne voit que
   les fichiers qu'elle a elle-même créés, jamais le reste de votre Drive.
3. Dans l'application : **Réglages → NotebookLM → Initialiser avec Google**.
   Le navigateur ouvre le consentement, puis l'application crée ou réutilise
   le Doc maître et active la synchronisation automatique. Le bouton
   **Tester la synchronisation** affiche immédiatement la cause exacte d'un
   éventuel problème de jeton, de connexion ou de configuration.
   En ligne de commande, l'équivalent est :

   ```bash
   python -m app.notebooklm_sync --init
   ```

   Ouvre le navigateur pour le consentement Google, écrit
   `data/google_token.json` (réutilisé et rafraîchi automatiquement
   ensuite), crée le Doc maître s'il n'existe pas encore, et affiche son
   `fileId` — enregistré directement dans `data/config.json`
   (`notebooklm_master_doc_id`), rien à recopier à la main.
4. Si des cours ont déjà été transcrits avant cette fonctionnalité, `data/cours/`
   est vide : `python -m app.notebooklm_sync --rebuild` le régénère depuis
   la base, sans appel réseau. `--dry-run` imprime la compilation sur la
   sortie standard pour la relire avant de l'envoyer.
5. Activez la synchronisation (`NOTEBOOKLM_SYNC_ENABLED=true`, ou le réglage
   équivalent dans `data/config.json`). Après chaque publication Obsidian
   réussie, le Doc est mis à jour ; le bouton de l'interface ou
   `python -m app.notebooklm_sync --sync` permettent aussi une relance.
6. Dans NotebookLM : **Ajouter une source → Google Drive**, choisissez le
   Doc — **une seule fois**. La resynchronisation se fait ensuite du côté de
   NotebookLM, à son propre rythme.

Le sommaire en tête du Doc pointe vers chaque section par des liens
`#ancre` : dans un vrai Google Doc, ce ne sont pas des signets cliquables —
la navigation s'y fait par le volet « Plan du document », alimenté par les
titres de section. Ces liens restent utiles si le Markdown est lu tel quel
(aperçu, `--dry-run`).

Un incident Google Drive (réseau, jeton expiré, bibliothèques absentes) est
journalisé et affiché comme une erreur de synchronisation. Il n'annule pas la
publication Obsidian déjà effectuée et peut être repris séparément.

## Réglages

Bouton **Réglages**, en haut à droite : accès à Claude (CLI ou clé), clé API
RunPod, vérification externe, lexique MJPM, coffre Obsidian. Les clés API
sont écrites dans `data/config.json`, sur votre disque, en permissions
restreintes. Elles ne sont jamais renvoyées à la page — l'interface sait
seulement si une clé est enregistrée ou non. `data/` est exclu du dépôt Git.
Le chemin du coffre Obsidian et les autres réglages n'ont rien de secret :
ils sont renvoyés tels quels.

Les clés et certains réglages peuvent aussi venir de l'environnement
(`ANTHROPIC_API_KEY`, `RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID`,
`CLAUDE_BACKEND`, `TRANSCRIPTION_OBSIDIAN_VAULT`,
`NOTEBOOKLM_SYNC_ENABLED`, `NOTEBOOKLM_DRIVE_FOLDER_ID`,
`NOTEBOOKLM_MASTER_DOC_ID`, `NOTEBOOKLM_CREDENTIALS_PATH`,
`NOTEBOOKLM_TOKEN_PATH`), qui a la priorité.

## Ce que produit l'application

| Format | Contenu | Dispo dès l'étape 1 |
|---|---|---|
| `.txt` | Texte de travail, relu ou brut selon l'avancement | oui |
| `.md` | Version éditoriale : titre, résumé, intertitres et points à vérifier | oui |
| `.json` | Données complètes : texte brut, blocs, annotations, vérifications et métadonnées | oui |
| Fiche Obsidian | Aperçu de la note à publier : frontmatter, encart et notes de bas de page | oui |
| Note Obsidian | Fiche, entités, MOC et glossaire écrits dans le coffre choisi | après publication |
| Doc maître Google | Compilation destinée à NotebookLM | après publication et synchronisation |

Le lecteur utilise la vidéo source lorsqu'elle est exploitable, sinon le WAV
extrait réellement envoyé au moteur. Si aucun média n'est disponible, l'état
est signalé au lieu de laisser un lecteur muet.

Les transcriptions restent dans une base SQLite locale (`data/transcription.db`)
et la barre de recherche fouille dans leur contenu. Déposer plusieurs fichiers
d'un coup les met en file : ils sont traités l'un après l'autre.

Chaque cours dispose aussi d'un `.md` dans `data/cours/`, régénéré depuis la
version éditoriale. Après publication, il devient la matière première du Doc
maître NotebookLM décrit plus haut.

## Ce qui a changé depuis le prototype HTML

Le prototype faisait tout dans le navigateur. C'est de là que venaient
[la plupart de ses bugs](#bugs-du-prototype-et-comment-ils-ont-disparu) :
l'extraction audio en JavaScript est un champ de mines. Le travail est
maintenant fait par ffmpeg, côté serveur.

| | Prototype HTML | Application |
|---|---|---|
| Extraction audio | Web Audio API, capture d'un `<video>`, parsing WAV maison | ffmpeg |
| Taille de fichier | Échec au-delà de ~180 Mo | Écriture en flux, sans limite pratique |
| Formats | Ce que le navigateur sait décoder | Tout ce que ffmpeg lit |
| Après la transcription | Texte brut de Whisper | Texte relu, structuré, résumé |
| Fidélité du résultat | À vérifier à la main | Vérifiée et signalée, horodatée |
| Relecture | — | Étape séparée, relançable sans refaire le calcul |
| Plusieurs fichiers | Un par un, à la main | File d'attente |
| Historique | Aucun | Base locale avec recherche |
| Sorties | `.txt` | `.txt`, `.md`, `.json`, aperçu et publication Obsidian |
| Cours d'une heure sur RunPod | Impossible (limite de 10 Mo par appel) | Découpage sur les silences |
| Clés API | `localStorage`, en clair | Fichier local en permissions restreintes |
| Tests | Scripts ponctuels | 325 tests automatisés |

### Bugs du prototype, et comment ils ont disparu

Les sept bugs de l'historique du projet, et leur sort :

1. **Audio muet à l'extraction** (`video.volume = 0` coupait le signal à la
   source dans Chrome) — l'extraction ne passe plus par le navigateur.
2. **WAV rejeté sur des chunks RIFF non standards** (« 1413894985 Hz ») — plus
   de parsing WAV maison à l'entrée.
3. **Fichiers de ~180 Mo en échec** (`decodeAudioData`) — l'envoi et
   l'extraction se font en flux, sur disque.
4. **Worker cross-origin refusé**, cassant le multi-threading WASM — le moteur
   navigateur n'existe plus.
5. **`LancerServeur.bat` se fermait sans rien afficher** (alias Microsoft Store
   de `python.exe`) — `lancer.bat` essaie `py` d'abord et ne retient un
   interpréteur que s'il répond vraiment à `--version`.
6. **0 segment sur un fichier audible** (`vad_filter=True`) — `vad_filter=False`
   partout, avec un commentaire à côté pour que personne ne le « corrige ».
   Et si un fichier ne rend malgré tout aucun segment, le travail échoue avec
   un message explicite au lieu de rendre une page blanche.
7. **`runpod.serverless.start()` non détecté** — l'appel reste inconditionnel,
   au niveau module, dans `handler.py`.

## RunPod : transcription pure, et rien d'autre

Le worker RunPod reçoit de l'audio et rend du texte avec ses horodatages. Il
ne relit pas, ne structure pas, ne vérifie pas, n'appelle aucun autre service.
C'est du calcul GPU, facturé à la seconde : tout ce qui peut se faire ailleurs
doit se faire ailleurs.

Le découpage de l'audio, lui, se fait **côté application, avant l'envoi** —
c'est une contrainte de transport, pas un traitement. L'API `/run` de RunPod
plafonne la charge utile à environ 10 Mo, et un WAV 16 kHz mono 16 bits encodé
en base64 pèse ~42 ko par seconde : **un seul appel ne peut donc porter que
quatre minutes d'audio.** Le prototype envoyait le fichier entier — au-delà de
quelques minutes, il ne pouvait pas fonctionner.

Le découpage n'est pas fait à intervalle fixe, ce qui couperait au milieu d'un
mot : l'application mesure l'énergie du signal, cherche le passage le plus
calme autour de la durée cible — une respiration entre deux phrases — et coupe
là. Chaque tronçon part comme un appel indépendant, et les horodatages sont
recalés sur le fichier d'origine à l'arrivée.

### Déployer le worker GPU

Le dépôt contient aussi le worker RunPod Serverless (`handler.py`,
`Dockerfile`, `requirements.txt`), inchangé.

1. RunPod → **Serverless** → **New Endpoint** → source **GitHub Repo**, ce
   dépôt, branche `main`, Dockerfile `/Dockerfile`.
2. GPU : **L4** — meilleur rapport prix/vitesse mesuré sur ce calcul
   (34-41× le temps réel en `large-v3`, à 0,69 $/h en serverless).
3. Workers : min 0 (aucun coût entre deux usages), max selon vos besoins.
4. Reportez l'**Endpoint ID** et une **clé API** dans les réglages de l'app.

> **« Could not find runpod.serverless.start() in your repo »**
>
> Cet avertissement est un faux négatif sur un dépôt **privé**, et il
> n'empêche pas le déploiement : cliquez sur **Next**.
>
> RunPod fait deux choses différentes. Il retrouve le `Dockerfile` par son
> chemin — d'où le « ✓ Dockerfile found » — mais il cherche
> `runpod.serverless.start()` avec l'API de *recherche de code* de GitHub, qui
> n'indexe pas les dépôts privés. La recherche ne renvoie donc rien, quoi que
> contienne le fichier. Vérifiable : une recherche de code sur ce dépôt
> renvoie `total_count: 0` et `incomplete_results: true` pour n'importe quel
> terme, y compris ceux qui y sont manifestement.
>
> La construction de l'image, elle, ne passe pas par cet index : elle copie le
> dépôt tel quel, donc `handler.py` est bien là et le worker démarre. Rendre
> le dépôt public ferait disparaître le message — c'est le seul effet.
>
> `tests/test_handler.py` vérifie de son côté que l'appel est bien au niveau
> module, non indenté et sans garde `__main__` : si le message venait un jour
> d'une vraie régression, ces tests échoueraient.

<details>
<summary>Contrat de l'API du worker</summary>

Entrée (`job["input"]`) :

```json
{"audio_base64": "<WAV en base64>", "model": "large-v3", "language": "fr",
 "initial_prompt": "amorce de vocabulaire, facultative"}
```

`initial_prompt` est optionnel : un worker déployé avant son ajout l'ignore
sans casser (voir [le lexique MJPM](#le-lexique-mjpm)).

Sortie :

```json
{"text": "…", "segments": [{"start": 0.0, "end": 2.5, "text": "…"}], "language": "fr"}
```

En cas d'erreur : `{"error": "message"}`.
</details>

### Pod : serverless, bascule, ou direct

Le serverless descend à zéro worker entre deux usages — rien ne tourne, rien
n'est facturé — mais l'inverse est aussi vrai : s'il n'y a **aucune**
capacité disponible (GPU rare, quota atteint), un job peut rester coincé « en
file » indéfiniment, sans jamais être pris en charge. Un **pod** RunPod
couvre ce cas : une machine GPU louée **à la minute** (pas à la requête),
créée seulement quand on en a besoin et détruite — pas seulement arrêtée, ce
qui laisserait le disque facturé.

Un seul travail est traité à la fois (un seul thread dépile la file), donc
quand plusieurs fichiers s'enchaînent, le pod créé pour le premier reste
disponible pour les suivants au lieu d'être détruit puis recréé à chaque
fois — ce qui rechargerait l'image et le modèle Whisper à chaque fichier
pour rien. Il n'est détruit que si plus aucun travail n'en a eu besoin
pendant `runpod_pod_idle_timeout_seconds` (90 secondes par défaut, réglable)
— jamais laissé vivre indéfiniment. Après le tout dernier fichier, ce délai
reste du temps GPU facturé pour rien : c'est le prix à payer pour couvrir
l'écart entre deux dépôts manuels rapprochés sans savoir à l'avance lequel
sera le dernier.

Réglages → **Pod RunPod** → **Démarrage** propose trois choix :

| Démarrage | Ce qui se passe | Quand le choisir |
|---|---|---|
| **Serverless uniquement** (défaut) | Uniquement le serverless. Si aucun worker ne le prend en charge, la transcription échoue avec un message explicite. | Pas de pod configuré, ou vous préférez échouer plutôt que payer un pod. |
| **Serverless, avec bascule sur pod** | Le serverless d'abord ; si aucun worker n'a pris en charge le premier tronçon après un court délai, bascule automatiquement sur un pod. | Le cas courant : un secours qui ne coûte rien tant que le serverless répond. |
| **Pod dès le premier tronçon** | Le pod est créé directement, sans jamais essayer le serverless. | Vous savez déjà que le serverless n'a pas de capacité (le tester à chaque fois ferait perdre le délai de bascule pour rien) — ou vous voulez tout simplement toujours passer par un pod. |

Avec la bascule, elle n'a lieu qu'au tout premier tronçon : si le serverless
démarre normalement, tout le reste s'y déroule comme d'habitude, même si un
tronçon suivant est ensuite anormalement lent — ce cas reste couvert par le
délai d'attente habituel, pas par le pod. En démarrage direct, le pod est
créé une seule fois, avant le premier tronçon, et sert à tous les tronçons
suivants.

**Ce que ça change, concrètement :**

- Réglages → **Pod RunPod**. Démarrage à « Serverless uniquement » par
  défaut : le pod n'est ni créé ni facturé tant que ce réglage ne le prévoit
  pas *et* qu'une image n'est pas configurée.
- En démarrage direct, l'identifiant de endpoint serverless n'est plus
  nécessaire : seules la clé API RunPod et l'image du pod comptent.
- Contrairement au serverless, RunPod ne construit pas cette image tout seul
  depuis ce dépôt pour un pod — mais ce dépôt le fait à votre place :
  `.github/workflows/pod-image.yml` reconstruit l'image et la pousse vers
  **GitHub Container Registry** à chaque modification de `Dockerfile`,
  `handler.py`, `pod_server.py` ou `requirements.txt` poussée sur `main`.
  Aucune commande à taper : l'image reste à jour toute seule.
  - Résultat : `ghcr.io/niveys-5169/transcription-pod:latest` — c'est cette
    référence qu'il faut coller dans les réglages, une seule fois.
  - **Une étape manuelle, une seule fois** : après le premier passage du
    workflow, GitHub crée le paquet en **privé** par défaut — RunPod ne peut
    pas le télécharger tel quel. Sur GitHub : onglet **Packages** du dépôt →
    `transcription-pod` → **Package settings** → **Change visibility** →
    **Public**.
  - Si le workflow échoue avec une erreur de permission (403 sur le push),
    c'est que les Actions du dépôt sont en lecture seule par défaut :
    **Settings → Actions → General → Workflow permissions** → **Read and
    write permissions**.
  - `docker build`/`docker push` à la main restent utiles pour tester une
    modification avant de la pousser, mais ne sont plus nécessaires pour la
    mise en production de l'image.
- GPU par défaut : **L4**, comme pour le serverless. Changez-le si ce type
  n'est pas disponible dans votre région.
- `handler.py` (le worker serverless, invoqué par job) et `pod_server.py`
  (le pod, un petit serveur HTTP écoutant `/health` et `/transcribe`)
  partagent la même image ; seule la commande de démarrage diffère, et c'est
  l'application qui la choisit à la création du pod.

### Pod : volume réseau (recommandé)

L'image du pod ne contient plus le modèle Whisper (il l'a longtemps fait,
mais ça la rendait lourde de plusieurs Go — RunPod devait alors la tirer en
entier à chaque pod créé sur un hôte qui ne l'avait pas déjà en cache local,
plus de 10 minutes dans certains cas). À la place, `handler.py` et
`pod_server.py` mettent le cache Hugging Face sur un **volume réseau**
RunPod quand un est monté (`/runpod-volume`), pour ne payer le
téléchargement qu'une seule fois, sans jamais alourdir l'image elle-même.

Mise en place :

1. RunPod → **Storage** → **Network Volumes** → créez-en un (quelques Go
   suffisent pour `large-v3`), dans le datacenter de votre choix.
2. Réglages → **Pod RunPod** → **Volume réseau** : collez son identifiant.
   L'application le transmet à `podFindAndDeployOnDemand` (`networkVolumeId`
   + `volumeMountPath`), monté au même chemin que RunPod utilise pour le
   serverless (`/runpod-volume`).
3. Pour le même bénéfice côté serverless (évite de retélécharger le modèle
   à chaque cold start) : RunPod → **Serverless** → votre endpoint →
   **Manage** → **Edit Endpoint** → **Advanced** → **Network Volumes** →
   attachez le même volume.

**Contrepartie à connaître** : un volume réseau est épinglé à un
datacenter précis, donc un pod qui en utilise un ne peut plus être créé
que dans ce datacenter — au lieu de « n'importe lequel » aujourd'hui. Ça
réduit la disponibilité GPU du pod de secours, précisément le mécanisme qui
existe pour pallier un manque de capacité serverless. Sans volume configuré
(réglage laissé vide), le pod garde son comportement actuel : image légère,
mais le modèle est retéléchargé depuis Hugging Face à chaque pod créé.

### Jeton Hugging Face (optionnel, évite le rate-limit)

Sans jeton, chaque téléchargement d'un modèle Whisper (`Systran/faster-whisper-*`
sur le Hub) part en anonyme — huggingface_hub le rappelle par un avertissement
(« Please set a HF_TOKEN to enable higher rate limits ») et se heurte plus
vite au rate-limit du Hub, en particulier quand un cold start ou un pod
fraîchement créé n'a rien en cache local.

- **Application locale et pod de secours** : rien à faire dans le code.
  `HF_TOKEN`, s'il est présent dans l'environnement de l'application au
  moment où elle tourne (ou crée un pod), est repris automatiquement —
  huggingface_hub le lit tout seul localement, et `app/engines/runpod_pod.py`
  le transmet explicitement au pod créé (les pods RunPod n'héritent pas de
  l'environnement de la machine qui les crée).
- **Étape manuelle, une seule fois — worker Serverless** : `handler.py`
  tourne dans un conteneur RunPod totalement séparé, qui n'a par construction
  aucun moyen de recevoir cette variable depuis votre machine. RunPod →
  **Serverless** → votre endpoint → **Manage** → **Edit Endpoint** →
  **Environment Variables** → ajoutez `HF_TOKEN` avec un jeton
  [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
  (lecture seule suffit).

<details>
<summary>Pourquoi l'API RunPod des pods n'a pas pu être vérifiée en direct</summary>

`app/engines/runpod_pod.py` s'appuie sur l'API GraphQL RunPod des pods
(`podFindAndDeployOnDemand`, `podTerminate`) et sur la convention d'URL de
son proxy HTTP (`https://{pod_id}-{port}.proxy.runpod.net`). L'environnement
où ce code a été écrit n'a pas d'accès réseau sortant vers `runpod.io` /
`runpod.ai`, donc ces appels n'ont pu être vérifiés que par des tests avec
double du réseau (`tests/test_runpod_pod_engine.py`), pas par un vrai essai
contre l'API. Si RunPod a fait évoluer les noms de champs depuis, un pod
échouera avec un message d'erreur explicite (jamais silencieusement) —
comparez alors avec la documentation RunPod à jour.
</details>

## Comment la relecture évite de perdre du contenu

Faire relire un cours d'une heure par un modèle de langue, c'est prendre deux
risques : qu'il résume au lieu de relire, et qu'il réécrive en croyant bien
faire. Trois garde-fous :

- **Le texte est découpé en blocs** d'environ 6 000 caractères, sur des
  frontières de segments et de préférence en fin de phrase. La fin du bloc
  précédent est fournie comme contexte, pour que les phrases à cheval restent
  cohérentes — mais elle n'est pas réécrite.
- **Un bloc dont la relecture revient nettement plus courte que l'entrée est
  rejeté** : le modèle a résumé. C'est la version mécanique qui est gardée,
  elle ne perd rien.
- **Les intertitres ne passent pas par une réécriture.** Le modèle indique
  seulement *où* commence chaque section, en citant ses premiers mots ; les
  titres sont insérés par le programme. Une citation introuvable est ignorée.
  Le texte relu n'est jamais modifié à cette étape, seulement complété.

Modèle par défaut : `claude-sonnet-5`, effort `high`, modifiable dans les
réglages — un seul modèle pour tous les appels (relecture, sommaire,
vérification de fidélité, extraction des affirmations, fact-check).

## Développement

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Les tests n'ont besoin ni de faster-whisper, ni d'anthropic, ni d'une clé API,
ni de ffmpeg, ni du CLI `claude` : les moteurs lourds et les back-ends Claude
sont remplacés par des doublures et les fichiers audio sont synthétisés (le
back-end est forcé sur « api », sans clé, pour toute la suite — voir
`tests/conftest.py` — pour ne jamais invoquer le vrai CLI même s'il est
installé et connecté sur la machine qui fait tourner les tests). La suite
tourne en quelques secondes.

| Fichier | Rôle |
|---|---|
| `run.py` | Point d'entrée |
| `app/server.py` | API HTTP et page unique |
| `app/pipeline.py` | Transcription, relecture, vérifications, publication et synchronisation |
| `app/media.py` | ffmpeg, découpage sur les silences |
| `app/engines/` | Moteurs de transcription (local, RunPod serverless, RunPod pod) |
| `app/proofread/backends/` | Accès à Claude : CLI (abonnement) ou API (clé) |
| `app/proofread/` | Relecture : par Claude, ou par règles |
| `app/proofread/verify.py` | Vérification de fidélité : règles, puis lecture par Claude |
| `app/proofread/factcheck.py` | Vérification externe : recherche web, affirmation par affirmation |
| `app/lexicon/` | Lexique MJPM : amorçage Whisper, résolution sans recherche, glossaire |
| `app/obsidian/` | Publication : fiche, verbatim, entités, MOC, glossaire et index du coffre |
| `app/notebooklm_sync.py` | Compilation du Doc maître Google après publication, pour NotebookLM |
| `app/exporters.py` | txt, md, json, fiche Obsidian et version éditoriale canonique |
| `app/db.py` | Historique SQLite |
| `app/static/` | Interface Verbatim : shell sombre, éditeur, timeline et panneaux de révision |
| `app/desktop.py` | Démarrage Windows : serveur local, navigateur et icône de barre système |
| `app/updates.py` | Vérification, téléchargement et remplacement différé d'une version Windows publiée |
| `app/build_info.py` | Identifiant de build injecté par GitHub Actions dans l'exécutable |
| `.github/workflows/windows-build.yml` | Build Windows, artefact téléchargeable et release `latest` pour l'auto-update |
| `handler.py`, `Dockerfile` | Worker RunPod Serverless |
| `pod_server.py` | Même calcul que `handler.py`, exposé en HTTP pour le pod RunPod |

Ajouter un moteur : implémenter le protocole de `app/engines/base.py`
(`is_available`, `transcribe` en générateur de `Segment`) et l'inscrire dans
`app/engines/__init__.py`. Un moteur ne voit que de l'audio et ne rend que des
segments — la relecture n'est pas son affaire.

Les principaux points d'entrée de l'API :

```
POST /api/jobs                              dépose un fichier et lance la transcription
GET  /api/jobs, /api/jobs/{id}, /api/search  historique, détail et recherche globale
GET  /api/jobs/{id}/media                    média source, sans exposer de chemin local
GET/PUT /api/jobs/{id}/review-blocks         lecture et édition des blocs horodatés
GET/POST/PATCH/DELETE /api/jobs/{id}/annotations  annotations persistées
POST /api/jobs/{id}/proofread                 relecture et vérification de fidélité
POST /api/jobs/{id}/factcheck                 vérification externe
POST /api/jobs/{id}/publish                   publication Obsidian
POST /api/jobs/{id}/knowledge                  propositions de concepts et thèmes, sans écriture
POST /api/jobs/{id}/notebooklm-sync           synchronisation Google Docs / NotebookLM
POST /api/jobs/{id}/cancel, /retry            interruption ou reprise
GET  /api/jobs/{id}/download/{fmt}            export txt, md, json ou aperçu Obsidian
GET/POST /api/lexicon                         consultation et ajout au lexique
GET /api/vault/index, POST /api/vault/reindex  consultation et reconstruction de l'index Obsidian
GET/POST /api/update                          vérification puis application d'une mise à jour Windows
```

## Ce qui n'est pas fait

- **Traitement par lot côté GPU.** Les tronçons partent chez RunPod l'un après
  l'autre. Les envoyer par paquets réduirait encore la facture.
- **Repérage des locuteurs.** Un seul orateur est supposé, ce qui convient à un
  cours magistral mais pas à une table ronde.
- **Acceptation des propositions de lexique dans l'interface.** Un terme
  confirmé par recherche web pendant un fact-check peut déjà être ajouté au
  lexique via `POST /api/lexicon` ; la page ne propose pas encore de bouton
  dédié pour ça — à faire à la main, pour l'instant.
- **Enveloppe exacte du CLI en mode `--output-format stream-json`.** Le
  back-end CLI (`app/proofread/backends/cli.py`) l'analyse de façon tolérante
  (jamais d'échec silencieux, un format inattendu donne un message d'erreur
  clair) ; un ajustement pourrait s'avérer nécessaire selon la version du
  CLI installée.
