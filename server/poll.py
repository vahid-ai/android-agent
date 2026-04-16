"""
Appium screen-capture poller.

Drives no UI itself — you interact with the device via scrcpy (or physically).
This script connects to a running Appium server, creates (or reuses) a
UiAutomator2 session, then polls the XML source hierarchy.  Every time the
screen changes it saves a timestamped XML + PNG screenshot pair into a
captures/ directory.

Usage:
    # Attach to whatever app is on screen (creates a new session):
    uv run poll.py

    # Attach to a specific package:
    uv run poll.py --package com.zoho.inventory

    # Reuse an existing Appium Inspector session:
    uv run poll.py --session-id <session-id>

    # Custom poll interval and Appium host/port:
    uv run poll.py --interval 0.3 --host 127.0.0.1 --port 4723
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CAPTURES_DIR = Path(__file__).parent / "captures"


def appium_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def create_session(
    base: str,
    package: str | None = None,
    activity: str | None = None,
) -> str:
    """Create a new Appium UiAutomator2 session and return its ID."""
    caps: dict = {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:deviceName": "Android",
        "appium:noReset": True,
    }
    if package:
        caps["appium:appPackage"] = package
        if activity:
            caps["appium:appActivity"] = activity
    else:
        # Attach to whatever is currently on screen
        caps["appium:autoLaunch"] = False

    payload = {
        "capabilities": {
            "alwaysMatch": caps,
        },
    }
    resp = requests.post(f"{base}/session", json=payload, timeout=30)
    resp.raise_for_status()
    session_id = resp.json()["value"]["sessionId"]
    print(f"Created Appium session: {session_id}")
    return session_id


def get_source(base: str, session_id: str) -> str:
    """Fetch the current XML page source from Appium."""
    resp = requests.get(f"{base}/session/{session_id}/source", timeout=10)
    resp.raise_for_status()
    return resp.json()["value"]


def get_screenshot_b64(base: str, session_id: str) -> str:
    """Fetch a base64-encoded PNG screenshot from Appium."""
    resp = requests.get(f"{base}/session/{session_id}/screenshot", timeout=10)
    resp.raise_for_status()
    return resp.json()["value"]


def save_capture(xml: str, png_b64: str, seq: int) -> Path:
    """Write an XML + PNG pair to the captures directory. Returns the XML path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{seq:04d}_{ts}"

    xml_path = CAPTURES_DIR / f"{stem}.xml"
    png_path = CAPTURES_DIR / f"{stem}.png"

    xml_path.write_text(xml, encoding="utf-8")
    png_path.write_bytes(base64.b64decode(png_b64))

    return xml_path


def poll(base: str, session_id: str, interval: float) -> None:
    """Poll Appium for screen changes until interrupted."""
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    seq = 0
    print(f"Polling every {interval}s — press Ctrl+C to stop.")
    print(f"Captures saved to: {CAPTURES_DIR.resolve()}\n")

    while True:
        try:
            xml = get_source(base, session_id)
        except requests.RequestException as exc:
            print(f"[warn] Failed to fetch source: {exc}")
            time.sleep(interval)
            continue

        digest = hashlib.sha1(xml.encode()).hexdigest()[:12]
        if digest in seen:
            time.sleep(interval)
            continue

        seen.add(digest)
        seq += 1

        try:
            png_b64 = get_screenshot_b64(base, session_id)
        except requests.RequestException:
            png_b64 = ""

        path = save_capture(xml, png_b64, seq)
        print(f"[{seq:>4}] New screen captured  hash={digest}  -> {path.name}")
        time.sleep(interval)


def delete_session(base: str, session_id: str) -> None:
    """Delete the Appium session (best-effort)."""
    try:
        requests.delete(f"{base}/session/{session_id}", timeout=5)
        print(f"\nDeleted Appium session: {session_id}")
    except requests.RequestException:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Poll Appium for screen changes and save XML + screenshots.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Appium server host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=4723, help="Appium server port (default: 4723)",
    )
    parser.add_argument(
        "--session-id",
        help="Reuse an existing Appium session instead of creating one.",
    )
    parser.add_argument(
        "--package", help="Android app package to attach to (e.g. com.zoho.inventory).",
    )
    parser.add_argument(
        "--activity", help="Launch activity (auto-resolved if omitted).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Polling interval in seconds (default: 0.5).",
    )
    args = parser.parse_args()

    base = appium_url(args.host, args.port)
    own_session = False

    if args.session_id:
        session_id = args.session_id
        print(f"Reusing existing session: {session_id}")
    else:
        session_id = create_session(base, args.package, args.activity)
        own_session = True

    # Clean up session on exit if we created it
    def handle_exit(*_: object) -> None:
        if own_session:
            delete_session(base, session_id)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    poll(base, session_id, args.interval)


if __name__ == "__main__":
    main()
