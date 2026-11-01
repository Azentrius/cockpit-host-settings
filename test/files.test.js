// SPDX-License-Identifier: Apache-2.0
/* What the page's files may and may not contain, checked without a browser:
 *   - values from the system only ever reach the page as text: no innerHTML and its relatives,
 *   - nothing Cockpit's CSP blocks: no inline scripts, no event handler or style attributes,
 *   - every stylesheet url() names a file of the page,
 *   - the page calls only the fixed verbs of host-settings.py, with administrative access only
 *     where it changes something (through the dialog); the token only on standard input; the
 *     menu icon reads status.json only, sends only its page status,
 *   - Cockpit's menu: read as the user, written through the dialog (just for me as the user,
 *     the plan on standard input); the one script the page adds is Cockpit's translations,
 *   - the manifest names the status verb as the status contract wants it,
 *   - the menu entry sits with the other Azentrius add-ons and links the README.
 * Run with: node test/files.test.js */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const PLUGIN = path.join(__dirname, "..", "plugin");
const read = (f) => fs.readFileSync(path.join(PLUGIN, f), "utf8");
const html = read("index.html");
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
const js = strip(read("settings.js"));

let passed = 0;
function t(name, fn) {
    try { fn(); passed++; } catch (e) { console.error("FAIL", name, "\n ", e.stack || e.message); process.exitCode = 1; }
}

t("no HTML from strings", () => ["settings.js", "menu-status.js", "i18n.js"].forEach((f) => {
    const code = strip(read(f));
    ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser", "createContextualFragment", "eval(",
     "new Function", "setTimeout(\"", "javascript:"].forEach((bad) => assert.ok(!code.includes(bad), f + " uses " + bad));
}));

t("index.html: only external scripts, no handlers, no style attributes", () => {
    const scripts = html.match(/<script\b[^>]*>[\s\S]*?<\/script>/g) || [];
    assert.ok(scripts.length > 0);
    scripts.forEach((s) => assert.ok(/^<script src="[^"]+"><\/script>$/.test(s), "external only: " + s));
    assert.ok(!/\son[a-z]+=/i.test(html), "no event handler attributes");
    assert.ok(!/\sstyle=/i.test(html), "no style attributes");
    assert.ok(!/<style\b/i.test(html), "no style elements");
});

t("index.html: the page frame", () => {
    assert.ok(html.includes('<h1 class="az-page-title az-page-title--dot">Host Settings</h1>'));
    assert.ok(html.includes("<title>Host Settings</title>"));
    assert.strictEqual((html.match(/az-btn--primary/g) || []).length, 1, "Refresh is the only filled button");
    assert.ok(html.indexOf("azentrius-theme.js") < html.indexOf("azentrius.css"), "the theme script before the stylesheet");
    assert.ok(html.includes('<script src="../base1/cockpit.js"></script>') && html.includes('<script src="../base1/po.js"></script>'));
    assert.ok(!/https?:\/\/(?!azentrius\.com|github\.com\/Azentrius)/.test(html), "no other external address");
});

t("stylesheets take every file from the page", () => fs.readdirSync(PLUGIN).filter((f) => f.endsWith(".css")).forEach((sheet) => {
    const css = read(sheet).replace(/\/\*[\s\S]*?\*\//g, "");
    [...css.matchAll(/url\(\s*(["']?)([^"')]*)\1\s*\)/g)].map((m) => m[2]).forEach((u) => {
        if (u.startsWith("data:")) return;
        assert.ok(!/^[a-z][a-z0-9+.-]*:/i.test(u) && !u.startsWith("/"), sheet + ": a relative path, not " + u);
        const file = path.resolve(PLUGIN, u);
        assert.ok(file.startsWith(PLUGIN + path.sep) && fs.statSync(file).isFile(), sheet + ": " + u + " exists");
    });
}));

t("only the fixed verbs, and require only for the dialog", () => {
    const direct = [...js.matchAll(/spawn\(\s*\[HELPER, ([^\]]*)\]\s*,\s*("try"|"require"|null)\)/g)].map((m) => m[1] + " " + m[2]);
    assert.deepStrictEqual(direct.sort(), ['"menu", "show" null', '"show" "try"', '"status", "--lang", LANG "try"'],
        "Cockpit's menu is read as the user, never with administrative access");
    assert.strictEqual((js.match(/spawn\(/g) || []).length, 6, "the three above, the dialog's, spawn() and its cockpit.spawn()");
    assert.ok(/proc = spawn\(\[HELPER\]\.concat\(args\), spec\.asUser === true \? null : "require", "out"\)/.test(js),
        "the dialog runs as administrator, unless it asks for the user");
    assert.ok(/if \(superuser\) opts\.superuser = superuser;/.test(js), "without superuser, the option is left out");
    // The dialog's calls: the first word of every argv in ACTIONS and the two forms.
    const block = js.slice(js.indexOf("const ACTIONS = {"), js.indexOf("function copyCommands"));
    const verbs = [...block.matchAll(/\["([a-z]+)"/g)].map((m) => m[1]).filter((v) => v !== "en" && v !== "de");
    assert.deepStrictEqual([...new Set(verbs)].sort(), ["check", "heartbeat", "install", "profile", "remove", "run", "set", "test", "token", "unset"]);
    // The watchdog's verbs, each a fixed form; its address goes on standard input.
    const beat = [...block.matchAll(/\["heartbeat", "([a-z]+)"/g)].map((m) => m[1]);
    assert.deepStrictEqual([...new Set(beat)].sort(), ["remove", "set", "test"]);
    assert.ok(/\["heartbeat", "set", v\.adapter\]/.test(block), "heartbeat set takes the adapter only");
    assert.ok(/input: \(v\) => v\.url \+ "\\n"/.test(block), "the address goes to standard input");
    // The profile's verbs, each a fixed form; the reason of a skip goes on standard input.
    const profile = [...block.matchAll(/\["profile", "([a-z]+)"(?:, "([a-z]+)")?/g)].map((m) => m[1] + (m[2] ? " " + m[2] : ""));
    assert.deepStrictEqual([...new Set(profile)].sort(), ["expect add", "expect remove", "remove", "roles", "skip add", "skip remove"]);
    assert.ok(/input: \(v\) => v\.reason\.replace\(/.test(block), "the reason goes to standard input");
    assert.ok(!/cockpit\.spawn\(\[/.test(js), "every call goes through spawn()");
    assert.ok(!/superuser: "require"/.test(js.replace(/err: superuser === "require"/, "")), "no other require");
    assert.ok(js.includes('cockpit.file(STATUS_PATH, { syntax: JSON })'), "only status.json is watched");
    assert.ok(!/\.watch\(/.test(js.replace("file.watch((content, tag)", "")), "no other watch");
});

t("Cockpit's menu: show as the user, apply system only through the dialog, apply user without administrative access", () => {
    // Every use of the verb: menu show (direct, as the user) and menu apply with the scope (the dialog).
    const uses = [...js.matchAll(/\[(?:HELPER, )?"menu"[^\]]*\]/g)].map((m) => m[0]);
    assert.deepStrictEqual(uses.sort(), ['["menu", "apply", scope]', '[HELPER, "menu", "show"]']);
    const save = js.slice(js.indexOf("function menuSave()"), js.indexOf("function reloadCockpit()"));
    assert.ok(save.includes("dlg.open({"), "applying goes through the dialog");
    assert.ok(save.includes('argv: () => ["menu", "apply", scope]'));
    assert.ok(save.includes("const scope = menuScopeNow();") && save.includes("MENU_SCOPES.indexOf(scope) < 0) return;"));
    assert.ok(save.includes('asUser: scope === "user",'), "only just for me runs as the user");
    assert.strictEqual((js.match(/asUser:/g) || []).length, 1, "no other dialog runs without administrative access");
    assert.ok(save.includes('input: () => JSON.stringify(plan) + "\\n",'), "the plan goes to standard input");
    // For all users only with administrative access.
    assert.ok(/function menuScopeNow\(\) \{\s*return admin !== true \? "user" : menuScope === "user" \? "user" : "system";\s*\}/.test(js));
    assert.deepStrictEqual(JSON.parse(js.match(/const MENU_SCOPES = (\[[^\]]*\])/)[1]), ["system", "user"]);
});

t("the one script the page adds: Cockpit's translations of the manifests", () => {
    assert.ok(js.includes('const MANIFEST_I18N = "../manifests-i18n.js";'));
    assert.strictEqual((js.match(/createElement\("script"\)/g) || []).length, 1);
    assert.strictEqual((js.match(/"script"/g) || []).length, 1, "no other script element");
    assert.ok(js.includes("script.src = MANIFEST_I18N;"));
    assert.ok(!/h\("script"|\.src = (?!MANIFEST_I18N)/.test(js), "no other source");
    assert.ok(js.includes("window.top.location.reload();"), "Reload Cockpit");
});

t("the token: on standard input, from a password field, never in an argument", () => {
    assert.ok(/argv: \(v\) => \(TOKEN_RE\.test\(v\.token\) \? \["token", "set"\]/.test(js), "token set takes no argument");
    assert.ok(/input: \(v\) => v\.token \+ "\\n"/.test(js), "the token goes to standard input");
    assert.ok(/if \(input !== null\) proc\.input\(input\)/.test(js));
    assert.ok(/type: "password"/.test(js));
    assert.ok(/\{ id: "url", label: t\("dlg\.beat\.url"\), type: "password"/.test(js), "the watchdog's address in a password field");
    assert.ok(!/console\.(log|info|debug)/.test(js), "nothing is logged to the console");
});

t("menu-status.js: only status.json, as the user, and only the page's status", () => {
    const m = strip(read("menu-status.js"));
    assert.ok(m.includes('const STATUS_PATH = "/var/lib/cockpit-host-settings/status.json";'));
    assert.deepStrictEqual(m.match(/cockpit\.file\([^)]*\)/g), ["cockpit.file(STATUS_PATH)"], "one file, no superuser");
    assert.ok(m.includes("cockpit.file(STATUS_PATH).watch("), "and only watched");
    assert.deepStrictEqual(m.match(/transport\.control\([^,]*/g), ['transport.control("notify"'], "only notify");
    ["spawn", "superuser", "cockpit.channel", "cockpit.dbus", "cockpit.http", "cockpit.jump", "cockpit.location"]
        .forEach((bad) => assert.ok(!m.includes(bad), "menu-status.js uses " + bad));
    assert.ok(html.indexOf('"menu-status.js?') < html.indexOf('"settings.js?'), "menu-status.js before settings.js");
});

t("the manifest names the status verb as the contract wants it", () => {
    const m = JSON.parse(read("manifest.json"));
    assert.strictEqual(m.menu.index.label, "Host Settings");
    assert.deepStrictEqual(m.preload, ["index"], "loaded hidden after the login, for the menu icon");
    const cmd = m.azentrius.status;
    assert.deepStrictEqual(cmd, ["/usr/lib/cockpit-host-settings/host-settings.py", "status"]);
    assert.ok(/^\/usr\/lib\/cockpit-[a-z0-9][a-z0-9-]{0,40}(\/[A-Za-z0-9_][A-Za-z0-9._-]{0,80}){1,3}$/.test(cmd[0]));
    assert.strictEqual(m.requires.cockpit, "264");
    assert.ok(fs.existsSync(path.join(__dirname, "..", "lib", path.basename(cmd[0]))), "the verb ships in lib/");
});

t("the menu entry sits with the other Azentrius add-ons and links the README", () => {
    const entry = JSON.parse(read("manifest.json")).menu.index;
    // One block at the end of System, after Services (100): Status Board 101 to Host Settings 106.
    assert.strictEqual(entry.order, 106);
    assert.ok(entry.keywords.some((k) => k.matches.includes("azentrius")), "found by searching for azentrius");
    // Shown in Cockpit's Help menu; the product name is not translated.
    assert.deepStrictEqual(entry.docs,
        [{ label: "Host Settings", url: "https://github.com/Azentrius/cockpit-host-settings#readme" }]);
});

t("the status module names the package folder", () => {
    const py = fs.readFileSync(path.join(__dirname, "..", "lib", "hsstatus.py"), "utf8");
    assert.ok(/^ADDON = "host-settings"$/m.test(py), "addon = the page's folder /usr/share/cockpit/host-settings");
    assert.ok(py.includes('"/usr/share/cockpit/host-settings/settings.js"'));
    const notify = fs.readFileSync(path.join(__dirname, "..", "lib", "hsnotify.py"), "utf8");
    assert.ok(/^ADDON = "host-settings"/m.test(notify), "the notifier leaves its own folder out");
});

t("the regular expressions of the page are those of the helper", () => {
    const py = fs.readFileSync(path.join(__dirname, "..", "lib", "hslib.py"), "utf8");
    const pyRe = (name) => {
        const m = py.match(new RegExp("^" + name + ' = re\\.compile\\(((?:r"[^"]*"\\s*)+)\\)', "m"));
        return m[1].match(/r"([^"]*)"/g).map((s) => s.slice(2, -1)).join("");
    };
    const jsRe = (name) => js.match(new RegExp("const " + name + " = /\\^(.*)\\$/;"))[1].replace(/\\\//g, "/");
    assert.strictEqual(jsRe("TOPIC_RE"), pyRe("TOPIC_RE"));
    assert.strictEqual(jsRe("TOKEN_RE"), pyRe("TOKEN_RE"));
    assert.strictEqual(jsRe("MAIL_RE"), pyRe("MAIL_RE"));
    assert.strictEqual(jsRe("URL_RE"), pyRe("URL_RE"));
    assert.strictEqual(jsRe("EXPECT_RE"), pyRe("EXPECT_RE"));
    assert.strictEqual(jsRe("SKIP_RE"), pyRe("SKIP_RE"));
    assert.strictEqual(jsRe("ROLE_RE"), pyRe("ROLE_RE"));
    assert.strictEqual(jsRe("DATE_RE"), pyRe("DATE_RE"));
    // Cockpit's menu (lib/hsmenu.py, matched whole there): a package folder, a key, the limits.
    const menu = fs.readFileSync(path.join(__dirname, "..", "lib", "hsmenu.py"), "utf8");
    const menuRe = (name) => menu.match(new RegExp("^" + name + ' = re\\.compile\\(r"([^"]*)"\\)', "m"))[1];
    assert.strictEqual(jsRe("DIR_RE"), menuRe("DIR_RE"));
    assert.strictEqual(jsRe("KEY_RE"), menuRe("KEY_RE"));
    assert.ok(/^LABEL_MAX = 60$/m.test(menu) && js.includes("const MENU_LABEL_MAX = 60;"));
    assert.ok(/^ORDER_MIN, ORDER_MAX = -1000, 10000$/m.test(menu) && js.includes("const MENU_ORDER_MIN = -1000;") &&
              js.includes("const MENU_ORDER_MAX = 10000;"));
    assert.ok(/^SECTIONS = \("dashboard", "menu", "tools"\)$/m.test(menu) && js.includes('const MENU_SECTIONS = ["dashboard", "menu", "tools"];'));
});

t("the label classes exist in azentrius.css", () => {
    const css = read("azentrius.css");
    [...js.matchAll(/"az-label(?: az-label--([a-z]+))?"/g)].forEach((m) => {
        if (m[1]) assert.ok(css.includes(".az-label--" + m[1]), "az-label--" + m[1]);
    });
});

console.log(process.exitCode ? "THERE ARE FAILURES" : `all ${passed} passed`);
