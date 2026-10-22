// SPDX-License-Identifier: Apache-2.0
// The real page (icons.js, i18n.js, menu-status.js, settings.js) in jsdom with a scripted
// Cockpit stub, for the page tests. boot() returns the window, the calls the page made and the
// stub's state; settle() waits until every stubbed answer has arrived and the page has handled it.
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const PLUGIN = path.join(__dirname, "..", "plugin");
const html = fs.readFileSync(path.join(PLUGIN, "index.html"), "utf8");
const js = ["icons.js", "i18n.js", "menu-status.js", "settings.js"].map((f) => fs.readFileSync(path.join(PLUGIN, f), "utf8")).join("\n");
const NOW = "2026-10-19T19:05:00Z";
const HELPER = "/usr/lib/cockpit-host-settings/host-settings.py";
const STATUS_FILE = "/var/lib/cockpit-host-settings/status.json";
const TOKEN = "tk_testtokentesttokentesttokent";
const EVIL = "<img src=x onerror=\"window.__pwned=1\">";

// What `host-settings.py show` says on a server that was never set up.
function fresh(extra) {
    return Object.assign({
        root: true, config: { ntfy_url: null, ntfy_topic: null, mail_to: null, language: "en" }, bad_lines: [],
        token: false, timer: "inactive", next: null, running: false, host: "server",
        smtp: { listening: false, sendmail: false }, last: null, check: null, test_mail: null,
        docs: "https://docs.ntfy.sh/install/",
    }, extra || {});
}
// ... and on one with ntfy and e-mail, the timer on, two runs behind it.
function ready(extra) {
    return fresh(Object.assign({
        config: { ntfy_url: "https://ntfy.example.com", ntfy_topic: "cockpit-server", mail_to: "ops@example.com", language: "en" },
        token: true, timer: "active", next: "2026-10-19T19:10:00Z", smtp: { listening: true, sendmail: true },
        last: {
            baseline_at: "2026-10-19T18:00:00Z",
            run: { at: "2026-10-19T19:00:00Z", took_ms: 812, addons: 3, bad_lines: [] },
            delivery: { at: "2026-10-19T18:30:00Z", summary: false, changes: 1, title: "server: Cron Jobs, Failed runs",
                        results: { ntfy: { ok: true, code: 200, reason: "sent" }, mail: { ok: true, code: null, reason: "sent" } } },
            access: { state: "ok", anon_read: "ok", anon_write: "ok", at: "2026-10-19T18:00:00Z", target: "https://ntfy.example.com/cockpit-server", full: true },
            seen: {
                cronjobs: { label: "Cron Jobs", kind: "ok", reason: null, code: null, state: "danger", version: "2.8.1", summary: "s",
                            items: [{ id: "failed", title: "Failed runs", state: "danger", value: "2", detail: null, link: null, stale: false },
                                    { id: "cron", title: "cron", state: "ok", value: null, detail: null, link: null, stale: false }] },
                "update-gaps": { label: "Update Gaps", kind: "invalid", reason: "not-json", code: null, state: "warning", version: null, summary: null, items: [] },
                evil: { label: EVIL, kind: "ok", reason: null, code: null, state: "warning", version: EVIL, summary: null,
                        items: [{ id: "x", title: EVIL, state: "warning", value: EVIL, detail: null, link: null, stale: false },
                                { id: "old", title: "Old", state: "ok", value: null, detail: null, link: null, stale: true }] },
            },
        },
        check: { at: "2026-10-19T18:00:00Z", channel: "ntfy", target: "https://ntfy.example.com/cockpit-server", token: true,
                 steps: [{ id: "reach", result: "ok", code: 200, reason: "answered" }, { id: "tls", result: "ok", code: null, reason: "https" },
                         { id: "publish", result: "ok", code: 200, reason: "sent" }, { id: "anon-read", result: "ok", code: 403, reason: "protected" },
                         { id: "anon-write", result: "ok", code: 403, reason: "protected" }, { id: "token", result: "ok", code: 200, reason: "write-only" }],
                 access: { state: "ok", anon_read: "ok", anon_write: "ok" } },
        test_mail: { at: "2026-10-19T18:10:00Z", channel: "mail", result: { ok: true, code: null, reason: "sent" } },
    }, extra || {}));
}
// What `host-settings.py status --lang xx` says.
function statusDoc(lang) {
    return {
        schema: 1, addon: "host-settings", label: "Host Settings", version: "0.1.0", checked_at: "2026-10-19T19:00:00Z",
        state: "warning", summary: lang === "de" ? "Braucht Aufmerksamkeit: Letzte Nachricht." : "Needs attention: Last message.",
        items: [{ id: "delivery", state: "warning", title: lang === "de" ? "Letzte Nachricht" : "Last message", detail: "ntfy (" + EVIL + ")",
                  checked_at: "2026-10-19T19:00:00Z", stale_after: 900, link: "#/notifications" }],
    };
}

// Every window boot() made: closeAll() ends their timers (the menu icon checks again when an item
// turns out of date, which can be many minutes ahead and would keep node running).
const windows = [];
function closeAll() {
    windows.splice(0).forEach((w) => w.close());
}

const pending = new Set();
function track(p) {
    const done = p.then(() => {}, () => {});
    pending.add(done);
    done.then(() => pending.delete(done));
    return p;
}
async function settle() {
    for (let round = 0; round < 500; round++) {
        if (pending.size) await Promise.all([...pending]);
        await new Promise((r) => setImmediate(r));
        if (!pending.size) return;
    }
    throw new Error("the page did not settle: helper calls keep coming");
}
// A stubbed process: resolves (or rejects with `fail`) after streaming `chunks`; input() is kept.
function proc(out, { chunks = [], fail = null, inputs = null } = {}) {
    let cb = null;
    const p = new Promise((resolve, reject) => {
        setImmediate(() => {
            chunks.forEach((c) => cb && cb(c));
            fail ? reject(fail) : resolve(out);
        });
    });
    p.stream = (f) => { cb = f; return p; };
    p.input = (data) => { if (inputs) inputs.push(data); return p; };
    return track(p);
}

function boot({ language = "en", view = ready(), admin = true, now = NOW, onSpawn = null, location = [], hidden = false,
                statusFile = null } = {}) {
    const dom = new JSDOM(html, { runScripts: "outside-only", url: "https://localhost:9090/host-settings/" });
    const w = dom.window;
    windows.push(w);
    w.document.querySelectorAll("script").forEach((s) => s.remove());
    w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
    w.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
    w.Date.now = () => Date.parse(now);
    const calls = { spawn: [], reads: [], go: [], notes: [], inputs: [] };
    const box = { view };
    const listeners = {};
    w.cockpit = {
        language,
        location: { path: location, go(p) { calls.go.push(p); this.path = p; } },
        hidden,
        addEventListener(ev, cb) { (listeners[ev] = listeners[ev] || []).push(cb); },
        removeEventListener(ev, cb) { listeners[ev] = (listeners[ev] || []).filter((x) => x !== cb); },
        file(p, opts) {
            return {
                read() { calls.reads.push(p); return track(Promise.resolve(p === "/etc/hostname" ? "server\n" : null)); },
                watch(cb) { cb(p === STATUS_FILE && !(opts && opts.syntax) ? statusFile : null, "tag-1"); },
            };
        },
        spawn(argv, opts) {
            calls.spawn.push({ argv: argv.slice(), opts: opts || {} });
            const verb = argv.slice(1).join(" ");
            if (onSpawn) {
                const r = onSpawn(verb, opts || {}, box, argv, calls);
                if (r) return r;
            }
            if (verb === "show") {
                if (!admin) return proc("", { fail: { exit_status: 77, message: "This needs administrative access." } });
                return proc(JSON.stringify(box.view));
            }
            if (verb.startsWith("status")) return proc(JSON.stringify(statusDoc(argv[3])));
            return proc("", { chunks: ["ok\n"], inputs: calls.inputs });
        },
        jump() {},
        transport: { wait() {}, control(c, o) { calls.notes.push([c, JSON.parse(JSON.stringify(o))]); } },
    };
    w.eval(js);
    const hint = (h) => { w.cockpit.hidden = h; (listeners.visibilitychange || []).slice().forEach((cb) => cb()); };
    return { w, d: w.document, calls, box, listeners, hint };
}

// The visible text, with spaces between cells and items as a reader sees them.
function text(el) {
    if (!el) return "";
    const c = el.cloneNode(true);
    c.querySelectorAll("td, th, li, dt, dd, p, button, span.az-label, span.hs-sub, h3, pre").forEach((x) => { x.prepend(" "); x.append(" "); });
    return c.textContent.replace(/\s+/g, " ").trim();
}

module.exports = { boot, settle, proc, text, fresh, ready, statusDoc, closeAll, NOW, HELPER, PLUGIN, STATUS_FILE, TOKEN, EVIL };
