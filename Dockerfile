FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LIDL_DATA_DIR=/data \
    LIDL_HOST=0.0.0.0

# Relie l'image au dépôt GitHub (visibilité et droits du paquet ghcr.io)
LABEL org.opencontainers.image.source="https://github.com/calimere/lidl-tickets"

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY *.py ./
COPY static ./static
COPY extension ./extension

# Utilisateur sans privilèges ; /data (SQLite + clé de chiffrement) est un volume
RUN useradd --system --uid 10001 --home-dir /app app \
    && mkdir /data && chown app /data
USER app
VOLUME /data

EXPOSE 3006
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3006/api/config', timeout=4)"

# --proxy-headers : l'IP réelle du client (anti brute-force) vient du reverse proxy ;
# le port n'est jamais publié directement, seul le proxy y accède.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3006", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
