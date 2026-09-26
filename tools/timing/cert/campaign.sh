#!/usr/bin/env bash
# Timing-certificate campaign: prepare, submit to Slurm, summarize.
#
#   campaign.sh prepare WORK [REV]   export REV (default HEAD) with git archive,
#                                    generate the formal RTL (formal/run.sh
#                                    --generate-only; needs the OCaml toolchain,
#                                    OCAML_ENV), emit every certificate, the
#                                    negative controls, the lemmas, the
#                                    equivalence script and the task lists
#   campaign.sh submit WORK [LIST..] one Slurm array per task list (short, chunks,
#                                    cover, neg, lemma; whole_long only when named)
#                                    and the equivalence jobs (equiv)
#   campaign.sh summarize WORK       WORK/results.md and WORK/results.json
#
# Environment:
#   OSS_CAD_SUITE   OSS CAD Suite root (its bin/ is put on PATH)
#   OCAML_ENV       shell file that puts dune on PATH (prepare)
#   CERT_IMAGES     images to certify (default: every firmware/*.image.json)
#   CERT_PARTITIONS Slurm partitions (default mit_preemptable,mit_normal)
#   CERT_MAX        array concurrency per list (default 40)
#   CERT_JOB_PREFIX job-name prefix (default pe-cert)
#   CERT_CHUNK      split segments longer than this many steps (default 96)
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
cmd=${1:?usage: campaign.sh prepare|submit|summarize WORK [REV]}
WORK=${2:?WORK directory}
mkdir -p "$WORK"
WORK=$(cd "$WORK" && pwd)
[ -n "${OSS_CAD_SUITE:-}" ] && export PATH=$OSS_CAD_SUITE/bin:$PATH
GEN="python3 $HERE/gen_cert.py"
SNAP=$WORK/src
RTL=$WORK/fbuild/rtl
CERTS=$WORK/certs
NEG=$WORK/neg
LONG=$WORK/long
CHUNK=${CERT_CHUNK:-96}
MUTANTS="wait-n-cycles xfer-late-edge count-n-iterations open-drain-as-push-pull halt-keeps-pins set-two-cycles"
SCHEDULE_MUTANTS="pad-late pad-early pad-level issue-late post-limit"

case "$cmd" in
prepare)
  REV=${3:-HEAD}
  rm -rf "$SNAP" && mkdir -p "$SNAP"
  git -C "$REPO" archive "$REV" | tar -x -C "$SNAP"
  git -C "$REPO" rev-parse "$REV" > "$WORK/REVISION"
  if [ ! -f "$RTL/processor_fv.v" ]; then
    (cd "$SNAP" && FORMAL_WORK=$WORK/fbuild formal/run.sh --generate-only)
  fi
  images=${CERT_IMAGES:-$(ls "$SNAP"/firmware/*.image.json)}
  rm -rf "$CERTS" "$NEG" "$LONG" && mkdir -p "$CERTS" "$NEG" "$LONG"
  # Whole-segment certificates for every node, and split (chunked) ones for
  # the segments longer than CHUNK steps.
  # shellcheck disable=SC2086
  $GEN emit $images --out "$CERTS" --rtl "$RTL" --models "$SNAP/models"
  # shellcheck disable=SC2086
  $GEN emit $images --out "$LONG" --rtl "$RTL" --models "$SNAP/models" --chunk "$CHUNK"
  python3 - "$LONG" <<'PY'
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
  $GEN emit-lemmas --out "$CERTS" --rtl "$RTL" --models "$SNAP/models"
  for m in $MUTANTS; do
    # shellcheck disable=SC2086
    $GEN emit $images --out "$NEG" --rtl "$RTL" --models "$SNAP/models" --engines bitwuzla,yices \
        --mutant "$m" --pick-one --append
  done
  for m in $SCHEDULE_MUTANTS; do
    # shellcheck disable=SC2086
    $GEN emit $images --out "$NEG" --rtl "$RTL" --models "$SNAP/models" --engines bitwuzla,yices \
        --schedule-mutant "$m" --pick-one --append
  done
  python3 "$HERE/equiv_src.py" --src "$SNAP/src" --rtl "$RTL" --models "$SNAP/models" --out "$WORK/equiv"
  # Task lists: certificates by depth class, covers, negatives, lemmas.
  python3 - "$CERTS" "$LONG" "$NEG" "$WORK" "$CHUNK" <<'PY'
import json, sys
certs, long, neg, work, chunk = sys.argv[1:6]
lists = {"short": [], "chunks": [], "whole_long": [], "cover": [], "neg": [], "lemma": []}
for c in json.load(open(f"{certs}/manifest.json"))["certificates"]:
    if c["depth"] <= int(chunk) + 2:
        lists["short"].append(f"{certs} {c['certificate']} bmc")
        lists["cover"].append(f"{certs} {c['certificate']} cover")
    else:
        lists["whole_long"].append(f"{certs} {c['certificate']} bmc")
for c in json.load(open(f"{long}/manifest.json"))["certificates"]:
    lists["chunks"].append(f"{long} {c['certificate']} bmc")
    lists["cover"].append(f"{long} {c['certificate']} cover")
for c in json.load(open(f"{neg}/manifest.json"))["certificates"]:
    lists["neg"].append(f"{neg} {c['certificate']} bmc")
lists["lemma"] = [f"{certs} boundary_lemmas {t}" for t in
                  ("k0", "k1", "k2", "k3", "cover_k0", "cover_k1", "cover_k2", "cover_k3", "neg")]
lists["lemma"] += [f"{certs} sram_lemma {t}" for t in ("prove", "cover", "neg")]
for name, lines in lists.items():
    open(f"{work}/tasks_{name}.txt", "w").write("".join(l + "\n" for l in lines))
    print(f"tasks_{name}.txt: {len(lines)}")
PY
  ;;
submit)
  parts=${CERT_PARTITIONS:-mit_preemptable,mit_normal}
  max=${CERT_MAX:-40}
  prefix=${CERT_JOB_PREFIX:-pe-cert}
  mkdir -p "$WORK/logs"
  only=" ${*:3} "
  # list  cpus  mem  time  lines-per-job  concurrent-lines
  # whole_long (monolithic BMC of the long segments) runs only when named.
  for spec in "short 6 32G 12:00:00 10 2" "chunks 6 48G 12:00:00 10 2" "cover 4 32G 12:00:00 20 4" \
              "neg 4 32G 12:00:00 10 2" "lemma 8 32G 2:00:00 12 4" "whole_long 3 96G 48:00:00 1 1"; do
    read -r name cpus mem time bundle par <<<"$spec"
    if [ "$only" = "  " ]; then [ "$name" != whole_long ] || continue
    else [[ $only == *" $name "* ]] || continue; fi
    list=$WORK/tasks_$name.txt
    n=$(grep -c . "$list" || true)
    [ "$n" -gt 0 ] || continue
    jobs=$(( (n + bundle - 1) / bundle ))
    sbatch -p "$parts" --requeue --array=0-$((jobs - 1))%"$max" -c "$cpus" --mem="$mem" -t "$time" \
      -J "$prefix-$name" -o "$WORK/logs/$name.%A_%a.out" \
      --export=ALL,TASKS="$list",BUNDLE="$bundle",PAR="$par",CERT_TOOL="$HERE",OSS_CAD_SUITE="${OSS_CAD_SUITE:-}" \
      "$HERE/array.sbatch"
  done
  if [ "$only" = "  " ] || [[ $only == *" equiv "* ]]; then
    # The equivalence check and its negative control (the formal/ mutant netlist).
    for gate in "" "$RTL/processor_fv_mutant.v"; do
      out=$WORK/equiv${gate:+_neg}
      sbatch -p "$parts" --requeue -c 2 --mem=32G -t 1:00:00 -J "$prefix-equiv${gate:+-neg}" \
        -o "$WORK/logs/equiv${gate:+_neg}.%j.out" --export=ALL,OSS_CAD_SUITE="${OSS_CAD_SUITE:-}" \
        --wrap "${OSS_CAD_SUITE:+PATH=$OSS_CAD_SUITE/bin:\$PATH} python3 $HERE/equiv_src.py --src $SNAP/src \
          --rtl $RTL --models $SNAP/models --out $out --run ${gate:+--gate $gate}"
    done
  fi
  ;;
summarize)
  extra=()
  # Optional: RTL-mutant certificates (rtl_mutants.py + gen_cert.py --rtl-mutant into
  # $WORK/rtlneg) and engine-log evidence for negatives whose sby run did not finish.
  [ -f "$WORK/rtlneg/manifest.json" ] && extra+=(--rtl-mutants "$WORK/rtlneg/manifest.json" --results "$WORK/rtlneg/results")
  [ -f "$WORK/engine_evidence.json" ] && extra+=(--engine-evidence "$WORK/engine_evidence.json")
  python3 "$HERE/summarize.py" --nodes "$CERTS/manifest.json" --chunks "$LONG/manifest.json" \
    --neg "$NEG/manifest.json" --results "$CERTS/results" --results "$LONG/results" \
    --results "$NEG/results" --lemmas "$CERTS/results" ${extra[@]+"${extra[@]}"} \
    --out-md "$WORK/results.md" --out-json "$WORK/results.json"
  ;;
*) echo "campaign.sh: unknown command $cmd" >&2; exit 2 ;;
esac
