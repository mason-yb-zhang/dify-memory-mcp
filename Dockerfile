FROM python:3.12-slim

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY scripts ./scripts
COPY config ./config

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/app/scripts/server.py"]
