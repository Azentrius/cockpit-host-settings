// SPDX-License-Identifier: Apache-2.0
/* The page under jsdom with a scripted Cockpit (test/page.js): what it shows on a fresh server
 * and on a set-up one, in English and German; that data from the server stays text; every change
 * goes through the dialog with the right fixed verb; the token goes on standard input only; the
 * menu icon; the summary without administrative access.
 * Run with: node test/page.test.js (jsdom from test/node_modules or NODE_PATH) */
"use strict";
const assert = require("assert");
const P = require("./page.js");
const { boot, settle, text, fresh, ready, HELPER, TOKEN, EVIL } = P;

let passed = 0;
const tests = [];
function t(name, fn) { tests.push([name, fn]); }

async function open(opts) {
    const page = boot(opts);
    await settle();
    return page;
}
function click(page, id) {
    const el = page.d.getElementById(id);
    assert.ok(el, "no element #" + id);
    el.dispatchEvent(new page.w.MouseEvent("click", { bubbles: true }));
}
// Arrays made by the page belong to the window's realm; JSON makes them plain for deepStrictEqual.
const plain = (v) => JSON.parse(JSON.stringify(v));
function helperCalls(page) {
    return plain(page.calls.spawn.filter((c) => c.argv[0] === HELPER).map((c) => [c.argv.slice(1).join(" "), c.opts.superuser]));
}
function changes(page) {
    return plain(page.calls.spawn.filter((c) => c.opts.superuser === "require").map((c) => c.argv.slice(1)));
}

t("a fresh server: nothing set up, the way to set it up", async () => {
    const page = await open({ view: fresh() });
    const d = page.d;
    assert.deepStrictEqual(helperCalls(page), [["show", "try"]]);
    assert.ok(text(d.getElementById("hs-overview")).includes("No channel is set up yet."));
    assert.ok(d.getElementById("hs-timer-on").disabled, "the timer needs a channel first");
    assert.ok(text(d.getElementById("hs-overview")).includes("Set up a channel first."));
    assert.ok(d.getElementById("hs-ntfy-set"));
    assert.ok(d.getElementById("hs-mail-set"));
    assert.ok(text(d.getElementById("hs-mail")).includes("No mail server accepts mail on localhost:25"));
    assert.ok(text(d.getElementById("hs-addons")).includes("Not read yet"));
    assert.ok(d.getElementById("hs-loading").hidden);
    assert.strictEqual(d.getElementById("hs-meta").textContent, "Not run yet");
    assert.ok(d.getElementById("hs-tabs").hidden, "one tab: no tab bar");
    assert.strictEqual(d.getElementById("hs-sig-version").textContent, "Host Settings 0.1.0");
});

t("a set-up server: channels, timer, last run and message, the check, the commands", async () => {
    const page = await open();
    const d = page.d;
    const ov = text(d.getElementById("hs-overview"));
    assert.ok(ov.includes("Messages about the add-ons of this server go to ntfy and e-mail"), ov);
    assert.ok(ov.includes("Every 5 minutes, next at"), ov);
    assert.ok(ov.includes("5 minutes ago: 3 add-ons read"), ov);
    assert.ok(ov.includes("35 minutes ago: server: Cron Jobs, Failed runs"), ov);
    assert.ok(ov.includes("ntfy: sent") && ov.includes("e-mail: sent"), ov);
    const ntfy = text(d.getElementById("hs-ntfy"));
    ["https://ntfy.example.com", "cockpit-server", "Set (readable by root only)", "Closed", "Last check",
     "Test message with the token", "The token may write to the topic and nothing more."].forEach((s) => assert.ok(ntfy.includes(s), s));
    const commands = d.getElementById("hs-commands").textContent;
    assert.strictEqual(commands, "ntfy user add cockpit-server\nntfy access cockpit-server cockpit-server write-only\n" +
        "ntfy token add --label=\"Host Settings server\" cockpit-server");
    assert.ok(ntfy.includes("ntfy access READER 'cockpit-*' read-only"), "one pattern for every server's topic");
    assert.ok(ntfy.includes("auth-default-access: deny-all"));
    const docs = d.querySelector("#hs-ntfy a[href]");
    assert.strictEqual(docs.getAttribute("href"), "https://docs.ntfy.sh/install/");
    assert.strictEqual(docs.getAttribute("rel"), "noopener noreferrer");
    const mail = text(d.getElementById("hs-mail"));
    assert.ok(mail.includes("A mail server accepts mail on localhost:25.") && mail.includes("ops@example.com"), mail);
    assert.ok(mail.includes("Test message handed over"), mail);
    const addons = text(d.getElementById("hs-addons"));
    assert.ok(addons.includes("Cron Jobs") && addons.includes("Failed runs (2)"), addons);
    assert.ok(addons.includes("Status not readable: the answer is not JSON"), addons);
    assert.ok(addons.includes("Out of date Old"), addons);
    assert.ok(!addons.includes("cron "), "OK items are not listed");
    assert.strictEqual(d.getElementById("hs-meta").textContent, "Last run 5 minutes ago");
});

t("data from the server stays text", async () => {
    const page = await open();
    const d = page.d;
    assert.strictEqual(d.querySelectorAll("#hs-addons img").length, 0);
    assert.ok(text(d.getElementById("hs-addons")).includes(EVIL));
    assert.strictEqual(page.w.__pwned, undefined);
    const summary = await open({ admin: false });
    assert.strictEqual(summary.d.querySelectorAll("img").length, 0);
    assert.ok(text(summary.d.getElementById("hs-summary")).includes(EVIL));
});

t("the check is shown only for the server and topic set now", async () => {
    const view = ready();
    view.config.ntfy_topic = "other";
    const page = await open({ view });
    assert.ok(!text(page.d.getElementById("hs-ntfy")).includes("Last check"));
});

t("an open topic and a message that did not go out are warnings", async () => {
    const view = ready();
    view.last.access = { state: "warning", anon_read: "warning", anon_write: "ok", target: "https://ntfy.example.com/cockpit-server" };
    let page = await open({ view });
    assert.ok(text(page.d.getElementById("hs-overview")).includes("the ntfy topic is open to anyone without a login"));
    assert.ok(text(page.d.querySelector("#hs-ntfy .az-card__header")).includes("Topic open"));
    const failed = ready();
    failed.last.delivery.results.ntfy = { ok: false, code: 403, reason: "rejected" };
    page = await open({ view: failed });
    assert.ok(text(page.d.getElementById("hs-overview")).includes("ntfy: refused (token or rights)"));
    assert.strictEqual(page.d.querySelector("#hs-overview .az-card__header .az-label").dataset.state, "warning");
});

t("set up ntfy: the dialog checks first, then calls set ntfy", async () => {
    const page = await open({ view: fresh() });
    const d = page.d;
    click(page, "hs-ntfy-set");
    assert.ok(d.getElementById("hs-dialog").hasAttribute("open"));
    assert.strictEqual(d.getElementById("hs-f-topic").value, "cockpit-server", "the topic from the host name");
    d.getElementById("hs-f-url").value = "https://user:pw@ntfy.example.com";
    click(page, "hs-dialog-confirm");
    await settle();
    assert.ok(!d.getElementById("hs-dialog-error").hidden);
    assert.deepStrictEqual(changes(page), [], "nothing runs with a bad address");
    d.getElementById("hs-f-url").value = "https://ntfy.example.com/";
    click(page, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(page), [["set", "ntfy", "https://ntfy.example.com", "cockpit-server"]]);
    assert.strictEqual(d.getElementById("hs-dialog-result").textContent, "Done.");
    assert.strictEqual(d.getElementById("hs-dialog-cancel").textContent, "Close");
    assert.deepStrictEqual(helperCalls(page).filter(([v]) => v === "show").length, 2, "loaded again after the change");
});

t("the token goes on standard input, never in an argument", async () => {
    const page = await open();
    const d = page.d;
    click(page, "hs-token-set");
    const field = d.getElementById("hs-f-token");
    assert.strictEqual(field.getAttribute("type"), "password");
    field.value = "two words";
    click(page, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(page), []);
    field.value = TOKEN;
    click(page, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(page), [["token", "set"]]);
    assert.deepStrictEqual(page.calls.inputs, [TOKEN + "\n"]);
    assert.ok(!JSON.stringify(page.calls.spawn).includes(TOKEN));
    assert.strictEqual(field.value, "", "cleared once sent");
    assert.ok(!d.body.textContent.includes(TOKEN));
});

t("check connection: the outcome from the RESULT line, not in the log", async () => {
    const result = { at: P.NOW, channel: "ntfy", target: "https://ntfy.example.com/cockpit-server", steps: [
        { id: "reach", result: "ok", code: 200, reason: "answered" },
        { id: "anon-write", result: "warning", code: 200, reason: "writable" },
        { id: "token", result: "info", code: 200, reason: "can-read" }] };
    const page = await open({
        onSpawn: (verb) => (verb === "check ntfy"
            ? P.proc("", { chunks: ["Checking the connection to the ntfy server …\nRESULT " + JSON.stringify(result) + "\n"] }) : null),
    });
    const d = page.d;
    click(page, "hs-ntfy-check");
    assert.ok(text(d.getElementById("hs-dialog-desc")).includes("that try arrives there as a warning"));
    click(page, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(page), [["check", "ntfy"]]);
    assert.ok(!d.getElementById("hs-dialog-log").textContent.includes("RESULT"));
    const outcome = text(d.getElementById("hs-dialog-outcome"));
    assert.ok(outcome.includes("Open: anyone who knows the topic can send messages to it"), outcome);
    assert.ok(outcome.includes("The token may read the topic as well"), outcome);
});

t("every button calls its fixed verb", async () => {
    const want = {
        "hs-ntfy-remove": ["unset", "ntfy"], "hs-token-remove": ["token", "remove"], "hs-mail-remove": ["unset", "mail"],
        "hs-mail-test": ["test", "mail"], "hs-timer-off": ["remove", "timer"], "hs-run": ["run"],
    };
    for (const [id, argv] of Object.entries(want)) {
        const page = await open();
        click(page, id);
        click(page, "hs-dialog-confirm");
        await settle();
        assert.deepStrictEqual(changes(page), [argv], id);
        assert.ok(page.calls.spawn.filter((c) => c.opts.superuser === "require").every((c) => c.opts.err === "out"));
    }
    const off = await open({ view: ready({ timer: "inactive" }) });
    click(off, "hs-timer-on");
    click(off, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(off), [["install", "timer"]]);
    const lang = await open();
    click(lang, "hs-lang");
    lang.d.getElementById("hs-f-lang").value = "de";
    click(lang, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(lang), [["set", "language", "de"]]);
    const mail = await open({ view: fresh() });
    click(mail, "hs-mail-set");
    mail.d.getElementById("hs-f-to").value = "not an address";
    click(mail, "hs-dialog-confirm");
    mail.d.getElementById("hs-f-to").value = "ops@example.com";
    click(mail, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(mail), [["set", "mail", "ops@example.com"]]);
});

t("a failing change says why", async () => {
    const page = await open({ onSpawn: (verb) => (verb === "run" ? P.proc("", { fail: { exit_status: 75 } }) : null) });
    click(page, "hs-run");
    click(page, "hs-dialog-confirm");
    await settle();
    assert.ok(page.d.getElementById("hs-dialog-result").textContent.startsWith("Busy"));
    const denied = await open({ onSpawn: (verb) => (verb === "run" ? P.proc("", { fail: { problem: "access-denied" } }) : null) });
    click(denied, "hs-run");
    click(denied, "hs-dialog-confirm");
    await settle();
    assert.strictEqual(denied.d.getElementById("hs-dialog-result").textContent, "Administrative access is needed for this.");
});

t("without administrative access: the summary", async () => {
    const page = await open({ admin: false });
    const d = page.d;
    assert.deepStrictEqual(helperCalls(page), [["show", "try"], ["status --lang en", "try"]]);
    assert.ok(!d.getElementById("hs-summary").hidden);
    assert.ok(d.getElementById("hs-tab-notifications").hidden);
    assert.ok(text(d.getElementById("hs-status")).includes("Shown without administrative access"));
    assert.ok(text(d.getElementById("hs-summary")).includes("Needs attention: Last message."));
});

t("German", async () => {
    const page = await open({ language: "de" });
    const d = page.d;
    assert.strictEqual(d.documentElement.lang, "de");
    assert.strictEqual(d.getElementById("hs-refresh").textContent.trim(), "Aktualisieren");
    const ov = text(d.getElementById("hs-overview"));
    assert.ok(ov.includes("Nachrichten über die Add-ons dieses Servers gehen an ntfy und E-Mail"), ov);
    assert.ok(text(d.getElementById("hs-ntfy")).includes("ntfy access LESER 'cockpit-*' read-only"));
    assert.ok(text(d.getElementById("hs-addons")).includes("Status nicht lesbar: die Antwort ist kein JSON"));
    const sum = await open({ admin: false, language: "de" });
    assert.deepStrictEqual(helperCalls(sum).slice(-1), [["status --lang de", "try"]]);
    assert.ok(sum.calls.spawn.every((c) => c.opts.environ.includes("HOST_SETTINGS_LANG=de")));
});

t("bad lines of the settings file and missing units are named", async () => {
    const page = await open({ view: ready({ bad_lines: [3, 7], timer: "missing" }) });
    const st = text(page.d.getElementById("hs-status"));
    assert.ok(st.includes("Lines 3, 7 of /etc/cockpit-host-settings/notify.conf could not be read"), st);
    assert.ok(st.includes("cockpit-host-settings-notify.timer was not found"), st);
    assert.ok(page.d.getElementById("hs-run").disabled);
});

t("the menu icon: hidden at first, the summary of status.json, nothing from the helper", async () => {
    const status = JSON.stringify(Object.assign(P.statusDoc("en"), { i18n: { de: { summary: "Braucht Aufmerksamkeit." } } }));
    const page = boot({ hidden: true, statusFile: status });
    await settle();
    assert.deepStrictEqual(page.calls.spawn, [], "nothing runs while hidden");
    assert.deepStrictEqual(page.calls.notes, [["notify", { page_status: { type: "warning", title: "Needs attention: Last message." } }]]);
    page.hint(false);
    await settle();
    assert.deepStrictEqual(helperCalls(page), [["show", "try"]]);
    const de = boot({ hidden: true, language: "de", statusFile: status });
    await settle();
    assert.strictEqual(de.calls.notes[0][1].page_status.title, "Braucht Aufmerksamkeit.");
    const ms = de.w.HostSettingsMenuStatus;
    const old = ms.statusOf(JSON.parse(status), Date.parse("2026-10-19T20:00:00Z"));
    assert.strictEqual(old.type, "warning");
    assert.ok(old.title.includes("veraltet"));
    const okDoc = Object.assign(P.statusDoc("en"), { state: "ok", items: [{ id: "run", state: "ok", title: "x", checked_at: "2026-10-19T19:00:00Z", stale_after: 900 }] });
    assert.strictEqual(ms.statusOf(okDoc, Date.parse("2026-10-19T19:01:00Z")), null, "OK: no icon");
    assert.strictEqual(ms.statusOf(okDoc, Date.parse("2026-10-19T19:30:00Z")).type, "warning", "out of date: a warning");
    assert.strictEqual(ms.statusOf({ schema: 2, items: [] }, 0), null);
});

t("a link to the tab follows the location", async () => {
    const page = await open({ location: ["notifications"] });
    assert.ok(!page.d.getElementById("hs-tab-notifications").hidden);
});

(async () => {
    for (const [name, fn] of tests) {
        try {
            await fn();
            passed++;
        } catch (e) {
            console.error("FAIL", name, "\n ", e.stack || e.message);
            process.exitCode = 1;
        }
    }
    console.log(process.exitCode ? "THERE ARE FAILURES" : `all ${passed} passed`);
})();
