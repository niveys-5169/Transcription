FROM runpod/base:0.6.2-cuda12.1.0

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

COPY handler.py /handler.py
COPY pod_server.py /pod_server.py

# Le serverless démarre toujours handler.py. pod_server.py n'est utilisé que
# si un pod de secours est créé (voir app/engines/runpod_pod.py) : sa
# création override la commande de démarrage du conteneur pour lancer
# pod_server.py à la place — la même image sert les deux usages.
CMD ["python3", "-u", "/handler.py"]
