"""Fetch Claude Code usage limits (/usage) via a headless PTY session.

Spawns `claude --dangerously-skip-permissions`, sends /usage, parses the
TUI output, and writes structured JSON to CACHE_FILE.  Designed to run in
a background thread so the UI is never blocked.
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path


_TMPDIR = Path(tempfile.gettempdir())
CACHE_FILE = _TMPDIR / "lazyagent_claude_usage.json"
LOCK_FILE  = _TMPDIR / "lazyagent_claude_fetch.lock"
CACHE_TTL   = 120  # seconds before a refresh is triggered
EXIT_WAIT   = 2.0


def _find_claude() -> str | None:
    """Locate the `claude` CLI binary via PATH."""
    return shutil.which("claude")


# ---------------------------------------------------------------------------
# Lock helpers
# ---------------------------------------------------------------------------

def _acquire_lock() -> bool:
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            os.kill(pid, 0)
            return False  # still running
        except (ProcessLookupError, ValueError, OSError):
            pass
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ANSI cleaning
# ---------------------------------------------------------------------------

def _clean_ansi(raw: str) -> str:
    s = raw
    s = re.sub(r'\x1b\[\d*C', ' ', s)
    s = re.sub(r'\x1b\[\d+;\d+H', '\n', s)
    s = re.sub(r'\x1b\[(\d+)(am|pm)', r'\1\2', s, flags=re.IGNORECASE)
    s = re.sub(r'\x1b\[[^A-Za-z]*[A-Za-z]', '', s)
    s = re.sub(r'\x1b\][^\x07]*\x07', '', s)
    s = re.sub(r'[█▉▊▋▌▍▎▏░▒▓▐▛▜▝▘▗▖▞▟]', '', s)
    s = s.replace('\r', '\n').replace('\t', ' ')
    s = re.sub(r' {2,}', ' ', s)
    lines = [l.strip() for l in s.split('\n') if l.strip()]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_usage(text: str) -> dict:
    result: dict = {
        "session":    None,
        "week":       None,
        "weekSonnet": None,
        "extra":      None,
        "timestamp":  int(time.time() * 1000),
        "fromCache":  False,
    }
    pct_matches = re.findall(r'(\d+)\s*%\s*used', text, re.IGNORECASE)
    reset_matches = re.findall(
        r'Rese\w*\s+([\w\d,: ]+\([\w\/]+\))', text, re.IGNORECASE
    )
    spend_match = re.search(
        r'\$(\d+\.?\d*)\s*/\s*\$(\d+\.?\d*)\s*spent', text, re.IGNORECASE
    )
    sections = ["session", "week", "weekSonnet", "extra"]
    for idx, key in enumerate(sections[:len(pct_matches)]):
        result[key] = {"percent": int(pct_matches[idx])}
        if idx < len(reset_matches):
            rt = reset_matches[idx].strip()
            rt = re.sub(r'^[a-z]{1,2}\s+', '', rt, flags=re.IGNORECASE)
            rt = re.sub(r'\s+', ' ', rt)
            result[key]["resetTime"] = rt
    if result["extra"] and spend_match:
        result["extra"]["spent"] = float(spend_match.group(1))
        result["extra"]["limit"] = float(spend_match.group(2))
    return result


# ---------------------------------------------------------------------------
# PTY fetch
# ---------------------------------------------------------------------------

def _fetch_via_pty() -> dict:
    claude = _find_claude()
    if not claude:
        raise FileNotFoundError("claude CLI not found in PATH")

    chunks: list[str] = []
    lock = threading.Lock()

    master, slave = pty.openpty()

    env = dict(os.environ)
    env.update({"NO_COLOR": "1", "FORCE_COLOR": "0",
                "TERM": "xterm-256color", "COLUMNS": "120", "LINES": "80"})
    for var in ("CLAUDECODE", "CLAUDE_SESSION_ID", "ANTHROPIC_CLAUDE_CODE"):
        env.pop(var, None)

    proc = subprocess.Popen(
        [claude, "--dangerously-skip-permissions"],
        stdin=slave, stdout=slave, stderr=slave,
        close_fds=True, cwd="/tmp", env=env,
    )
    os.close(slave)

    def _read_loop() -> None:
        while True:
            try:
                r, _, _ = select.select([master], [], [], 0.5)
                if r:
                    data = os.read(master, 4096)
                    with lock:
                        chunks.append(data.decode("utf-8", errors="replace"))
            except OSError:
                break

    threading.Thread(target=_read_loop, daemon=True).start()

    def _write(data: bytes) -> None:
        try:
            os.write(master, data)
        except OSError:
            pass

    def _cleaned() -> str:
        with lock:
            return _clean_ansi("".join(chunks))

    def _wait_for(pattern: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if re.search(pattern, _cleaned()):
                return True
            time.sleep(0.2)
        return False

    try:
        _wait_for(r"bypass permissions", timeout=8.0)
        time.sleep(0.3)
        _write(b"/usage")
        time.sleep(0.8)
        _write(b"\r")
        time.sleep(0.8)
        _write(b"\r")
        _wait_for(r"\d+\s*%\s*used", timeout=12.0)
        time.sleep(0.5)
        _write(b"/exit\r")
        time.sleep(EXIT_WAIT)
    except OSError:
        pass

    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    try:
        os.close(master)
    except OSError:
        pass

    cleaned = _cleaned()
    if re.search(r'rate.limit', cleaned, re.IGNORECASE):
        return {"error": "rate_limited", "timestamp": int(time.time() * 1000)}
    return _parse_usage(cleaned)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_cache() -> dict | None:
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return None


def _save_cache(data: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def cache_age() -> float:
    """Return seconds since the cache was last written (inf if missing)."""
    try:
        return time.time() - CACHE_FILE.stat().st_mtime
    except OSError:
        return float("inf")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_cache() -> tuple[dict | None, int]:
    """Return (cache_data, age_seconds). Drop-in for the old waybar reader."""
    data = _load_cache()
    if data is None:
        return None, 0
    age = int(time.time() - data.get("timestamp", 0) / 1000)
    return data, age


def fetch_and_cache() -> None:
    """Run a PTY fetch and update the cache. Safe to call from a thread.

    No-ops if a fetch is already in progress.
    """
    if not _acquire_lock():
        return
    try:
        data = _fetch_via_pty()
        if data.get("error") == "rate_limited":
            old = _load_cache()
            payload = old if old else {}
            payload["error"] = "rate_limited"
            payload["timestamp"] = int(time.time() * 1000)
            _save_cache(payload)
        elif data.get("session") or data.get("week"):
            _save_cache(data)
        else:
            old = _load_cache()
            if old:
                old["fromCache"] = True
                _save_cache(old)
    except Exception:
        old = _load_cache()
        if old:
            old["fromCache"] = True
            _save_cache(old)
    finally:
        _release_lock()
