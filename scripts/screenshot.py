"""Write docs/img/filingintel-ask.png, the README screenshot of the Ask view (Step 14).

Starts the app headless with dummy API keys, so a cache miss fails instead of
spending, clicks the lookup sample chip, waits for the answer (its first
citation's passage opens with it), opens the answer trace, and saves the page from the question down
to the end of the trace.
Needs the dev Postgres and Neo4j running, since a replay still retrieves, and
Playwright's headless Chromium (``uv run --group screenshot playwright install
chromium --only-shell``, once).  Run by hand, never in CI:

    uv run --group screenshot --env-file .env python scripts/screenshot.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import Page, sync_playwright

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "img" / "filingintel-ask.png"
PORT = 8599
URL = f"http://127.0.0.1:{PORT}"
DUMMY_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "VOYAGE_API_KEY")
VIEWPORT = {"width": 1400, "height": 3200}  # tall: Streamlit scrolls its own container
CHIP = "lookup:"  # the lookup chip: a cached sample with citations
START_TIMEOUT_S = 60
LOG_TAIL_LINES = 20
ANSWER_TIMEOUT_MS = 120_000
IDLE = "[data-testid='stApp'][data-test-script-state='notRunning']"


def _app_env() -> dict[str, str]:
    env = {**os.environ, **{name: "dummy" for name in DUMMY_KEYS}}
    env.pop("ANTHROPIC_BASE_URL", None)
    env.pop("PHOENIX_COLLECTOR_ENDPOINT", None)  # no trace link in the picture
    return env


def _wait_for_server() -> None:
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{URL}/_stcore/health", timeout=2) as reply:
                if reply.status == 200:
                    return
        except (URLError, OSError):
            time.sleep(0.5)
    raise SystemExit(f"the app did not start on {URL} within {START_TIMEOUT_S} s")


def _settle(page: Page) -> None:
    """Wait until Streamlit reports the script is not running (its own e2e hook)."""
    page.wait_for_timeout(500)
    page.wait_for_selector(IDLE, timeout=ANSWER_TIMEOUT_MS)


def _capture(page: Page) -> None:
    page.set_default_timeout(ANSWER_TIMEOUT_MS)
    page.goto(URL)
    page.get_by_text(CHIP).first.click()
    page.get_by_text("Open the filing on EDGAR").wait_for(timeout=ANSWER_TIMEOUT_MS)
    _settle(page)
    if page.locator("[data-testid='stAlert']").count():
        raise SystemExit("the page shows an error; is the sample still in the cache?")
    page.locator("summary", has_text="Answer trace").click()
    _settle(page)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    top = page.locator("h3").first.bounding_box()
    bottom = page.get_by_text("Metrics", exact=True).bounding_box()
    if top is None or bottom is None:
        raise SystemExit("the answer or its metrics heading is not on the page")
    clip = {"x": 0, "y": top["y"] - 24, "width": VIEWPORT["width"],
            "height": bottom["y"] - top["y"]}
    page.screenshot(path=str(OUT), animations="disabled", clip=clip)


def main() -> int:
    with tempfile.TemporaryFile("w+", encoding="utf-8") as log:
        app = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "ui/app.py", "--server.port", str(PORT),
             "--server.headless", "true"],
            cwd=REPO, env=_app_env(), stdout=log, stderr=subprocess.STDOUT)
        try:
            return _run(app)
        except BaseException:
            log.seek(0)
            tail = log.read().splitlines()[-LOG_TAIL_LINES:]
            print("the app's last output:", *tail, sep="\n", file=sys.stderr)
            raise


def _run(app: subprocess.Popen) -> int:
    try:
        _wait_for_server()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport=VIEWPORT, color_scheme="light")
                _capture(page)
            finally:
                browser.close()
    finally:
        app.terminate()
        app.wait(timeout=10)
    print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
