#!/usr/bin/env bash
# Prove the timing certificates of some firmware images on one machine (the
# certs CI workflow; also usable on a laptop). Bounded version of a campaign:
#
#   - segments of at most CHUNK steps: the whole-segment certificate;
#   - longer segments: the chain of chunk certificates (gen_cert.py --chunk);
#   - one cover per certificate run;
#   - the negative controls of campaign.sh (one per mutation and image, the
#     cheapest node; per schedule mutation also the cheapest chunk of a long
#     segment) of BMC depth at most CHUNK + 2 (98, i.e. 96 steps); deeper
#     ones are listed as skipped (the cluster campaign runs them). Nodes a
#     wrong analyzer's schedule cannot be written for are listed in
#     neg/manifest.json (not_generated) and do not stop the run.
#
# Exit 0 only when every certificate passes, every cover is reached and every
# negative control that ran fails.
#
#   ci_prove.sh OUT RTL_DIR MODELS_DIR IMAGE...
#
# Environment: OSS CAD Suite on PATH (sby, yosys, yices, boolector);
#   CI_PAR     proof (bmc) runs in parallel (default 2: a GitHub runner has 4
#              CPUs and 16 GB; 4 runs of 2 solvers each exceed 16 GB on the
#              longest chunks)
#   CI_COVER_PAR cover runs in parallel (default 4: yices alone; four chunk
#              covers at once peaked at about 9 GB on the cluster)
#   CI_ENGINES solver portfolio of the proof (bmc) runs (default boolector,yices)
#   CI_COVER_ENGINES solvers of the cover runs (default yices: on the first
#              GitHub run, 36624435421, a boolector+yices cover of
#              uart-rx-idle's first chunk used up the runner's 16 GB and the
#              job was killed; the cluster campaign of 24f31f0 also ran its
#              covers with yices alone)
#   CI_CHUNK   chunk size in steps (default 96)
#   SBY_TIMEOUT seconds per run (default 3600)
#   CI_MAX_RUNS an image needing more runs is not proved here (default 200:
#              at the slowest rate measured, about 50 s per run with 4 CPUs
#              including yices reruns, about 2 h 45 min, under the workflow's
#              300-minute job limit): the script says so (a
#              ::warning:: annotation under GitHub Actions) and exits 0; such
#              images are certified by the cluster campaign only. The certs
#              workflow does not start a job for them at all: ledger.py plan
#              --budget leaves them out with the same count (gen_cert
#              preflight).
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${1:?usage: ci_prove.sh OUT RTL_DIR MODELS_DIR IMAGE...}
RTL=${2:?RTL_DIR}
MODELS=${3:?MODELS_DIR}
shift 3
[ $# -gt 0 ] || { echo "ci_prove.sh: no images" >&2; exit 2; }
CHUNK=${CI_CHUNK:-96}
ENGINES=${CI_ENGINES:-boolector,yices}
COVER_ENGINES=${CI_COVER_ENGINES:-yices}
PAR=${CI_PAR:-2}
COVER_PAR=${CI_COVER_PAR:-4}
export SBY_TIMEOUT=${SBY_TIMEOUT:-3600}
GEN="python3 $HERE/gen_cert.py"
MUTANTS="wait-n-cycles xfer-late-edge count-n-iterations open-drain-as-push-pull halt-keeps-pins set-two-cycles"
SCHEDULE_MUTANTS="pad-late pad-early pad-level issue-late post-limit branch-late"

mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
rm -rf "$OUT/certs" "$OUT/long" "$OUT/neg"
set -e
$GEN emit "$@" --out "$OUT/certs" --rtl "$RTL" --models "$MODELS" --engines "$ENGINES"
$GEN emit "$@" --out "$OUT/long" --rtl "$RTL" --models "$MODELS" --engines "$ENGINES" --chunk "$CHUNK"
# long/ keeps only the chunks of the split segments, as in campaign.sh
# prepare (the whole-segment certificates are in certs/)
python3 - "$OUT/long" <<'PY'
import json, os, sys
d = sys.argv[1]
m = json.load(open(f"{d}/manifest.json"))
for c in m["certificates"]:
    if c["chunk"] is None:
        for ext in ("sv", "sby"):
            os.remove(f"{d}/{c['certificate']}.{ext}")
m["certificates"] = [c for c in m["certificates"] if c["chunk"] is not None]
json.dump(m, open(f"{d}/manifest.json", "w"), indent=1)
PY
for m in $MUTANTS; do
  $GEN emit "$@" --out "$OUT/neg" --rtl "$RTL" --models "$MODELS" --engines "$ENGINES" \
      --mutant "$m" --pick-one --append
done
for m in $SCHEDULE_MUTANTS; do
  $GEN emit "$@" --out "$OUT/neg" --rtl "$RTL" --models "$MODELS" --engines "$ENGINES" \
      --schedule-mutant "$m" --pick-one --append
  $GEN emit "$@" --out "$OUT/neg" --rtl "$RTL" --models "$MODELS" --engines "$ENGINES" \
      --schedule-mutant "$m" --chunk "$CHUNK" --chunk-controls --pick-one --append
done
set +e
python3 - "$OUT" "$CHUNK" "${COVER_ENGINES//,/+}" <<'PY'
import json, sys
out, chunk, cover = sys.argv[1], int(sys.argv[2]), sys.argv[3]   # covers: CI_COVER_ENGINES
tasks, skipped = [], []
for c in json.load(open(f"{out}/certs/manifest.json"))["certificates"]:
    if c["depth"] <= chunk + 2:
        tasks += [f"{out}/certs {c['certificate']} bmc", f"{out}/certs {c['certificate']} cover {cover}"]
for c in json.load(open(f"{out}/long/manifest.json"))["certificates"]:
    tasks += [f"{out}/long {c['certificate']} bmc", f"{out}/long {c['certificate']} cover {cover}"]
try:
    negs = json.load(open(f"{out}/neg/manifest.json"))["certificates"]
except FileNotFoundError:
    negs = []
for c in negs:
    if c["depth"] <= chunk + 2:
        tasks.append(f"{out}/neg {c['certificate']} bmc")
    else:
        skipped.append(c["certificate"])
# Longest first, so the slowest runs do not start last.
depth = {}
for sub in ("certs", "long", "neg"):
    try:
        for c in json.load(open(f"{out}/{sub}/manifest.json"))["certificates"]:
            depth[c["certificate"]] = c["depth"]
    except FileNotFoundError:
        pass
tasks.sort(key=lambda t: -depth[t.split()[1]])
open(f"{out}/tasks.txt", "w").write("".join(t + "\n" for t in tasks))
open(f"{out}/skipped_negatives.txt", "w").write("".join(s + "\n" for s in skipped))
print(f"ci_prove: {len(tasks)} runs, {len(skipped)} deep negative control(s) skipped")
PY
runs=$(grep -c . "$OUT/tasks.txt" || true)
if [ "$runs" -gt "${CI_MAX_RUNS:-200}" ]; then
  msg="ci_prove: NOT RUN: $runs runs exceed the CI budget of ${CI_MAX_RUNS:-200}; these images are certified by the cluster campaign only (tools/timing/cert/campaign.sh certify), and the staleness check still requires that"
  echo "$msg"
  echo "$msg" > "$OUT/results.md"
  if [ "${GITHUB_ACTIONS:-}" = true ]; then echo "::warning title=certs: not proved in CI::$msg"; fi
  exit 0
fi
start=$(date +%s)
# The proof runs (portfolio) first, then the covers (yices alone, more at once).
grep -v ' cover ' "$OUT/tasks.txt" > "$OUT/tasks_bmc.txt" || true
grep ' cover ' "$OUT/tasks.txt" > "$OUT/tasks_cover.txt" || true
# shellcheck disable=SC2016  # $0 and $@ belong to the inner bash
xargs -P "$PAR" -L 1 bash -c '"$0"/run_one.sh "$@"' "$HERE" < "$OUT/tasks_bmc.txt"
# shellcheck disable=SC2016  # $0 and $@ belong to the inner bash
xargs -P "$COVER_PAR" -L 1 bash -c '"$0"/run_one.sh "$@"' "$HERE" < "$OUT/tasks_cover.txt"
# A portfolio run ends in ERROR when one solver crashes (sby then stops the
# other); run those again with yices alone (retry_list.py, as campaign.sh
# retry does). summarize takes the best result.
python3 "$HERE/retry_list.py" "$OUT/tasks.txt" > "$OUT/retry.txt"
if [ -s "$OUT/retry.txt" ]; then
  echo "ci_prove: $(wc -l < "$OUT/retry.txt") run(s) ended in ERROR; again with yices alone"
  # shellcheck disable=SC2016  # $0 and $@ belong to the inner bash
  xargs -P "$PAR" -L 1 bash -c '"$0"/run_one.sh "$@"' "$HERE" < "$OUT/retry.txt"
fi
echo "ci_prove: runs finished in $(( $(date +%s) - start )) s with $PAR proof and $COVER_PAR cover runs in parallel"
[ -f "$OUT/neg/manifest.json" ] || echo '{"certificates": []}' > "$OUT/neg/manifest.json"
mkdir -p "$OUT/certs/results" "$OUT/long/results" "$OUT/neg/results"
python3 "$HERE/summarize.py" --nodes "$OUT/certs/manifest.json" --chunks "$OUT/long/manifest.json" \
  --neg "$OUT/neg/manifest.json" --results "$OUT/certs/results" --results "$OUT/long/results" \
  --results "$OUT/neg/results" --out-md "$OUT/results.md" --out-json "$OUT/results.json" > /dev/null
python3 - "$OUT" <<'PY'
import json, sys
out = sys.argv[1]
r = json.load(open(f"{out}/results.json"))
bad = []
for n in r["nodes"]:
    if not n["proved"]:
        bad.append(f"{n['image']} n{n['node']}: not proved")
    if not n["cover_ok"]:
        bad.append(f"{n['image']} n{n['node']}: cover not reached")
skipped = set(open(f"{out}/skipped_negatives.txt").read().split())
for g in r["negatives"]:
    if g["certificate"] in skipped:
        continue
    if not g["met"]:
        bad.append(f"{g['certificate']}: negative control did not fail ({g['status']})")
t = r["totals"]
print(f"ci_prove: {t['proved']}/{t['segments']} segments proved, {t['covers']}/{t['segments']} covers, "
      f"{sum(g['met'] for g in r['negatives'])}/{len(r['negatives']) - len(skipped)} negative controls failed "
      f"as required ({len(skipped)} deep ones skipped)")
for b in bad:
    print("ci_prove: FAIL", b)
sys.exit(1 if bad else 0)
PY
