"""Watch a shared folder. Every new export is read, matched, stored and archived within seconds.

    python -m mqi.ingest.watcher            # watches data/inbox
    python -m mqi.ingest.watcher D:/LabShare
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .. import config
from .pipeline import process_file


class _Handler(FileSystemEventHandler):
    def __init__(self, settle: float = 1.0):
        self.settle = settle

    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.name.startswith((".", "~")):
            return
        time.sleep(self.settle)  # let the instrument finish writing
        r = process_file(p, archive=True)
        mark = {"stored": "OK ", "review": "?? ", "unknown": "NEW", "error": "ERR"}[r.status]
        conf = f"{r.confidence:.2f}" if r.confidence is not None else "  - "
        print(f"[{mark}] {r.file:<40} {r.profile or '-':<22} conf={conf} rows={r.rows} {r.note}", flush=True)


def watch(folder: Path | str | None = None) -> None:
    folder = Path(folder or config.INBOX_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    obs = Observer()
    obs.schedule(_Handler(), str(folder), recursive=False)
    obs.start()
    print(f"Watching {folder}  (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        obs.stop()
    obs.join()


if __name__ == "__main__":
    watch(sys.argv[1] if len(sys.argv) > 1 else None)
