# Contributing

Bug reports and small, focused pull requests are welcome. Include your macOS
version, CPU architecture, Camoufox library/browser versions, and reproduction
steps. Use a temporary profile when reproducing issues; do not attach cookies,
personal session directories, or credentials.

The launcher currently targets macOS 14+. Keep session data separate from the app
bundle and preserve profile isolation, graceful shutdown, and system proxy behavior.
Session deletion must remain limited to closed, archived sessions with confirmation.

For changes to the Swift session store, run `tests/test_deletion.py`. For browser
lifecycle or tab restoration changes, build the app and run `tests/test_sessions.py`.
The README includes the commands. Do not commit build products or test data.
