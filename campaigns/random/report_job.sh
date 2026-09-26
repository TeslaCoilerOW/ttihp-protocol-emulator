#!/bin/bash
# Merge all campaign results on a compute node (thousands of small JSON reads).
# Environment (defaults: the 73536f0 base campaign):
#   RC_ROOT        campaign root (default $PE_WORK/vcamp/random)
#   LABELS         campaigns of the report, in table order
#   DEFAULT_LABEL  campaign of the unmodified generator (holes reported separately)
#   NEGCTL         negative-control campaign (empty: none)
#   GL_PAIRS       JSON {gl campaign: rtl campaign with the same seeds} for the cycle cross-check
set -e
export PE_WORK="${PE_WORK:?set PE_WORK to the cluster work directory}"
export R=${RC_ROOT:-$PE_WORK/vcamp/random}
LABELS=${LABELS:-"rtl-default rtl-xcov-default rtl-long rtl-xlong rtl-dense rtl-xcov-dense rtl-faulty \
rtl-xcov-faulty rtl-hostile rtl-xcov-hostile rtl-xcov-deselect gl-default gl-extended gl-hostile gl-deselect"}
DEFAULT_LABEL=${DEFAULT_LABEL:-rtl-default}
export NEGCTL=${NEGCTL-negctl-xor}
export GL_PAIRS=${GL_PAIRS:-'{"gl-default": "rtl-default", "gl-extended": "rtl-default", "gl-hostile": "rtl-hostile", "gl-deselect": "rtl-xcov-deselect"}'}
cd $R/scripts
mkdir -p $R/summaries
# shellcheck disable=SC2086
python3 -B report.py $R $LABELS --default-label "$DEFAULT_LABEL" \
  --summary-dir $R/summaries > $R/summaries/report.md
if [ -n "$NEGCTL" ]; then
  python3 -B merge.py $R/$NEGCTL --json $R/summaries/$NEGCTL.json > /dev/null
fi
python3 -B - <<'PY'
# GL vs RTL per-case cycle cross-check (same seed, same case index => same stimulus).
import json, glob, os
R = os.environ["R"]
pairs = json.loads(os.environ["GL_PAIRS"])
out = {}
for gl, rtl in pairs.items():
    same = diff = missing = 0
    for f in sorted(glob.glob(f"{R}/{gl}/results/*.json")):
        g = json.load(open(f))
        try:
            r = json.load(open(f.replace(f"/{gl}/", f"/{rtl}/")))
        except FileNotFoundError:
            missing += 1
            continue
        if g.get("infra_error") or r.get("infra_error"):
            missing += 1
            continue
        rc = {c["index"]: c["cycles"] for c in r["cases"]}
        for c in g["cases"]:
            if c["index"] not in rc:
                missing += 1
            elif rc[c["index"]] == c["cycles"]:
                same += 1
            else:
                diff += 1
    out[gl] = {"rtl_campaign": rtl, "same_cycles": same, "different": diff, "unpaired": missing}
json.dump(out, open(f"{R}/summaries/gl-vs-rtl.json", "w"), indent=1)
print(out)
PY
# Negative control: per-case detection.
python3 -B - <<'PY'
import json, glob, os
R, NEGCTL = os.environ["R"], os.environ["NEGCTL"]
if NEGCTL:
    cases = det = seeds = seeds_det = 0
    for f in sorted(glob.glob(f"{R}/{NEGCTL}/results/*.json")):
        d = json.load(open(f)); seeds += 1
        cases += d["cases_run"]; det += d["cases_failed"]; seeds_det += d["cases_failed"] > 0
    out = {"seeds": seeds, "seeds_detecting": seeds_det, "cases": cases, "cases_detecting": det}
    json.dump(out, open(f"{R}/summaries/negctl-detection.json", "w"), indent=1)
    print(out)
PY
echo done
