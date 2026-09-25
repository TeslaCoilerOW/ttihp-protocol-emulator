#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Record Slurm job ids in the shared campaign manifest (vcamp/manifest.json).

The manifest is shared with other campaigns, so updates take an flock and only
touch the "random" section:
  manifest.py <manifest.json> job <name> <jobid> [key=value ...]
  manifest.py <manifest.json> set <key> <json-value>
"""

import fcntl
import json
import os
import sys
import time
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1])
    action = sys.argv[2]
    lock = path.with_name(path.name + ".lock")
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        data = json.loads(path.read_text()) if path.exists() and path.stat().st_size else {}
        section = data.setdefault("random", {})
        if action == "job":
            name, jobid = sys.argv[3], sys.argv[4]
            extra = dict(arg.split("=", 1) for arg in sys.argv[5:])
            section.setdefault("jobs", []).append({"name": name, "job_id": jobid,
                                                   "submitted": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                                   **extra})
        elif action == "set":
            section[sys.argv[3]] = json.loads(sys.argv[4])
        else:
            raise SystemExit(f"unknown action {action}")
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=1) + "\n")
        os.replace(tmp, path)


if __name__ == "__main__":
    main()
