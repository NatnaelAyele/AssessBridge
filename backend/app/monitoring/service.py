"""Keep the analysis worker outside the API process, including during reloads."""

import asyncio
import os
import subprocess
import sys

from ..config import ROOT_DIR


async def supervise():
    if os.getenv("MONITORING_AUTO_START", "true").lower() != "true":
        return
    directory = ROOT_DIR / "var"
    directory.mkdir(exist_ok=True)
    process = None
    with (directory / "monitoring-worker.log").open("ab", buffering=0) as log:
        try:
            while True:
                process = subprocess.Popen(
                    [sys.executable, "-m", "app.monitoring.worker", "--managed"],
                    cwd=ROOT_DIR / "backend", stdin=subprocess.PIPE, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                while process.poll() is None:
                    await asyncio.sleep(1)
                # Another API process may own the worker; its database lock is authoritative.
                await asyncio.sleep(15)
        finally:
            if process and process.poll() is None:
                try:
                    process.stdin.write(b"stop\n")
                    process.stdin.flush()
                    await asyncio.to_thread(process.wait, 12)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
                    await asyncio.to_thread(process.wait, 5)
            if process and process.stdin:
                process.stdin.close()
