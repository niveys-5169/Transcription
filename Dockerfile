FROM runpod/base:0.6.2-cuda12.1.0

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

COPY handler.py /handler.py

CMD ["python3", "-u", "/handler.py"]
