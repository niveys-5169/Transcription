# Transcription de cours

Application locale qui transforme l'enregistrement d'un cours — vidéo ou
audio — en **texte relu et lisible**.

On dépose le fichier, on attend, on récupère un document ponctué, débarrassé
des hésitations, découpé en paragraphes, avec un titre, un résumé et des
intertitres. Les sous-titres horodatés sont produits au passage.

Tout tourne sur votre machine. Rien n'est envoyé sur Internet, sauf si vous
activez explicitement le GPU RunPod ou la relecture par Claude.

```
  ÉTAPE 1 — TRANSCRIPTION                    │  ÉTAPE 2 — RELECTURE
  un calcul : rendre ce qui a été dit        │  une lecture : rendre ça lisible
                                             │
  ┌────────────┐  ffmpeg   ┌────────────┐    │   ┌──────────────┐   ┌──────────┐
  │ vidéo/audio│ ────────► │ WAV 16 kHz │    │   │  relecture   │──►│ vérifi-  │
  │  n'importe │ extraction│ mono 16 b. │    │   │              │   │ cation   │
  │ quel format│           └─────┬──────┘    │   └──────────────┘   └────┬─────┘
  └────────────┘                 │ Whisper   │          ▲                │
                                 ▼           │          │                ▼
                        ┌─────────────────┐  │  ┌───────┴────────┐  ┌─────────┐
                        │ texte brut +    │──┼─►│ texte ponctué, │  │ points  │
                        │ segments datés  │  │  │ structuré,     │  │ à véri- │
                        └─────────────────┘  │  │ résumé         │  │ fier    │
                        .txt .srt .vtt .json │  └────────────────┘  └─────────┘
                        déjà téléchargeables │        .md, .json enrichis
```

**Les deux étapes sont indépendantes.** La transcription rend le texte brut et
s'arrête là ; la relecture part de ce texte, plus tard si vous voulez, et peut
être relancée autant de fois que nécessaire sans jamais refaire tourner le
moteur de transcription.

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
`--no-browser`, `--reload` (développement).
</details>

## Pourquoi deux étapes séparées

Transcrire et relire sont deux métiers différents, et les mélanger coûte cher.

**Transcrire est un calcul.** On donne de l'audio, on récupère les mots
prononcés et leurs horodatages. Whisper fait ça, que ce soit sur votre
processeur ou sur un GPU loué. Le résultat est vérifiable, reproductible, et
c'est tout ce qu'on lui demande — d'où le mot « pure » : le worker RunPod ne
fait rien d'autre, ne reformule rien, ne corrige rien.

**Relire est une lecture.** Il faut comprendre le propos pour savoir qu'« a
priori » n'était pas « appris ou rit », rétablir la ponctuation, décider où
commence une nouvelle idée. C'est du travail sur du texte, et il n'a aucune
raison de se produire au même moment que le calcul.

Les séparer donne trois choses :

- **Le texte brut arrive tout de suite** et ne dépend de rien d'autre. Pas de
  clé API, pas de réseau, pas d'attente supplémentaire. Sous-titres et
  segments horodatés sont téléchargeables dès la fin de l'étape 1.
- **La relecture se rejoue.** Pas satisfait du découpage ? Envie d'essayer
  sans les intertitres, ou avec un effort plus élevé ? On relance l'étape 2
  seule : quelques secondes d'appels API, au lieu de plusieurs dizaines de
  minutes de GPU.
- **Un échec de relecture ne détruit rien.** Clé expirée, quota atteint,
  panne réseau : le travail retombe à l'état « transcrit », le texte brut est
  toujours là, et le bouton **Relire** attend.

Concrètement : décochez **« Relire dans la foulée »** au dépôt pour ne faire
que transcrire, puis lancez la relecture quand ça vous arrange — le soir, en
lot, ou jamais.

## Les trois choix à faire

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

| Mode | Ce qu'il fait | Ce qu'il coûte |
|---|---|---|
| **Complète (Claude)** | Ponctuation, orthographe, suppression des hésitations, correction des erreurs de reconnaissance d'après le contexte, paragraphes, titre, résumé, intertitres | Une clé API Anthropic |
| **Simple** | Hésitations, bégaiements, ponctuation, majuscules, paragraphes — par règles, sans rien « comprendre » | Rien, fonctionne hors ligne |
| **Aucune** | Le texte de Whisper tel quel, juste regroupé en paragraphes | Rien |

Sans clé Anthropic, l'application bascule d'elle-même sur la relecture simple.
Si un appel échoue en cours de route, elle fait de même : **le texte n'est
jamais perdu.**

### La vérification

Une relecture réussie est invisible — c'est bien le problème. Rien ne
distingue, à la lecture, un texte fidèle d'un texte où une date a changé ou
une phrase a disparu. Chaque passage est donc comparé à sa version brute, et
ce qui cloche est listé dans l'onglet **Vérification**, horodaté.

Deux niveaux, complémentaires :

- **Des règles**, gratuites, hors ligne, toujours actives — y compris en
  relecture simple. Elles voient ce qui est objectif : un nombre prononcé et
  absent du texte relu, un sigle disparu, un passage qui a perdu 40 % de sa
  longueur. « 1 000 » relu en « 1000 » n'est pas une perte ; « vingt » relu en
  « 20 » non plus — seul le sens de la disparition compte.
- **Une lecture par Claude**, qui repère ce qu'aucune règle ne voit : un sens
  qui glisse, une nuance perdue, une phrase ajoutée. Elle ignore délibérément
  la ponctuation, les majuscules et le retrait des hésitations, qui sont
  précisément le travail attendu.

Les points relevés partent aussi dans le `.md` et le `.json`. Décochable au
dépôt : la vérification par Claude double approximativement le coût de la
relecture, puisqu'elle relit les deux versions.

Une vérification automatique reste une aide, pas une garantie. Sur un passage
décisif, l'audio fait foi — l'onglet segments et le lecteur intégré sont là
pour ça.

## Réglages

Bouton **Réglages**, en haut à droite. Les clés API sont écrites dans
`data/config.json`, sur votre disque, en permissions restreintes. Elles ne
sont jamais renvoyées à la page — l'interface sait seulement si une clé est
enregistrée ou non. `data/` est exclu du dépôt Git.

Les clés peuvent aussi venir de l'environnement (`ANTHROPIC_API_KEY`,
`RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID`), qui a la priorité.

## Ce que produit l'application

| Format | Contenu | Dispo dès l'étape 1 |
|---|---|---|
| `.txt` | Le texte relu — ou le texte brut s'il n'a pas encore été relu | oui |
| `.srt` / `.vtt` | Sous-titres horodatés | oui |
| `.json` | Tout : texte relu, texte brut, segments, vérification, métadonnées | oui |
| `.md` | Document complet : titre, résumé, intertitres, points à vérifier | oui |

L'onglet **Audio extrait** rejoue le WAV réellement envoyé au moteur. S'il est
muet, le problème vient de l'extraction et non de la transcription — c'est la
première chose à vérifier quand un résultat est vide.

Les transcriptions restent dans une base SQLite locale (`data/transcription.db`)
et la barre de recherche fouille dans leur contenu. Déposer plusieurs fichiers
d'un coup les met en file : ils sont traités l'un après l'autre.

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
| Sorties | `.txt` | `.txt` `.md` `.srt` `.vtt` `.json` |
| Cours d'une heure sur RunPod | Impossible (limite de 10 Mo par appel) | Découpage sur les silences |
| Clés API | `localStorage`, en clair | Fichier local en permissions restreintes |
| Tests | Scripts ponctuels | 107 tests automatisés |

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
{"audio_base64": "<WAV en base64>", "model": "large-v3", "language": "fr"}
```

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
pendant `runpod_pod_idle_timeout_seconds` (5 minutes par défaut, réglable) —
jamais laissé vivre indéfiniment.

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

Modèle par défaut : `claude-opus-5`, effort `medium`, modifiable dans les
réglages.

## Développement

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Les tests n'ont besoin ni de faster-whisper, ni d'anthropic, ni d'une clé API,
ni de ffmpeg : les moteurs lourds sont remplacés par des doublures et les
fichiers audio sont synthétisés. La suite tourne en quelques secondes.

| Fichier | Rôle |
|---|---|
| `run.py` | Point d'entrée |
| `app/server.py` | API HTTP et page unique |
| `app/pipeline.py` | Les deux étapes : transcription, puis relecture |
| `app/media.py` | ffmpeg, découpage sur les silences |
| `app/engines/` | Moteurs de transcription (local, RunPod serverless, RunPod pod) |
| `app/proofread/` | Relecture : par Claude, ou par règles |
| `app/proofread/verify.py` | Vérification : règles, puis lecture par Claude |
| `app/exporters.py` | txt, md, srt, vtt, json |
| `app/db.py` | Historique SQLite |
| `app/static/` | Interface |
| `handler.py`, `Dockerfile` | Worker RunPod Serverless |
| `pod_server.py` | Même calcul que `handler.py`, exposé en HTTP pour le pod RunPod |

Ajouter un moteur : implémenter le protocole de `app/engines/base.py`
(`is_available`, `transcribe` en générateur de `Segment`) et l'inscrire dans
`app/engines/__init__.py`. Un moteur ne voit que de l'audio et ne rend que des
segments — la relecture n'est pas son affaire.

Les deux étapes exposées par l'API :

```
POST /api/jobs                    dépose un fichier et lance l'étape 1
POST /api/jobs/{id}/proofread     lance ou relance l'étape 2, seule
POST /api/jobs/{id}/retry         relance l'étape 1 depuis le fichier d'origine
```

## Ce qui n'est pas fait

- **Traitement par lot côté GPU.** Les tronçons partent chez RunPod l'un après
  l'autre. Les envoyer par paquets réduirait encore la facture.
- **Exécutable autonome.** Il faut Python sur la machine ; le lanceur s'occupe
  du reste. Un empaquetage PyInstaller ou Tauri reste à faire.
- **Repérage des locuteurs.** Un seul orateur est supposé, ce qui convient à un
  cours magistral mais pas à une table ronde.
