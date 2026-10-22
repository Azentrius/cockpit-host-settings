#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Screenshots of the preview in the light and the dark theme, on a desktop
and a phone, for the README and the AppStream data, or to check a UI change.
The page follows the browser's colour scheme like Cockpit's "auto" style
(azentrius-theme.js). Needs Playwright with Chromium:

    pip install playwright && python3 -m playwright install chromium
    python3 tools/preview/build.py
    python3 tools/preview/shoot.py [output-dir]      (default: docs/img)

Fails if the page throws a JavaScript error or scrolls horizontally on a
phone-sized screen (every scenario, both themes, both languages, without
administrative access, and with the dialog open).
"""
import os
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PAGE = os.path.join(HERE, "out", "host-settings", "index.html")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "docs", "img")
SCENARIOS = ("fresh", "ready", "open", "nomta")


def main():
    if not os.path.exists(PAGE):
        sys.exit("Build first: python3 tools/preview/build.py")
    os.makedirs(OUT, exist_ok=True)
    errors, widths = [], {}
    with sync_playwright() as p:
        browser = p.chromium.launch()

        def page(width, height, query, scheme="light"):
            pg = browser.new_page(viewport={"width": width, "height": height}, color_scheme=scheme)
            pg.on("pageerror", lambda e: errors.append(f"{query}: {e}"))
            pg.goto(f"file://{PAGE}?{query}")
            pg.wait_for_timeout(700)
            return pg

        def width(pg):
            return pg.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")

        pg = page(1440, 1000, "scenario=ready&lang=en")
        pg.screenshot(path=os.path.join(OUT, "overview.png"))
        pg.locator("#hs-ntfy").screenshot(path=os.path.join(OUT, "ntfy.png"))
        pg.locator("#hs-addons").screenshot(path=os.path.join(OUT, "addons.png"))
        pg = page(1440, 1000, "scenario=ready&lang=en", "dark")
        pg.screenshot(path=os.path.join(OUT, "overview-dark.png"))
        pg = page(1440, 1000, "scenario=open&lang=en")
        pg.locator("#hs-ntfy").screenshot(path=os.path.join(OUT, "ntfy-open.png"))
        pg = page(1440, 1000, "scenario=ready&lang=en")
        pg.click("#hs-ntfy-check")
        pg.click("#hs-dialog-confirm")
        pg.wait_for_timeout(700)
        pg.screenshot(path=os.path.join(OUT, "check-dialog.png"))
        pg = page(1440, 900, "scenario=fresh&lang=en")
        pg.screenshot(path=os.path.join(OUT, "fresh.png"))

        for scheme in ("light", "dark"):
            for sc in SCENARIOS:
                for lang in ("en", "de"):
                    for admin in ("1", "0"):
                        pg = page(390, 1400, f"scenario={sc}&lang={lang}&admin={admin}", scheme)
                        widths[f"{sc} {lang} admin={admin} {scheme}"] = width(pg)
                        if sc == "ready" and lang == "en" and admin == "1":
                            pg.screenshot(path=os.path.join(OUT, f"mobile{'-dark' if scheme == 'dark' else ''}.png"))
                        pg.close()
        pg = page(390, 844, "scenario=ready&lang=de")
        pg.click("#hs-token-set")
        pg.wait_for_timeout(300)
        widths["dialog on a phone"] = pg.evaluate("() => { const d = document.getElementById('hs-dialog');"
                                                  " return [d.scrollWidth, d.clientWidth]; }")
        browser.close()

    print("Screenshots:", OUT)
    if errors:
        sys.exit("JavaScript errors: " + "; ".join(errors))
    for what, (scroll, client) in widths.items():
        if scroll > client:
            sys.exit(f"Horizontal scrolling on the phone ({what}): {scroll} > {client}")


if __name__ == "__main__":
    main()
