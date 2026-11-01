// SPDX-License-Identifier: Apache-2.0
/* Host Settings, a Cockpit add-on: where messages about the Azentrius add-ons of this
 * server go (ntfy, e-mail or both), and whether the way there is safe; and, on the
 * Cockpit tab, Cockpit's own menu.
 *
 *   show    -> cockpit.spawn(host-settings.py show), superuser "try"   settings, timer, last run, profile (root)
 *   status  -> cockpit.spawn(host-settings.py status --lang xx)        the summary without root
 *   changes -> host-settings.py set|unset|token|check|test|run|install|remove|heartbeat|profile …, through the dialog
 *   watch   -> cockpit.file(/var/lib/cockpit-host-settings/status.json) written by every run
 *   menu    -> cockpit.spawn(host-settings.py menu show), as the user   Cockpit's menu, read once the tab opens
 *              host-settings.py menu apply system|user, plan on standard input, through the dialog
 *              (for all users with administrative access, just for me as the user)
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
    const VERSION = "0.3.0";
    const HELPER = "/usr/lib/cockpit-host-settings/host-settings.py";
    const STATUS_PATH = "/var/lib/cockpit-host-settings/status.json";
    const NTFY_DOCS = "https://docs.ntfy.sh/install/";
    const TABS = ["notifications", "cockpit"];
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
    // Cockpit's menu (lib/hsmenu.py): its groups, the scopes of the override files, a package folder
    // and a key the helper writes, the limits of an order and a label, and Cockpit's own order for an
    // entry without one. What a label may not hold as INVISIBLE_RE of lib/hslib.py, with tab and line
    // breaks (code points, so this file holds none of them).
    const MENU_SECTIONS = ["dashboard", "menu", "tools"];
    const MENU_SCOPES = ["system", "user"];
    const MENU_FILE_ERRORS = ["unreadable", "too-large", "invalid", "not-an-object"];
    const DIR_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
    const KEY_RE = /^[A-Za-z0-9_-]{1,64}$/;
    const MENU_ORDER = 1000;
    const MENU_ORDER_MIN = -1000;
    const MENU_ORDER_MAX = 10000;
    const MENU_LABEL_MAX = 60;
    const INVISIBLE = [[0x00, 0x1F], [0x7F, 0x9F], [0xAD, 0xAD], [0x61C, 0x61C], [0x180E, 0x180E], [0x200B, 0x200F],
                       [0x2028, 0x202E], [0x2060, 0x206F], [0xFEFF, 0xFEFF], [0xFFF9, 0xFFFB]];
    // The translations of every package's manifest (their po.manifest files), served by Cockpit's
    // bridge; the one script the page adds itself.
    const MANIFEST_I18N = "../manifests-i18n.js";

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
    // A table that becomes blocks on a phone: one cell per column, labelled; rowAttrs (optional)
    // holds the attributes of each row.
    function table(cols, rows, rowAttrs) {
        return h("div", { class: "az-table-wrap" },
            h("table", { class: "az-table az-table--stack" },
                h("thead", null, h("tr", null, cols.map((c) => h("th", { scope: "col", text: c })))),
                h("tbody", null, rows.map((cells, r) => h("tr", rowAttrs ? rowAttrs[r] : null,
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
    // superuser: "require" (a change through the dialog), "try" (show, status) or null (as the user
    // who is logged in: Cockpit's menu). err "out": the helper's messages go to the dialog's log.
    function spawn(argv, superuser, err) {
        const opts = { err: err || (superuser === "require" ? "out" : "message"), environ: ["HOST_SETTINGS_LANG=" + LANG] };
        if (superuser) opts.superuser = superuser;
        return cockpit.spawn(argv, opts);
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
    let started = false;    // the page is shown: reading from the server may start
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
    // Both tabs also without administrative access: Notifications then holds the summary, Cockpit
    // the menu just for the user. The page messages and the first loading card belong to
    // Notifications; the Cockpit tab has its own.
    function renderTabs() {
        const nav = $("hs-tabs");
        nav.hidden = TABS.length < 2;
        nav.querySelectorAll("[role=tab]").forEach((b) => {
            const on = b.dataset.tab === tab;
            b.setAttribute("aria-selected", on ? "true" : "false");
            b.tabIndex = on ? 0 : -1;
        });
        TABS.forEach((k) => { $("hs-tab-" + k).hidden = k !== tab; });
        $("hs-summary").hidden = admin !== false;
        $("hs-status").hidden = tab !== "notifications";
        $("hs-loading").hidden = loadedOnce || tab !== "notifications";
    }
    function showTab(k) {
        tab = k;
        renderTabs();
        if (tab === "cockpit") openMenu();
    }
    function followLocation() {
        const first = ((cockpit.location && cockpit.location.path) || [])[0] || "";
        if (TABS.indexOf(first) >= 0) showTab(first);
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
    // Why an expected check is not checked: it is not reported at all (a check the profile names
    // that the add-on does not send), or the add-on's own reason (text of the add-on).
    function coverageWhy(x) {
        if (x.not_reported === true) return h("span", { class: "hs-sub", text: t("cov.notReported") });
        return typeof x.reason === "string" && x.reason ? h("span", { class: "hs-sub", text: x.reason }) : null;
    }
    function coverageCell(row) {
        if (!row || COVERAGES.indexOf(row.coverage) < 0) return h("span", { class: "hs-sub", text: t("profile.notRead") });
        const gaps = (Array.isArray(row.gaps) ? row.gaps : []).filter((g) => g && COVERAGES.indexOf(g.coverage) >= 0);
        return [coverageLabel(row.coverage), coverageWhy(row), gaps.length ? h("ul", { class: "hs-findings" }, gaps.map((g) =>
            h("li", null, coverageLabel(g.coverage), " ", String(g.title || g.id || ""),
              g.not_reported === true ? coverageWhy(g) : null))) : null];
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
        const panel = $("hs-notifications");
        if (!view) { fill(panel); return; }
        fill(panel, renderOverview(), renderNtfy(), renderMail(), renderHeartbeat(), renderAddons(), renderProfile(), renderLimits());
    }

    function render() {
        renderMeta();
        renderStatus();
        renderTabs();
        renderSummary();
        renderNotifications();
        renderMenu();
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
                renderMenu();
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
        $("hs-tab-notifications").classList.toggle("is-busy", on && !!view);
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
    //   outcome(result) -> nodes for a RESULT line of the helper, extra -> nodes after the fields,
    //   note -> the line about how it runs, asUser -> run without administrative access (only
    //   "Just for me" of Cockpit's menu), busy -> the text for exit 75, after(ok, code) -> instead
    //   of loading the settings again, local(values) -> an error or null: a change of the menu's
    //   draft that runs nothing }
    const dlg = {
        running: false,
        spec: null,
        open(spec) {
            this.spec = spec;
            $("hs-dialog-title").textContent = spec.title;
            fill($("hs-dialog-desc"), rich(spec.desc));
            $("hs-dialog-note").textContent = spec.note || t("dlg.note");
            fill($("hs-dialog-fields"), spec.extra || null, (spec.fields || []).map((f) => {
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
            if (spec.local) {
                const bad = spec.local(values);
                if (bad) this.error(bad); else this.close();
                return;
            }
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
            const finish = (text, ok, code) => {
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
                if (spec.after) spec.after(ok, code); else safeLoad();
            };
            let proc;
            try {
                // With administrative access, except the user's own menu ("Just for me").
                proc = spawn([HELPER].concat(args), spec.asUser === true ? null : "require", "out");
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
                finish(t("dlg.success"), true, 0);
            }).catch((ex) => {
                ex = ex || {};
                if (buffer && !buffer.startsWith("RESULT ")) log.textContent += buffer;
                const code = typeof ex.exit_status === "number" ? ex.exit_status : null;
                if (isNoAdmin(ex)) finish(t("dlg.noAdmin"), false, code);
                else if (code === 75) finish(spec.busy || t("dlg.busy"), false, code);
                else if (code !== null) finish(t("dlg.failed", { code }), false, code);
                else finish(t("dlg.error", { msg: ex.message || ex.problem || String(ex) }), false, code);
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

    // --- The Cockpit tab: Cockpit's menu ------------------------------------------------
    // `menu show` runs as the user who is logged in, never with administrative access: every entry
    // of Cockpit's navigation and what the override files of both scopes do to it (lib/hsmenu.py,
    // view()). Changes go into a draft in memory. "Save changes" lists them in the dialog in words
    // and writes them with one `menu apply system|user`: the plan holds, for every package folder
    // touched, the checksum of the scope's file the draft was made from (75 when it changed
    // meanwhile) and the editor's whole part of that file, so what the draft leaves alone stays.
    let menuView = null;        // host-settings.py menu show
    let menuError = null;
    let menuLoading = false;
    let menuAsked = false;      // the tab was opened: the menu is read from then on
    let menuScope = null;       // as chosen; menuScopeNow() is the one that counts
    let menuSaved = false;      // saved: Cockpit reads its menu when it loads
    let menuNotice = null;      // a move that could not be made
    let canReload = true;       // window.top.location.reload() threw: the hint alone
    let manifestTexts = null;   // the manifests' translations: null not asked, "loading", true, false
    const draft = new Map();    // entry id -> the mark wanted in the scope being changed
    const moved = new Set();    // entry ids moved here; the other new orders only make room
    const dropped = new Set();  // orphan ids removed when saving

    // Without administrative access only the user's own files ("Just for me").
    function menuScopeNow() {
        return admin !== true ? "user" : menuScope === "user" ? "user" : "system";
    }
    function entryId(e) { return e.dir + "/" + e.section + "/" + e.key; }
    function orphanId(o) { return o.scope + "/" + o.dir + "/" + o.section + "/" + o.key; }
    function menuFile(scope, dir) {
        return menuView.files.find((f) => f.scope === scope && f.dir === dir) || null;
    }
    function changeCount() { return draft.size + dropped.size; }
    // Values put into a translated text that `…` would format.
    function plainVar(s) { return String(s == null ? "" : s).replace(/`/g, "'"); }
    function say(key, vars) {
        const v = {};
        Object.keys(vars || {}).forEach((k) => { v[k] = typeof vars[k] === "number" ? vars[k] : plainVar(vars[k]); });
        return t(key, v);
    }

    // Only what the page needs from `menu show`, each part checked for its shape.
    function menuData(doc) {
        const list = (v) => (Array.isArray(v) ? v : []);
        const text = (v) => typeof v === "string";
        doc = doc && typeof doc === "object" ? doc : {};
        return {
            entries: list(doc.entries).filter((e) => e && MENU_SECTIONS.indexOf(e.section) >= 0 && text(e.key) && text(e.dir))
                .map((e) => Object.assign({}, e, { base: e.base && typeof e.base === "object" ? e.base : {},
                                                   package: text(e.package) ? e.package : e.dir })),
            orphans: list(doc.orphans).filter((o) => o && MENU_SCOPES.indexOf(o.scope) >= 0 && MENU_SECTIONS.indexOf(o.section) >= 0 &&
                                                     text(o.dir) && text(o.key)),
            files: list(doc.files).filter((f) => f && MENU_SCOPES.indexOf(f.scope) >= 0 && text(f.dir)),
            paths: doc.paths && typeof doc.paths === "object" ? doc.paths : {},
            user: text(doc.user) ? doc.user : "",
            cut: doc.cut === true,
        };
    }

    // Cockpit's bridge serves the translations of every package's manifest; once they are loaded
    // the labels read as the navigation shows them (cockpit.gettext). Without them (an error, an
    // older Cockpit) the manifest's own label.
    function loadManifestTexts() {
        if (manifestTexts !== null) return;
        manifestTexts = "loading";
        const script = document.createElement("script");
        script.addEventListener("load", () => { manifestTexts = typeof cockpit.gettext === "function"; renderMenu(); });
        script.addEventListener("error", () => { manifestTexts = false; renderMenu(); });
        script.src = MANIFEST_I18N;
        document.head.appendChild(script);
    }
    function navText(text) {
        if (manifestTexts !== true) return text;
        try {
            const s = cockpit.gettext(text);
            return typeof s === "string" && s ? s : text;
        } catch (e) {
            return text;
        }
    }

    // What one scope's override does to an entry (or an orphan's value), as far as the editor
    // manages it: {hidden: true}, or the order and label it sets ({} for nothing, or for only what
    // the editor leaves alone). markOf() has the draft's for the scope being changed.
    function savedMark(e, scope) {
        return markFrom(e[scope]);
    }
    function markFrom(m) {
        if (!m || typeof m !== "object") return {};
        if (m.hidden === true) return { hidden: true };
        const out = {};
        if (typeof m.order === "number" && isFinite(m.order)) out.order = m.order;
        if (typeof m.label === "string") out.label = m.label;
        return out;
    }
    function markOf(e, scope) {
        const id = entryId(e);
        return scope === menuScopeNow() && draft.has(id) ? draft.get(id) : savedMark(e, scope);
    }
    function hasMark(m) { return !!m.hidden || "order" in m || "label" in m; }
    function sameMark(a, b) { return !!a.hidden === !!b.hidden && a.order === b.order && a.label === b.label; }
    // The entry as the navigation shows it for a scope: the package's own, then the override for
    // all users, then (just for me) the user's own, each later one winning as Cockpit merges them.
    // Hidden entries keep their order, so they stay in their place in the list.
    function layer(e, scope, saved) {
        let label = typeof e.base.label === "string" ? e.base.label : null;
        let order = typeof e.base.order === "number" ? e.base.order : null;
        let hidden = false;
        (scope === "user" ? MENU_SCOPES : ["system"]).forEach((s) => {
            const m = saved ? savedMark(e, s) : markOf(e, s);
            if (m.hidden) { hidden = true; return; }
            if (!hasMark(m)) return;
            // On an entry an earlier file hid, Cockpit makes a new one of only what this one sets.
            if (hidden) { hidden = false; label = null; order = null; }
            if ("order" in m) order = m.order;
            if ("label" in m) label = m.label;
        });
        return { label, order, hidden, renamed: label !== null && label !== e.base.label };
    }
    function originalText(e) {
        return typeof e.base.label === "string" ? navText(e.base.label) : e.key;
    }
    // A name set in an override is fixed text; the manifest's own is translated.
    function shownText(e, l) {
        return l.renamed ? l.label : originalText(e);
    }
    // A group of the navigation in Cockpit's order: by order (1000 without one), then by label.
    function sectionList(section, scope, saved) {
        return menuView.entries.map((e, i) => ({ e, i })).filter((x) => x.e.section === section).map((x) => {
            const l = layer(x.e, scope, saved);
            return { e: x.e, i: x.i, l, text: shownText(x.e, l), at: l.order === null ? MENU_ORDER : l.order };
        }).sort((a, b) => (a.at - b.at) || a.text.localeCompare(b.text));
    }
    // The page Cockpit opens after the login: the first entry of System it shows.
    function startOf(scope, saved) {
        const first = sectionList("menu", scope, saved).find((x) => !x.l.hidden);
        return first ? first.i : -1;
    }
    // Why an entry cannot be changed in a scope, or null.
    function lockedWhy(e, scope) {
        if (e.editable !== true || !DIR_RE.test(e.dir) || !KEY_RE.test(e.key)) return t("menu.ro.notEditable");
        const f = menuFile(scope, e.dir);
        if (f && f.valid !== true) return t("menu.ro.broken");
        if (scope === "user" && savedMark(e, "system").hidden) return t("menu.ro.hiddenAll");
        return null;
    }
    // Hiding replaces the entry in that file with null, and with it whatever else the file says of it.
    function holdsMore(e, scope) {
        return !!(e[scope] && typeof e[scope] === "object" && e[scope].other === true);
    }
    function labelBad(s) {
        const chars = Array.from(s);
        return chars.length < 1 || chars.length > MENU_LABEL_MAX || s !== s.trim() || chars.some((c) => {
            const n = c.codePointAt(0);
            return INVISIBLE.some(([lo, hi]) => n >= lo && n <= hi);
        });
    }

    // --- The draft ---
    function putDraft(e, mark) {
        const id = entryId(e);
        if (sameMark(mark, savedMark(e, menuScopeNow()))) {
            draft.delete(id);
            moved.delete(id);
        } else {
            draft.set(id, mark);
        }
    }
    function withOrder(e, order) {
        const m = Object.assign({}, markOf(e, menuScopeNow()));
        delete m.hidden;
        m.order = order;
        return m;
    }
    function round3(n) { return Math.round(n * 1000) / 1000; }
    // Moving gives only the moved entry a new order: midway between its new neighbours, at most
    // three decimals as the helper takes it, one less or more at either end. Without room there
    // (equal orders, or too close), the entries from its new place on get the next whole numbers,
    // as far as needed to keep their order.
    function moveEntry(i, step) {
        const scope = menuScopeNow();
        const e = menuView.entries[i];
        const list = sectionList(e.section, scope, false);
        const from = list.findIndex((x) => x.i === i);
        const to = from + step;
        if (from < 0 || to < 0 || to >= list.length) return;
        const seq = list.slice();
        seq.splice(to, 0, seq.splice(from, 1)[0]);
        const a = to > 0 ? seq[to - 1].at : null;
        const b = to < seq.length - 1 ? seq[to + 1].at : null;
        const o = round3(a === null ? b - 1 : b === null ? Math.floor(a) + 1 : (a + b) / 2);
        menuNotice = null;
        if ((a === null || o > a) && (b === null || o < b) && o >= MENU_ORDER_MIN && o <= MENU_ORDER_MAX) {
            putDraft(e, withOrder(e, o));
        } else {
            const changes = [];
            let prev = a === null ? MENU_ORDER_MIN - 1 : a;
            for (let k = to; k < seq.length; k++) {
                const x = seq[k];
                if (k > to && x.at > prev) break;
                const v = Math.floor(prev) + 1;
                if (v > MENU_ORDER_MAX || lockedWhy(x.e, scope) || markOf(x.e, scope).hidden) {
                    menuNotice = t("menu.err.move");
                    return;
                }
                changes.push([x.e, v]);
                prev = v;
            }
            changes.forEach(([x, v]) => putDraft(x, withOrder(x, v)));
        }
        if (draft.has(entryId(e))) moved.add(entryId(e));
    }
    function hideEntry(i) {
        const e = menuView.entries[i];
        putDraft(e, { hidden: true });
        moved.delete(entryId(e));
    }
    // Shown again: as it was saved, or without anything when it was saved hidden.
    function showEntry(i) {
        const e = menuView.entries[i];
        const saved = savedMark(e, menuScopeNow());
        putDraft(e, saved.hidden ? {} : saved);
    }
    function renameEntry(i) {
        const scope = menuScopeNow();
        const e = menuView.entries[i];
        const now = shownText(e, layer(e, scope, false));
        const original = originalText(e);
        dlg.open({
            title: t("menu.rename.title"),
            desc: say(scope === "system" ? "menu.rename.descSystem" : "menu.rename.descUser", { label: now }),
            note: t("menu.note.draft"), confirm: t("menu.rename.ok"),
            fields: [{ id: "label", label: t("menu.rename.label"), value: now, max: MENU_LABEL_MAX * 2,
                       help: say("menu.rename.help", { original }) }],
            // Empty, or the original name: the override sets no name any more.
            local: (v) => {
                const text = v.label.normalize("NFC");
                if (text === now) return null;
                const m = Object.assign({}, markOf(e, scope));
                delete m.hidden;
                if (!text || text === original || text === e.base.label) delete m.label;
                else if (labelBad(text)) return t("menu.err.label");
                else m.label = text;
                putDraft(e, m);
                renderMenu();
                return null;
            },
        });
    }
    function discardDraft() {
        draft.clear();
        moved.clear();
        dropped.clear();
        menuNotice = null;
    }
    // After `menu show` again: what no longer applies leaves the draft.
    function pruneDraft() {
        const scope = menuScopeNow();
        const byId = new Map(menuView.entries.map((e) => [entryId(e), e]));
        Array.from(draft.keys()).forEach((id) => {
            const e = byId.get(id);
            if (!e || lockedWhy(e, scope) || sameMark(draft.get(id), savedMark(e, scope))) {
                draft.delete(id);
                moved.delete(id);
            }
        });
        const orphans = new Set(menuView.orphans.map(orphanId));
        Array.from(dropped).forEach((id) => { if (!orphans.has(id)) dropped.delete(id); });
    }

    // --- The plan of `menu apply` and the changes in words ---
    // For every package folder the draft touches: the checksum of the scope's file (empty when there
    // is none) and the editor's whole part of it after the draft: every null, order and label of its
    // entries, and of the keys it has no entry for (orphans, with their value from `menu show`)
    // unless they were removed here. What the plan cannot hold, a key the helper does not take, is
    // named as lost.
    function menuPlan(scope) {
        const touched = new Set();
        menuView.entries.forEach((e) => { if (draft.has(entryId(e))) touched.add(e.dir); });
        menuView.orphans.forEach((o) => { if (o.scope === scope && dropped.has(orphanId(o))) touched.add(o.dir); });
        const base = {};
        const set = {};
        const lost = [];
        Array.from(touched).sort().forEach((dir) => {
            const f = menuFile(scope, dir);
            base[dir] = f && typeof f.sha256 === "string" ? f.sha256 : "";
            const part = {};
            // Only null, order and label: what else the file says of a key stays there anyway.
            const put = (x, m) => {
                if (!hasMark(m)) return;
                if (!KEY_RE.test(x.key)) { lost.push(x); return; }
                const value = {};
                if ("order" in m) value.order = m.order;
                if ("label" in m) value.label = m.label;
                (part[x.section] = part[x.section] || {})[x.key] = m.hidden ? null : value;
            };
            menuView.entries.filter((e) => e.dir === dir).forEach((e) => put(e, markOf(e, scope)));
            menuView.orphans.filter((o) => o.scope === scope && o.dir === dir && !dropped.has(orphanId(o)))
                .forEach((o) => put(o, markFrom(o.value)));
            if (Object.keys(part).length) set[dir] = part;
        });
        return { plan: { base, set }, lost, touched };
    }
    function menuChanges(scope, lost, touched) {
        const out = [];
        const start = startOf(scope, false);
        let startSaid = start === startOf(scope, true);
        let renumbered = 0;
        MENU_SECTIONS.forEach((section) => {
            const list = sectionList(section, scope, false);
            list.forEach((x, k) => {
                const id = entryId(x.e);
                if (!draft.has(id)) return;
                const was = layer(x.e, scope, true);
                const before = shownText(x.e, was);
                if (x.l.hidden !== was.hidden) out.push(x.l.hidden ? say("menu.change.hide", { label: before }) : say("menu.change.show", { label: x.text }));
                if (x.l.hidden) return;
                if (x.l.label !== was.label) {
                    out.push(say(x.l.renamed ? "menu.change.rename" : "menu.change.unrename", { from: before, to: x.text }));
                }
                if (x.l.order === was.order) return;
                if (!moved.has(id)) { renumbered++; return; }
                const next = list[k + 1];
                const becomes = x.i === start && !startSaid;
                if (becomes) startSaid = true;
                out.push(next ? say(becomes ? "menu.change.beforeStart" : "menu.change.before", { label: x.text, other: next.text })
                    : say("menu.change.after", { label: x.text, other: k > 0 ? list[k - 1].text : "" }));
            });
        });
        if (renumbered) out.push(say("menu.change.renumber", { n: renumbered }));
        if (!startSaid && start >= 0) {
            const e = menuView.entries[start];
            out.push(say("menu.change.startPage", { label: shownText(e, layer(e, scope, false)) }));
        }
        const where = (o) => ({ key: o.key, folder: o.dir, section: t("menu.section." + o.section) });
        menuView.orphans.filter((o) => o.scope === scope && dropped.has(orphanId(o))).forEach((o) => out.push(say("menu.change.removeOrphan", where(o))));
        lost.forEach((o) => out.push(say("menu.change.lost", where(o))));
        Array.from(touched).sort().forEach((dir) => {
            const f = menuFile(scope, dir);
            if (f && f.valid === true && f.foreign > 0) out.push(say("menu.change.foreign", { path: f.path }));
        });
        return out;
    }

    // --- Load, save, reload ---
    function openMenu() {
        if (!started || menuAsked) return;
        menuAsked = true;
        loadMenu();
    }
    function loadMenu() {
        if (menuLoading) return;
        menuLoading = true;
        loadManifestTexts();
        renderMenu();
        spawn([HELPER, "menu", "show"], null).then((out) => {
            menuView = menuData(JSON.parse(out));
            menuError = null;
            pruneDraft();
        }).catch((ex) => {
            menuError = String((ex && (ex.message || ex.problem)) || ex);
        }).finally(() => {
            menuLoading = false;
            renderMenu();
        });
    }
    function menuSave() {
        const scope = menuScopeNow();
        if (!changeCount() || MENU_SCOPES.indexOf(scope) < 0) return;
        const { plan, lost, touched } = menuPlan(scope);
        const lines = menuChanges(scope, lost, touched);
        dlg.open({
            title: t(scope === "system" ? "menu.save.titleSystem" : "menu.save.titleUser"),
            desc: say(scope === "system" ? "menu.save.descSystem" : "menu.save.descUser", { path: menuView.paths[scope] || "" }),
            extra: h("ul", { class: "hs-plain-list", id: "hs-menu-changes" }, lines.map((line) => h("li", null, rich(line)))),
            note: scope === "user" ? t("menu.note.user") : null,
            confirm: t("btn.save"),
            asUser: scope === "user",
            argv: () => ["menu", "apply", scope],
            // The plan goes on standard input, as the token does.
            input: () => JSON.stringify(plan) + "\n",
            busy: t("menu.save.changed"),
            after: (ok, code) => {
                if (ok) {
                    discardDraft();
                    menuSaved = true;
                }
                if (ok || code === 75) loadMenu();
                else renderMenu();
            },
        });
    }
    // The whole Cockpit page, so that its shell reads the menu again; where that is not allowed
    // (another origin), the hint alone.
    function reloadCockpit() {
        try {
            window.top.location.reload();
        } catch (e) {
            canReload = false;
            renderMenu();
        }
    }
    function setScope(scope) {
        if (changeCount() || admin !== true || MENU_SCOPES.indexOf(scope) < 0) return;
        menuScope = scope;
        menuNotice = null;
        renderMenu();
    }
    const MENU_ACTIONS = {
        "hs-menu-scope-system": () => setScope("system"),
        "hs-menu-scope-user": () => setScope("user"),
        "hs-menu-save": () => menuSave(),
        "hs-menu-discard": () => { discardDraft(); renderMenu(); },
        "hs-menu-reload": () => reloadCockpit(),
    };
    // The buttons of a row: data-menu (up, down, rename, hide, show, drop) and the entry's or the
    // orphan's place in `menu show`. Focus stays on the entry's buttons.
    function menuClick(el) {
        if (Object.prototype.hasOwnProperty.call(MENU_ACTIONS, el.id)) { MENU_ACTIONS[el.id](); return; }
        const kind = el.dataset.menu;
        const scope = menuScopeNow();
        if (kind === "drop") {
            const o = menuView.orphans[Number(el.dataset.orphan)];
            if (!o || o.scope !== scope || !DIR_RE.test(o.dir)) return;
            if (dropped.has(orphanId(o))) dropped.delete(orphanId(o)); else dropped.add(orphanId(o));
            renderMenu();
            return;
        }
        const i = Number(el.dataset.entry);
        const e = Number.isInteger(i) ? menuView.entries[i] : null;
        if (!e || lockedWhy(e, scope)) return;
        if (kind === "rename") { renameEntry(i); return; }
        if (kind === "up" || kind === "down") moveEntry(i, kind === "up" ? -1 : 1);
        else if (kind === "hide" && !holdsMore(e, scope)) hideEntry(i);
        else if (kind === "show") showEntry(i);
        else return;
        renderMenu();
        const again = document.querySelector('[data-entry="' + i + '"][data-menu="' + kind + '"]:not(:disabled)') ||
            document.querySelector('[data-entry="' + i + '"]:not(:disabled)');
        if (again) again.focus();
    }

    // --- Render ---
    function tag(state, text) {
        return h("span", { class: LABEL_CLASS[state] || LABEL_CLASS.none, text });
    }
    function moveButton(kind, i, text, disabled) {
        return h("button", { type: "button", class: "az-btn az-btn--plain az-btn--sm hs-" + kind, "data-menu": kind, "data-entry": i,
                             "aria-label": t("menu.aria." + kind, { label: text }), title: t("menu.btn." + kind), disabled },
            icon("angle-down"));
    }
    function menuRow(x, k, list, scope, start, savedStart) {
        const e = x.e;
        const l = x.l;
        const tags = [];
        if (draft.has(entryId(e))) tags.push(tag("warning", t("menu.badge.draft")));
        if (x.i === start) tags.push(tag("ok", t(start === savedStart ? "menu.badge.start" : "menu.badge.startNew")));
        if (l.hidden) tags.push(tag("none", scope === "user" && savedMark(e, "system").hidden ? t("menu.badge.hiddenAll") : t("menu.badge.hidden")));
        if (l.renamed) tags.push(tag("info", t("menu.badge.renamed")));
        if (hasMark(markOf(e, "system"))) tags.push(tag("info", t("menu.badge.system")));
        if (scope === "user" && hasMark(markOf(e, "user"))) tags.push(tag("info", t("menu.badge.user")));
        if (scope === "system" && hasMark(savedMark(e, "user"))) tags.push(tag("info", t("menu.badge.userWins")));
        const why = lockedWhy(e, scope);
        const hiddenHere = !!markOf(e, scope).hidden;
        const more = holdsMore(e, scope);
        const actions = why ? h("span", { class: "hs-sub", text: why }) : [
            h("span", { class: "hs-menu-btns" },
                moveButton("up", x.i, x.text, k === 0 || hiddenHere),
                moveButton("down", x.i, x.text, k === list.length - 1 || hiddenHere),
                btn("az-btn--link az-btn--sm", null, t("menu.btn.rename"), { "data-menu": "rename", "data-entry": x.i,
                    "aria-label": t("menu.aria.rename", { label: x.text }), disabled: hiddenHere }),
                hiddenHere
                    ? btn("az-btn--link az-btn--sm", null, t("menu.btn.show"), { "data-menu": "show", "data-entry": x.i,
                        "aria-label": t("menu.aria.show", { label: x.text }) })
                    : btn("az-btn--link az-btn--sm", null, t("menu.btn.hide"), { "data-menu": "hide", "data-entry": x.i,
                        "aria-label": t("menu.aria.hide", { label: x.text }), disabled: more })),
            more && !hiddenHere ? h("span", { class: "hs-sub", text: t("menu.ro.noHide") }) : null,
        ];
        return [
            h("span", null, h("strong", { text: x.text }),
                l.renamed ? h("span", { class: "hs-sub", text: t("menu.original", { label: originalText(e) }) }) : null,
                h("span", { class: "hs-sub", text: e.package + " · " + e.key })),
            tags.length ? h("span", { class: "hs-tags" }, tags) : null,
            actions,
        ];
    }
    function scopeBlock(scope) {
        const where = menuView.paths[scope] || "";
        const note = h("p", { class: "hs-note", id: "hs-menu-scope-note" },
            rich(say(scope === "system" ? "menu.scope.systemNote" : "menu.scope.userNote", { path: where, user: menuView.user })));
        if (admin !== true) {
            return [h("p", { class: "hs-line" }, h("strong", { text: t("menu.scope.label") }), tag("info", t("menu.scope.user"))),
                    note, h("p", { class: "hs-note", text: t("menu.scope.noAdmin") })];
        }
        const locked = changeCount() > 0;
        return [
            h("div", { class: "hs-line", role: "group", "aria-labelledby": "hs-menu-scope-label" },
                h("strong", { id: "hs-menu-scope-label", text: t("menu.scope.label") }),
                MENU_SCOPES.map((s) => h("button", { type: "button", id: "hs-menu-scope-" + s, text: t("menu.scope." + s),
                                                     class: "az-btn az-btn--sm " + (s === scope ? "az-btn--secondary" : "az-btn--plain"),
                                                     "aria-pressed": s === scope ? "true" : "false", disabled: locked && s !== scope }))),
            note,
            locked ? h("p", { class: "hs-note", text: t("menu.scope.locked") }) : null,
        ];
    }
    function orphanBlock(scope, lost) {
        const orphans = menuView.orphans;
        if (!orphans.length) return null;
        const gone = new Set(lost.filter((o) => "phantom" in o).map(orphanId));
        return [
            h("h3", { class: "hs-sub-title", text: t("menu.orphans.title") }),
            h("p", { class: "hs-intro", text: t("menu.orphans.intro") }),
            table([t("menu.col.setting"), t("menu.col.notes"), t("menu.col.actions")], orphans.map((o, n) => {
                const id = orphanId(o);
                const tags = [tag(o.phantom === true ? "warning" : "none", t(o.phantom === true ? "menu.orphan.phantom" : "menu.orphan.null")),
                              tag("info", t("menu.scope." + o.scope))];
                if (dropped.has(id)) tags.unshift(tag("warning", t("menu.orphan.removing")));
                else if (gone.has(id)) tags.unshift(tag("warning", t("menu.orphan.lost")));
                const f = menuFile(o.scope, o.dir);
                const can = o.scope === scope && DIR_RE.test(o.dir) && !(f && f.valid !== true);
                return [
                    h("span", null, h("strong", { text: o.key }), h("span", { class: "hs-sub", text: o.dir + " · " + t("menu.section." + o.section) })),
                    h("span", { class: "hs-tags" }, tags),
                    can ? btn("az-btn--link az-btn--sm", null, dropped.has(id) ? t("menu.btn.keep") : t("btn.remove"),
                              { "data-menu": "drop", "data-orphan": n,
                                "aria-label": t(dropped.has(id) ? "menu.aria.keep" : "menu.aria.remove", { key: o.key }) }) : null,
                ];
            }), orphans.map((o) => ({ "data-id": orphanId(o) }))),
        ];
    }
    function renderMenu() {
        const panel = $("hs-tab-cockpit");
        if (!menuAsked) { fill(panel); return; }
        if (!menuView || !loadedOnce) {
            fill(panel, menuError && !menuView ? alertBox("danger", t("menu.loadError"), menuError)
                : h("section", { class: "az-card hs-loading", id: "hs-menu-loading", "aria-live": "polite" },
                    h("span", { class: "hs-spinner", "aria-hidden": "true" }), h("span", { class: "hs-loading-title", text: t("menu.loading") })));
            return;
        }
        const scope = menuScopeNow();
        const start = startOf(scope, false);
        const savedStart = startOf(scope, true);
        const { lost } = menuPlan(scope);
        const n = changeCount();
        const body = [
            h("p", { class: "hs-intro", text: t("menu.intro") }),
            h("p", { class: "hs-line", id: "hs-menu-host" }, hostName ? t("menu.host", { host: hostName }) : t("menu.hostUnknown")),
            scopeBlock(scope),
        ];
        if (menuError) body.push(alertBox("danger", t("menu.loadError"), menuError));
        if (menuSaved) {
            const box = alertBox("success", t("menu.savedTitle"), t("menu.saved"));
            box.id = "hs-menu-saved";
            if (canReload) add(box, h("div", { class: "az-alert__actions" }, btn("az-btn--secondary az-btn--sm", "redo", t("menu.btn.reload"), { id: "hs-menu-reload" })));
            body.push(box);
        }
        if (menuNotice) body.push(alertBox("warning", menuNotice, null));
        if (menuView.cut) body.push(alertBox("info", t("menu.cut"), null));
        menuView.files.filter((f) => f.valid !== true).forEach((f) => body.push(alertBox("warning", t("menu.file.invalidTitle"),
            rich(say(f.scope === "system" ? "menu.file.invalidSystem" : "menu.file.invalidUser",
                     { path: f.path, folder: f.dir, why: t(MENU_FILE_ERRORS.indexOf(f.error) >= 0 ? "menu.err." + f.error : "menu.err.invalid") })))));
        MENU_SECTIONS.forEach((section) => {
            const list = sectionList(section, scope, false);
            body.push(h("h3", { class: "hs-sub-title", id: "hs-menu-" + section, text: t("menu.section." + section) }));
            body.push(list.length
                ? table([t("menu.col.entry"), t("menu.col.notes"), t("menu.col.actions")], list.map((x, k) => menuRow(x, k, list, scope, start, savedStart)),
                        list.map((x) => ({ "data-id": entryId(x.e), class: x.l.hidden ? "is-off" : null })))
                : h("p", { class: "hs-sub", text: t("menu.empty") }));
        });
        const foreign = menuView.files.filter((f) => f.scope === scope && f.valid === true && f.foreign > 0 && f.served === true);
        if (foreign.length) {
            body.push(h("ul", { class: "hs-plain-list hs-note", id: "hs-menu-foreign" },
                foreign.map((f) => h("li", null, rich(say("menu.file.foreign", { path: f.path, n: Number(f.foreign) }))))));
        }
        body.push(orphanBlock(scope, lost));
        body.push(h("div", { class: "hs-actions hs-menu-bar" },
            btn("az-btn--secondary", "check", t("menu.btn.save"), { id: "hs-menu-save", disabled: !n }),
            btn("az-btn--link", null, t("menu.btn.discard"), { id: "hs-menu-discard", disabled: !n }),
            h("span", { class: "hs-note", id: "hs-menu-count", text: n ? t("menu.draft", { n }) : t("menu.noDraft") })));
        fill(panel, card("hs-menu", t("menu.title"), n ? tag("warning", t("menu.draft", { n })) : null, body));
    }

    // --- Init ------------------------------------------------------------------
    function applyStaticTexts() {
        document.documentElement.lang = LANG;
        document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
        document.querySelectorAll("[data-i18n-aria]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.i18nAria)); });
        document.querySelectorAll("span[data-icon]").forEach((el) => { const i = icon(el.dataset.icon); if (i) el.replaceWith(i); });
    }

    applyStaticTexts();
    $("hs-refresh").addEventListener("click", () => {
        safeLoad();
        if (menuAsked) loadMenu();
    });
    $("hs-tabs").addEventListener("click", (e) => {
        const b = e.target instanceof Element ? e.target.closest("[role=tab]") : null;
        if (!b || TABS.indexOf(b.dataset.tab) < 0) return;
        if (typeof cockpit.location.go === "function") cockpit.location.go([b.dataset.tab]);
        showTab(b.dataset.tab);
    });
    document.addEventListener("click", (e) => {
        const target = e.target instanceof Element ? e.target : null;
        const el = target && target.closest("button");
        if (!el || el.closest("dialog")) return;
        if (el.id === "hs-copy") { copyCommands(); return; }
        // Cockpit's menu: its own buttons, also without administrative access.
        if (el.dataset.menu || Object.prototype.hasOwnProperty.call(MENU_ACTIONS, el.id)) {
            if (!el.disabled && menuView && !dlg.running) menuClick(el);
            return;
        }
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
        started = true;
        renderSignature();
        reloadOnChange(cockpit.file(STATUS_PATH, { syntax: JSON }));
        safeLoad();
        if (tab === "cockpit") openMenu();
    }
    const menu = window.HostSettingsMenuStatus;
    if (menu) menu.shown.then(start);
    else start();

    cockpit.transport.wait(function () {});
})();
