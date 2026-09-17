# Historique technique

Les passages du README consacrés à RunPod Serverless sont conservés uniquement
pour retracer le prototype. Ils ne décrivent plus un chemin supporté.

Depuis le plan v2, Verbatim utilise un pod HTTP RunPod avec WhisperX,
alignement mot à mot et diarisation pyannote ; les détails d’usage et de
validation sont maintenus dans [PLAN.md](PLAN.md) et [RECETTE.md](RECETTE.md).

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
