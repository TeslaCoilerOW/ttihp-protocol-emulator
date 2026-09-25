#!/usr/bin/env python3
"""Record a Slurm job in the shared campaign manifest (read-modify-write under flock).

usage: manifest.py MANIFEST SECTION KEY=VALUE ...

Appends {KEY: VALUE, ...} to MANIFEST[SECTION]["jobs"], leaving every other
section untouched. The manifest is shared with the other campaign workflows,
so the update holds an exclusive lock on MANIFEST.lock.
"""
import fcntl
import json
import os
import sys
import time
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1])
    section = sys.argv[2]
    entry = dict(kv.split("=", 1) for kv in sys.argv[3:])
    entry.setdefault("recorded", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    with open(str(path) + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text()) if path.exists() and path.stat().st_size else {}
        data.setdefault(section, {}).setdefault("jobs", []).append(entry)
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=1) + "\n")
        os.replace(tmp, path)
    print(json.dumps(entry))


if __name__ == "__main__":
    main()
