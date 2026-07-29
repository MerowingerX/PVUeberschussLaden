FROM python:3.13-slim

# Zeitzone: Nachttarif-Fenster (00-08 Uhr) rechnet mit lokaler Zeit
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Europe/Berlin

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY poc/charge_loop.py poc/

# Woher der Stand kommt: im Image gibt es kein Git-Verzeichnis, also wandert
# der Commit beim Bauen herein (siehe Makefile-Ziel `image`). Ohne Argument
# steht auf der Info-Seite „unbekannt" statt einer falschen Zahl.
ARG GIT_COMMIT=""
ARG GIT_DESCRIBE=""
ARG BUILD_TIME=""
ENV PVUEB_GIT_COMMIT=$GIT_COMMIT \
    PVUEB_GIT_DESCRIBE=$GIT_DESCRIBE \
    PVUEB_BUILD_TIME=$BUILD_TIME

EXPOSE 9000 8080

# Gesund heißt: der Regeltakt läuft. Nicht „der Prozess existiert" und nicht
# „die Web-UI antwortet" — beides war am 28.07.2026 der Fall, während 17 Stunden
# lang nichts geregelt wurde (docs/issue_nightly_load_did_not_work.md). Der
# Prozess-eigene Wächter beendet sich in so einem Fall längst selbst; dieser
# Healthcheck macht den Zustand zusätzlich in `docker ps` sichtbar, statt einen
# scheintoten Dienst als „Up 24 hours" zu führen.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import json,sys,urllib.request; \
d=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=4)); \
a=d.get('tick_age_s'); \
sys.exit(0 if a is not None and a < d.get('tick_timeout_s', 120) else 1)"

CMD ["python", "-u", "poc/charge_loop.py"]
