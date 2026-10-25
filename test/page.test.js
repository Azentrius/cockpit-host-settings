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

// ---------- the target profile ----------

function withProfile(extra) {
    const view = ready(extra);
    view.profile = P.webProfile();
    view.last.coverage = P.COVERAGE;
    return view;
}

t("the target profile: roles, what is expected and why, its coverage, the checks left out", async () => {
    const page = await open({ view: withProfile() });
    const d = page.d;
    const card = d.getElementById("hs-profile");
    assert.ok(card, "a section on the Notifications tab");
    assert.ok(d.getElementById("hs-tab-notifications").contains(card));
    const all = text(card);
    ["Target profile", "Roles web", "Expected", "Every server", "Role web", "Added here", "Security Monitor: exposure",
     "Image Update Monitor", "Not installed", "Failed", "Container hardening", "Left out on purpose", "network data off on purpose",
     "Until 31 Dec 2026", "CrowdSec instead", "Ended on 1 Oct 2026, counts again"].forEach((s) => assert.ok(all.includes(s), s + " in: " + all));
    assert.strictEqual(card.querySelector(".az-card__header .az-label").dataset.state, "warning");
    assert.ok(text(card.querySelector(".az-card__header")).includes("2 gaps"), "security failed, image-updates not installed");
    // only the extra expectation can be removed here; every skip can
    assert.deepStrictEqual(plain([...card.querySelectorAll("[data-action]")].map((b) => [b.dataset.action, b.dataset.value])),
        [["expect-remove", "security/exposure"], ["skip-remove", "update-gaps/cve"], ["skip-remove", "security/fail2ban"]]);
    ["hs-profile-roles", "hs-profile-expect-add", "hs-profile-skip-add", "hs-profile-remove"].forEach((id) => assert.ok(d.getElementById(id), id));
    // before the notifier ran with the profile, nothing is claimed about its coverage
    const early = withProfile();
    early.last.coverage = null;
    const before = await open({ view: early });
    assert.ok(text(before.d.getElementById("hs-profile")).includes("Not read yet"));
    assert.strictEqual(before.d.querySelector("#hs-profile .az-card__header .az-label").dataset.state, "ok");
    // German
    const de = await open({ view: withProfile(), language: "de" });
    const deText = text(de.d.getElementById("hs-profile"));
    ["Sollprofil", "Jeder Server", "Hier hinzugefügt", "Nicht installiert", "Bewusst ausgelassen", "Endete am"].forEach((s) =>
        assert.ok(deText.includes(s), s + " in: " + deText));
});

t("no profile yet: what it is, and a dialog with a switch per role", async () => {
    const page = await open({ view: fresh() });
    const d = page.d;
    const card = text(d.getElementById("hs-profile"));
    assert.ok(card.includes("No profile") && card.includes("Without a profile, every installed add-on counts as expected."), card);
    click(page, "hs-profile-roles");
    assert.ok(d.getElementById("hs-dialog").hasAttribute("open"));
    assert.strictEqual(d.getElementById("hs-dialog-title").textContent, "Set up the target profile");
    const switches = [...d.querySelectorAll("#hs-f-roles input[type=checkbox]")];
    assert.deepStrictEqual(plain(switches.map((s) => [s.value, s.checked])), [["web", false], ["database", false], ["build", false], ["bastion", false]]);
    const fields = text(d.getElementById("hs-dialog-fields"));
    assert.ok(fields.includes("Adds Image Update Monitor.") && fields.includes("Adds nothing that reports a status."), fields);
    assert.deepStrictEqual(changes(page), [], "nothing runs before the dialog is confirmed");
    switches[0].checked = true;
    switches[3].checked = true;
    click(page, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(page), [["profile", "roles", "web", "bastion"]]);
    assert.ok(page.calls.spawn.filter((c) => c.opts.superuser === "require").every((c) => c.opts.err === "out"));
    // changing the roles starts from those set; none switched on is the add-ons of every server
    const set = await open({ view: withProfile() });
    click(set, "hs-profile-roles");
    assert.strictEqual(set.d.getElementById("hs-dialog-title").textContent, "Change the roles");
    const on = [...set.d.querySelectorAll("#hs-f-roles input[type=checkbox]")];
    assert.deepStrictEqual(plain(on.map((s) => s.checked)), [true, false, false, false]);
    on[0].checked = false;
    click(set, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(set), [["profile", "roles"]]);
});

t("every change of the profile goes through the dialog, with its fixed verb", async () => {
    const page = await open({ view: withProfile() });
    const d = page.d;
    click(page, "hs-profile-expect-add");
    d.getElementById("hs-f-item").value = "Security Monitor";
    click(page, "hs-dialog-confirm");
    await settle();
    assert.ok(!d.getElementById("hs-dialog-error").hidden);
    assert.deepStrictEqual(changes(page), [], "a bad name runs nothing");
    d.getElementById("hs-f-item").value = "update-gaps/held";
    click(page, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(page), [["profile", "expect", "add", "update-gaps/held"]]);

    const skip = await open({ view: withProfile() });
    click(skip, "hs-profile-skip-add");
    const sd = skip.d;
    assert.ok(text(sd.getElementById("hs-dialog-fields")).includes("An add-on alone cannot be left out."));
    sd.getElementById("hs-f-check").value = "security";
    sd.getElementById("hs-f-reason").value = "fail2ban instead";
    click(skip, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(skip), [], "an add-on alone cannot be left out");
    assert.ok(text(sd.getElementById("hs-dialog-error")).includes("as add-on/check (update-gaps/cve)"));
    sd.getElementById("hs-f-check").value = "security/crowdsec";
    sd.getElementById("hs-f-until").value = "2026-10-19";
    sd.getElementById("hs-f-reason").value = "fail2ban instead";
    click(skip, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(skip), [], "an end that is not after today runs nothing");
    sd.getElementById("hs-f-until").value = "";
    sd.getElementById("hs-f-reason").value = "";
    click(skip, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(skip), [], "a skip needs a reason");
    sd.getElementById("hs-f-until").value = "2026-12-01";
    sd.getElementById("hs-f-reason").value = "fail2ban instead";
    click(skip, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(skip), [["profile", "skip", "add", "security/crowdsec", "2026-12-01"]]);
    assert.deepStrictEqual(skip.calls.inputs, ["fail2ban instead\n"], "the reason on standard input");
    assert.ok(!JSON.stringify(skip.calls.spawn.map((c) => c.argv)).includes("fail2ban instead"), "never in an argument");

    const rows = { "expect-remove": ["profile", "expect", "remove", "security/exposure"],
                   "skip-remove": ["profile", "skip", "remove", "update-gaps/cve"] };
    for (const [action, argv] of Object.entries(rows)) {
        const row = await open({ view: withProfile() });
        row.d.querySelector(`#hs-profile [data-action="${action}"]`).dispatchEvent(new row.w.MouseEvent("click", { bubbles: true }));
        assert.ok(row.d.getElementById("hs-dialog").hasAttribute("open"), action);
        assert.ok(text(row.d.getElementById("hs-dialog-desc")).includes(argv[3]), action);
        assert.deepStrictEqual(changes(row), [], action + ": nothing before the dialog is confirmed");
        click(row, "hs-dialog-confirm");
        await settle();
        assert.deepStrictEqual(changes(row), [argv], action);
    }
    const remove = await open({ view: withProfile() });
    click(remove, "hs-profile-remove");
    assert.ok(text(remove.d.getElementById("hs-dialog-desc")).includes("profile.conf.bak"));
    click(remove, "hs-dialog-confirm");
    await settle();
    assert.deepStrictEqual(changes(remove), [["profile", "remove"]]);
    assert.strictEqual(helperCalls(remove).filter(([v]) => v === "show").length, 2, "loaded again after the change");
});

t("without administrative access: the profile from the status, and no button to change it", async () => {
    const doc = Object.assign(P.statusDoc("en"), { features: ["profile"], profile: {
        roles: ["web"], expect: ["host-settings", "update-gaps", "security", "cronjobs", "image-updates"],
        skip: [{ check: "update-gaps/cve", reason: EVIL }], set_at: "2026-10-19T09:00:00Z" } });
    const page = await open({ admin: false, onSpawn: (verb) => (verb.startsWith("status") ? P.proc(JSON.stringify(doc)) : null) });
    const d = page.d;
    assert.strictEqual(d.getElementById("hs-sum-profile").textContent, "Target profile: web; 5 add-ons and checks expected.");
    assert.strictEqual(d.getElementById("hs-profile"), null, "the section needs administrative access");
    assert.strictEqual(d.querySelectorAll("[data-action], [id^=hs-profile-]").length, 0, "no button to change it");
    assert.deepStrictEqual([...d.querySelectorAll("button")].filter((b) => !b.closest("dialog, [hidden]")).map((b) => b.id), ["hs-refresh"],
        "only Refresh is shown");
    const none = await open({ admin: false });
    assert.strictEqual(none.d.getElementById("hs-sum-profile").textContent, "No target profile.");
    const de = await open({ admin: false, language: "de", onSpawn: (verb) => (verb.startsWith("status") ? P.proc(JSON.stringify(doc)) : null) });
    assert.strictEqual(de.d.getElementById("hs-sum-profile").textContent, "Sollprofil: web; 5 Add-ons und Prüfungen erwartet.");
});

t("the profile's texts stay text, and its bad lines are named", async () => {
    const view = withProfile();
    view.profile.skip[0].reason = EVIL;
    view.profile.bad_lines = [2, 5];
    const page = await open({ view });
    assert.strictEqual(page.d.querySelectorAll("#hs-profile img").length, 0);
    assert.ok(text(page.d.getElementById("hs-profile")).includes(EVIL));
    assert.strictEqual(page.w.__pwned, undefined);
    const st = text(page.d.getElementById("hs-status"));
    assert.ok(st.includes("Lines 2, 5 of /etc/cockpit-host-settings/profile.conf could not be read"), st);
    const broken = await open({ view: ready({ profile: P.noProfile({ unreadable: true }) }) });
    assert.ok(text(broken.d.getElementById("hs-status")).includes("The profile could not be read"));
});

// A test that waits forever fails with its name instead of holding up the run.
function limit(name, promise, ms = 30000) {
    let timer;
    const timeout = new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new Error("no end after " + ms / 1000 + " s: " + name)), ms);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

(async () => {
    for (const [name, fn] of tests) {
        try {
            await limit(name, fn());
            passed++;
        } catch (e) {
            console.error("FAIL", name, "\n ", e.stack || e.message);
            process.exitCode = 1;
        } finally {
            P.closeAll();
        }
    }
    console.log(process.exitCode ? "THERE ARE FAILURES" : `all ${passed} passed`);
    process.exit(process.exitCode || 0);
})();
