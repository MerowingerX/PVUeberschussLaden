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
const _keyMessengerPort = 'messenger_port';
const _keyMessengerUser = 'messenger_user';
const _secureKeyMessengerPassword = 'myhome_password';

const _storage = FlutterSecureStorage();

class Settings {
  const Settings({
    required this.host,
    required this.port,
    required this.user,
    required this.password,
    required this.https,
    this.messengerPort = 0,
    this.messengerUser = '',
    this.messengerPassword = '',
  });

  final String host;
  final int port;
  final String user;
  final String password;
  final bool https;

  /// Port von myhome-messenger, 0 = nicht eingetragen. Der Host ist derselbe:
  /// beide Dienste laufen auf demselben Rechner, und zwei Adressen zu pflegen,
  /// die immer gleich sind, wäre nur eine Fehlerquelle mehr.
  final int messengerPort;
  final String messengerUser;
  final String messengerPassword;

  /// Standard-Port des Web-UI, siehe PVUEB_WEB_PORT im Regler.
  static const defaultPort = 8080;

  /// Standard-Port der Meldestelle, siehe MYHOME_PORT im Messenger.
  static const defaultMessengerPort = 8090;

  /// Zustand vor dem ersten Laden — [isComplete] ist false, es wird nichts geladen.
  static const empty = Settings(
      host: '', port: defaultPort, user: '', password: '', https: false);

  bool get isComplete => host.isNotEmpty;

  /// Gibt es eine Meldestelle? Ohne sie bleibt die App genau wie vorher —
  /// keine Leiste, kein zweiter Reiter.
  bool get hasMessenger => host.isNotEmpty && messengerPort > 0;

  String get baseUrl => '${https ? 'https' : 'http'}://$host:$port/';

  String get messengerUrl =>
      '${https ? 'https' : 'http'}://$host:$messengerPort/';

  static Future<Settings> load() async {
    final prefs = await SharedPreferences.getInstance();
    return Settings(
      host: prefs.getString(_keyHost) ?? '',
      port: prefs.getInt(_keyPort) ?? defaultPort,
      user: prefs.getString(_keyUser) ?? '',
      password: await _storage.read(key: _secureKeyPassword) ?? '',
      https: prefs.getBool(_keyHttps) ?? false,
      messengerPort: prefs.getInt(_keyMessengerPort) ?? 0,
      messengerUser: prefs.getString(_keyMessengerUser) ?? '',
      messengerPassword:
          await _storage.read(key: _secureKeyMessengerPassword) ?? '',
    );
  }

  Future<void> save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyHost, host);
    await prefs.setInt(_keyPort, port);
    await prefs.setString(_keyUser, user);
    await prefs.setBool(_keyHttps, https);
    await prefs.setInt(_keyMessengerPort, messengerPort);
    await prefs.setString(_keyMessengerUser, messengerUser);
    if (password.isEmpty) {
      await _storage.delete(key: _secureKeyPassword);
    } else {
      await _storage.write(key: _secureKeyPassword, value: password);
    }
    if (messengerPassword.isEmpty) {
      await _storage.delete(key: _secureKeyMessengerPassword);
    } else {
      await _storage.write(
          key: _secureKeyMessengerPassword, value: messengerPassword);
    }
  }
}
