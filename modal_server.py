"""Déploiement serverless GPU sur Modal.com pour Verbatim.

Modal offre 30 $/mois de crédits gratuits renouvelés automatiquement et
éteint le conteneur GPU dès la fin de la transcription (scale-to-zero).

Déploiement (une seule fois) :
    pip install modal
    modal setup
    modal deploy modal_server.py

L'URL affichée (``https://<espace>--verbatim-transcription-serve.modal.run``)
est à coller dans Verbatim → Réglages → Modal.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import modal

APP_NAME = os.environ.get("MODAL_APP_NAME", "verbatim-transcription")
VOLUME_NAME = os.environ.get("MODAL_VOLUME_NAME", "verbatim-model-cache")
GPU_TYPE = os.environ.get("MODAL_GPU", "L4")
PORT = 8000
SCALEDOWN_WINDOW = int(os.environ.get("MODAL_SCALEDOWN_WINDOW", "90"))
JOB_TIMEOUT = int(os.environ.get("MODAL_JOB_TIMEOUT", str(4 * 3600)))

ROOT_DIR = Path(__file__).resolve().parent

# Même pile logicielle que le Dockerfile (CUDA 12.8 / Ubuntu 22.04, Python 3.10,
# PyTorch 2.5.1, WhisperX 3.3.2, ctranslate2 4.5.0, pyannote.audio 3.3.2, cuDNN 9).
image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.8.2-runtime-ubuntu22.04",
        add_python="3.10",
    )
    .env(
        {
            "DEBIAN_FRONTEND": "noninteractive",
            "PYTHONUNBUFFERED": "1",
            "PIP_NO_CACHE_DIR": "1",
            "PIP_ROOT_USER_ACTION": "ignore",
            "LD_LIBRARY_PATH": (
                "/usr/local/lib/python3.10/site-packages/nvidia/cudnn/lib:"
                "/usr/local/lib/python3.10/site-packages/nvidia/cublas/lib:"
                "/usr/local/lib/python3.10/dist-packages/nvidia/cudnn/lib:"
                "/usr/local/lib/python3.10/dist-packages/nvidia/cublas/lib"
            ),
        }
    )
    .apt_install("ffmpeg", "ca-certificates")
    .add_local_file(
        str(ROOT_DIR / "requirements.txt"),
        remote_path="/requirements.txt",
        copy=True,
    )
    .run_commands(
        "python3 -m pip install --no-cache-dir "
        "--index-url https://download.pytorch.org/whl/cu121 "
        "torch==2.5.1+cu121 torchaudio==2.5.1+cu121",
        "python3 -m pip install --no-cache-dir -r /requirements.txt",
        'python3 -c "import torch, ctranslate2, whisperx, pyannote.audio, nvidia.cudnn"',
    )
    .add_local_file(
        str(ROOT_DIR / "pod_server.py"),
        remote_path="/pod_server.py",
        copy=True,
    )
)

volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
app = modal.App(APP_NAME)


@app.function(
    image=image,
    gpu=GPU_TYPE,
    volumes={"/runpod-volume": volume},
    timeout=JOB_TIMEOUT,
    scaledown_window=SCALEDOWN_WINDOW,
    max_containers=1,
)
@modal.concurrent(max_inputs=20)
@modal.web_server(PORT, startup_timeout=600.0)
def serve() -> None:
    """Lance le serveur HTTP de transcription sur le conteneur GPU Modal."""
    env = dict(os.environ)
    env["PORT"] = str(PORT)
    subprocess.Popen(["python3", "-u", "/pod_server.py"], env=env)
