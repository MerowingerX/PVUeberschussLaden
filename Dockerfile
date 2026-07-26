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
CMD ["python", "-u", "poc/charge_loop.py"]
