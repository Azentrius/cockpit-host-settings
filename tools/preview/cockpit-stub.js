// SPDX-License-Identifier: Apache-2.0
// Stand-in for Cockpit's base1/cockpit.js in the preview (tools/preview/build.py): answers the
// page's calls from made-up scenarios. ?scenario=fresh|ready|open|nomta&lang=de&admin=0
(function () {
    "use strict";
    const DATA = __DATA__;
    const NOW = Date.parse(__NOW__);
    const q = new URLSearchParams(location.search);
    const view = DATA.scenarios[q.get("scenario")] || DATA.scenarios.ready;
    const admin = q.get("admin") !== "0";
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
        locale(po) { if (po && po[""] && po[""].language) this.language = po[""].language; },
        location: {
            path: [],
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
            return proc((stream) => { stream("+ " + argv.join(" ") + "\n"); return ""; });
        },
        jump() {},
        // control: the menu icon (menu-status.js), which a plain browser has no menu for.
        transport: { wait() {}, control() {} },
    };
})();
