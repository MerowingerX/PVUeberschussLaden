import 'package:flutter/material.dart';

import 'settings.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key, required this.initial});

  final Settings initial;

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _host =
      TextEditingController(text: widget.initial.host);
  late final TextEditingController _port =
      TextEditingController(text: widget.initial.port.toString());
  late final TextEditingController _user =
      TextEditingController(text: widget.initial.user);
  late final TextEditingController _password =
      TextEditingController(text: widget.initial.password);
  late final TextEditingController _messengerPort = TextEditingController(
      text: widget.initial.messengerPort == 0
          ? ''
          : widget.initial.messengerPort.toString());
  late final TextEditingController _messengerUser =
      TextEditingController(text: widget.initial.messengerUser);
  late final TextEditingController _messengerPassword =
      TextEditingController(text: widget.initial.messengerPassword);
  late bool _https = widget.initial.https;
  bool _showPassword = false;

  @override
  void dispose() {
    _host.dispose();
    _port.dispose();
    _user.dispose();
    _password.dispose();
    _messengerPort.dispose();
    _messengerUser.dispose();
    _messengerPassword.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    final settings = Settings(
      host: _host.text.trim(),
      port: int.parse(_port.text.trim()),
      user: _user.text.trim(),
      password: _password.text,
      https: _https,
      // Leeres Feld heißt: keine Meldestelle. Dann bleibt die Leiste unten
      // weg und die App sieht aus wie vorher.
      messengerPort: int.tryParse(_messengerPort.text.trim()) ?? 0,
      messengerUser: _messengerUser.text.trim(),
      messengerPassword: _messengerPassword.text,
    );
    await settings.save();
    if (mounted) Navigator.of(context).pop(settings);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Einstellungen')),
      body: Form(
        key: _form,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextFormField(
              controller: _host,
              autocorrect: false,
              decoration: const InputDecoration(
                labelText: 'Host',
                hintText: 'z. B. 192.168.1.50 oder pvueb.fritz.box',
              ),
              validator: (v) =>
                  (v == null || v.trim().isEmpty) ? 'Host fehlt' : null,
            ),
            TextFormField(
              controller: _port,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: 'Port'),
              validator: (v) {
                final n = int.tryParse(v?.trim() ?? '');
                return (n == null || n < 1 || n > 65535) ? 'Port 1–65535' : null;
              },
            ),
            const SizedBox(height: 8),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('HTTPS'),
              subtitle: const Text('Nur wenn ein Reverse Proxy davorsteht'),
              value: _https,
              onChanged: (v) => setState(() => _https = v),
            ),
            const Divider(height: 32),
            Text('Basic Auth', style: Theme.of(context).textTheme.titleSmall),
            const Text('Leer lassen, wenn PVUEB_WEB_USER im Regler nicht gesetzt ist.',
                style: TextStyle(color: Colors.white54, fontSize: 12)),
            TextFormField(
              controller: _user,
              autocorrect: false,
              decoration: const InputDecoration(labelText: 'Benutzer'),
            ),
            TextFormField(
              controller: _password,
              obscureText: !_showPassword,
              autocorrect: false,
              decoration: InputDecoration(
                labelText: 'Passwort',
                suffixIcon: IconButton(
                  icon: Icon(
                      _showPassword ? Icons.visibility_off : Icons.visibility),
                  onPressed: () =>
                      setState(() => _showPassword = !_showPassword),
                ),
              ),
            ),
            const Divider(height: 32),
            Text('Meldestelle', style: Theme.of(context).textTheme.titleSmall),
            const Text(
                'myhome-messenger auf demselben Host. Leerer Port = kein '
                'zweiter Reiter.',
                style: TextStyle(color: Colors.white54, fontSize: 12)),
            TextFormField(
              controller: _messengerPort,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: 'Port',
                hintText: '${Settings.defaultMessengerPort}',
              ),
              validator: (v) {
                final text = v?.trim() ?? '';
                if (text.isEmpty) return null;
                final n = int.tryParse(text);
                return (n == null || n < 1 || n > 65535)
                    ? 'Port 1–65535 oder leer'
                    : null;
              },
            ),
            TextFormField(
              controller: _messengerUser,
              autocorrect: false,
              decoration: const InputDecoration(
                labelText: 'Benutzer',
                hintText: 'MYHOME_WEB_USER',
              ),
            ),
            TextFormField(
              controller: _messengerPassword,
              obscureText: !_showPassword,
              autocorrect: false,
              decoration: const InputDecoration(labelText: 'Passwort'),
            ),
            const SizedBox(height: 24),
            FilledButton(onPressed: _save, child: const Text('Speichern')),
          ],
        ),
      ),
    );
  }
}
