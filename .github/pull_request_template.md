<!--
Title: short, imperative, English, without a version number. It becomes the commit on main.
Security problems: do not open a pull request, see SECURITY.md.
-->

## What and why

<!-- What does this change, and why? Link the issue if there is one. -->

## How tested

- [ ] `make check`
- [ ] `make lint`
- [ ] The page in the preview (`python3 tools/preview/build.py`) or in Cockpit (when `plugin/` changed); new screenshots in `docs/img` when the page looks different
- [ ] On a throw-away machine or in the job `e2e` (when the notifier, the units or the packaging changed)

<!-- Anything else you tried, and on which system. -->

## Checklist

- [ ] Every new or changed text exists in English and German (`plugin/i18n.js`, the `say(de, en)` messages, `lib/hsnotify.py`, `lib/hsstatus.py`)
- [ ] Changed formats (`last.json`, `status.json`, `notify.conf`, the verbs' JSON) are changed together with the page, the tests and `docs/INTERNALS.md`
- [ ] The notifier still runs without root, the token still goes only through `LoadCredential=` and standard input
- [ ] The rules for status answers still match Status Board's (`test/fixtures/board`, `tools/board-fixtures.sha256`)
- [ ] No secrets, tokens or real server data (host names, IP addresses, credentials) in code, tests, fixtures or this description
- [ ] SPDX header on every new source file
