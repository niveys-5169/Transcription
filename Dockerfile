FROM runpod/base:0.6.2-cuda12.1.0

COPY requirements.txt /requirements.txt
# `python3 -m pip` plutôt que `pip` : garantit que l'installation atterrit
# dans l'interprète que `python3 -u /handler.py` (ou /pod_server.py)
# exécutera réellement, même si l'image de base résout `pip` et `python3`
# vers des environnements différents (Conda notamment). L'import de
# vérification fait échouer le build tout de suite si ce n'est pas le cas,
# plutôt que de livrer une image qui ne le découvre qu'à l'exécution — un
# pod de secours a échoué en production avec « No module named
# 'faster_whisper' » alors que ce `pip install` avait pourtant réussi.
RUN python3 -m pip install --no-cache-dir -r /requirements.txt \
    && python3 -c "import faster_whisper, runpod"

# Precharge le modele large-v3 (seul modele utilise en prod) dans le cache
# Hugging Face de l'image, pour qu'aucun cold start ne le retelecharge.
# device="cpu" : le build n'a pas de GPU, et le download ne depend pas du
# device — seul le chargement en memoire au runtime (handler.py/pod_server.py)
# utilisera cuda/float16.
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8')"

COPY handler.py /handler.py
COPY pod_server.py /pod_server.py

# Le serverless démarre toujours handler.py. pod_server.py n'est utilisé que
# si un pod de secours est créé (voir app/engines/runpod_pod.py) : sa
# création override la commande de démarrage du conteneur pour lancer
# pod_server.py à la place — la même image sert les deux usages.
CMD ["python3", "-u", "/handler.py"]
