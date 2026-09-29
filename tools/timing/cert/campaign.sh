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
#   campaign.sh record WORK          summarize, then merge the campaign into the
#                                    ledger (results/summary.json, summary.md,
#                                    campaigns/) with ledger.py record
#   campaign.sh retry WORK [record]  after the arrays: every run of the short,
#                                    chunks, cover and neg lists whose results
#                                    are all ERROR (a solver crashed, e.g.
#                                    boolector killed for running out of
#                                    memory) again with yices alone, and every
#                                    run without a result again as it was
#                                    (retry_list.py), as one more array (list
#                                    retry); with 'record', then a record job
#                                    that waits for it
#   campaign.sh certify WORK [REV]   one command for new or changed images:
#                                    export REV (default HEAD), certify every
#                                    image ledger.py check flags there (prepare
#                                    with CERT_IMAGES), submit all lists, and
#                                    submit a job that runs 'retry WORK record'
#                                    once the others have finished. An image that
#                                    gen_cert preflight refuses (it runs every
#                                    emission prepare does) is reported and
#                                    left out, so it cannot stop the others.
#
# Environment:
#   OSS_CAD_SUITE   OSS CAD Suite root (its bin/ is put on PATH)
#   OCAML_ENV       shell file that puts dune on PATH (prepare)
#   CERT_IMAGES     images to certify (default: every firmware/*.image.json)
#   CERT_PARTITIONS Slurm partitions (default mit_preemptable,mit_normal)
#   CERT_ONLY       certify: only these image names (space-separated) of the
#                   images ledger.py check flags
#   CERT_DRY_RUN    certify: 1 = stop after prepare and print the task-list
#                   sizes (what would be submitted); nothing is submitted
#   CERT_PRUNE_PASSED certify: 1 (default there) = runs that passed keep only
#                   their log and result file (run_one.sh), to save inodes
#   CERT_KEEP_SNAPSHOT 1 = use WORK/src (and WORK/REVISION, WORK/fbuild/rtl)
#                   as they are instead of exporting REV (prepare; certify
#                   when WORK/REVISION exists)
#   CERT_MAX        array concurrency per list (default 40; certify: per-list
#                   defaults that keep the campaign at 128 CPUs or fewer)
#   CERT_MAX_<list> concurrency of one list (short chunks cover neg lemma whole_long
#                   retry)
#   CERT_MAX_ARRAY  at most this many array tasks per list (default 60); a longer
#                   list gets larger bundles (per-user submit limit)
#   CERT_JOB_PREFIX job-name prefix (default pe-cert)
#   CERT_CHUNK      split segments longer than this many steps (default 96)
#   CERT_SHORT      whole-segment certificates of at most this many steps run in
#                   the short list with the solver portfolio (default 200); the
#                   longer ones go to whole_long
#   CERT_RTL_MUTANTS 1: also build every rtl_mutants.py netlist and run the real
#                   certificates of one image per mutant on it (list rtlneg).
#                   rtl_sync_3flop is always built: it is the equivalence
#                   check's negative control.
#   CERT_WHOLE_MAX  whole-segment certificates of more steps than this are not
#                   run at all (default 2000; such segments are proved by their
#                   chunk chains, and BMC to that depth does not finish)
#   CERT_NEG_MAX    negative controls of more steps than this are not run
#                   (default 2000; they are listed as not run, i.e. undecided,
#                   and the chunk-level controls cover those segments)
#   CERT_COVER_ENGINES solvers of the cover runs (default boolector+yices; the
#                   generated sby files name yices alone). With two solvers
#                   most chunk covers are several times faster (uart-rx-idle:
#                   71 to 86 s against 507 to 840 s), but boolector can run out
#                   of memory on the first chunk of a long segment; the retry
#                   step reruns such covers with yices alone
#   CERT_LONG_ENGINE solver for whole_long and for negative controls longer
#                   than CERT_SHORT (default bmc3, ABC; see run_one.sh)
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
cmd=${1:?usage: campaign.sh prepare|submit|summarize|record|retry|certify WORK [...]}
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
SHORT=${CERT_SHORT:-200}
LONG_ENGINE=${CERT_LONG_ENGINE:-bmc3}
MUTANTS="wait-n-cycles xfer-late-edge count-n-iterations open-drain-as-push-pull halt-keeps-pins set-two-cycles"
SCHEDULE_MUTANTS="pad-late pad-early pad-level issue-late post-limit branch-late"

snapshot() {
  rm -rf "$SNAP" "$WORK/fbuild" && mkdir -p "$SNAP"
  git -C "$REPO" archive "$1" | tar -x -C "$SNAP"
  git -C "$REPO" rev-parse "$1" > "$WORK/REVISION"
}

case "$cmd" in
prepare)
  REV=${3:-HEAD}
  if [ "${CERT_KEEP_SNAPSHOT:-0}" != 1 ]; then snapshot "$REV"; fi
  if [ ! -f "$RTL/processor_fv.v" ]; then
    (cd "$SNAP" && FORMAL_WORK=$WORK/fbuild formal/run.sh --generate-only)
  fi
  images=${CERT_IMAGES:-$(ls "$SNAP"/firmware/*.image.json)}
  rm -rf "$CERTS" "$NEG" "$LONG" && mkdir -p "$CERTS" "$NEG" "$LONG"
  # Whole-segment certificates for every node, and split (chunked) ones for
  # the segments longer than CHUNK steps. (Negative controls: a node a wrong
  # analyzer's schedule cannot be written for is skipped and listed in
  # neg/manifest.json under not_generated; it does not stop the emission.)
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
    # the same perturbation in the cheapest chunk certificate of a long segment
    # (the chunk chains prove those segments)
    # shellcheck disable=SC2086
    $GEN emit $images --out "$NEG" --rtl "$RTL" --models "$SNAP/models" --engines bitwuzla,yices \
        --schedule-mutant "$m" --chunk "$CHUNK" --chunk-controls --pick-one --append
  done
  python3 "$HERE/equiv_src.py" --src "$SNAP/src" --rtl "$RTL" --models "$SNAP/models" --out "$WORK/equiv"
  # RTL mutants: rtl_sync_3flop for the equivalence negative control; with
  # CERT_RTL_MUTANTS=1 all of them, each with the certificates of one image
  # that uses the mutated feature.
  rm -rf "$WORK/rtlmut" "$WORK/rtlneg"
  if [ "${CERT_RTL_MUTANTS:-0}" = 1 ]; then
    python3 "$HERE/rtl_mutants.py" "$SNAP" "$WORK/rtlmut"
    for pair in rtl_wait_plus1:uart-tx rtl_xfer_short_tick:spi-controller-mode0 rtl_sync_3flop:uart-rx \
                rtl_od_oe_ungated:i2c-write rtl_od_drive_high:i2c-write; do
      m=${pair%%:*} img=$SNAP/firmware/${pair##*:}.image.json
      [ -f "$img" ] || continue
      $GEN emit "$img" --out "$WORK/rtlneg" --rtl "$WORK/rtlmut/$m/fbuild/rtl" --models "$SNAP/models" \
          --chunk "$CHUNK" --rtl-mutant "$m" --append
    done
  else
    python3 "$HERE/rtl_mutants.py" "$SNAP" "$WORK/rtlmut" rtl_sync_3flop
  fi
  # Task lists: certificates by depth class, covers, negatives, lemmas.
  python3 - "$CERTS" "$LONG" "$NEG" "$WORK" "$CHUNK" "$SHORT" "$LONG_ENGINE" "${CERT_COVER_ENGINES:-boolector+yices}" \
      "${CERT_WHOLE_MAX:-2000}" "${CERT_NEG_MAX:-2000}" <<'PY'
import json, sys
certs, long, neg, work, chunk, short, long_engine, cover_engines, whole_max, neg_max = sys.argv[1:11]
not_run = []
lists = {"short": [], "chunks": [], "whole_long": [], "cover": [], "neg": [], "lemma": []}
for c in json.load(open(f"{certs}/manifest.json"))["certificates"]:
    if c["depth"] <= int(short) + 2:
        lists["short"].append(f"{certs} {c['certificate']} bmc")
    elif c["depth"] <= int(whole_max) + 2:
        lists["whole_long"].append(f"{certs} {c['certificate']} bmc {long_engine}")
    if c["depth"] <= int(chunk) + 2:
        lists["cover"].append(f"{certs} {c['certificate']} cover {cover_engines}")
for c in json.load(open(f"{long}/manifest.json"))["certificates"]:
    lists["chunks"].append(f"{long} {c['certificate']} bmc")
    lists["cover"].append(f"{long} {c['certificate']} cover {cover_engines}")
for c in json.load(open(f"{neg}/manifest.json"))["certificates"]:
    if c["depth"] > int(neg_max) + 2:
        # BMC to that depth does not finish in useful time: not run (undecided)
        not_run.append(c["certificate"])
        continue
    deep = c["depth"] > int(short) + 2
    lists["neg"].append(f"{neg} {c['certificate']} bmc" + (f" {long_engine}" if deep else ""))
lists["lemma"] = [f"{certs} boundary_lemmas {t}" for t in
                  ("k0", "k1", "k2", "k3", "cover_k0", "cover_k1", "cover_k2", "cover_k3", "neg")]
lists["lemma"] += [f"{certs} sram_lemma {t}" for t in ("prove", "cover", "neg")]
try:
    for c in json.load(open(f"{work}/rtlneg/manifest.json"))["certificates"]:
        lists.setdefault("rtlneg", []).append(f"{work}/rtlneg {c['certificate']} bmc")
except FileNotFoundError:
    pass
for name, lines in lists.items():
    open(f"{work}/tasks_{name}.txt", "w").write("".join(l + "\n" for l in lines))
    print(f"tasks_{name}.txt: {len(lines)}")
open(f"{work}/neg_not_run.txt", "w").write("".join(n + "\n" for n in not_run))
if not_run:
    print(f"neg_not_run.txt: {len(not_run)} negative control(s) deeper than {neg_max} steps, not run")
PY
  python3 "$HERE/ledger.py" fingerprint "$WORK" --chunk "$CHUNK" --short "$SHORT"
  ;;
submit)
  parts=${CERT_PARTITIONS:-mit_preemptable,mit_normal}
  max=${CERT_MAX:-40}
  prefix=${CERT_JOB_PREFIX:-pe-cert}
  mkdir -p "$WORK/logs"
  only=" ${*:3} "
  # list  cpus  mem  time  lines-per-job  concurrent-lines
  # whole_long (monolithic BMC of the long segments) and retry (written by
  # 'campaign.sh retry'; mostly yices-alone reruns, four at a time used about
  # 9 GB) run only when named.
  # CERT_MAX_<list> (e.g. CERT_MAX_short) overrides CERT_MAX for one list, so the
  # sum of cpus x concurrency can be kept under a CPU budget.
  for spec in "short 6 32G 12:00:00 10 2" "chunks 6 48G 12:00:00 10 2" "cover 4 32G 12:00:00 20 4" \
              "neg 4 32G 12:00:00 10 2" "lemma 8 32G 2:00:00 12 4" "whole_long 2 64G 24:00:00 1 1" \
              "rtlneg 6 32G 12:00:00 10 2" "retry 4 32G 12:00:00 8 4"; do
    read -r name cpus mem time bundle par <<<"$spec"
    if [ "$only" = "  " ]; then [ "$name" != whole_long ] && [ "$name" != retry ] || continue
    else [[ $only == *" $name "* ]] || continue; fi
    list=$WORK/tasks_$name.txt
    n=$(grep -c . "$list" 2>/dev/null || true)
    [ -f "$list" ] && [ "$n" -gt 0 ] || continue
    jobs=$(( (n + bundle - 1) / bundle ))
    # Every array task counts against the per-user submit limit (448 on
    # mit_preemptable), so a long list gets larger bundles instead of more
    # tasks: at most CERT_MAX_ARRAY tasks per list.
    cap=${CERT_MAX_ARRAY:-60}
    if [ "$jobs" -gt "$cap" ]; then
      bundle=$(( (n + cap - 1) / cap ))
      jobs=$(( (n + bundle - 1) / bundle ))
    fi
    lmax_var=CERT_MAX_$name
    lmax=${!lmax_var:-$max}
    sbatch --parsable -p "$parts" --requeue --array=0-$((jobs - 1))%"$lmax" -c "$cpus" --mem="$mem" -t "$time" \
      -J "$prefix-$name" -o "$WORK/logs/$name.%A_%a.out" \
      --export=ALL,TASKS="$list",BUNDLE="$bundle",PAR="$par",CERT_TOOL="$HERE",OSS_CAD_SUITE="${OSS_CAD_SUITE:-}" \
      "$HERE/array.sbatch" | tee -a "$WORK/jobs.txt" | sed "s/^/$name: job /"
  done
  if [ "$only" = "  " ] || [[ $only == *" equiv "* ]]; then
    # The equivalence check (ABC dprove), and its negative control: the same
    # flow against the rtl_sync_3flop netlist must find a counterexample
    # (ABC bmc3, 30 frames). (The formal/ mutant netlist needs about 45 frames
    # to diverge, too deep for bmc3 on this miter.)
    for gate in "" "$WORK/rtlmut/rtl_sync_3flop/fbuild/rtl/processor_fv.v"; do
      out=$WORK/equiv${gate:+_neg}
      sbatch --parsable -p "$parts" --requeue -c 2 --mem=32G -t 1:00:00 -J "$prefix-equiv${gate:+-neg}" \
        -o "$WORK/logs/equiv${gate:+_neg}.%j.out" --export=ALL,OSS_CAD_SUITE="${OSS_CAD_SUITE:-}" \
        --wrap "${OSS_CAD_SUITE:+PATH=$OSS_CAD_SUITE/bin:\$PATH} python3 $HERE/equiv_src.py --src $SNAP/src \
          --rtl $RTL --models $SNAP/models --out $out --run ${gate:+--gate $gate --bmc-only 30}" \
        | tee -a "$WORK/jobs.txt" | sed "s/^/equiv${gate:+_neg}: job /"
    done
  fi
  ;;
summarize)
  extra=()
  # Optional: RTL-mutant certificates (rtl_mutants.py + gen_cert.py --rtl-mutant into
  # $WORK/rtlneg) and engine-log evidence for negatives whose sby run did not finish.
  [ -f "$WORK/rtlneg/manifest.json" ] && extra+=(--rtl-mutants "$WORK/rtlneg/manifest.json" --results "$WORK/rtlneg/results")
  [ -f "$WORK/engine_evidence.json" ] && extra+=(--engine-evidence "$WORK/engine_evidence.json")
  [ -s "$WORK/neg_not_run.txt" ] && extra+=(--neg-not-run "$WORK/neg_not_run.txt")
  python3 "$HERE/summarize.py" --nodes "$CERTS/manifest.json" --chunks "$LONG/manifest.json" \
    --neg "$NEG/manifest.json" --results "$CERTS/results" --results "$LONG/results" \
    --results "$NEG/results" --lemmas "$CERTS/results" ${extra[@]+"${extra[@]}"} \
    --out-md "$WORK/results.md" --out-json "$WORK/results.json"
  ;;
record)
  "$HERE/campaign.sh" summarize "$WORK"
  python3 "$HERE/ledger.py" record "$WORK"
  ;;
retry)
  # With 'record' (the job certify submits) it is idempotent, so that a
  # requeued job does not submit twice: the ids of the retry array and the
  # record job are kept in WORK/retry_job.txt and WORK/record_job.txt, which
  # certify clears. By hand, without 'record', it always looks again (a second
  # round after a retry array).
  prefix=${CERT_JOB_PREFIX:-pe-cert}
  if [ "${3:-}" = record ] && [ -s "$WORK/record_job.txt" ]; then
    echo "campaign.sh retry: already done (record job $(cat "$WORK/record_job.txt"))"; exit 0
  fi
  lists=()
  for l in short chunks cover neg; do [ -f "$WORK/tasks_$l.txt" ] && lists+=("$WORK/tasks_$l.txt"); done
  retry_id=""
  if [ "${3:-}" = record ] && [ -s "$WORK/retry_job.txt" ]; then
    retry_id=$(cat "$WORK/retry_job.txt")
    echo "campaign.sh retry: retry array already submitted: job $retry_id"
  elif [ ${#lists[@]} -gt 0 ]; then
    python3 "$HERE/retry_list.py" --missing "${lists[@]}" > "$WORK/tasks_retry.txt"
    n=$(grep -c . "$WORK/tasks_retry.txt" || true)
    echo "campaign.sh retry: $n run(s) to repeat (ERROR: again with yices alone; no result: again as they were)"
    sed 's/^/  /' "$WORK/tasks_retry.txt"
    if [ "$n" -gt 0 ]; then
      retry_id=$(CERT_MAX_retry=${CERT_MAX_retry:-8} "$HERE/campaign.sh" submit "$WORK" retry \
        | sed -n 's/^retry: job \([0-9]*\).*/\1/p')
      [ -n "$retry_id" ] || { echo "campaign.sh retry: submission failed" >&2; exit 1; }
      echo "$retry_id" > "$WORK/retry_job.txt"
      echo "campaign.sh retry: retry array: job $retry_id"
    fi
  fi
  if [ "${3:-}" = record ]; then
    mkdir -p "$WORK/logs"
    dep=()
    [ -z "$retry_id" ] || dep=(--dependency=afterany:"$retry_id")
    rec=$(sbatch --parsable -p "${CERT_PARTITIONS:-mit_preemptable,mit_normal}" --requeue -c 1 --mem=8G -t 1:00:00 \
      ${dep[@]+"${dep[@]}"} -J "$prefix-record" -o "$WORK/logs/record.%j.out" \
      --export=ALL --wrap "$HERE/campaign.sh record $WORK")
    echo "$rec" | tee -a "$WORK/jobs.txt" > "$WORK/record_job.txt"
    echo "record: job $rec"
    echo "campaign.sh retry: when the record job has finished, run tools/timing/cert/ledger.py check and commit tools/timing/cert/results/"
  fi
  ;;
certify)
  REV=${3:-HEAD}
  if [ -z "${SLURM_JOB_ID:-}" ] && [ "${CERT_LOCAL:-0}" != 1 ]; then
    # Run the rest (RTL generation, emission, submission) as a short Slurm job.
    rev=$(git -C "$REPO" rev-parse "$REV")
    mkdir -p "$WORK/logs"
    sbatch --parsable -p "${CERT_PREP_PARTITION:-mit_quicktest}" -c 4 --mem=16G -t "${CERT_PREP_TIME:-15}" \
      -J "${CERT_JOB_PREFIX:-pe-cert}-certify" -o "$WORK/logs/certify.%j.out" --export=ALL \
      --wrap "$HERE/campaign.sh certify $WORK $rev" | sed "s/^/certify: job /"
    echo "campaign.sh certify: the job submits the proof arrays and a retry job, which submits the record job; logs in $WORK/logs/"
    exit 0
  fi
  if [ "${CERT_KEEP_SNAPSHOT:-0}" = 1 ] && [ -f "$WORK/REVISION" ] && [ -d "$SNAP/firmware" ]; then
    echo "campaign.sh certify: CERT_KEEP_SNAPSHOT=1: using $SNAP as it is"
  else
    snapshot "$REV"
  fi
  # The images ledger.py check flags at REV (image, RTL, includes, lemmas or
  # generator output changed, or no certificate), minus those the generator
  # refuses: gen_cert preflight runs, in memory, every emission prepare does
  # (whole segments, chunk chains with the cycle-level re-derivation, every
  # negative control), so an image that passes it cannot stop prepare.
  stale=$(python3 "$HERE/ledger.py" stale --repo "$SNAP")
  images=""
  : > "$WORK/refused.txt"
  for f in $stale; do
    # CERT_ONLY="name ...": certify only these of the flagged images
    if [ -n "${CERT_ONLY:-}" ] && [[ " $CERT_ONLY " != *" $(basename "$f" .image.json) "* ]]; then continue; fi
    if python3 "$HERE/gen_cert.py" preflight "$SNAP/$f" --chunk "$CHUNK" > "$WORK/preflight.txt" 2>&1; then
      images="$images $SNAP/$f"
      sed 's/^/campaign.sh certify: preflight: /' "$WORK/preflight.txt"
    else
      echo "campaign.sh certify: $f is not certifiable as is (left out): $(cat "$WORK/preflight.txt")" \
        | tee -a "$WORK/refused.txt" >&2
    fi
  done
  if [ -z "$images" ]; then
    echo "campaign.sh certify: nothing to certify at $(cut -c1-7 "$WORK/REVISION")"; exit 0
  fi
  echo "campaign.sh certify: $(cut -c1-7 "$WORK/REVISION"):$images" | sed "s|$SNAP/||g"
  CERT_IMAGES=$images CERT_KEEP_SNAPSHOT=1 "$HERE/campaign.sh" prepare "$WORK" "$REV"
  if [ "${CERT_DRY_RUN:-0}" = 1 ]; then
    echo "campaign.sh certify: CERT_DRY_RUN=1: prepared in $WORK, nothing submitted; task lists:"
    for f in "$WORK"/tasks_*.txt; do echo "  $(basename "$f" .txt | cut -c7-): $(grep -c . "$f" || true)"; done
    exit 0
  fi
  : > "$WORK/jobs.txt"
  export CERT_PRUNE_PASSED=${CERT_PRUNE_PASSED:-1}
  CERT_MAX_short=${CERT_MAX_short:-7} CERT_MAX_chunks=${CERT_MAX_chunks:-3} CERT_MAX_cover=${CERT_MAX_cover:-5} \
  CERT_MAX_neg=${CERT_MAX_neg:-5} CERT_MAX_lemma=${CERT_MAX_lemma:-1} CERT_MAX_whole_long=${CERT_MAX_whole_long:-8} \
    "$HERE/campaign.sh" submit "$WORK" short chunks cover neg lemma whole_long equiv
  deps=$(cut -d';' -f1 "$WORK/jobs.txt" | paste -sd: -)
  rm -f "$WORK/retry_job.txt" "$WORK/record_job.txt" "$WORK/tasks_retry.txt"
  # Once every array has finished: rerun what ended in ERROR (yices alone) or
  # left no result, then record (campaign.sh retry WORK record).
  sbatch --parsable -p "${CERT_PARTITIONS:-mit_preemptable,mit_normal}" --requeue -c 1 --mem=4G -t 0:30:00 \
    --dependency=afterany:"$deps" -J "${CERT_JOB_PREFIX:-pe-cert}-retry" -o "$WORK/logs/retry.%j.out" \
    --export=ALL --wrap "$HERE/campaign.sh retry $WORK record" | tee -a "$WORK/jobs.txt" | sed "s/^/retry: job /"
  echo "campaign.sh certify: after the arrays, the retry job reruns the runs that ended in ERROR or left no"
  echo "  result, and submits the record job; when that has finished, run tools/timing/cert/ledger.py check"
  echo "  and commit tools/timing/cert/results/"
  ;;
*) echo "campaign.sh: unknown command $cmd" >&2; exit 2 ;;
esac
