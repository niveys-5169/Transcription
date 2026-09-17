# Historique technique

Les passages du README consacrés à RunPod Serverless sont conservés uniquement
pour retracer le prototype. Ils ne décrivent plus un chemin supporté.

Depuis le plan v2, Verbatim utilise un pod HTTP RunPod avec WhisperX,
alignement mot à mot et diarisation pyannote ; les détails d’usage et de
validation sont maintenus dans [PLAN.md](PLAN.md) et [RECETTE.md](RECETTE.md).

## 2026-09-17 — 400 à la diarisation : huggingface_hub 1.x

Troisième et dernier maillon de la même journée. Une fois cuDNN réglé
(ci-dessous), le pod transcrivait et alignait, puis répondait 400 à
l'étape diarisation : `hf_hub_download() got an unexpected keyword
argument 'use_auth_token'`. pyannote.audio 3.3.2 ne déclare que
`huggingface_hub>=0.13` mais appelle encore `hf_hub_download(...,
use_auth_token=...)`, argument supprimé par huggingface_hub 1.0 (remplacé
par `token`). Rien ne bornait la version : `transformers`, que whisperx
déclare sans version, sort en 5.x depuis fin 2025 et exige
`huggingface_hub>=1.3` ; pip suivait.

Correctif : `transformers>=4.48,<5` et `huggingface_hub>=0.34,<1.0` dans
`requirements.txt` (les 4.5x de transformers exigent hub `<1.0`, les deux
bornes vont ensemble), et une vérification au build qui appelle
`hf_hub_download` avec `use_auth_token` — exactement le contrat dont
pyannote 3.3.2 a besoin.

Faux pas intermédiaire : la première version de cette vérification
inspectait la signature de `hf_hub_download`, et a fait échouer le build de
l'image sur la 0.36.2 pourtant correcte. En 0.x, `use_auth_token` n'est
pas dans la signature : c'est le décorateur `validate_hf_hub_args` qui
l'accepte et le convertit en `token`. D'où la vérification par appel réel
(`local_files_only=True`, sans réseau) : seul un `TypeError` fait échouer
le build.

Au passage : httpx journalise l'URL complète de chaque requête en INFO, et
la clé API RunPod voyage en paramètre `?api_key=...` de l'API GraphQL. Elle
finissait donc en clair dans `app.log`, et de là dans les extraits collés
pour diagnostic. `app/logging_setup.py` masque désormais la valeur des
paramètres sensibles sur le logger `httpx`. La clé exposée dans un log
avant ce correctif est à révoquer et régénérer dans la console RunPod.

Règle retenue : chaque dépendance lourde du pod (torch, whisperx,
ctranslate2, pyannote, huggingface_hub) est épinglée explicitement dans
`requirements.txt`, avec sa raison ; le minimum transitif déclaré par une
bibliothèque ne dit rien de son maximum réel.

## 2026-09-17 — 502 puis 404 : sous-bibliothèques cuDNN 9 introuvables

Deuxième plantage du même genre, juste après le passage à cuDNN 9 ci-dessous.
Le pod répondait 200 sur `/health`, chargeait bien le modèle VAD (torch),
puis mourait à la première inférence ctranslate2 avec
`Unable to load any of {libcudnn_cnn.so.9.1.0, libcudnn_cnn.so.9.1,
libcudnn_cnn.so.9, libcudnn_cnn.so}` puis `Invalid handle. Cannot load
symbol cudnnCreateConvolutionDescriptor` — un abort natif, pas une
exception Python. Le conteneur redémarrait ; l'application voyait 502 (port
orphelin), 502, puis 404 (route retirée par le proxy pendant le
réenregistrement) et abandonnait sur « Réponse du pod illisible (HTTP
404) », un message qui suggérait à tort un problème de propagation de
route.

Cause : cuDNN 9 est découpé en une bibliothèque principale
(`libcudnn.so.9`, que torch précharge) et des sous-bibliothèques
(`libcudnn_graph`, `libcudnn_ops`, `libcudnn_cnn`...) que la principale
ouvre elle-même par `dlopen` sur leur seul nom, à la première convolution.
Le wheel `nvidia-cudnn-cu12` les dépose dans
`site-packages/nvidia/cudnn/lib`, un dossier que le chargeur dynamique ne
parcourt pas. Avoir importé torch ne suffit donc pas (leçon du correctif
précédent, incomplète) : c'est exactement le cas documenté dans le README
de faster-whisper, dont la réponse est `LD_LIBRARY_PATH`.

Correctif, ceinture et bretelles :

- `Dockerfile` : `LD_LIBRARY_PATH` inclut les dossiers `lib` des wheels
  `nvidia/cudnn` et `nvidia/cublas`, et le build vérifie que
  `ctypes.CDLL('libcudnn_cnn.so.9')` — le même `dlopen` par nom que fait
  cuDNN en production — réussit.
- `pod_server.py` : `_preload_cudnn()` charge les sous-bibliothèques par
  chemin complet en `RTLD_GLOBAL` avant toute inférence (chemin WhisperX et
  chemin faster-whisper direct) ; un `dlopen` ultérieur sur leur `SONAME`
  les retrouve sans parcourir de dossier, même si RunPod écrase la variable
  d'environnement au lancement.
- `app/engines/runpod_pod.py` : le message d'erreur après trois tentatives
  retient le statut le plus parlant vu (502/503/504 prime sur 404) plutôt
  que le dernier, pour orienter vers les logs du pod.

Règle retenue : toute image qui fait tourner ctranslate2 sur le cuDNN des
wheels pip doit exposer `nvidia/cudnn/lib` au chargeur dynamique
(`LD_LIBRARY_PATH` ou préchargement), l'import de torch ne le fait pas.

## 2026-09-17 — 502 après création du pod : cuDNN 8 vs 9

Le pod répondait 200 sur `/health` puis plantait (processus tué, pas une
exception Python) à la première transcription, avec
`libcudnn_ops_infer.so.8` introuvable côté ctranslate2 ; le proxy RunPod
renvoyait alors 502 aux trois tentatives, le conteneur redémarrant sans
personne derrière le port 8000. Cause : torch 2.5.1 installe
`nvidia-cudnn-cu12==9.1` (cuDNN 9), mais whisperx==3.3.1 épinglait
`ctranslate2<4.5.0`, qui exige cuDNN 8. Règle retenue : avec torch 2.5.x,
`ctranslate2` doit être en `>=4.5.0` (cuDNN 9). Correctif : whisperx 3.3.2
(qui autorise `ctranslate2>=4.5.0`) et épinglage explicite de
`ctranslate2==4.5.0` dans `requirements.txt`.

À la même occasion, l'image du pod a changé de base :
`runpod/base:0.6.2-cuda12.1.0` (couche `nvidia/cuda:12.1.0-*`) affichait la
bannière « THIS IMAGE IS DEPRECATED and is scheduled for DELETION » —
NVIDIA supprime les tags CUDA en fin de vie de Docker Hub six mois après
leur passage en EOL. Nouvelle base : `nvidia/cuda:12.8.2-runtime-ubuntu22.04`
(image officielle maintenue), sur laquelle le `Dockerfile` installe
lui-même Python 3.10 (celui d'Ubuntu 22.04, même version que l'image
précédente) et `ffmpeg` explicitement. Les roues PyTorch cu121 gardent leur
propre runtime CUDA embarqué, donc la version CUDA de la base n'a pas
d'incidence sur la pile de transcription.
