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

EXPOSE 9000 8080
CMD ["python", "-u", "poc/charge_loop.py"]
