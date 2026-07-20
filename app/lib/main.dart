// PVueb — Android-Hülle um das Web-UI des Laderegelers.
//
// Die Oberfläche lebt im Regler (poc/charge_loop.py, INDEX_HTML) und wird hier
// nur angezeigt. Nachgebaut wird sie bewusst nicht: sie ändert sich mit dem
// Regler, und zwei Stellen für dieselben Knöpfe laufen sicher auseinander.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:webview_flutter/webview_flutter.dart';

import 'settings.dart';
import 'settings_page.dart';

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
  WebViewController? _controller;
  Settings _settings = Settings.empty;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _start();
  }

  Future<void> _start() async {
    final settings = await Settings.load();
    if (settings.isComplete) return _applySettings(settings);

    // Erster Start: ohne Host gibt es nichts zu laden.
    if (!mounted) return;
    setState(() {
      _settings = settings;
      _loading = false;
      _error = 'Kein Regler eingetragen — Zahnrad oben rechts.';
    });
    await _openSettings(settings);
  }

  Future<void> _applySettings(Settings settings) async {
    final controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0xFF111111))
      ..setNavigationDelegate(
        NavigationDelegate(
          onHttpAuthRequest: (request) {
            // Der Regler schützt sein UI per Basic Auth, sobald PVUEB_WEB_USER
            // gesetzt ist. Ohne diesen Zweig bliebe die WebView leer.
            if (settings.user.isEmpty) {
              request.onCancel();
              return;
            }
            request.onProceed(WebViewCredential(
              user: settings.user,
              password: settings.password,
            ));
          },
          onPageFinished: (_) {
            if (mounted) setState(() => _loading = false);
          },
          onWebResourceError: (error) {
            // Unterressourcen (Favicon o. ä.) dürfen keine Fehlerseite auslösen.
            if (error.isForMainFrame == false) return;
            if (mounted) {
              setState(() {
                _loading = false;
                _error = '${settings.baseUrl} nicht erreichbar\n'
                    '(${error.description})';
              });
            }
          },
        ),
      );
    await controller.loadRequest(Uri.parse(settings.baseUrl));
    if (!mounted) return;
    setState(() {
      _settings = settings;
      _controller = controller;
      _loading = true;
      _error = null;
    });
  }

  Future<Settings?> _openSettings(Settings current) async {
    final saved = await Navigator.of(context).push<Settings>(
      MaterialPageRoute(builder: (_) => SettingsPage(initial: current)),
    );
    if (saved != null) await _applySettings(saved);
    return saved;
  }

  Future<void> _reload() async {
    final settings = _settings;
    if (!settings.isComplete) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    // Nach einem Fehler ist die History leer — reload() liefe ins Nichts.
    await _controller?.loadRequest(Uri.parse(settings.baseUrl));
  }

  @override
  Widget build(BuildContext context) {
    final controller = _controller;

    return PopScope(
      // Zurück blättert im UI; ist nichts mehr zu blättern, endet die App —
      // genau das erwartet man von einem Launcher-Icon.
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop) return;
        if (controller != null && await controller.canGoBack()) {
          await controller.goBack();
          return;
        }
        // Navigator.pop() verpufft auf der Wurzelroute; die App soll aber
        // wirklich zugehen, so wie jede andere App am Launcher-Icon.
        await SystemNavigator.pop();
      },
      child: Scaffold(
        backgroundColor: const Color(0xFF111111),
        appBar: AppBar(
          title: const Text('PVueb'),
          backgroundColor: const Color(0xFF1B1B1B),
          actions: [
            IconButton(
              icon: const Icon(Icons.refresh),
              tooltip: 'Neu laden',
              onPressed: _reload,
            ),
            IconButton(
              icon: const Icon(Icons.settings),
              tooltip: 'Einstellungen',
              onPressed: () => _openSettings(_settings),
            ),
          ],
        ),
        body: Stack(
          children: [
            if (controller != null && _error == null)
              WebViewWidget(controller: controller),
            if (_error != null) _ErrorView(message: _error!, onRetry: _reload),
            if (_loading && _error == null)
              const Center(child: CircularProgressIndicator()),
          ],
        ),
      ),
    );
  }
}

class _ErrorView extends StatelessWidget {
  const _ErrorView({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.cloud_off, size: 48, color: Colors.white54),
            const SizedBox(height: 16),
            Text(message,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white70)),
            const SizedBox(height: 24),
            FilledButton(
                onPressed: onRetry, child: const Text('Erneut versuchen')),
          ],
        ),
      ),
    );
  }
}
