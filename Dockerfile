FROM runpod/base:0.6.2-cuda12.1.0

RUN pip install --no-cache-dir faster-whisper runpod

COPY handler.py /handler.py

CMD ["python3", "-u", "/handler.py"]
