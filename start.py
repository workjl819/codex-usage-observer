#!/usr/bin/env python3
"""Run a full refresh, then start the local dashboard."""

from __future__ import annotations

import threading
import time

from collector import ingest_all, DB_PATH
from dashboard import HOST, PORT, ThreadingHTTPServer, Handler

REFRESH_INTERVAL_SECONDS = 5


def run_ingest_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        scanned, written = ingest_all()
        print(
            f"[ingest] scanned={scanned} upserted={written} interval={REFRESH_INTERVAL_SECONDS}s",
            flush=True,
        )
        stop_event.wait(REFRESH_INTERVAL_SECONDS)


def main() -> int:
    scanned, written = ingest_all()
    print(f"Database: {DB_PATH}")
    print(f"Scanned session files: {scanned}")
    print(f"Upserted completed turns: {written}")
    stop_event = threading.Event()
    worker = threading.Thread(target=run_ingest_loop, args=(stop_event,), daemon=True)
    worker.start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard: http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Codex Usage Observer...")
    finally:
        stop_event.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
