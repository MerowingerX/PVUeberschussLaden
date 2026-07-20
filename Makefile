# Android-App bauen. Von simonStore/bin/publish.sh aufgerufen (Ziel app-release).

# versionCode aus der Commit-Anzahl statt aus pubspec.yaml: er muss bei jedem
# Release steigen, sonst liefert F-Droid den neuen Build stillschweigend nicht
# aus. Die Commit-Anzahl steigt von allein, eine Zahl in pubspec.yaml nicht.
APP_BUILD_NUMBER := $(shell git rev-list --count HEAD)
APP_VERSION      := 1.0.$(APP_BUILD_NUMBER)

.PHONY: app-release app-analyze app-clean

app-release:
	cd app && flutter build apk --release \
		--build-number=$(APP_BUILD_NUMBER) \
		--build-name=$(APP_VERSION)

app-analyze:
	cd app && flutter analyze

app-clean:
	cd app && flutter clean
