"""Command line: python -m mqi <command>

  init          create the demo plant if the store is empty or holds an older demo (safe to run on every start)
  rebuild-demo  rebuild the demo plant (refused when the store holds plant data)
  info          show which store is used and its state
"""
from __future__ import annotations

import sys

from . import db, demo


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "info"
    if cmd == "info":
        print(f"backend={db.backend()} exists={db.exists()} demo_version={db.get_meta('demo_version') if db.exists() else None}")
        return 0
    if cmd == "init":
        if not db.exists() or demo.is_stale():
            print("building demo plant ...", flush=True)
            demo.build()
        print(f"ready: backend={db.backend()} demo_version={db.get_meta('demo_version')}")
        return 0
    if cmd == "rebuild-demo":
        if db.exists() and db.get_meta("demo_version") is None:
            print("refused: the store holds plant data, not demo data", file=sys.stderr)
            return 1
        demo.build()
        print("demo rebuilt")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
