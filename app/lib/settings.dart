// Verbindungsdaten zum Regler.
//
// Host, Port und Benutzer stehen in den SharedPreferences, das Passwort im
// Keystore des Geräts — SharedPreferences liegen im Klartext in der App-Sandbox
// und wären in jedem Backup lesbar.

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _keyHost = 'host';
const _keyPort = 'port';
const _keyUser = 'user';
const _keyHttps = 'https';
const _secureKeyPassword = 'pvueb_password';

const _storage = FlutterSecureStorage();

class Settings {
  const Settings({
    required this.host,
    required this.port,
    required this.user,
    required this.password,
    required this.https,
  });

  final String host;
  final int port;
  final String user;
  final String password;
  final bool https;

  /// Standard-Port des Web-UI, siehe PVUEB_WEB_PORT im Regler.
  static const defaultPort = 8080;

  /// Zustand vor dem ersten Laden — [isComplete] ist false, es wird nichts geladen.
  static const empty = Settings(
      host: '', port: defaultPort, user: '', password: '', https: false);

  bool get isComplete => host.isNotEmpty;

  String get baseUrl => '${https ? 'https' : 'http'}://$host:$port/';

  static Future<Settings> load() async {
    final prefs = await SharedPreferences.getInstance();
    return Settings(
      host: prefs.getString(_keyHost) ?? '',
      port: prefs.getInt(_keyPort) ?? defaultPort,
      user: prefs.getString(_keyUser) ?? '',
      password: await _storage.read(key: _secureKeyPassword) ?? '',
      https: prefs.getBool(_keyHttps) ?? false,
    );
  }

  Future<void> save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyHost, host);
    await prefs.setInt(_keyPort, port);
    await prefs.setString(_keyUser, user);
    await prefs.setBool(_keyHttps, https);
    if (password.isEmpty) {
      await _storage.delete(key: _secureKeyPassword);
    } else {
      await _storage.write(key: _secureKeyPassword, value: password);
    }
  }
}
