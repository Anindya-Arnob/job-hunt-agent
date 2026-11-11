"""Start the app:  python -m jobagent   (opens http://127.0.0.1:8780 in your browser)."""
from __future__ import annotations

import logging
import sys
import threading
import webbrowser

import uvicorn

from .config import settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())   # needed by Playwright
    url = f"http://{settings.host}:{settings.port}"
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((settings.host, settings.port))
    except OSError:
        print(f"\n  Port {settings.port} is already used by another program (maybe a second copy of Job Hunt Agent).\n"
              f"  Close that program, or put a line like  PORT=8781  into the .env file, then start again.\n")
        input("  Press Enter to close this window.")
        return
    finally:
        probe.close()
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"\n  Job Hunt Agent running at {url}  (close this window to stop)\n")
    uvicorn.run("jobagent.api.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
