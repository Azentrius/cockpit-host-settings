// SPDX-License-Identifier: Apache-2.0
/* The texts of the page (plugin/i18n.js), without a browser:
 *   - every key in English and German, with the same placeholders and plural shape,
 *   - every key settings.js and menu-status.js ask for exists (t("…"), and the families
 *     settings.js builds: step., why., reason., …),
 *   - no German in English, German with "Sie", no long dash (U+2014) anywhere.
 * Run with: node test/i18n.test.js */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const PLUGIN = path.join(__dirname, "..", "plugin");
const box = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(PLUGIN, "i18n.js"), "utf8"), box);
const { en, de } = box.window.HostSettingsI18n;
const js = ["settings.js", "menu-status.js"].map((f) => fs.readFileSync(path.join(PLUGIN, f), "utf8")).join("\n");
const html = fs.readFileSync(path.join(PLUGIN, "index.html"), "utf8");

let passed = 0;
function t(name, fn) {
    try { fn(); passed++; } catch (e) { console.error("FAIL", name, "\n ", e.stack || e.message); process.exitCode = 1; }
}
const vars = (s) => (typeof s === "object" ? [s.one, s.other] : [s]).map((x) => (x.match(/\{\w+\}/g) || []).sort().join());
const texts = (o) => Object.values(o).flatMap((s) => (typeof s === "object" ? [s.one, s.other] : [s]));

t("the same keys in both languages", () => {
    assert.deepStrictEqual(Object.keys(de).sort(), Object.keys(en).sort());
});

t("the same placeholders and plural shape", () => {
    Object.keys(en).forEach((k) => {
        assert.strictEqual(typeof de[k], typeof en[k], k);
        assert.deepStrictEqual(vars(de[k]), vars(en[k]), k);
        [en[k], de[k]].forEach((s) => assert.strictEqual(((typeof s === "object" ? s.other : s).match(/`/g) || []).length % 2, 0,
            "backticks in pairs: " + k));
    });
});

t("every key the page asks for exists", () => {
    const used = new Set([...js.matchAll(/\bt\("([a-zA-Z0-9.-]+)"/g), ...js.matchAll(/\btr\("([a-zA-Z0-9.-]+)"/g),
                          ...html.matchAll(/data-i18n(?:-aria)?="([^"]+)"/g)].map((m) => m[1]));
    used.forEach((k) => assert.ok(k.endsWith(".") || k in en, "missing: " + k));
    const stepKeys = js.match(/const STEP_KEYS = \[([^\]]*)\]/)[1].match(/"([^"]+)"/g).map((s) => s.slice(1, -1));
    const families = {
        "label.": ["none", "ok", "info", "warning", "danger", "stale"],
        "channel.": ["ntfy", "mail"],
        "lang.": ["en", "de"],
        "access.": ["ok", "warning", "none"],
        "step.": ["reach", "tls", "publish", "anon-read", "anon-write", "token", "other", "skipped.no-token", "skipped.unreachable",
                  "skipped.other", "token.info.can-read", "token.info.admin", "token.info.unknown"].concat(stepKeys.map((k) => k.slice(5))),
        "why.": ["not-json", "not-object", "not-text", "too-large", "schema", "addon", "addon-mismatch", "state", "items",
                 "rejected", "untrusted", "manifest", "missing", "failed", "timeout", "unsupported"],
        "reason.": JSON.parse(js.match(/const REASONS = (\[[^\]]*\])/)[1]).concat(["http"]),
        "limits.": ["contract", "offline", "content", "mail", "server"],
        "mail.": ["noMta", "sendmailOnly", "testOk", "testFailed", "testOkNow", "testFailedNow"],
        "chan.ok.": ["run", "test"],
        "chan.again.": ["run", "test"],
        "btn.": ["refresh", "loading"],
        "cov.": JSON.parse(js.match(/const COVERAGES = (\[[^\]]*\])/)[1]),
        "role.": ["web", "database", "build", "bastion"],
        "beat.reason.": JSON.parse(js.match(/const BEAT_REASONS = (\[[^\]]*\])/)[1]),
        "menu.section.": JSON.parse(js.match(/const MENU_SECTIONS = (\[[^\]]*\])/)[1]),
        "menu.scope.": JSON.parse(js.match(/const MENU_SCOPES = (\[[^\]]*\])/)[1]),
        "menu.err.": JSON.parse(js.match(/const MENU_FILE_ERRORS = (\[[^\]]*\])/)[1]),
        "menu.aria.": ["up", "down"],
        "menu.btn.": ["up", "down"],
    };
    Object.entries(families).forEach(([prefix, keys]) => keys.forEach((k) => assert.ok(prefix + k in en, "missing: " + prefix + k)));
});

t("no German in English, Sie in German", () => {
    const german = /[äöüßÄÖÜ„“]|\b(und|nicht|oder|der|die|das|Sie|Ihre?)\b/;
    texts(en).forEach((s) => assert.ok(!german.test(s.replace(/„|“/g, "")) || /“[A-Z][^”]*”/.test(s), "German in English: " + s));
    texts(de).forEach((s) => assert.ok(!/\b(du|dein|deine|dich|dir)\b/i.test(s), "du in German: " + s));
});

t("no long dash", () => {
    const dash = String.fromCharCode(0x2014);
    texts(en).concat(texts(de)).forEach((s) => assert.ok(!s.includes(dash), s));
    [js, html].forEach((f) => assert.ok(!f.includes(dash)));
});

console.log(process.exitCode ? "THERE ARE FAILURES" : `all ${passed} passed`);
