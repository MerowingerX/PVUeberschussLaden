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
  late bool _https = widget.initial.https;
  bool _showPassword = false;

  @override
  void dispose() {
    _host.dispose();
    _port.dispose();
    _user.dispose();
    _password.dispose();
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
            const SizedBox(height: 24),
            FilledButton(onPressed: _save, child: const Text('Speichern')),
          ],
        ),
      ),
    );
  }
}
