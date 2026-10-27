# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.45"]
# ///
"""Screenshots of Boiler Demand in the dev Home Assistant (dev/docker-compose.yml).

Takes the integration page, the options dialog, the cards (dashboard dashboard-test)
and the main device page, into dev/screenshots/.

One-time setup: in the dev instance open your profile -> Security -> Long-lived access
tokens -> Create token, and save it in dev/.ha_token (or set HA_TOKEN).

    uv run dev/screenshots.py
    uv run dev/screenshots.py --dark --lang de
    uv run dev/screenshots.py --width 420          # phone layout
    uv run dev/screenshots.py --only cards

Chromium for Playwright is installed on the first run.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

from playwright.sync_api import Error as PlaywrightError, Page, sync_playwright

DEV = Path(__file__).resolve().parent
TOKEN_FILE = DEV / ".ha_token"
DASHBOARD = "dashboard-test"
CARDS = ("boiler-demand-card", "boiler-demand-history-card")
SHOTS = ("config", "cards", "device")
SETTLE_MS = 1500  # charts, fonts and animations after the page is loaded


def token() -> str:
    value = os.environ.get("HA_TOKEN") or (TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else "")
    if not value:
        sys.exit(
            f"No token: create a long-lived access token in the dev instance (profile -> Security)\n"
            f"and save it in {TOKEN_FILE} or set HA_TOKEN."
        )
    return value


def render_template(url: str, access_token: str, template: str) -> str:
    request = urllib.request.Request(
        f"{url}/api/template",
        data=json.dumps({"template": template}).encode(),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode()


def main_device_id(url: str, access_token: str) -> str | None:
    """The Boiler Demand device itself (room devices hang below it via via_device_id)."""
    ids = render_template(
        url,
        access_token,
        "{{ integration_entities('boiler_demand') | map('device_id') | reject('none') | unique"
        " | select('is_device_attr', 'via_device_id', None) | list | tojson }}",
    )
    found = json.loads(ids)
    return found[0] if found else None


def settle(page: Page) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(SETTLE_MS)
    # Survey/notice toasts would cover the bottom of every shot.
    close = page.locator("ha-toast").get_by_role("button", name=re.compile(r"^(Close|Schließen|Dismiss)$"))
    if close.count() and close.first.is_visible():
        close.first.click()
        page.wait_for_timeout(500)


def shoot_config(page: Page, url: str, out: Path) -> None:
    page.goto(f"{url}/config/integrations/integration/boiler_demand")
    settle(page)
    page.screenshot(path=out / "config-integration.png")
    print("  config-integration.png")

    configure = page.get_by_role("button", name=re.compile(r"^(Configure|Konfigurieren)$")).first
    try:
        configure.click(timeout=5000)
    except PlaywrightError:
        print("  ! no Configure button found, options dialog skipped")
        return
    # The dialog host itself is 0x0, so wait for the form inside it.
    dialog = page.locator("dialog-data-entry-flow")
    dialog.locator("step-flow-form").wait_for(timeout=10000)
    settle(page)
    page.screenshot(path=out / "config-options.png")
    print("  config-options.png")
    page.keyboard.press("Escape")  # closed without submitting, nothing is saved


def shoot_cards(page: Page, url: str, out: Path) -> None:
    page.goto(f"{url}/{DASHBOARD}")
    page.locator(CARDS[0]).first.wait_for(timeout=20000)
    try:
        page.locator(f"{CARDS[1]} svg").first.wait_for(timeout=20000)  # history loaded
    except PlaywrightError:
        print("  ! history card did not render its charts")
    settle(page)
    for card in CARDS:
        for i, element in enumerate(page.locator(card).all()):
            name = f"{card.removeprefix('boiler-demand-')}{f'-{i + 1}' if i else ''}.png"
            element.screenshot(path=out / name)
            print(f"  {name}")


def shoot_device(page: Page, url: str, out: Path, device_id: str | None) -> None:
    if device_id is None:
        print("  ! no Boiler Demand device found")
        return
    page.goto(f"{url}/config/devices/device/{device_id}")
    settle(page)
    page.screenshot(path=out / "device.png")
    print("  device.png")


def launch(playwright):
    try:
        return playwright.chromium.launch()
    except PlaywrightError:
        print("Installing Chromium for Playwright (first run)...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        return playwright.chromium.launch()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8123")
    ap.add_argument("--out", type=Path, default=DEV / "screenshots")
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--height", type=int, default=1200)
    ap.add_argument("--dark", action="store_true", help="dark theme")
    ap.add_argument("--lang", help="frontend language, e.g. de")
    ap.add_argument("--only", choices=SHOTS, action="append", help="only these (repeatable)")
    args = ap.parse_args()

    url = args.url.rstrip("/")
    access_token = token()
    try:
        device_id = main_device_id(url, access_token)
    except OSError as err:
        sys.exit(f"Cannot reach {url} ({err}). Is the dev instance running? "
                 "docker compose -f dev/docker-compose.yml up -d")
    args.out.mkdir(parents=True, exist_ok=True)

    # The frontend reads its login from localStorage; a long-lived token never expires.
    storage = {
        "hassTokens": {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 1800,
            "hassUrl": url,
            "clientId": f"{url}/",
            "expires": 9999999999999,
            "refresh_token": "",
        },
        "selectedTheme": {"theme": "default", "dark": args.dark},
    }
    if args.lang:
        storage["selectedLanguage"] = args.lang
    init = "".join(
        f"localStorage.setItem({json.dumps(k)}, {json.dumps(json.dumps(v))});" for k, v in storage.items()
    )

    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=2 if args.width < 600 else 1,
            color_scheme="dark" if args.dark else "light",
        )
        context.add_init_script(init)
        page = context.new_page()
        print(f"Screenshots -> {args.out}")
        for shot in args.only or SHOTS:
            try:
                if shot == "config":
                    shoot_config(page, url, args.out)
                elif shot == "cards":
                    shoot_cards(page, url, args.out)
                else:
                    shoot_device(page, url, args.out, device_id)
            except PlaywrightError as err:
                print(f"  ! {shot} failed: {err.message.splitlines()[0]}")
        browser.close()


if __name__ == "__main__":
    main()
