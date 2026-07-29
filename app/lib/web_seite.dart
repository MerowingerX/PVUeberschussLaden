// Eine WebView mit allem, was dazugehört: Anmeldung, Ladeanzeige, Fehlerbild.
//
// Herausgezogen, weil die App zwei davon zeigt — den Regler und die
// Meldestelle. Zwei Kopien derselben Delegate-Logik laufen sicher
// auseinander, und die Fehlerbehandlung ist genau die Stelle, an der das
// wehtut.

import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

class WebSeite extends StatefulWidget {
  const WebSeite({
    super.key,
    required this.url,
    required this.user,
    required this.password,
    required this.fehlertext,
  });

  final String url;
  final String user;
  final String password;

  /// Was dasteht, wenn nichts kommt. Je Seite anders: „Regler nicht
  /// erreichbar" und „Meldestelle nicht erreichbar" sind verschiedene
  /// Nachrichten, auch wenn dieselbe Leitung schuld ist.
  final String fehlertext;

  @override
  State<WebSeite> createState() => WebSeiteState();
}

class WebSeiteState extends State<WebSeite> {
  WebViewController? _controller;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _laden();
  }

  @override
  void didUpdateWidget(WebSeite oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.url != widget.url ||
        oldWidget.user != widget.user ||
        oldWidget.password != widget.password) {
      _laden();
    }
  }

  Future<void> _laden() async {
    final controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0xFF111111))
      ..setNavigationDelegate(
        NavigationDelegate(
          onHttpAuthRequest: (request) {
            // Beide Dienste schützen ihre Oberfläche per Basic Auth, sobald
            // ein Benutzer gesetzt ist. Ohne diesen Zweig bliebe die WebView
            // leer.
            if (widget.user.isEmpty) {
              request.onCancel();
              return;
            }
            request.onProceed(WebViewCredential(
              user: widget.user,
              password: widget.password,
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
                _error = '${widget.fehlertext}\n(${error.description})';
              });
            }
          },
        ),
      );
    await controller.loadRequest(Uri.parse(widget.url));
    if (!mounted) return;
    setState(() {
      _controller = controller;
      _loading = true;
      _error = null;
    });
  }

  /// Neu laden. Nach einem Fehler ist die History leer — reload() liefe ins
  /// Nichts, deshalb die Adresse noch einmal.
  Future<void> neuLaden() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    await _controller?.loadRequest(Uri.parse(widget.url));
  }

  /// Konnte im Verlauf zurückgeblättert werden?
  Future<bool> zurueck() async {
    final controller = _controller;
    if (controller == null) return false;
    if (!await controller.canGoBack()) return false;
    await controller.goBack();
    return true;
  }

  @override
  Widget build(BuildContext context) {
    final controller = _controller;
    return Stack(
      children: [
        if (controller != null && _error == null)
          WebViewWidget(controller: controller),
        if (_error != null) _Fehlerbild(text: _error!, onRetry: neuLaden),
        if (_loading && _error == null)
          const Center(child: CircularProgressIndicator()),
      ],
    );
  }
}

class _Fehlerbild extends StatelessWidget {
  const _Fehlerbild({required this.text, required this.onRetry});

  final String text;
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
            Text(text,
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
