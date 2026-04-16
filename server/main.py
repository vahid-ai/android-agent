"""
Event-driven screen capture server.

Instead of polling, this watches `adb logcat` for ActivityManager and
WindowManager events that signal a screen change.  When one is detected it
waits a short debounce period (to let the UI settle), fetches the XML source
and a screenshot from a running Appium session, deduplicates by hash, and
saves new captures to disk.

A FastAPI server exposes endpoints to manage the Appium session, start/stop
the logcat watcher, and list or retrieve captures.

Usage:
    # Start the server (creates an Appium session automatically):
    uv run main.py

    # Attach to a specific package:
    uv run main.py --package com.zoho.inventory

    # Reuse an existing Appium session:
    uv run main.py --session-id <id>
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("capture")

CAPTURES_DIR = Path(__file__).parent / "captures"

# Logcat patterns that indicate a meaningful screen change
SCREEN_CHANGE_PATTERNS = [
    re.compile(r"ActivityTaskManager.*START.*cmp="),       # activity launch
    re.compile(r"ActivityTaskManager.*Displayed"),          # activity rendered
    re.compile(r"WindowManager.*Adding window"),            # new window (dialog, popup)
    re.compile(r"WindowManager.*Removing window"),          # window dismissed
    re.compile(r"InputDispatcher.*Focus.*entered"),         # focus change
]

# ---------------------------------------------------------------------------
# Appium helpers (async)
# ---------------------------------------------------------------------------

class AppiumClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 4723):
        self.base = f"http://{host}:{port}"
        self.session_id: str | None = None
        self._own_session = False
        self._http = httpx.AsyncClient(timeout=30)

    async def create_session(
        self,
        package: str | None = None,
        activity: str | None = None,
    ) -> str:
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
            caps["appium:autoLaunch"] = False

        payload = {"capabilities": {"alwaysMatch": caps}}
        resp = await self._http.post(f"{self.base}/session", json=payload)
        resp.raise_for_status()
        self.session_id = resp.json()["value"]["sessionId"]
        self._own_session = True
        logger.info("Created Appium session: %s", self.session_id)
        return self.session_id

    def attach_session(self, session_id: str) -> None:
        self.session_id = session_id
        self._own_session = False
        logger.info("Attached to existing session: %s", session_id)

    async def get_source(self) -> str:
        resp = await self._http.get(
            f"{self.base}/session/{self.session_id}/source",
        )
        resp.raise_for_status()
        return resp.json()["value"]

    async def get_screenshot_b64(self) -> str:
        resp = await self._http.get(
            f"{self.base}/session/{self.session_id}/screenshot",
        )
        resp.raise_for_status()
        return resp.json()["value"]

    async def delete_session(self) -> None:
        if self.session_id and self._own_session:
            try:
                await self._http.delete(
                    f"{self.base}/session/{self.session_id}",
                )
                logger.info("Deleted Appium session: %s", self.session_id)
            except httpx.HTTPError:
                pass
            self.session_id = None

    async def close(self) -> None:
        await self.delete_session()
        await self._http.aclose()


# ---------------------------------------------------------------------------
# Capture store
# ---------------------------------------------------------------------------

class CaptureStore:
    def __init__(self, directory: Path = CAPTURES_DIR):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        self._seq = 0

    def save(self, xml: str, png_b64: str) -> dict | None:
        digest = hashlib.sha1(xml.encode()).hexdigest()[:12]
        if digest in self._seen:
            return None
        self._seen.add(digest)
        self._seq += 1

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stem = f"{self._seq:04d}_{ts}"

        xml_path = self.dir / f"{stem}.xml"
        png_path = self.dir / f"{stem}.png"

        xml_path.write_text(xml, encoding="utf-8")
        if png_b64:
            png_path.write_bytes(base64.b64decode(png_b64))

        logger.info("[%4d] Captured  hash=%s  -> %s", self._seq, digest, stem)
        return {
            "seq": self._seq,
            "hash": digest,
            "xml": xml_path.name,
            "png": png_path.name,
        }

    def list_captures(self) -> list[dict]:
        items = []
        for xml_path in sorted(self.dir.glob("*.xml")):
            png_path = xml_path.with_suffix(".png")
            items.append({
                "xml": xml_path.name,
                "png": png_path.name if png_path.exists() else None,
            })
        return items


# ---------------------------------------------------------------------------
# Logcat watcher
# ---------------------------------------------------------------------------

class LogcatWatcher:
    def __init__(
        self,
        appium: AppiumClient,
        store: CaptureStore,
        debounce: float = 0.8,
        serial: str | None = None,
    ):
        self.appium = appium
        self.store = store
        self.debounce = debounce
        self.serial = serial
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self.running = False

    async def start(self) -> None:
        if self.running:
            return
        cmd = ["adb"]
        if self.serial:
            cmd += ["-s", self.serial]
        # -T 1: only stream new logcat lines (skip history buffer)
        cmd += ["logcat", "-v", "time", "-T", "1"]

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.running = True
        self._task = asyncio.create_task(self._read_loop())
        logger.info("Logcat watcher started (debounce=%.1fs)", self.debounce)

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        pending_capture: asyncio.Task | None = None

        while self.running:
            raw = await self._proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace")

            if not any(p.search(line) for p in SCREEN_CHANGE_PATTERNS):
                continue

            logger.info("Screen-change event: %s", line.strip())

            # Cancel any pending debounced capture and restart the timer
            if pending_capture and not pending_capture.done():
                pending_capture.cancel()
            pending_capture = asyncio.create_task(self._debounced_capture())

    async def _debounced_capture(self) -> None:
        """Wait for the debounce window, then capture."""
        await asyncio.sleep(self.debounce)
        try:
            xml = await self.appium.get_source()
            png_b64 = await self.appium.get_screenshot_b64()
            self.store.save(xml, png_b64)
        except Exception as exc:
            logger.warning("Capture failed: %s", exc)

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
        if self._proc:
            self._proc.terminate()
            await self._proc.wait()
            self._proc = None
        logger.info("Logcat watcher stopped")


# ---------------------------------------------------------------------------
# App state & lifespan
# ---------------------------------------------------------------------------

class AppState:
    appium: AppiumClient
    store: CaptureStore
    watcher: LogcatWatcher


state = AppState()


def parse_args() -> dict:
    import argparse

    parser = argparse.ArgumentParser(
        description="Event-driven Appium screen capture server.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Appium host")
    parser.add_argument("--port", type=int, default=4723, help="Appium port")
    parser.add_argument("--session-id", help="Reuse an existing Appium session")
    parser.add_argument("--package", help="Android app package")
    parser.add_argument("--activity", help="Launch activity")
    parser.add_argument("--serial", help="ADB device serial (e.g. emulator-5554)")
    parser.add_argument(
        "--debounce",
        type=float,
        default=0.8,
        help="Seconds to wait after a logcat event before capturing (default: 0.8)",
    )
    parser.add_argument(
        "--server-port",
        type=int,
        default=8000,
        help="FastAPI server port (default: 8000)",
    )
    args = parser.parse_args()
    return vars(args)


@asynccontextmanager
async def lifespan(app: FastAPI):
    args = app.state.startup_args

    # Appium client
    state.appium = AppiumClient(host=args["host"], port=args["port"])
    if args.get("session_id"):
        state.appium.attach_session(args["session_id"])
    else:
        await state.appium.create_session(args.get("package"), args.get("activity"))

    # Capture store
    state.store = CaptureStore()

    # Logcat watcher
    state.watcher = LogcatWatcher(
        appium=state.appium,
        store=state.store,
        debounce=args.get("debounce", 0.8),
        serial=args.get("serial"),
    )
    await state.watcher.start()

    yield

    await state.watcher.stop()
    await state.appium.close()


app = FastAPI(title="Android Screen Capture", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/status")
async def get_status():
    return {
        "session_id": state.appium.session_id,
        "watcher_running": state.watcher.running,
        "captures": len(state.store.list_captures()),
    }


@app.post("/capture")
async def trigger_capture():
    """Manually trigger a capture right now."""
    try:
        xml = await state.appium.get_source()
        png_b64 = await state.appium.get_screenshot_b64()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    result = state.store.save(xml, png_b64)
    if result is None:
        return {"status": "duplicate", "message": "Screen unchanged since last capture"}
    return {"status": "captured", **result}


@app.get("/captures")
async def list_captures():
    return state.store.list_captures()


@app.get("/captures/{filename}")
async def get_capture(filename: str):
    path = CAPTURES_DIR / filename
    if not path.exists() or not path.is_relative_to(CAPTURES_DIR):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)


class SessionRequest(BaseModel):
    package: str | None = None
    activity: str | None = None
    session_id: str | None = None


@app.post("/session")
async def create_session(req: SessionRequest):
    """Create a new Appium session (drops the current one first)."""
    await state.appium.delete_session()
    if req.session_id:
        state.appium.attach_session(req.session_id)
    else:
        await state.appium.create_session(req.package, req.activity)
    return {"session_id": state.appium.session_id}


@app.delete("/session")
async def delete_session():
    await state.appium.delete_session()
    return {"status": "deleted"}


@app.post("/watcher/start")
async def start_watcher():
    await state.watcher.start()
    return {"status": "started"}


@app.post("/watcher/stop")
async def stop_watcher():
    await state.watcher.stop()
    return {"status": "stopped"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args()
    server_port = args.pop("server_port")

    app.state.startup_args = args
    uvicorn.run(app, host="0.0.0.0", port=server_port, log_level="info")


if __name__ == "__main__":
    main()
