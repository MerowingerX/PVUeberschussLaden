# Android-App bauen. Von simonStore/bin/publish.sh aufgerufen (Ziel app-release).

# versionCode aus der Commit-Anzahl statt aus pubspec.yaml: er muss bei jedem
# Release steigen, sonst liefert F-Droid den neuen Build stillschweigend nicht
# aus. Die Commit-Anzahl steigt von allein, eine Zahl in pubspec.yaml nicht.
APP_BUILD_NUMBER := $(shell git rev-list --count HEAD)
APP_VERSION      := 1.0.$(APP_BUILD_NUMBER)

# Dienst bauen und starten. Der Commit wandert als Build-Argument ins Image,
# sonst weiß der laufende Container nicht, welcher Stand er ist (/info).
GIT_COMMIT   := $(shell git rev-parse --short HEAD)
GIT_DESCRIBE := $(shell git describe --always --dirty --tags)
BUILD_TIME   := $(shell date -Is)
DOCKER_ENV   := GIT_COMMIT=$(GIT_COMMIT) GIT_DESCRIBE=$(GIT_DESCRIBE) BUILD_TIME=$(BUILD_TIME)

.PHONY: app-release app-analyze app-clean up down logs test

up:
	$(DOCKER_ENV) docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

test:
	cd poc && .venv/bin/python test_sim.py

app-release:
	cd app && flutter build apk --release \
		--build-number=$(APP_BUILD_NUMBER) \
		--build-name=$(APP_VERSION)

app-analyze:
	cd app && flutter analyze

app-clean:
	cd app && flutter clean
