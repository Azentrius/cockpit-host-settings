# Contributing

Bug reports, ideas and pull requests for Host Settings are welcome. Security problems do not
belong in public issues: report them as described in [SECURITY.md](SECURITY.md). For a larger
change, open an issue first so we can agree on the approach.

## Setup

```bash
git clone https://github.com/Azentrius/cockpit-host-settings.git
cd cockpit-host-settings
(cd test && npm ci)                    # jsdom for the page tests
pip install -r test/requirements.txt   # pinned check tools: shellcheck, ruff, playwright
```

- The helpers need **Python ≥ 3.8, standard library only**. `ruff` checks Python 3.8 syntax.
- The page is plain JavaScript without a build step.
- Node.js and npm (CI uses Node 22) are needed for the tests only.

## Tests and checks

```bash
make check      # every offline test: the notifier and the helper with stand-ins, the page under jsdom, design, version
make lint       # shellcheck, ruff, systemd-analyze for the units, as in CI
make packages   # .deb and .rpm in dist/ (needs dpkg-deb and rpmbuild)
python3 tools/preview/build.py   # the page with made-up data: tools/preview/out/host-settings/index.html?scenario=ready&lang=en
python3 tools/preview/shoot.py   # screenshots to docs/img (needs Playwright with Chromium)
```

The offline tests need no root and no network: the notifier reads Status Board's own test answers
in `test/fixtures/board` (pinned in `tools/board-fixtures.sha256`), recorded answers of an ntfy
server and a stand-in for SMTP. The `test` workflow runs the lint, the tests and the package build
on every pull request and every push to `main`; the job `e2e` runs the package with its real units against a real
ntfy server and postfix (`test/e2e/run.sh`, only on a throw-away machine), and the job `e2e-el`
runs the same test with the `.rpm` in Rocky Linux 9 and 10 VMs with SELinux enforcing and fails on
any SELinux denial (`tools/vm.sh`, CI only: it needs KVM).

The preview (`tools/preview/`) shows the page with a stand-in for Cockpit and made-up data, in the
scenarios `fresh` (nothing set up), `ready` (ntfy and e-mail, checked), `open` (a topic open
without a login) and `nomta` (no mail server); add `lang=de` for German and `admin=0` for the page
without administrative access. In CI, `shoot.py` opens every scenario in both themes and both
languages, fails on a JavaScript error or on sideways scrolling at phone width, and keeps the
screenshots as the artifact `screenshots`. The pictures in `docs/img` are taken from there; a
change to the page that shows in them takes new ones.

## How the add-on is built

- **The notifier never runs as root.** It runs as the system user `cockpit-host-settings` and reads
  only what the add-ons' status verbs say, which work without root by the status contract.
- **The token has one way.** The page hands it to the helper on standard input; the helper writes
  `/etc/cockpit-host-settings/ntfy.token` (root, 0600); systemd hands it to the notifier as a
  credential; it leaves the host only as the `Authorization` header. Never an argument, a file of
  the state, the page or the journal.
- **Root code only in `lib/`**, installed to `/usr/lib/cockpit-host-settings` (root-owned).
  Nothing is ever run or imported from a directory others can write; a status command is run only
  when it and its folders are root's and nobody else can change them.
- **Fixed verbs.** The page calls `host-settings.py` only with fixed verbs; every argument is
  checked there. Every change goes through the dialog. New verbs go into `docs/INTERNALS.md`.
- **Judged as Status Board judges.** The rules for status answers (`lib/hslib.py`) follow the
  board's `src/lib/status.js`; `test/notify_test.py` holds them against the board's own fixtures.
  When the board's rules change, take its fixtures again and adjust both.
- **No ntfy server.** Host Settings shows what the server needs for this host and checks the
  result; it does not install, configure or run the server.
- **Formats are contracts.** `notify.conf`, `last.json`, `status.json` and the verbs' JSON change
  together with the page, the tests and `docs/INTERNALS.md`.
- **No build step, no runtime dependencies.** Python standard library, POSIX `sh`, JavaScript without
  a framework. `node_modules` is for the tests only.
- **Browser without network.** No CDNs, external fonts or images; Cockpit's strict CSP stays.
- **Shared design files** (`plugin/azentrius.*`, `plugin/icons.js`, the fonts) are byte-identical
  copies from other Azentrius add-ons, pinned in `tools/design-files.sha256`. Do not edit them here.

## Requirements for a change

- **English and German.** Every user-facing text goes into both languages of `plugin/i18n.js`; the
  texts of messages into both of `lib/hsnotify.py`; the status texts into both of
  `lib/hsstatus.py`; the messages of `host-settings.py` through `say(de, en)`. German addresses the
  user formally ("Sie"). The tests check that the English page contains no German.
- **English in the repository.** Code, comments, docs, commit messages and pull requests are in
  English; the only German is the translation and the tests that check it.
- **Tests** for new behaviour; `make check` and `make lint` green.
- **SPDX header** `SPDX-License-Identifier: Apache-2.0` in every new source file.
- **No private data.** No secrets, tokens, topics, host names, public addresses other than the
  documentation ranges, e-mail addresses or other real server data in issues, pull requests,
  tests or fixtures.

## Pull requests

- Open them against `main`. `main` is protected: every change goes through a pull request and is
  squash-merged once the checks are green.
- The title becomes the commit on `main`: short, imperative, English, without a version number.
- Keep one topic per pull request and fill in the template: what and why, how it was tested.

## License

Host Settings is licensed under the [Apache License 2.0](LICENSE); contributions are accepted under
the same license. Third-party material keeps its own license and is listed in [NOTICE](NOTICE).
