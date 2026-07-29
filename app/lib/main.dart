// PVueb — Android-Hülle um das Web-UI des Laderegelers.
//
// Die Oberfläche lebt im Regler (poc/charge_loop.py, INDEX_HTML) und wird hier
// nur angezeigt. Nachgebaut wird sie bewusst nicht: sie ändert sich mit dem
// Regler, und zwei Stellen für dieselben Knöpfe laufen sicher auseinander.
//
// Dasselbe gilt für den zweiten Reiter: die Meldungen kommen als Seite aus
// myhome-messenger (eigenes Projekt, /). Der Reiter erscheint nur, wenn dort
// ein Port eingetragen ist — ohne Meldestelle sieht die App aus wie zuvor.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'settings.dart';
import 'settings_page.dart';
import 'web_seite.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const PvuebApp());
}

class PvuebApp extends StatelessWidget {
  const PvuebApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'PVueb',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF2E7D32),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final _regler = GlobalKey<WebSeiteState>();
  final _meldungen = GlobalKey<WebSeiteState>();

  Settings _settings = Settings.empty;
  int _reiter = 0;
  String? _hinweis;

  @override
  void initState() {
    super.initState();
    _start();
  }

  Future<void> _start() async {
    final settings = await Settings.load();
    if (!mounted) return;
    setState(() => _settings = settings);

    // Erster Start: ohne Host gibt es nichts zu laden.
    if (!settings.isComplete) {
      setState(() => _hinweis = 'Kein Regler eingetragen — Zahnrad oben rechts.');
      await _openSettings(settings);
    }
  }

  Future<Settings?> _openSettings(Settings current) async {
    final saved = await Navigator.of(context).push<Settings>(
      MaterialPageRoute(builder: (_) => SettingsPage(initial: current)),
    );
    if (saved != null && mounted) {
      setState(() {
        _settings = saved;
        _hinweis = null;
        // Ist die Meldestelle wieder abgemeldet worden, während ihr Reiter
        // offen war, gäbe es sonst einen Reiter ohne Seite.
        if (!saved.hasMessenger) _reiter = 0;
      });
    }
    return saved;
  }

  WebSeiteState? get _aktuelle =>
      _reiter == 0 ? _regler.currentState : _meldungen.currentState;

  @override
  Widget build(BuildContext context) {
    final settings = _settings;
    final zeigeLeiste = settings.hasMessenger;

    return PopScope(
      // Zurück blättert im UI; ist nichts mehr zu blättern, endet die App —
      // genau das erwartet man von einem Launcher-Icon.
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop) return;
        if (await _aktuelle?.zurueck() ?? false) return;
        // Aus den Meldungen führt der Weg zurück zum Regler, nicht aus der App.
        if (_reiter != 0) {
          setState(() => _reiter = 0);
          return;
        }
        // Navigator.pop() verpufft auf der Wurzelroute; die App soll aber
        // wirklich zugehen, so wie jede andere App am Launcher-Icon.
        await SystemNavigator.pop();
      },
      child: Scaffold(
        backgroundColor: const Color(0xFF111111),
        appBar: AppBar(
          title: Text(_reiter == 0 ? 'PVueb' : 'Meldungen'),
          backgroundColor: const Color(0xFF1B1B1B),
          actions: [
            IconButton(
              icon: const Icon(Icons.refresh),
              tooltip: 'Neu laden',
              onPressed: () => _aktuelle?.neuLaden(),
            ),
            IconButton(
              icon: const Icon(Icons.settings),
              tooltip: 'Einstellungen',
              onPressed: () => _openSettings(_settings),
            ),
          ],
        ),
        body: _hinweis != null
            ? _Hinweis(text: _hinweis!)
            : !settings.isComplete
                ? const SizedBox.shrink()
                : IndexedStack(
                    index: _reiter,
                    children: [
                      WebSeite(
                        key: _regler,
                        url: settings.baseUrl,
                        user: settings.user,
                        password: settings.password,
                        fehlertext: '${settings.baseUrl} nicht erreichbar',
                      ),
                      if (zeigeLeiste)
                        WebSeite(
                          key: _meldungen,
                          url: settings.messengerUrl,
                          user: settings.messengerUser,
                          password: settings.messengerPassword,
                          fehlertext:
                              'Meldestelle ${settings.messengerUrl} nicht erreichbar',
                        )
                      else
                        const SizedBox.shrink(),
                    ],
                  ),
        bottomNavigationBar: zeigeLeiste
            ? NavigationBar(
                backgroundColor: const Color(0xFF1B1B1B),
                selectedIndex: _reiter,
                onDestinationSelected: (i) => setState(() => _reiter = i),
                destinations: const [
                  NavigationDestination(
                      icon: Icon(Icons.bolt_outlined),
                      selectedIcon: Icon(Icons.bolt),
                      label: 'Regler'),
                  NavigationDestination(
                      icon: Icon(Icons.mail_outline),
                      selectedIcon: Icon(Icons.mail),
                      label: 'Meldungen'),
                ],
              )
            : null,
      ),
    );
  }
}

class _Hinweis extends StatelessWidget {
  const _Hinweis({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Text(text,
            textAlign: TextAlign.center,
            style: const TextStyle(color: Colors.white70)),
      ),
    );
  }
}
