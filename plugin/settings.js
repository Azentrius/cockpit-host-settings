// SPDX-License-Identifier: Apache-2.0
/* Host Settings, a Cockpit add-on: where messages about the Azentrius add-ons of this
 * server go (ntfy, e-mail or both), and whether the way there is safe.
 *
 *   show    -> cockpit.spawn(host-settings.py show), superuser "try"   settings, timer, last run, profile (root)
 *   status  -> cockpit.spawn(host-settings.py status --lang xx)        the summary without root
 *   changes -> host-settings.py set|unset|token|check|test|run|install|remove|heartbeat|profile …, through the dialog
 *   watch   -> cockpit.file(/var/lib/cockpit-host-settings/status.json) written by every run
 *
 * menu-status.js runs first: it sets the status icon in Cockpit's menu, also while
 * Cockpit keeps the page loaded hidden, and says when the page is shown; only then
 * does this file call the helper.
 *
 * Every change goes through the dialog: it says what happens, then runs the helper
 * with administrative access and shows its output and the result. The ntfy token
 * and the watchdog's address go to the helper on standard input, never in an
 * argument, and are never shown.
 * Texts: plugin/i18n.js. Everything from the system reaches the page as text (h(),
 * textContent), never as markup. No build step, no framework, no external requests.
 */

(function () {
    "use strict";

    // --- Configuration -----------------------------------------------------
    // The one place for the version (SemVer). index.html loads every script and
    // stylesheet with ?v=VERSION; tools/check-version.sh keeps them in step.
    const VERSION = "0.2.0";
    const HELPER = "/usr/lib/cockpit-host-settings/host-settings.py";
    const STATUS_PATH = "/var/lib/cockpit-host-settings/status.json";
    const NTFY_DOCS = "https://docs.ntfy.sh/install/";
    const TABS = ["notifications"];
    const ORDER = { none: 0, ok: 1, info: 2, warning: 3, danger: 4 };
    const ICONS = { none: "ban", ok: "check-circle", info: "info-circle", warning: "exclamation-triangle",
                    danger: "exclamation-circle" };
    const LABEL_CLASS = { none: "az-label", ok: "az-label az-label--success", info: "az-label az-label--info",
                          warning: "az-label az-label--warning", danger: "az-label az-label--danger" };
    // A step of "Check connection" as a state of the status language.
    const STEP_STATE = { ok: "ok", warning: "warning", info: "info", failed: "danger", skipped: "none" };
    const STEPS = ["reach", "tls", "publish", "anon-read", "anon-write", "token"];
    const REASONS = ["rejected", "no-token", "rate-limited", "too-large", "tls", "timeout", "network", "no-mta",
                     "refused", "smtp", "sent"];
    // As lib/hslib.py: checked here for a quick answer, and again by the helper.
    const URL_RE = /^(https?):\/\/([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]{2,45}\])(?::([0-9]{1,5}))?((?:\/[A-Za-z0-9._~-]+)*)\/?$/;
    const TOPIC_RE = /^[A-Za-z0-9_-]{1,64}$/;
    const MAIL_RE = /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/;
    const TOKEN_RE = /^[A-Za-z0-9_.~+/=-]{1,256}$/;
    const HOST_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,62}$/;
    // The target profile: an add-on or one of its checks, a check left out (never an add-on
    // alone: Status Board lets only a check be left out), a role, a day.
    const EXPECT_RE = /^[a-z0-9][a-z0-9-]{0,40}(?:\/[a-z0-9-]{1,40})?$/;
    const SKIP_RE = /^[a-z0-9][a-z0-9-]{0,40}\/[a-z0-9-]{1,40}$/;
    const ROLE_RE = /^[a-z0-9-]{1,40}$/;
    const DATE_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
    // What the board knows about an expected add-on or check (its coverage); every gap of
    // something expected is a warning, what does not apply is no gap.
    const COVERAGES = ["checked", "partial", "stale", "failed", "unconfigured", "noaccess", "unreadable", "missing",
                       "unsupported", "unchecked", "notapplicable"];
    const COVERAGE_STATE = { checked: "ok", notapplicable: "none" };
    // The watchdog (lib/hsheartbeat.py): its adapters, a first look at its address (the helper
    // checks it fully: private networks only for http, the path each adapter needs), the words of
    // its answers.
    const ADAPTERS = ["healthchecks", "uptime-kuma"];
    const ADAPTER_LABEL = { healthchecks: "Healthchecks", "uptime-kuma": "Uptime Kuma" };
    const BEAT_URL_RE = /^https?:\/\/[^\s/?#@]+\/[^\s#]{1,490}$/;
    const BEAT_REASONS = ["accepted", "not-found", "rate-limited", "ambiguous", "invalid-url", "refused", "unexpected",
                          "redirect", "tls", "timeout", "network", "invalid"];

    // --- i18n --------------------------------------------------------------
    const I18N = window.HostSettingsI18n;
    const LANG = /^de([-_]|$)/i.test(String(cockpit.language || "")) ? "de" : "en";
    const LOCALE = LANG === "de" ? "de-DE" : "en-GB";

    function t(key, vars) {
        let s = I18N[LANG][key];
        if (s === undefined) s = I18N.en[key];
        if (s === undefined) return key;
        if (typeof s === "object") s = (vars && vars.n === 1) ? s.one : s.other;
        return s.replace(/\{(\w+)\}/g, (m, k) => (vars && k in vars ? String(vars[k]) : m));
    }

    // --- DOM, text only ------------------------------------------------------
    // h(tag, attrs, ...children): children are nodes or strings (as text).
    function h(tag, attrs) {
        const el = document.createElement(tag);
        Object.keys(attrs || {}).forEach((k) => {
            const v = attrs[k];
            if (v === null || v === undefined || v === false) return;
            if (k === "class") el.className = v;
            else if (k === "text") el.textContent = v;
            else if (k === "hidden") el.hidden = !!v;
            else if (k === "disabled") el.disabled = !!v;
            else el.setAttribute(k, v === true ? "" : String(v));
        });
        for (let i = 2; i < arguments.length; i++) add(el, arguments[i]);
        return el;
    }
    function add(el, kid) {
        if (kid === null || kid === undefined || kid === false) return;
        if (Array.isArray(kid)) { kid.forEach((k) => add(el, k)); return; }
        el.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
    }
    function fill(el) {
        while (el.firstChild) el.removeChild(el.firstChild);
        for (let i = 1; i < arguments.length; i++) add(el, arguments[i]);
        return el;
    }
    // A translated text: `…` becomes <code>; values from the system cannot open or close one.
    function rich(text) {
        const frag = document.createDocumentFragment();
        String(text).split("`").forEach((part, i) => {
            if (!part) return;
            frag.appendChild(i % 2 ? h("code", { text: part }) : document.createTextNode(part));
        });
        return frag;
    }
    function tr(key, vars) {
        const v = {};
        Object.keys(vars || {}).forEach((k) => { v[k] = String(vars[k] == null ? "" : vars[k]).replace(/`/g, "'"); });
        return rich(t(key, v));
    }
    const SVG = "http://www.w3.org/2000/svg";
    function icon(name) {
        const def = (window.AzIcons || {})[name];
        if (!def) return null;
        const svg = document.createElementNS(SVG, "svg");
        svg.setAttribute("class", "az-icon");
        svg.setAttribute("viewBox", def.viewBox);
        svg.setAttribute("aria-hidden", "true");
        svg.setAttribute("focusable", "false");
        svg.setAttribute("data-icon", name);
        const path = document.createElementNS(SVG, "path");
        path.setAttribute("d", def.d);
        svg.appendChild(path);
        return svg;
    }
    function label(state, text) {
        const s = ORDER[state] === undefined ? "none" : state;
        return h("span", { class: LABEL_CLASS[s], "data-state": s }, icon(ICONS[s]), text || t("label." + s));
    }
    function code(s) { return h("code", { text: String(s) }); }
    function btn(cls, iconName, text, attrs) {
        return h("button", Object.assign({ type: "button", class: "az-btn " + cls }, attrs || {}),
            iconName ? icon(iconName) : null, h("span", { text }));
    }
    function alertBox(kind, title, body) {
        const ic = kind === "danger" ? "exclamation-circle" : kind === "warning" ? "exclamation-triangle" : "info-circle";
        return h("div", { class: "az-alert" + (kind === "info" ? "" : " az-alert--" + kind), role: kind === "danger" ? "alert" : "status" },
            h("span", { class: "az-alert__icon" }, icon(ic)),
            h("p", { class: "az-alert__title" }, title),
            body ? h("p", { class: "az-alert__body" }, body) : null);
    }
    function card(id, title, extra, body) {
        return h("section", { class: "az-card hs-card", id, "aria-labelledby": id + "-title" },
            h("div", { class: "az-card__header" }, h("h2", { class: "az-card__title", id: id + "-title", text: title }), extra || null),
            h("div", { class: "az-card__body" }, body));
    }
    // A table that becomes blocks on a phone: one cell per column, labelled.
    function table(cols, rows) {
        return h("div", { class: "az-table-wrap" },
            h("table", { class: "az-table az-table--stack" },
                h("thead", null, h("tr", null, cols.map((c) => h("th", { scope: "col", text: c })))),
                h("tbody", null, rows.map((cells) => h("tr", null,
                    cells.map((cell, i) => h("td", { "data-label": cols[i] }, h("span", { class: "hs-v" }, cell))))))));
    }
    function kv(pairs) {
        return h("dl", { class: "hs-kv" }, pairs.filter(Boolean).map(([k, v]) => [h("dt", { text: k }), h("dd", null, v)]));
    }
    const $ = (id) => document.getElementById(id);

    // --- Time --------------------------------------------------------------
    function fmtDate(iso) {
        const d = new Date(iso || "");
        return isNaN(d) ? "?" : d.toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" });
    }
    function fmtTime(iso) {
        const d = new Date(iso || "");
        return isNaN(d) ? "?" : d.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
    }
    // A day "YYYY-MM-DD" (an end of a skip), and today in UTC in the same form.
    function fmtDay(day) {
        const d = DATE_RE.test(String(day || "")) ? new Date(day + "T00:00:00Z") : new Date("");
        return isNaN(d) ? "?" : d.toLocaleDateString(LOCALE, { dateStyle: "medium", timeZone: "UTC" });
    }
    function today() {
        return new Date(Date.now()).toISOString().slice(0, 10);
    }
    function ago(iso) {
        const ts = Date.parse(iso || "");
        if (isNaN(ts)) return "?";
        const m = Math.max(0, (Date.now() - ts) / 60000);
        if (m < 1) return t("ago.now");
        if (m < 60) return t("ago.min", { n: Math.round(m) });
        const hr = Math.round(m / 60);
        return hr < 48 ? t("ago.h", { n: hr }) : t("ago.d", { n: Math.round(hr / 24) });
    }

    // --- The system ----------------------------------------------------------
    function spawn(argv, superuser) {
        return cockpit.spawn(argv, { superuser, err: superuser === "require" ? "out" : "message",
                                     environ: ["HOST_SETTINGS_LANG=" + LANG] });
    }
    function isNoAdmin(ex) {
        ex = ex || {};
        return ex.exit_status === 77 || /access-denied|not-authorized|authentication/.test(ex.problem || "");
    }

    let view = null;        // host-settings.py show
    let summary = null;     // host-settings.py status, without administrative access
    let admin = null;       // true, false, or null while not known
    let loadError = null;
    let loadedOnce = false;
    let tab = "notifications";
    let hostName = "";

    function cfg() { return (view && view.config) || {}; }
    function channels() {
        const c = cfg();
        return [c.ntfy_url && c.ntfy_topic ? "ntfy" : null, c.mail_to ? "mail" : null].filter(Boolean);
    }
    function channelText(list) {
        return list.map((c) => t("channel." + c)).join(t("and"));
    }
    function reasonText(r) {
        const reason = (r && r.reason) || "network";
        if (/^http-\d{3}$/.test(reason)) return t("reason.http", { code: reason.slice(5) });
        return REASONS.indexOf(reason) >= 0 ? t("reason." + reason) : reason;
    }

    // --- Page messages -----------------------------------------------------
    function renderStatus() {
        const out = [];
        if (loadError) out.push(alertBox("danger", t("status.loadErrorTitle"), loadError));
        if (admin === false) out.push(alertBox("info", t("status.noAdminTitle"), rich(t("status.noAdmin"))));
        if (view && view.bad_lines && view.bad_lines.length) {
            out.push(alertBox("warning", t("status.badLinesTitle"),
                tr("status.badLines", { lines: view.bad_lines.join(", ") })));
        }
        if (view && view.timer === "missing") out.push(alertBox("warning", t("status.noUnitTitle"), rich(t("status.noUnit"))));
        const prof = view && view.profile;
        if (prof && prof.unreadable) out.push(alertBox("warning", t("status.profileUnreadableTitle"), rich(t("status.profileUnreadable"))));
        if (prof && Array.isArray(prof.bad_lines) && prof.bad_lines.length) {
            out.push(alertBox("warning", t("status.profileBadLinesTitle"),
                tr("status.profileBadLines", { lines: prof.bad_lines.join(", ") })));
        }
        fill($("hs-status"), out);
    }

    function renderMeta() {
        const el = $("hs-meta");
        const run = view && view.last && view.last.run;
        const at = run ? run.at : summary && summary.state !== "none" ? summary.checked_at : null;
        if (!at) {
            el.textContent = t("meta.never");
            el.removeAttribute("title");
            return;
        }
        el.textContent = t("meta.run", { ago: ago(at) });
        el.title = t("meta.tip", { when: fmtDate(at) });
    }

    // --- Tabs --------------------------------------------------------------
    function renderTabs() {
        const nav = $("hs-tabs");
        nav.hidden = admin === false || TABS.length < 2;
        nav.querySelectorAll("[role=tab]").forEach((b) => {
            const on = b.dataset.tab === tab;
            b.setAttribute("aria-selected", on ? "true" : "false");
            b.tabIndex = on ? 0 : -1;
        });
        TABS.forEach((k) => { $("hs-tab-" + k).hidden = admin === false || k !== tab; });
        $("hs-summary").hidden = admin !== false;
    }
    function followLocation() {
        const first = ((cockpit.location && cockpit.location.path) || [])[0] || "";
        if (TABS.indexOf(first) >= 0) {
            tab = first;
            renderTabs();
        }
    }

    // --- Summary without administrative access ---------------------------------
    function renderSummary() {
        const box = $("hs-summary");
        if (admin !== false) { fill(box); return; }
        const items = (summary && summary.items) || [];
        const list = h("ul", { class: "hs-plain-list hs-summary-list" }, items.map((i) =>
            h("li", null, label(i.state), " ", h("strong", { text: i.title }), i.value ? " " + i.value : "",
                i.detail ? h("span", { class: "hs-sub", text: i.detail }) : null)));
        // The target profile as the status answer names it, read only.
        const prof = summary && summary.profile && Array.isArray(summary.profile.expect) ? summary.profile : null;
        fill(box, card("hs-sum", t("sum.title"), summary ? label(summary.state) : null,
            [h("p", { class: "hs-intro", text: summary ? summary.summary : t("sum.none") }), items.length ? list : null,
             summary ? h("p", { class: "hs-line", id: "hs-sum-profile",
                                text: prof ? t("sum.profile", { roles: rolesText(prof.roles), n: prof.expect.length }) : t("sum.noProfile") }) : null,
             h("p", { class: "hs-note", text: t("sum.intro") })]));
    }

    // --- Channels ----------------------------------------------------------------
    // What `show` says of each channel set up (lib/hschannels.py, health()): ok once it accepted
    // the last message or a test with the settings set now, failed from a failure until it
    // accepts again (a run with nothing to send changes nothing), unknown before that. Accepted:
    // the ntfy server or the mail server took it; whether someone read it, nobody here knows.
    function health(c) {
        const all = (view && view.channels) || {};
        const v = all[c];
        return v && (v.state === "ok" || v.state === "failed") ? v : { state: "unknown" };
    }
    function failingChannels() { return channels().filter((c) => health(c).state === "failed"); }
    // After a test: the channel works again (the test ended its failure), or the result belongs
    // to settings changed while it ran and counts for nothing.
    function recoveredLine(r) {
        if (r && r.recovered) return h("p", { class: "hs-line", id: "hs-recovered" }, label("ok"), t("chan.recoveredNow"));
        if (r && r.health === "failed" && (r.result ? r.result.ok : (r.steps || []).some((s) => s.id === "publish" && s.result === "ok"))) {
            return h("p", { class: "hs-line" }, label("warning"), t("chan.stillFailedNow"));
        }
        return null;
    }
    function healthCell(c) {
        const v = health(c);
        if (v.state === "failed") {
            return [label("warning", t("chan.failedLabel")),
                    h("span", { text: t("chan.failed", { since: fmtDate(v.since), why: reasonText(v) }) })];
        }
        if (v.state === "ok") {
            const via = v.via === "test" ? "test" : "run";
            const text = v.recovered ? t("chan.again." + via, { when: fmtDate(v.at), since: fmtDate(v.recovered.since) })
                : t("chan.ok." + via, { when: fmtDate(v.at) });
            return [label("ok", t("chan.okLabel")), h("span", { text })];
        }
        return [label("none", t("chan.unknownLabel")), h("span", { text: t("chan.unknown") })];
    }

    // --- Overview ----------------------------------------------------------------
    function delivery() { return view && view.last && view.last.delivery; }
    function overviewState() {
        const chans = channels();
        if (!chans.length && !beatOn()) return ["none", t("ov.none")];
        if (view.timer !== "active") {
            return ["warning", chans.length ? t("ov.off", { channels: channelText(chans) }) : t("ov.offWatchdog")];
        }
        const bad = failingChannels();
        if (bad.length) return ["warning", t("ov.failed", { channels: channelText(bad) })];
        if (beatState() === "warning") return ["warning", t("ov.watchdogFailed")];
        if (chans.indexOf("ntfy") >= 0 && accessState() === "warning") return ["warning", t("ov.open")];
        if (!chans.length) return ["none", t("ov.watchdogOnly")];
        return ["ok", t("ov.ok", { channels: channelText(chans) })];
    }
    function timerCell() {
        // A channel or a watchdog: the notifier also runs for the watchdog alone.
        const chans = channels().concat(beatOn() ? ["watchdog"] : []);
        if (view.timer === "active") {
            return [h("span", { text: view.next ? t("timer.on", { time: fmtTime(view.next) }) : t("timer.onNoNext") }),
                    btn("az-btn--link", null, t("btn.switchOff"), { id: "hs-timer-off" })];
        }
        if (view.timer === "missing") return h("span", { text: t("timer.missing") });
        return [h("span", { text: t("timer.off") }),
                btn("az-btn--secondary", "play", t("btn.setupTimer"), { id: "hs-timer-on", disabled: !chans.length }),
                chans.length ? null : h("span", { class: "hs-sub", text: t("timer.needsChannel") })];
    }
    function deliveryCell() {
        const d = delivery();
        if (!d) return h("span", { text: t("ov.noMessage") });
        const results = Object.keys(d.results || {}).sort().map((c) => {
            const r = d.results[c];
            return label(r.ok ? "ok" : "warning", t("channel." + c) + ": " + (r.ok ? t("ov.accepted") : reasonText(r)));
        });
        return [h("span", { text: t("ov.message", { ago: ago(d.at), title: d.title || "" }) }), results];
    }
    function runCell() {
        const run = view.last && view.last.run;
        if (!run) return h("span", { text: t("ov.noRun") });
        return h("span", { text: t("ov.run", { ago: ago(run.at), n: run.addons || 0 }) });
    }
    function renderOverview() {
        const [state, text] = overviewState();
        return card("hs-overview", t("ov.title"), label(state), [
            h("p", { class: "hs-line" }, text),
            kv([
                [t("ov.timer"), timerCell()],
                [t("ov.lastRun"), runCell()],
                [t("ov.lastMessage"), deliveryCell()],
                [t("ov.language"), [h("span", { text: t("lang." + (cfg().language === "de" ? "de" : "en")) }),
                                    btn("az-btn--link", null, t("btn.change"), { id: "hs-lang" })]],
            ]),
            h("div", { class: "hs-actions" },
                btn("az-btn--secondary", "redo", t("btn.runNow"), { id: "hs-run", disabled: view.timer === "missing" })),
            // A run, a channel that accepts a message and a person who reads it are three things.
            channels().length ? h("p", { class: "hs-note", id: "hs-ov-note", text: t("ov.note") }) : null,
        ]);
    }

    // --- ntfy ---------------------------------------------------------------------
    function defaultTopic() {
        return "cockpit-" + (HOST_RE.test(hostName) ? hostName.toLowerCase() : "server");
    }
    // The pattern for a reader of every host's topic: the host name in the topic becomes *.
    function readPattern(topic) {
        const host = (hostName || "").toLowerCase();
        return host && topic.toLowerCase().indexOf(host) >= 0
            ? topic.slice(0, topic.toLowerCase().indexOf(host)) + "*" + topic.slice(topic.toLowerCase().indexOf(host) + host.length)
            : topic;
    }
    function commandsText(topic) {
        const name = "Host Settings " + (HOST_RE.test(hostName) ? hostName : "server");
        return [
            "ntfy user add " + topic,
            "ntfy access " + topic + " " + topic + " write-only",
            "ntfy token add --label=\"" + name + "\" " + topic,
        ].join("\n");
    }
    // The last "Check connection", when it was for the server and topic set now.
    function checkFor() {
        const c = cfg();
        const check = view && view.check;
        return check && check.channel === "ntfy" && Array.isArray(check.steps) &&
            check.target === c.ntfy_url + "/" + c.ntfy_topic ? check : null;
    }
    const STEP_KEYS = ["step.reach.ok", "step.reach.failed", "step.tls.ok", "step.tls.warning", "step.publish.ok",
                       "step.publish.failed", "step.anon-read.ok", "step.anon-read.warning", "step.anon-read.info",
                       "step.anon-write.ok", "step.anon-write.warning", "step.anon-write.info", "step.token.ok"];
    function stepKey(s) {
        if (s.result === "skipped") return "step.skipped." + (s.reason === "no-token" || s.reason === "unreachable" ? s.reason : "other");
        if (s.id === "token" && s.result === "info") return "step.token.info." + (["can-read", "admin"].indexOf(s.reason) >= 0 ? s.reason : "unknown");
        const key = "step." + s.id + "." + s.result;
        return STEP_KEYS.indexOf(key) >= 0 ? key : "step.other";
    }
    function stepList(steps) {
        return h("ul", { class: "hs-plain-list hs-steps" }, steps.filter((s) => STEPS.indexOf(s.id) >= 0).map((s) => {
            const state = STEP_STATE[s.result] || "none";
            const what = typeof s.code === "number" ? "HTTP " + s.code : reasonText(s);
            return h("li", null, label(state), h("span", { class: "hs-step-title", text: t("step." + s.id) }),
                h("span", null, tr(stepKey(s), { code: what, why: reasonText(s) })));
        }));
    }
    function accessState() {
        const a = view.last && view.last.access;
        return a && (a.state === "ok" || a.state === "warning") ? a.state : "none";
    }
    function renderNtfy() {
        const c = cfg();
        if (!(c.ntfy_url && c.ntfy_topic)) {
            return card("hs-ntfy", t("ntfy.title"), null, [
                h("p", { class: "hs-intro" }, rich(t("ntfy.intro"))),
                h("div", { class: "hs-actions" }, btn("az-btn--secondary", "plus", t("btn.setupNtfy"), { id: "hs-ntfy-set" })),
            ]);
        }
        const check = checkFor();
        const parts = [
            kv([
                [t("ntfy.server"), [code(c.ntfy_url)]],
                [t("ntfy.topic"), [code(c.ntfy_topic)]],
                [t("ntfy.token"), [h("span", { text: view.token ? t("ntfy.tokenSet") : t("ntfy.tokenNone") }),
                                   btn("az-btn--link", null, view.token ? t("btn.replaceToken") : t("btn.setToken"), { id: "hs-token-set" }),
                                   view.token ? btn("az-btn--link", null, t("btn.removeToken"), { id: "hs-token-remove" }) : null]],
                [t("ntfy.access"), [label(accessState(), t("access." + accessState()))]],
                [t("chan.label"), healthCell("ntfy")],
            ]),
            h("div", { class: "hs-actions" },
                btn("az-btn--secondary", "check", t("btn.check"), { id: "hs-ntfy-check" }),
                btn("az-btn--link", null, t("btn.change"), { id: "hs-ntfy-change" }),
                btn("az-btn--link", null, t("btn.removeNtfy"), { id: "hs-ntfy-remove" })),
        ];
        if (check) {
            parts.push(h("h3", { class: "hs-sub-title", text: t("ntfy.checkTitle", { when: fmtDate(check.at) }) }), stepList(check.steps));
        }
        parts.push(
            h("h3", { class: "hs-sub-title", text: t("ntfy.serverTitle") }),
            h("p", { class: "hs-intro" }, rich(t("ntfy.serverIntro"))),
            h("pre", { class: "hs-commands", id: "hs-commands", text: commandsText(c.ntfy_topic) }),
            h("ul", { class: "hs-plain-list" },
                h("li", null, tr("ntfy.denyAll")),
                h("li", null, tr("ntfy.reader", { pattern: readPattern(c.ntfy_topic) }))),
            h("div", { class: "hs-actions" },
                btn("az-btn--link", "copy", t("btn.copy"), { id: "hs-copy" }),
                h("a", { class: "az-btn az-btn--link", href: NTFY_DOCS, target: "_blank", rel: "noopener noreferrer" },
                    icon("external-link-alt"), h("span", { text: t("ntfy.docs") }))));
        return card("hs-ntfy", t("ntfy.title"), accessState() === "warning" ? label("warning", t("ntfy.open")) : label("ok", t("ntfy.on")), parts);
    }

    // --- E-mail ---------------------------------------------------------------------
    function mtaLine() {
        const smtp = (view && view.smtp) || {};
        if (smtp.listening === true) return h("p", { class: "hs-line" }, label("ok"), t("mail.mta"));
        if (smtp.listening === false) {
            return h("p", { class: "hs-line" }, label("warning"), rich(t(smtp.sendmail ? "mail.sendmailOnly" : "mail.noMta")));
        }
        return h("p", { class: "hs-line" }, label("none"), t("mail.mtaUnknown"));
    }
    function renderMail() {
        const c = cfg();
        if (!c.mail_to) {
            return card("hs-mail", t("mail.title"), null, [
                h("p", { class: "hs-intro" }, rich(t("mail.intro"))), mtaLine(),
                h("div", { class: "hs-actions" }, btn("az-btn--secondary", "plus", t("btn.setupMail"), { id: "hs-mail-set" })),
            ]);
        }
        // The last test message, when it went to the address set now (0.2.0 kept no address).
        const test = view.test_mail && view.test_mail.channel === "mail" && view.test_mail.target === c.mail_to
            ? view.test_mail : null;
        const parts = [
            mtaLine(),
            kv([[t("mail.to"), [code(c.mail_to)]], [t("chan.label"), healthCell("mail")]]),
            h("div", { class: "hs-actions" },
                btn("az-btn--secondary", "check", t("btn.testMail"), { id: "hs-mail-test" }),
                btn("az-btn--link", null, t("btn.change"), { id: "hs-mail-change" }),
                btn("az-btn--link", null, t("btn.removeMail"), { id: "hs-mail-remove" })),
        ];
        if (test && test.result) {
            parts.push(h("p", { class: "hs-line" }, label(test.result.ok ? "ok" : "warning"),
                t(test.result.ok ? "mail.testOk" : "mail.testFailed", { when: fmtDate(test.at), why: reasonText(test.result) })));
        }
        return card("hs-mail", t("mail.title"), label("ok", t("mail.on")), parts);
    }

    // --- The watchdog ----------------------------------------------------------------
    // What `show` says about /etc/cockpit-host-settings/heartbeat.conf: set up or not, the adapter,
    // whether the address is encrypted or this server itself, the last signal and the last test.
    // Never the address: it is a secret, and the page cannot read it.
    function beat() { return (view && view.heartbeat) || {}; }
    function beatOn() { return !!beat().configured; }
    function beatReason(r) {
        const reason = (r && r.reason) || "network";
        if (/^http-\d{3}$/.test(reason)) return t("reason.http", { code: reason.slice(5) });
        return BEAT_REASONS.indexOf(reason) >= 0 ? t("beat.reason." + reason) : reason;
    }
    function beatState() {
        const b = beat();
        if (b.invalid) return "warning";
        if (!b.configured) return "none";
        const r = b.last && b.last.result;
        return !r ? "none" : r.ok ? "ok" : "warning";
    }
    function lastSignal(b) {
        const last = b.last && b.last.result ? b.last : null;
        if (!last) return h("span", { text: t("beat.noSignal") });
        const r = last.result;
        const text = !r.ok ? t("beat.notAccepted", { ago: ago(last.at), why: beatReason(r) })
            : last.signal === "failure" ? t("beat.toldFailure", { ago: ago(last.at) }) : t("beat.accepted", { ago: ago(last.at) });
        return [label(r.ok ? "ok" : "warning"), h("span", { text })];
    }
    function renderHeartbeat() {
        const b = beat();
        if (!b.configured) {
            return card("hs-beat", t("beat.title"), b.invalid ? label("warning", t("beat.invalidLabel")) : null, [
                h("p", { class: "hs-intro" }, rich(t("beat.intro"))),
                b.invalid ? h("p", { class: "hs-line" }, label("warning"), rich(t("beat.invalid"))) : null,
                h("div", { class: "hs-actions" }, btn("az-btn--secondary", "plus", t("btn.setupBeat"), { id: "hs-beat-set" }),
                    b.invalid ? btn("az-btn--link", null, t("btn.removeBeat"), { id: "hs-beat-remove" }) : null),
            ]);
        }
        const test = b.test && b.test.channel === "heartbeat" && b.test.result ? b.test : null;
        const parts = [
            kv([
                [t("beat.service"), [h("span", { text: ADAPTER_LABEL[b.adapter] || String(b.adapter || "") })]],
                [t("beat.address"), [h("span", { text: t("beat.stored") }), b.encrypted === false ? label("info", t("beat.plain")) : null]],
                [t("beat.last"), lastSignal(b)],
            ]),
            b.local ? h("p", { class: "hs-line" }, label("warning"), t("beat.local")) : null,
            h("div", { class: "hs-actions" },
                btn("az-btn--secondary", "check", t("btn.testBeat"), { id: "hs-beat-test" }),
                btn("az-btn--link", null, t("btn.change"), { id: "hs-beat-change" }),
                btn("az-btn--link", null, t("btn.removeBeat"), { id: "hs-beat-remove" })),
        ];
        if (test) {
            parts.push(h("p", { class: "hs-line" }, label(test.result.ok ? "ok" : "warning"),
                t(test.result.ok ? "beat.testOk" : "beat.testFailed", { when: fmtDate(test.at), why: beatReason(test.result) })));
        }
        parts.push(h("h3", { class: "hs-sub-title", text: t("beat.setupTitle") }),
            h("ul", { class: "hs-plain-list" }, ["beat.period", "beat.outside", "beat.off"].map((k) => h("li", null, rich(t(k))))));
        const state = beatState();
        return card("hs-beat", t("beat.title"),
            label(state, state === "ok" ? t("beat.on") : state === "warning" ? t("beat.failedLabel") : t("beat.waiting")), parts);
    }

    // --- The add-ons ---------------------------------------------------------------
    const ERROR_KINDS = ["rejected", "untrusted", "manifest", "invalid", "missing", "failed", "timeout", "unsupported"];
    function addonFindings(a) {
        if (ERROR_KINDS.indexOf(a.kind) >= 0) {
            const why = a.kind === "invalid" ? a.reason : a.kind;
            return h("span", { text: t("addons.unreadable", { why: t("why." + why, { code: a.code }) }) });
        }
        if (a.kind === "noaccess") return h("span", { text: t("addons.noaccess") });
        const found = (a.items || []).filter((i) => i.stale || ORDER[i.state] >= ORDER.info);
        if (!found.length) return h("span", { class: "hs-sub", text: t("addons.nothing") });
        return h("ul", { class: "hs-findings" }, found.map((i) => h("li", null,
            label(i.stale && ORDER[i.state] < ORDER.warning ? "warning" : i.state, i.stale && ORDER[i.state] < ORDER.warning ? t("label.stale") : null),
            " ", i.title + (i.value ? " (" + i.value + ")" : ""))));
    }
    function renderAddons() {
        const seen = (view.last && view.last.seen) || null;
        const names = seen ? Object.keys(seen).sort((a, b) => String(seen[a].label).localeCompare(String(seen[b].label), LOCALE)) : [];
        const body = [];
        if (!seen) body.push(h("p", { class: "hs-intro", text: t("addons.notYet") }));
        else if (!names.length) body.push(h("p", { class: "hs-intro" }, rich(t("addons.none"))));
        else {
            body.push(h("p", { class: "hs-intro", text: t("addons.intro", { ago: ago(view.last.run.at) }) }));
            body.push(table([t("addons.col.addon"), t("addons.col.state"), t("addons.col.findings")], names.map((p) => {
                const a = seen[p];
                const state = ERROR_KINDS.indexOf(a.kind) >= 0 ? "warning" : a.state;
                return [h("span", null, h("strong", { text: a.label || p }), a.version ? h("span", { class: "hs-sub", text: a.version }) : null),
                        label(state), addonFindings(a)];
            })));
        }
        return card("hs-addons", t("addons.title"), null, body);
    }

    // --- The target profile ----------------------------------------------------------
    // What `show` says about /etc/cockpit-host-settings/profile.conf (host-settings.py, profile_view),
    // and what the notifier found last for each expected add-on or check (last.coverage).
    function profile() { return (view && view.profile) || {}; }
    function catalog() { return profile().catalog || {}; }
    function rolesText(roles) {
        const list = (Array.isArray(roles) ? roles : []).filter((r) => typeof r === "string" && ROLE_RE.test(r));
        return list.length ? list.join(", ") : t("profile.noRole");
    }
    // "Security Monitor" for security, "Security Monitor: exposure" for one of its checks.
    function addonName(id) {
        const parts = String(id).split("/");
        const labels = catalog().labels || {};
        const name = Object.prototype.hasOwnProperty.call(labels, parts[0]) ? String(labels[parts[0]]) : parts[0];
        return parts[1] ? name + ": " + parts[1] : name;
    }
    function sourceOf(id) {
        const c = catalog();
        if ((c.base || []).indexOf(id) >= 0) return t("profile.from.base");
        const roles = (c.roles || []).filter((r) => Array.isArray(r) && (profile().roles || []).indexOf(r[0]) >= 0 &&
            Array.isArray(r[1]) && r[1].indexOf(id) >= 0).map((r) => r[0]);
        return roles.length ? t("profile.from.role", { roles: roles.join(", ") }) : t("profile.from.extra");
    }
    function coverageLabel(coverage) {
        return label(COVERAGE_STATE[coverage] || "warning", t("cov." + coverage));
    }
    function coverageCell(row) {
        if (!row || COVERAGES.indexOf(row.coverage) < 0) return h("span", { class: "hs-sub", text: t("profile.notRead") });
        const gaps = (Array.isArray(row.gaps) ? row.gaps : []).filter((g) => g && COVERAGES.indexOf(g.coverage) >= 0);
        return [coverageLabel(row.coverage), gaps.length ? h("ul", { class: "hs-findings" }, gaps.map((g) =>
            h("li", null, coverageLabel(g.coverage), " ", String(g.title || g.id || "")))) : null];
    }
    function nameCell(id) {
        return h("span", null, h("strong", { text: addonName(id) }), h("span", { class: "hs-sub" }, code(id)));
    }
    function renderProfile() {
        const p = profile();
        if (!p.set) {
            return card("hs-profile", t("profile.title"), label("none", t("profile.none")), [
                h("p", { class: "hs-intro" }, rich(t("profile.intro"))),
                h("div", { class: "hs-actions" }, btn("az-btn--secondary", "plus", t("btn.setupProfile"), { id: "hs-profile-roles" })),
            ]);
        }
        const found = new Map(((view.last && Array.isArray(view.last.coverage)) ? view.last.coverage : [])
            .filter((r) => r && typeof r.id === "string").map((r) => [r.id, r]));
        const expect = (p.expect || []).filter((id) => EXPECT_RE.test(String(id)));
        const extra = p.extra || [];
        const gaps = expect.filter((id) => found.has(id) && ["checked", "notapplicable"].indexOf(found.get(id).coverage) < 0).length;
        const skips = (p.skip || []).filter((s) => s && SKIP_RE.test(String(s.check)));
        return card("hs-profile", t("profile.title"), gaps ? label("warning", t("profile.gaps", { n: gaps })) : label("ok", t("profile.on")), [
            h("p", { class: "hs-intro", text: t("profile.introSet") }),
            kv([
                [t("profile.roles"), [h("span", { text: rolesText(p.roles) }),
                                      btn("az-btn--link", null, t("btn.changeRoles"), { id: "hs-profile-roles" })]],
                p.set_at ? [t("profile.setAt"), h("span", { text: fmtDate(p.set_at) })] : null,
            ]),
            h("h3", { class: "hs-sub-title", text: t("profile.expectTitle") }),
            table([t("profile.col.what"), t("profile.col.why"), t("profile.col.coverage")], expect.map((id) => [
                nameCell(id),
                [h("span", { text: sourceOf(id) }), extra.indexOf(id) >= 0
                    ? btn("az-btn--link", null, t("btn.remove"), { "data-action": "expect-remove", "data-value": id }) : null],
                coverageCell(found.get(id)),
            ])),
            h("h3", { class: "hs-sub-title", text: t("profile.skipTitle") }),
            skips.length ? table([t("profile.col.check"), t("profile.col.reason"), t("profile.col.until")], skips.map((s) => [
                nameCell(s.check),
                h("span", { text: String(s.reason || "") }),
                [s.active ? label("none", s.until ? t("profile.until", { date: fmtDay(s.until) }) : t("profile.noEnd"))
                          : label("warning", t("profile.ended", { date: fmtDay(s.until) })),
                 btn("az-btn--link", null, t("btn.remove"), { "data-action": "skip-remove", "data-value": s.check })],
            ])) : h("p", { class: "hs-sub", text: t("profile.noSkips") }),
            h("div", { class: "hs-actions" },
                btn("az-btn--secondary", "plus", t("btn.addExpect"), { id: "hs-profile-expect-add" }),
                btn("az-btn--secondary", "ban", t("btn.addSkip"), { id: "hs-profile-skip-add" }),
                btn("az-btn--link", null, t("btn.removeProfile"), { id: "hs-profile-remove" })),
        ]);
    }

    function renderLimits() {
        return card("hs-limits", t("limits.title"), null,
            h("ul", { class: "hs-plain-list" }, ["limits.contract", "limits.offline", "limits.content", "limits.mail", "limits.server"]
                .map((k) => h("li", null, rich(t(k))))));
    }

    function renderNotifications() {
        const panel = $("hs-tab-notifications");
        if (!view) { fill(panel); return; }
        fill(panel, renderOverview(), renderNtfy(), renderMail(), renderHeartbeat(), renderAddons(), renderProfile(), renderLimits());
    }

    function render() {
        renderMeta();
        renderStatus();
        renderTabs();
        renderSummary();
        renderNotifications();
        $("hs-loading").hidden = loadedOnce;
    }

    // --- Signature -----------------------------------------------------------
    function renderSignature() {
        $("hs-sig-version").textContent = "Host Settings " + VERSION;
        Promise.resolve().then(() => cockpit.file("/etc/hostname").read())
            .then((text) => {
                const host = String(text || "").trim().split(/\s+/)[0];
                hostName = host;
                $("hs-sig-host").textContent = host;
                $("hs-sig-host").hidden = !host;
                if (view) renderNotifications();
            })
            .catch(() => { /* no host name: the signature shows the product only */ });
    }

    // --- Load ----------------------------------------------------------------
    function load() {
        return spawn([HELPER, "show"], "try").then((out) => {
            view = JSON.parse(out);
            admin = true;
            summary = null;
            loadError = null;
            if (view.host && !hostName) hostName = String(view.host);
        }).catch((ex) => {
            if (!isNoAdmin(ex)) {
                loadError = String((ex && (ex.message || ex.problem)) || ex);
                return;
            }
            admin = false;
            view = null;
            return spawn([HELPER, "status", "--lang", LANG], "try").then((out) => {
                summary = JSON.parse(out);
                loadError = null;
            }).catch((e) => { summary = null; loadError = String((e && (e.message || e.problem)) || e); });
        });
    }

    function setBusy(on) {
        const b = $("hs-refresh");
        b.disabled = on;
        b.classList.toggle("is-busy", on);
        b.setAttribute("aria-busy", on ? "true" : "false");
        b.querySelector(".hs-btn-label").textContent = t(on ? "btn.loading" : "btn.refresh");
        document.querySelectorAll("[role=tabpanel]").forEach((p) => p.classList.toggle("is-busy", on && !!view));
    }

    let loading = false;
    let pending = false;
    function safeLoad() {
        if (loading) { pending = true; return Promise.resolve(); }
        loading = true;
        setBusy(true);
        return Promise.resolve().then(load).catch((err) => {
            loadError = String((err && err.message) || err);
        }).finally(() => {
            loading = false;
            loadedOnce = true;
            if (pending) { pending = false; return safeLoad(); }
            setBusy(false);
            render();
            followLocation();
        });
    }

    // Reload when status.json really changed (a new tag), not when the page's own read reports it again.
    function reloadOnChange(file) {
        let seen = false;
        let lastTag = null;
        file.watch((content, tag) => {
            if (seen && tag === lastTag) return;
            const first = !seen;
            seen = true;
            lastTag = tag;
            if (!first && !dlg.running) safeLoad();
        });
    }

    // --- Dialog: every change ----------------------------------------------------------
    // open(spec): spec = { title, desc, confirm, fields: [{ id, label, type, value, help, max, options }],
    //   argv(values) -> [args after HELPER] or { error }, input(values) -> text for stdin,
    //   outcome(result) -> nodes for a RESULT line of the helper }
    const dlg = {
        running: false,
        spec: null,
        open(spec) {
            this.spec = spec;
            $("hs-dialog-title").textContent = spec.title;
            fill($("hs-dialog-desc"), rich(spec.desc));
            fill($("hs-dialog-fields"), (spec.fields || []).map((f) => {
                // A group of switches (the roles): the values of those switched on.
                if (f.type === "switches") {
                    return h("fieldset", { id: "hs-f-" + f.id, class: "az-field hs-switches" },
                        h("legend", { class: "az-field__label", text: f.label }),
                        f.options.map(([v, text, sub]) => h("label", { class: "az-switch" },
                            h("input", { type: "checkbox", value: v, checked: (f.value || []).indexOf(v) >= 0 }),
                            h("span", { class: "az-switch__track", "aria-hidden": "true" }),
                            h("span", { class: "az-switch__label" }, h("strong", { text: v }), " ", text,
                                sub ? h("span", { class: "hs-sub", text: sub }) : null))));
                }
                const input = f.type === "select"
                    ? h("select", { id: "hs-f-" + f.id, class: "az-input" }, f.options.map(([v, text]) => h("option", { value: v, text, selected: v === f.value })))
                    : h("input", { id: "hs-f-" + f.id, class: "az-input" + (f.mono ? " az-mono" : ""), type: f.type || "text",
                                   maxlength: f.max || 200, autocomplete: "off", spellcheck: "false", value: f.value || "",
                                   placeholder: f.placeholder || null, "aria-describedby": f.help ? "hs-f-" + f.id + "-help" : null });
                return h("div", { class: "az-field" },
                    h("label", { class: "az-field__label", for: "hs-f-" + f.id, text: f.label }), input,
                    f.help ? h("p", { class: "az-field__help", id: "hs-f-" + f.id + "-help" }, rich(f.help)) : null);
            }));
            this.error(null);
            const log = $("hs-dialog-log");
            log.textContent = "";
            log.hidden = true;
            fill($("hs-dialog-outcome"));
            $("hs-dialog-outcome").hidden = true;
            this.result("", null);
            const b = $("hs-dialog-confirm");
            b.hidden = false;
            b.disabled = false;
            b.classList.remove("is-busy");
            b.querySelector(".hs-btn-label").textContent = spec.confirm;
            const cancel = $("hs-dialog-cancel");
            cancel.textContent = t("dlg.cancel");
            cancel.disabled = false;
            const el = $("hs-dialog");
            if (typeof el.showModal === "function") el.showModal(); else el.setAttribute("open", "");
            const first = el.querySelector(".hs-dialog-fields input, .hs-dialog-fields select");
            if (first) first.focus();
        },
        values() {
            const out = {};
            (this.spec.fields || []).forEach((f) => {
                const el = $("hs-f-" + f.id);
                out[f.id] = f.type === "switches" ? Array.from(el.querySelectorAll("input:checked")).map((i) => i.value)
                    : String(el.value || "").trim();
            });
            return out;
        },
        error(text) {
            const el = $("hs-dialog-error");
            fill(el, text ? [icon("exclamation-circle"), text] : null);
            el.hidden = !text;
        },
        result(text, ok) {
            const res = $("hs-dialog-result");
            fill(res, text ? [ok === true ? icon("check-circle") : ok === false ? icon("exclamation-circle") : null, text] : null);
            res.className = "hs-dialog-result" + (ok === true ? " is-ok" : ok === false ? " is-error" : "");
        },
        close() {
            if (this.running) return;
            (this.spec && this.spec.fields || []).forEach((f) => { const el = $("hs-f-" + f.id); if (el && f.type !== "switches") el.value = ""; });
            const el = $("hs-dialog");
            if (typeof el.close === "function") el.close(); else el.removeAttribute("open");
        },
        run() {
            if (this.running || !this.spec) return;
            const spec = this.spec;
            const values = this.values();
            const args = spec.argv(values);
            if (!Array.isArray(args)) { this.error(args && args.error); return; }
            this.error(null);
            const input = spec.input ? spec.input(values) : null;
            (spec.fields || []).forEach((f) => { $("hs-f-" + f.id).disabled = true; if (f.type === "password") $("hs-f-" + f.id).value = ""; });
            this.running = true;
            const log = $("hs-dialog-log");
            log.hidden = false;
            const b = $("hs-dialog-confirm");
            b.disabled = true;
            b.classList.add("is-busy");
            b.querySelector(".hs-btn-label").textContent = t("dlg.running");
            $("hs-dialog-cancel").disabled = true;
            let buffer = "";
            let outcome = null;
            const finish = (text, ok) => {
                this.running = false;
                b.hidden = true;
                const cancel = $("hs-dialog-cancel");
                cancel.textContent = t("dlg.close");
                cancel.disabled = false;
                if (outcome && spec.outcome) {
                    fill($("hs-dialog-outcome"), spec.outcome(outcome));
                    $("hs-dialog-outcome").hidden = false;
                }
                this.result(text, ok);
                safeLoad();
            };
            let proc;
            try {
                proc = spawn([HELPER].concat(args), "require");
            } catch (ex) {
                finish(t("dlg.error", { msg: (ex && ex.message) || String(ex) }), false);
                return;
            }
            // The RESULT line of check and test is data for the outcome, not for the log.
            proc.stream((text) => {
                buffer += text;
                const lines = buffer.split("\n");
                buffer = lines.pop();
                lines.forEach((line) => {
                    if (line.startsWith("RESULT ")) {
                        try { outcome = JSON.parse(line.slice(7)); } catch (e) { outcome = null; }
                        return;
                    }
                    log.textContent += line + "\n";
                });
                log.scrollTop = log.scrollHeight;
            });
            if (input !== null) proc.input(input);
            proc.then(() => {
                if (buffer && !buffer.startsWith("RESULT ")) log.textContent += buffer;
                finish(t("dlg.success"), true);
            }).catch((ex) => {
                ex = ex || {};
                if (isNoAdmin(ex)) finish(t("dlg.noAdmin"), false);
                else if (ex.exit_status === 75) finish(t("dlg.busy"), false);
                else if (typeof ex.exit_status === "number") finish(t("dlg.failed", { code: ex.exit_status }), false);
                else finish(t("dlg.error", { msg: ex.message || ex.problem || String(ex) }), false);
            });
        },
    };

    function checkUrl(v) {
        if (!URL_RE.test(v) || v.length > 200) return t("err.url");
        const port = URL_RE.exec(v)[3];
        if (port && !(Number(port) > 0 && Number(port) < 65536)) return t("err.url");
        return null;
    }
    const ACTIONS = {
        "hs-ntfy-set": () => ntfyForm(t("dlg.ntfy.setTitle"), "", defaultTopic()),
        "hs-ntfy-change": () => ntfyForm(t("dlg.ntfy.changeTitle"), cfg().ntfy_url, cfg().ntfy_topic),
        "hs-ntfy-remove": () => dlg.open({ title: t("dlg.ntfy.removeTitle"), desc: t("dlg.ntfy.removeDesc"), confirm: t("btn.remove"),
                                           argv: () => ["unset", "ntfy"] }),
        "hs-token-set": () => dlg.open({
            title: t("dlg.token.title"), desc: t("dlg.token.desc"), confirm: t("btn.save"),
            fields: [{ id: "token", label: t("dlg.token.label"), type: "password", max: 256, mono: true, help: t("dlg.token.help") }],
            argv: (v) => (TOKEN_RE.test(v.token) ? ["token", "set"] : { error: t("err.token") }),
            input: (v) => v.token + "\n",
        }),
        "hs-token-remove": () => dlg.open({ title: t("dlg.tokenRemove.title"), desc: t("dlg.tokenRemove.desc"), confirm: t("btn.remove"),
                                            argv: () => ["token", "remove"] }),
        "hs-ntfy-check": () => dlg.open({
            title: t("dlg.check.title"), desc: t("dlg.check.desc"), confirm: t("btn.check"),
            argv: () => ["check", "ntfy"],
            outcome: (r) => (r && Array.isArray(r.steps) ? [stepList(r.steps), recoveredLine(r)] : null),
        }),
        "hs-mail-set": () => mailForm(t("dlg.mail.setTitle"), ""),
        "hs-mail-change": () => mailForm(t("dlg.mail.changeTitle"), cfg().mail_to),
        "hs-mail-remove": () => dlg.open({ title: t("dlg.mail.removeTitle"), desc: t("dlg.mail.removeDesc"), confirm: t("btn.remove"),
                                           argv: () => ["unset", "mail"] }),
        "hs-mail-test": () => dlg.open({
            title: t("dlg.test.title"), desc: t("dlg.test.desc", { to: cfg().mail_to || "" }), confirm: t("btn.send"),
            argv: () => ["test", "mail"],
            outcome: (r) => (r && r.result ? [h("p", { class: "hs-line" }, label(r.result.ok ? "ok" : "warning"),
                t(r.result.ok ? "mail.testOkNow" : "mail.testFailedNow", { why: reasonText(r.result) })), recoveredLine(r)] : null),
        }),
        "hs-lang": () => dlg.open({
            title: t("dlg.lang.title"), desc: t("dlg.lang.desc"), confirm: t("btn.save"),
            fields: [{ id: "lang", label: t("dlg.lang.label"), type: "select", value: cfg().language === "de" ? "de" : "en",
                       options: [["en", t("lang.en")], ["de", t("lang.de")]] }],
            argv: (v) => (v.lang === "en" || v.lang === "de" ? ["set", "language", v.lang] : { error: t("err.lang") }),
        }),
        "hs-timer-on": () => dlg.open({ title: t("dlg.timerOn.title"), desc: t("dlg.timerOn.desc"), confirm: t("btn.setupTimer"),
                                        argv: () => ["install", "timer"] }),
        // With a watchdog, switching off makes the signals stop: say so before.
        "hs-timer-off": () => dlg.open({ title: t("dlg.timerOff.title"),
                                         desc: t("dlg.timerOff.desc") + (beatOn() ? " " + t("dlg.timerOff.beat") : ""),
                                         confirm: t("btn.switchOff"), argv: () => ["remove", "timer"] }),
        "hs-beat-set": () => beatForm(t("dlg.beat.setTitle"), "healthchecks"),
        "hs-beat-change": () => beatForm(t("dlg.beat.changeTitle"), beat().adapter),
        "hs-beat-remove": () => dlg.open({ title: t("dlg.beat.removeTitle"), desc: t("dlg.beat.removeDesc"), confirm: t("btn.remove"),
                                           argv: () => ["heartbeat", "remove"] }),
        "hs-beat-test": () => dlg.open({
            title: t("dlg.beat.testTitle"),
            desc: beat().adapter === "uptime-kuma" ? t("dlg.beat.testDescKuma") : t("dlg.beat.testDescHc"),
            confirm: t("btn.send"),
            argv: () => ["heartbeat", "test"],
            outcome: (r) => (r && r.result ? h("p", { class: "hs-line" }, label(r.result.ok ? "ok" : "warning"),
                r.result.ok ? t("beat.testOkNow") : t("beat.testFailedNow", { why: beatReason(r.result) }))
                : r && r.error ? h("p", { class: "hs-line" }, label("warning"), t("beat.testNotSetUp")) : null),
        }),
        "hs-run": () => dlg.open({ title: t("dlg.run.title"), desc: t("dlg.run.desc"), confirm: t("btn.runNow"), argv: () => ["run"] }),
        "hs-profile-roles": () => rolesForm(),
        "hs-profile-expect-add": () => dlg.open({
            title: t("dlg.expect.title"), desc: t("dlg.expect.desc"), confirm: t("btn.add"),
            fields: [{ id: "item", label: t("dlg.expect.label"), max: 82, mono: true, placeholder: "security/exposure", help: t("dlg.expect.help") }],
            argv: (v) => (EXPECT_RE.test(v.item) ? ["profile", "expect", "add", v.item] : { error: t("err.expect") }),
        }),
        "hs-profile-skip-add": () => dlg.open({
            title: t("dlg.skip.title"), desc: t("dlg.skip.desc"), confirm: t("btn.leaveOut"),
            fields: [
                { id: "check", label: t("dlg.skip.check"), max: 82, mono: true, placeholder: "update-gaps/cve", help: t("dlg.skip.checkHelp") },
                { id: "until", label: t("dlg.skip.until"), type: "date", max: 10, help: t("dlg.skip.untilHelp") },
                { id: "reason", label: t("dlg.skip.reason"), max: 200, help: t("dlg.skip.reasonHelp") },
            ],
            argv: (v) => {
                if (!SKIP_RE.test(v.check)) return { error: t("err.skip") };
                if (v.until && !(DATE_RE.test(v.until) && v.until > today())) return { error: t("err.until") };
                if (!v.reason) return { error: t("err.reason") };
                return ["profile", "skip", "add", v.check].concat(v.until ? [v.until] : []);
            },
            // The reason is free text: on standard input, never in an argument.
            input: (v) => v.reason.replace(/[\r\n]+/g, " ") + "\n",
        }),
        "hs-profile-remove": () => dlg.open({ title: t("dlg.profileRemove.title"), desc: t("dlg.profileRemove.desc"), confirm: t("btn.remove"),
                                              argv: () => ["profile", "remove"] }),
    };
    // The buttons of a row of the profile: data-action and the add-on or check in data-value.
    const ROW_ACTIONS = {
        "expect-remove": (item) => dlg.open({ title: t("dlg.expectRemove.title"), desc: t("dlg.expectRemove.desc", { item }),
                                              confirm: t("btn.remove"), argv: () => ["profile", "expect", "remove", item] }),
        "skip-remove": (check) => dlg.open({ title: t("dlg.skipRemove.title"), desc: t("dlg.skipRemove.desc", { check }),
                                             confirm: t("btn.remove"), argv: () => ["profile", "skip", "remove", check] }),
    };
    function rolesForm() {
        const roles = (catalog().roles || []).filter((r) => Array.isArray(r) && ROLE_RE.test(String(r[0])) && Array.isArray(r[1]));
        const known = roles.map((r) => r[0]);
        dlg.open({
            title: profile().set ? t("dlg.roles.changeTitle") : t("dlg.roles.setTitle"), desc: t("dlg.roles.desc"), confirm: t("btn.save"),
            fields: [{ id: "roles", label: t("dlg.roles.label"), type: "switches", value: profile().roles || [],
                       options: roles.map((r) => [r[0], I18N.en["role." + r[0]] !== undefined ? t("role." + r[0]) : "",
                                                  r[1].length ? t("dlg.roles.adds", { list: r[1].map(addonName).join(", ") }) : t("dlg.roles.addsNothing")]) }],
            argv: (v) => (v.roles.every((r) => known.indexOf(r) >= 0) ? ["profile", "roles"].concat(v.roles) : { error: t("err.role") }),
        });
    }
    function ntfyForm(title, url, topic) {
        dlg.open({
            title, desc: t("dlg.ntfy.desc"), confirm: t("btn.save"),
            fields: [
                { id: "url", label: t("dlg.ntfy.url"), value: url, max: 200, mono: true, placeholder: "https://ntfy.example.com", help: t("dlg.ntfy.urlHelp") },
                { id: "topic", label: t("dlg.ntfy.topic"), value: topic, max: 64, mono: true, help: t("dlg.ntfy.topicHelp") },
            ],
            argv: (v) => {
                const bad = checkUrl(v.url) || (TOPIC_RE.test(v.topic) ? null : t("err.topic"));
                return bad ? { error: bad } : ["set", "ntfy", v.url.replace(/\/+$/, ""), v.topic];
            },
        });
    }
    // The address is a secret: a password field, on standard input, never in an argument.
    function beatForm(title, adapter) {
        dlg.open({
            title, desc: t("dlg.beat.desc"), confirm: t("btn.save"),
            fields: [
                { id: "adapter", label: t("dlg.beat.adapter"), type: "select", value: ADAPTERS.indexOf(adapter) >= 0 ? adapter : "healthchecks",
                  options: ADAPTERS.map((a) => [a, ADAPTER_LABEL[a]]) },
                { id: "url", label: t("dlg.beat.url"), type: "password", max: 500, mono: true, help: t("dlg.beat.urlHelp") },
            ],
            argv: (v) => {
                if (ADAPTERS.indexOf(v.adapter) < 0) return { error: t("err.adapter") };
                return BEAT_URL_RE.test(v.url) && v.url.length <= 500 ? ["heartbeat", "set", v.adapter] : { error: t("err.beatUrl") };
            },
            input: (v) => v.url + "\n",
        });
    }
    function mailForm(title, to) {
        dlg.open({
            title, desc: t("dlg.mail.desc"), confirm: t("btn.save"),
            fields: [{ id: "to", label: t("dlg.mail.to"), value: to, max: 254, type: "email", help: t("dlg.mail.toHelp") }],
            argv: (v) => (MAIL_RE.test(v.to) && v.to.length <= 254 ? ["set", "mail", v.to] : { error: t("err.mail") }),
        });
    }

    function copyCommands() {
        const pre = $("hs-commands");
        if (!pre) return;
        const text = pre.textContent;
        const done = () => { const b = $("hs-copy"); if (b) fill(b, icon("check"), h("span", { text: t("btn.copied") })); };
        if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
            navigator.clipboard.writeText(text).then(done).catch(() => select(pre));
        } else {
            select(pre);
        }
    }
    function select(el) {
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
    }

    // --- Init ------------------------------------------------------------------
    function applyStaticTexts() {
        document.documentElement.lang = LANG;
        document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
        document.querySelectorAll("[data-i18n-aria]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.i18nAria)); });
        document.querySelectorAll("span[data-icon]").forEach((el) => { const i = icon(el.dataset.icon); if (i) el.replaceWith(i); });
    }

    applyStaticTexts();
    $("hs-refresh").addEventListener("click", () => safeLoad());
    $("hs-tabs").addEventListener("click", (e) => {
        const b = e.target instanceof Element ? e.target.closest("[role=tab]") : null;
        if (!b || TABS.indexOf(b.dataset.tab) < 0) return;
        if (typeof cockpit.location.go === "function") cockpit.location.go([b.dataset.tab]);
        tab = b.dataset.tab;
        renderTabs();
    });
    document.addEventListener("click", (e) => {
        const target = e.target instanceof Element ? e.target : null;
        const el = target && target.closest("button");
        if (!el || el.closest("dialog")) return;
        if (el.id === "hs-copy") { copyCommands(); return; }
        const row = el.dataset.action && Object.prototype.hasOwnProperty.call(ROW_ACTIONS, el.dataset.action) ? ROW_ACTIONS[el.dataset.action] : null;
        const valid = el.dataset.action === "skip-remove" ? SKIP_RE : EXPECT_RE;
        if (row && !el.disabled && view && valid.test(el.dataset.value || "")) { row(el.dataset.value); return; }
        const action = Object.prototype.hasOwnProperty.call(ACTIONS, el.id) ? ACTIONS[el.id] : null;
        if (action && !el.disabled && view) action();
    });
    $("hs-dialog-confirm").addEventListener("click", () => dlg.run());
    $("hs-dialog-cancel").addEventListener("click", () => dlg.close());
    $("hs-dialog").addEventListener("cancel", (e) => { if (dlg.running) e.preventDefault(); else dlg.close(); });
    $("hs-dialog").addEventListener("keydown", (e) => {
        if (e.key === "Enter" && e.target instanceof Element && e.target.matches("input") && !dlg.running) {
            e.preventDefault();
            dlg.run();
        }
    });
    if (typeof cockpit.addEventListener === "function") cockpit.addEventListener("locationchanged", followLocation);
    followLocation();
    renderTabs();
    // Cockpit loads this page hidden right after the login, for the status icon in
    // its menu (menu-status.js). What reads or runs on the server, the root helper
    // above all, starts only once the page is shown.
    function start() {
        renderSignature();
        reloadOnChange(cockpit.file(STATUS_PATH, { syntax: JSON }));
        safeLoad();
    }
    const menu = window.HostSettingsMenuStatus;
    if (menu) menu.shown.then(start);
    else start();

    cockpit.transport.wait(function () {});
})();
