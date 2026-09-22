# Base officielle NVIDIA maintenue (remplace runpod/base:0.6.2-cuda12.1.0,
# dont la couche nvidia/cuda:12.1.0-*-ubuntu22.04 est en fin de vie —
# NVIDIA supprime les tags EOL de Docker Hub six mois après leur passage en
# EOL, voir doc/unsupported-tags.md du dépôt NVIDIA ; le jour où c'est fait,
# `docker pull` échoue sur tout hôte RunPod sans l'image déjà en cache et le
# pod ne démarre plus). nvidia/cuda:12.8.2-runtime-ubuntu22.04 est listée
# dans doc/supported-tags.md. On y installe nous-mêmes le peu dont le pod a
# besoin : Python 3.10 (celui d'Ubuntu 22.04 — exactement la version de
# l'image précédente), pip, ffmpeg (requis par whisperx.load_audio).
#
# La pile PyTorch n'a pas à changer : les roues torch==2.5.1+cu121 embarquent
# leur propre runtime CUDA via les paquets pip nvidia-* (cuBLAS 12.4,
# cuDNN 9.1...). Le CUDA de l'image de base ne sert qu'au pilote/conteneur ;
# 12.8 reste compatible avec les pilotes des hôtes RunPod.
FROM nvidia/cuda:12.8.2-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_ROOT_USER_ACTION=ignore PIP_BREAK_SYSTEM_PACKAGES=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         python3 python3-pip ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /requirements.txt
# `python3 -m pip` plutôt que `pip` : garantit que l'installation atterrit
# dans l'interprète que `python3 -u /pod_server.py`
# exécutera réellement, même si l'image de base résout `pip` et `python3`
# vers des environnements différents (Conda notamment). L'import de
# vérification fait échouer le build tout de suite si ce n'est pas le cas,
# plutôt que de livrer une image qui ne le découvre qu'à l'exécution — un
# pod a échoué en production avec une dépendance de transcription absente.
RUN python3 -m pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cu121 \
      torch==2.5.1+cu121 torchaudio==2.5.1+cu121 \
    && python3 -m pip install --no-cache-dir -r /requirements.txt \
    && python3 -c "import torch, ctranslate2, whisperx, pyannote.audio, os, nvidia.cudnn; \
         assert tuple(map(int, ctranslate2.__version__.split('.')[:2])) >= (4, 5), ctranslate2.__version__; \
         lib = os.path.join(os.path.dirname(nvidia.cudnn.__file__), 'lib'); \
         assert os.path.exists(os.path.join(lib, 'libcudnn_ops.so.9')), lib" \
    && ffmpeg -version | head -1

# pyannote 3.3.2 appelle hf_hub_download(..., use_auth_token=...), argument
# supprimé par huggingface_hub 1.0 (voir requirements.txt). Vérification par
# un appel réel, pas par inspection de la signature : en 0.x, l'argument
# n'apparaît pas dans la signature, c'est le décorateur validate_hf_hub_args
# qui l'accepte et le convertit en `token` — une inspection de signature
# faisait échouer le build à tort sur la 0.36.2. local_files_only=True :
# aucun accès réseau, l'appel échoue sur LocalEntryNotFoundError (toléré) ;
# seul un TypeError (argument refusé) fait échouer le build. Le corps est
# passé à exec() car `python3 -c` n'admet pas de try/except sur une ligne.
RUN python3 -W ignore -c "exec(\"import huggingface_hub\\nfrom huggingface_hub import hf_hub_download\\ntry:\\n    hf_hub_download(repo_id='pyannote/x', filename='config.yaml', use_auth_token='t', local_files_only=True)\\nexcept TypeError as exc:\\n    raise SystemExit(f'huggingface_hub {huggingface_hub.__version__} refuse use_auth_token ({exc}) : pyannote 3.3.2 en a besoin')\\nexcept Exception:\\n    pass\\nprint(f'huggingface_hub {huggingface_hub.__version__} accepte use_auth_token.')\")"

# cuDNN 9 (wheel nvidia-cudnn-cu12, installé par torch) est découpé en une
# bibliothèque principale et des sous-bibliothèques (libcudnn_cnn.so.9,
# libcudnn_ops.so.9...) que la principale ouvre elle-même par dlopen sur
# leur seul nom, à la première convolution. Le dossier du wheel n'est pas
# sur le chemin de recherche du chargeur : ctranslate2 mourait d'un
# « Unable to load any of {libcudnn_cnn.so.9.1.0, ...} » (abort natif, pas
# d'exception Python) à la première transcription, le conteneur
# redémarrait, et l'application ne voyait que des 502/404 du proxy RunPod.
# Chemin fixe plutôt que calculé : ENV ne peut pas exécuter Python, et la
# vérification ci-dessous fait échouer le build si le dossier bouge ou si
# le chargeur ne trouve toujours pas la sous-bibliothèque par son nom —
# exactement l'opération que cuDNN fait en production. pod_server.py
# précharge aussi ces bibliothèques (ceinture et bretelles, voir
# _preload_cudnn), au cas où RunPod écraserait la variable au lancement.
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.10/dist-packages/nvidia/cudnn/lib:/usr/local/lib/python3.10/dist-packages/nvidia/cublas/lib:${LD_LIBRARY_PATH}
RUN python3 -c "import os, nvidia.cudnn, nvidia.cublas; \
      chemins = os.environ['LD_LIBRARY_PATH'].split(':'); \
      manquants = [p.__file__ for p in (nvidia.cudnn, nvidia.cublas) \
                   if os.path.join(os.path.dirname(p.__file__), 'lib') not in chemins]; \
      assert not manquants, f'dossiers absents de LD_LIBRARY_PATH : {manquants}'" \
    && python3 -c "import ctypes; ctypes.CDLL('libcudnn_cnn.so.9'); print('libcudnn_cnn.so.9 trouvée par son nom.')"

# Le modele large-v3 n'est plus precharge ici (comme avant) : ca gonflait
# cette image de plusieurs Go, et RunPod doit la retirer en entier a chaque
# fois qu'un pod de secours atterrit sur un hote qui ne l'a pas deja en
# cache local — plus de 10 minutes, largement au-dela du budget prevu pour
# tout le demarrage (voir runpod_pod_boot_timeout_seconds). L'image reste
# donc legere ; pod_server.py met les caches Hugging Face et torch.hub sur le
# volume reseau RunPod (/runpod-volume) quand un pod l'a attache, pour ne payer
# qu'une fois les telechargements de Whisper, de l'alignement et de pyannote sans
# alourdir l'image elle-meme (voir le README, section « Pod : volume
# reseau »).

COPY pod_server.py /pod_server.py

# Cette image est exclusivement destinée au pod RunPod. Il démarre le serveur
# HTTP qui expose `/health` et `/transcribe`.
CMD ["python3", "-u", "/pod_server.py"]
