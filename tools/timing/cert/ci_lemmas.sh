#!/usr/bin/env bash
# Prove the image-independent lemmas the certificate composition rests on, on
# one machine (the certs CI workflow; also usable on a laptop):
#
#   boundary_lemmas.sv  k0..k3 (one per engine), cover_k0..cover_k3, neg
#   sram_lemma.sv       prove, cover, neg
#
# the same 12 tasks as the lemma list of campaign.sh. Exit 0 only when every
# proof and cover passes and both negative controls fail.
#
#   ci_lemmas.sh OUT RTL_DIR MODELS_DIR
#
# Environment: OSS CAD Suite on PATH (sby, yosys, yices, bitwuzla, ABC);
#   CI_PAR      tasks in parallel (default 2)
#   SBY_TIMEOUT seconds per task (default 1800; each takes under a minute)
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${1:?usage: ci_lemmas.sh OUT RTL_DIR MODELS_DIR}
RTL=${2:?RTL_DIR}
MODELS=${3:?MODELS_DIR}
PAR=${CI_PAR:-2}
export SBY_TIMEOUT=${SBY_TIMEOUT:-1800}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
rm -rf "$OUT/lemmas"
python3 "$HERE/gen_cert.py" emit-lemmas --out "$OUT/lemmas" --rtl "$RTL" --models "$MODELS" || exit 1
tasks="k0 k1 k2 k3 cover_k0 cover_k1 cover_k2 cover_k3 neg"
{
  for t in $tasks; do echo "$OUT/lemmas boundary_lemmas $t"; done
  for t in prove cover neg; do echo "$OUT/lemmas sram_lemma $t"; done
} > "$OUT/lemma_tasks.txt"
start=$(date +%s)
# shellcheck disable=SC2016  # $0 and $@ belong to the inner bash
xargs -P "$PAR" -L 1 bash -c '"$0"/run_one.sh "$@"' "$HERE" < "$OUT/lemma_tasks.txt"
echo "ci_lemmas: 12 tasks finished in $(( $(date +%s) - start )) s with $PAR in parallel"
python3 - "$OUT" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
rows, bad = [], []
for line in (out / "lemma_tasks.txt").read_text().splitlines():
    d, name, task = line.split()
    want = "FAIL" if task == "neg" else "PASS"
    f = Path(d, "results", f"{name}.{task}.json")
    r = json.loads(f.read_text()) if f.exists() else {"status": "not run", "seconds": None}
    ok = r["status"] == want
    rows.append(f"| `{name}` {task} | {want} | {r['status']} | {r.get('seconds')} |")
    if not ok:
        bad.append(f"{name} {task}: {r['status']} (expected {want})")
md = ["| lemma task | expected | outcome | s |", "|---|---|---|---:|"] + rows
(out / "lemmas.md").write_text("\n".join(md) + "\n")
print("\n".join(md))
print(f"ci_lemmas: {12 - len(bad)}/12 lemma tasks met their expectation")
for b in bad:
    print("ci_lemmas: FAIL", b)
sys.exit(1 if bad else 0)
PY
