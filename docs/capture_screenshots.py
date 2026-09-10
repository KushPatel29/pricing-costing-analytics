"""
Screenshot every page of the running app, for the README.

Chrome's ``--headless --screenshot`` flag is the obvious way to do this and it
does not work here. It fires the capture when its *virtual* clock runs out, and
virtual time races ahead of real time -- so on a Streamlit page, whose body
arrives over a websocket after the document has loaded, the shot lands on an
empty page with the running indicator still spinning. It worked for whichever
pages happened to answer fast enough, which is worse than not working.

So this drives Chrome over the DevTools protocol instead: navigate, wait for
the page to actually stop running, then capture. "Stop running" is asked of the
DOM rather than timed -- Streamlit marks the app container with
``data-test-script-state``, and a fixed sleep is a guess that is either too
short on a cold cache or wasted on a warm one.

Usage::

    python -m streamlit run app/streamlit_app.py --server.port 8534 &
    python docs/capture_screenshots.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

# (url path, output file). The path is the slug st.navigation gives the view.
PAGES: tuple[tuple[str, str], ...] = (
    ("", "01-executive-summary.png"),
    ("recommendations", "02-recommendations.png"),
    ("simulator", "03-pricing-simulator.png"),
    ("data_quality", "04-data-quality.png"),
    ("profitability", "05-profitability.png"),
    ("price_waterfall", "06-price-waterfall.png"),
    ("elasticity", "07-elasticity.png"),
    ("unit_economics", "08-unit-economics.png"),
    ("cost_to_price_calculator", "09-cost-to-price-calculator.png"),
    ("forecast", "10-forecast.png"),
)


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    sys.exit("no Chrome found; set one of the paths in CHROME_CANDIDATES")


def debugger_url(port: int, tries: int = 40) -> str:
    """
    The websocket for a *page*, once Chrome is listening.

    Not the one from ``/json/version`` -- that is the browser-level target, and
    it accepts ``Browser.*`` but silently ignores ``Page.navigate``. The symptom
    is a script that runs to its full timeout on every page while Chrome sits on
    about:blank, which looks like a slow app rather than a wrong endpoint.
    """
    for _ in range(tries):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=1) as response:
                targets = json.loads(response.read())
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return target["webSocketDebuggerUrl"]
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.5)
    sys.exit("Chrome never opened a page target on its debugging port")


async def capture(ws_url: str, base: str, width: int, height: int,
                  settle: float, timeout: float) -> None:
    import websockets

    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as socket:
        message_id = 0

        async def send(method: str, **params):
            nonlocal message_id
            message_id += 1
            await socket.send(json.dumps(
                {"id": message_id, "method": method, "params": params}))
            while True:
                reply = json.loads(await socket.recv())
                if reply.get("id") == message_id:
                    return reply.get("result", {})

        await send("Page.enable")
        await send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                   deviceScaleFactor=1, mobile=False)

        for path, filename in PAGES:
            await send("Page.navigate", url=f"{base}/{path}")
            deadline = time.monotonic() + timeout
            ready = False
            while time.monotonic() < deadline:
                await asyncio.sleep(0.5)
                result = await send(
                    "Runtime.evaluate",
                    expression=(
                        "(() => {"
                        "  const app = document.querySelector('[data-testid=stApp]');"
                        "  if (!app) return 'no-app';"
                        "  const state = app.getAttribute('data-test-script-state');"
                        "  const body = document.querySelector('.block-container');"
                        "  const drawn = body ? body.innerText.trim().length : 0;"
                        "  return state + ':' + drawn;"
                        "})()"
                    ),
                    returnByValue=True)
                value = str(result.get("result", {}).get("value", ""))
                state, _, drawn = value.partition(":")
                if state == "notRunning" and drawn.isdigit() and int(drawn) > 400:
                    ready = True
                    break
            # Charts animate in after the script finishes; the settle is for the
            # paint, not for the data.
            await asyncio.sleep(settle)
            shot = await send("Page.captureScreenshot", format="png",
                              captureBeyondViewport=False)
            data = shot.get("data")
            if not data:
                print(f"  {filename:34s} FAILED (no image)")
                continue
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / filename).write_bytes(base64.b64decode(data))
            size = (OUT / filename).stat().st_size // 1024
            print(f"  {filename:34s} {size:>4d}KB"
                  + ("" if ready else "   (page never settled)"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8534, help="the Streamlit port")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1150)
    ap.add_argument("--settle", type=float, default=2.5,
                    help="seconds to let charts paint after the script stops")
    ap.add_argument("--timeout", type=float, default=90.0,
                    help="seconds to wait for one page to stop running")
    args = ap.parse_args()

    chrome = find_chrome()
    # ignore_cleanup_errors because Chrome on Windows keeps a handle on
    # CrashpadMetrics-active.pma for a moment after terminate(), and a
    # PermissionError there would fail a run whose screenshots all succeeded.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
        process = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--remote-debugging-port=9222", f"--user-data-dir={profile}",
             f"--window-size={args.width},{args.height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            ws_url = debugger_url(9222)
            print(f"capturing {len(PAGES)} pages to {OUT}")
            asyncio.run(capture(ws_url, f"http://localhost:{args.port}",
                                args.width, args.height, args.settle, args.timeout))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
