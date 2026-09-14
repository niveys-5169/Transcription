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

# Le modele large-v3 n'est plus precharge ici (comme avant) : ca gonflait
# cette image de plusieurs Go, et RunPod doit la retirer en entier a chaque
# fois qu'un pod de secours atterrit sur un hote qui ne l'a pas deja en
# cache local — plus de 10 minutes, largement au-dela du budget prevu pour
# tout le demarrage (voir runpod_pod_boot_timeout_seconds). L'image reste
# donc legere ; handler.py/pod_server.py mettent le cache Hugging Face sur
# le volume reseau RunPod (/runpod-volume) quand un pod ou un endpoint en a
# un d'attache, pour ne payer le telechargement qu'une seule fois sans
# alourdir l'image elle-meme (voir le README, section « Pod : volume
# reseau »).

COPY handler.py /handler.py
COPY pod_server.py /pod_server.py

# Le serverless démarre toujours handler.py. pod_server.py n'est utilisé que
# si un pod de secours est créé (voir app/engines/runpod_pod.py) : sa
# création override la commande de démarrage du conteneur pour lancer
# pod_server.py à la place — la même image sert les deux usages.
CMD ["python3", "-u", "/handler.py"]
