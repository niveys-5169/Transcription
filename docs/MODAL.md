# Architecture et guide opérationnel : Moteur Modal (GPU Serverless)

Ce document décrit l'intégration de **Modal.com** comme moteur de transcription GPU serverless dans Verbatim.

---

## 1. Pourquoi Modal ?

Historiquement, l'application proposait deux moteurs :
1. **Local (faster-whisper) :** 100 % gratuit et privé, mais lent sur un processeur sans carte graphique NVIDIA.
2. **RunPod (Pod dédié) :** machine GPU puissante louée à l'heure, nécessitant de construire et pousser une image Docker manuellement, et avec un risque de facturation continue si le pod n'est pas détruit.

**Modal apporte le meilleur des deux mondes :**
- **30 $/mois offerts :** crédit de calcul gratuit renouvelé chaque mois (soit environ 40 à 50 heures de transcription GPU par mois).
- **Scale-to-zero automatique :** le conteneur GPU s'éteint automatiquement après **90 secondes d'inactivité** (`scaledown_window=90`). Quand vous ne transcrivez pas, vous payez **0,00 $**.
- **Zéro build Docker manuel :** Modal construit l'image directement dans le cloud depuis [modal_server.py](file:///c:/Users/Famille/Documents/GitHub/Transcription/modal_server.py) en 3 minutes via `modal deploy modal_server.py`.
- **Pile GPU de pointe :** CUDA 12.8, PyTorch 2.5.1, WhisperX 3.3.2 (`large-v3`), alignement mot à mot précis et diarisation Pyannote 3.3.2 sur GPU **NVIDIA L4** (ou T4).

---

## 2. Architecture technique

```
┌─────────────────────────────────┐           HTTPS (REST / Jobs)
│       Verbatim (Client)         │──────────────────────────────────────────┐
│  - app/engines/modal.py         │                                          ▼
│  - Découpage audio WAV          │                         ┌──────────────────────────────────┐
│  - En-tête X-HF-Token           │                         │      Modal Serverless App        │
│  - Contrôle strict U+FFFD       │                         │   (https://...modal.run)         │
└─────────────────────────────────┘                         └──────────────────────────────────┘
                                                                             │
                                                                 Wakes on request / Scaledown 90s
                                                                             │
                                                                             ▼
                                                            ┌──────────────────────────────────┐
                                                            │   Conteneur GPU (NVIDIA L4)      │
                                                            │   - modal_server.py              │
                                                            │   - pod_server.py (port 8000)    │
                                                            │   - cuDNN 9 préchargé            │
                                                            │   - WhisperX + Pyannote          │
                                                            │                                  │
                                                            │   Volume persistant :            │
                                                            │   /runpod-volume (cache HF)      │
                                                            └──────────────────────────────────┘
```

### Contrat HTTP ([pod_server.py](file:///c:/Users/Famille/Documents/GitHub/Transcription/pod_server.py))
Le serveur expose une API asynchrone pour gérer les gros fichiers sans blocage :
- `GET /health` : vérification de santé (et réveil du conteneur en cold start).
- `POST /jobs` : soumission asynchrone de l'audio (multipart/form-data ou JSON base64). Répond immédiatement avec un `job_id` (`202 Accepted`).
- `GET /jobs/{id}` : interrogation du statut (`queued`, `running`, `done`, `error`).
- `POST /transcribe` : repli synchrone direct.

### Volume persistant
Un volume Modal persistant nommé `verbatim-model-cache` est monté sur `/runpod-volume`. Les caches Hugging Face et Torch y sont redirigés :
- `HF_HOME = /runpod-volume/huggingface-cache`
- `TORCH_HOME = /runpod-volume/torch-cache`
Le modèle `large-v3`, les modèles d'alignement et les poids de diarisation Pyannote ne sont donc téléchargés **qu'une seule fois**.

---

## 3. Sécurité et gestion du jeton Hugging Face

1. **Jeton Hugging Face (`HF_TOKEN`) :**
   - La diarisation Pyannote requiert un jeton Hugging Face (conditions d'utilisation de `pyannote/speaker-diarization-3.1`).
   - Le jeton saisi dans Verbatim (section **Réglages**) est transmis dynamiquement dans l'en-tête HTTP `X-HF-Token` lors de la requête de transcription.
   - Le serveur Modal l'injecte dans son environnement d'exécution pour la durée du travail, sans nécessiter de configuration manuelle dans le tableau de bord Modal.

2. **Jeton d'authentification optionnel (`MODAL_AUTH_TOKEN`) :**
   - Si vous souhaitez restreindre l'accès à votre URL Modal, définissez la variable d'environnement `MODAL_AUTH_TOKEN=mon_secret` sur Modal.
   - Renseignez ce même secret dans Verbatim (**Réglages → Modal → Jeton d'authentification**).
   - Les requêtes sont alors protégées par un en-tête `Authorization: Bearer <secret>`.

3. **Intégrité UTF-8 et protection U+FFFD :**
   - Toutes les entrées et sorties sont encodées et décodées en **UTF-8 strict**.
   - Le moteur client vérifie qu'aucun caractère de remplacement Unicode (`\ufffd`) n'est présent dans les segments ou mots transcrits avant de les valider.

---

## 4. Déploiement et mise à jour

### Déploiement initial
```powershell
.venv\Scripts\pip install modal
.venv\Scripts\python -m modal setup
.venv\Scripts\python -m modal deploy modal_server.py
```

### Mise à jour du code serveur
Si vous modifiez `pod_server.py` ou `modal_server.py`, redéployez simplement :
```powershell
.venv\Scripts\python -m modal deploy modal_server.py
```
La mise à jour est appliquée sans interruption de service.

---

## 5. Dépannage fréquent

| Symptôme | Cause probable | Solution |
|---|---|---|
| `GET /health` met 15-20 s | Démarrage à froid (cold start) | Normal au premier appel après inactivité : Modal démarre la machine GPU. |
| `HF_TOKEN présent : non` au démarrage | Le jeton n'est pas fixé en dur dans l'image | Normal : Verbatim le transmet à chaque envoi d'audio via `X-HF-Token`. |
| Erreur 401 sur le serveur Modal | `MODAL_AUTH_TOKEN` est défini côté Modal mais absent ou erroné dans Verbatim | Vérifiez le jeton dans les Réglages Verbatim. |
| Le conteneur reste actif quelques minutes | Fenêtre d'inactivité de sécurité | Le conteneur s'éteint automatiquement après 90 s sans requête. |
