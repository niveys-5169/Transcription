# Transcription de cours

Application locale qui transforme l'enregistrement d'un cours — vidéo ou
audio — en **texte relu et lisible**.

On dépose le fichier, on attend, on récupère un document ponctué, débarrassé
des hésitations, découpé en paragraphes, avec un titre, un résumé et des
intertitres. Les sous-titres horodatés sont produits au passage.

Tout tourne sur votre machine. Rien n'est envoyé sur Internet, sauf si vous
activez explicitement le GPU RunPod ou la relecture par Claude.

```
   fichier déposé
        │
        ▼
   ┌────────────┐   ffmpeg          ┌──────────────┐   Whisper        ┌───────────┐
   │ vidéo/audio│ ───────────────►  │ WAV 16 kHz   │ ───────────────► │  texte    │
   │  n'importe │   extraction      │ mono 16 bits │  transcription   │   brut    │
   │ quel format│                   └──────────────┘                  └─────┬─────┘
   └────────────┘                                                           │
                                                                relecture   ▼
                            ┌────────────────────────────────────────────────────┐
                            │  texte ponctué, sans hésitations, en paragraphes,   │
                            │  avec titre, résumé et intertitres                  │
                            │  → .txt  .md  .srt  .vtt  .json                     │
                            └────────────────────────────────────────────────────┘
```

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

## Réglages

Bouton **Réglages**, en haut à droite. Les clés API sont écrites dans
`data/config.json`, sur votre disque, en permissions restreintes. Elles ne
sont jamais renvoyées à la page — l'interface sait seulement si une clé est
enregistrée ou non. `data/` est exclu du dépôt Git.

Les clés peuvent aussi venir de l'environnement (`ANTHROPIC_API_KEY`,
`RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID`), qui a la priorité.

## Ce que produit l'application

| Format | Contenu |
|---|---|
| `.txt` | Le texte relu, seul |
| `.md` | Document complet : titre, résumé, intertitres, texte |
| `.srt` / `.vtt` | Sous-titres horodatés |
| `.json` | Tout : texte relu, texte brut, segments horodatés, métadonnées |

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
| Plusieurs fichiers | Un par un, à la main | File d'attente |
| Historique | Aucun | Base locale avec recherche |
| Sorties | `.txt` | `.txt` `.md` `.srt` `.vtt` `.json` |
| Cours d'une heure sur RunPod | Impossible (limite de 10 Mo par appel) | Découpage sur les silences |
| Clés API | `localStorage`, en clair | Fichier local en permissions restreintes |
| Tests | Scripts ponctuels | 74 tests automatisés |

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

## RunPod : le découpage, et pourquoi il fallait le faire

L'API `/run` de RunPod plafonne la charge utile à environ 10 Mo. Un WAV
16 kHz mono 16 bits encodé en base64 pèse ~42 ko par seconde : **un seul appel
ne peut donc porter que quatre minutes d'audio.** Le prototype envoyait le
fichier entier — au-delà de quelques minutes, il ne pouvait pas fonctionner.

L'application découpe donc le WAV avant l'envoi. Pas à intervalle fixe, ce qui
couperait au milieu d'un mot : elle mesure l'énergie du signal, cherche le
passage le plus calme autour de la durée cible — une respiration entre deux
phrases — et coupe là. Les horodatages de chaque tronçon sont ensuite recalés
sur le fichier d'origine.

### Déployer le worker GPU

Le dépôt contient aussi le worker RunPod Serverless (`handler.py`,
`Dockerfile`, `requirements.txt`), inchangé.

1. RunPod → **Serverless** → **New Endpoint** → source **GitHub Repo**, ce
   dépôt, branche `main`, Dockerfile `/Dockerfile`.
2. GPU : **L4** — meilleur rapport prix/vitesse mesuré sur ce calcul
   (34-41× le temps réel en `large-v3`, à 0,69 $/h en serverless).
3. Workers : min 0 (aucun coût entre deux usages), max selon vos besoins.
4. Reportez l'**Endpoint ID** et une **clé API** dans les réglages de l'app.

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
| `app/pipeline.py` | Enchaînement extraction → transcription → relecture |
| `app/media.py` | ffmpeg, découpage sur les silences |
| `app/engines/` | Moteurs de transcription (local, RunPod) |
| `app/proofread/` | Relecture : par Claude, ou par règles |
| `app/exporters.py` | txt, md, srt, vtt, json |
| `app/db.py` | Historique SQLite |
| `app/static/` | Interface |
| `handler.py`, `Dockerfile` | Worker RunPod Serverless |

Ajouter un moteur : implémenter le protocole de `app/engines/base.py`
(`is_available`, `transcribe` en générateur de `Segment`) et l'inscrire dans
`app/engines/__init__.py`.

## Ce qui n'est pas fait

- **Traitement par lot côté GPU.** Les tronçons partent chez RunPod l'un après
  l'autre. Les envoyer par paquets réduirait encore la facture.
- **Exécutable autonome.** Il faut Python sur la machine ; le lanceur s'occupe
  du reste. Un empaquetage PyInstaller ou Tauri reste à faire.
- **Repérage des locuteurs.** Un seul orateur est supposé, ce qui convient à un
  cours magistral mais pas à une table ronde.
