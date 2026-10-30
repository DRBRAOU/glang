# BRAOU Gondi Vaani - application image
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GV_STORAGE_DIR=/data \
    GV_DATABASE_URL=sqlite:////data/gondi_vaani.db \
    GV_FRONTEND_DIR=/app/frontend

# ffmpeg: validates audio and converts browser recordings to MP3
# sqlite3: handy for inspection;  util-linux: setpriv (drop root in entrypoint)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg sqlite3 util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 gondi \
 && useradd --system --uid 10001 --gid gondi --home-dir /app --shell /usr/sbin/nologin gondi

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY frontend ./frontend
COPY migrations ./migrations
COPY alembic.ini ./
COPY deployment/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh \
 && mkdir -p /data && chown gondi:gondi /data \
 && python -m compileall -q app

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["serve"]
