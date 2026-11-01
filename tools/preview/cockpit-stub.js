// SPDX-License-Identifier: Apache-2.0
// Stand-in for Cockpit's base1/cockpit.js in the preview (tools/preview/build.py): answers the
// page's calls from made-up scenarios. ?scenario=fresh|ready|open|nomta|menu|menu-noadmin&lang=de&admin=0
// The menu scenarios open the Cockpit tab.
(function () {
    "use strict";
    const DATA = __DATA__;
    const NOW = Date.parse(__NOW__);
    const q = new URLSearchParams(location.search);
    const scenario = q.get("scenario");
    const view = DATA.scenarios[scenario] || DATA.scenarios.ready;
    const admin = q.get("admin") !== "0" && scenario !== "menu-noadmin";
    const po = {};
    Date.now = () => NOW;

    function proc(fn) {
        let cb = null;
        const p = new Promise((resolve, reject) => setTimeout(() => {
            try {
                resolve(fn((text) => cb && cb(text)));
            } catch (e) {
                reject(e);
            }
        }, 200));
        p.stream = (f) => { cb = f; return p; };
        p.input = () => p;
        return p;
    }
    const listeners = {};
    window.cockpit = {
        language: "en",
        locale(data) {
            if (!data) return;
            Object.assign(po, data);
            if (data[""] && data[""].language) this.language = data[""].language;
        },
        gettext(text) { return Array.isArray(po[text]) && typeof po[text][1] === "string" ? po[text][1] : text; },
        location: {
            path: /^menu/.test(scenario || "") ? ["cockpit"] : [],
            go(path) { this.path = path; (listeners.locationchanged || []).forEach((f) => f()); },
        },
        addEventListener(ev, f) { (listeners[ev] = listeners[ev] || []).push(f); },
        removeEventListener(ev, f) { listeners[ev] = (listeners[ev] || []).filter((x) => x !== f); },
        file(path) {
            return {
                read() { return Promise.resolve(path === "/etc/hostname" ? "web1\n" : null); },
                watch(cb) { cb(null, "tag-1"); },
            };
        },
        spawn(argv) {
            const verb = argv.slice(1).join(" ");
            if (verb === "show") {
                return proc(() => {
                    if (!admin) {
                        const e = new Error("access denied");
                        e.exit_status = 77;
                        throw e;
                    }
                    return JSON.stringify(view);
                });
            }
            if (verb.startsWith("status")) return proc(() => JSON.stringify(DATA.status[argv[3] === "de" ? "de" : "en"]));
            if (verb === "check ntfy") return proc((stream) => { stream("Checking …\nRESULT " + JSON.stringify(DATA.check) + "\n"); return ""; });
            if (verb === "menu show") return proc(() => JSON.stringify(DATA.menu));
            if (verb.startsWith("menu apply")) {
                return proc((stream) => { stream("/etc/cockpit/systemd.override.json: changed.\nIn effect once Cockpit is reloaded.\n"); return ""; });
            }
            return proc((stream) => { stream("+ " + argv.join(" ") + "\n"); return ""; });
        },
        jump() {},
        // control: the menu icon (menu-status.js), which a plain browser has no menu for.
        transport: { wait() {}, control() {} },
    };
})();
