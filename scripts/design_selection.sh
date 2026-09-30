#!/usr/bin/env bash
# Read the design selection (configs/design-selection.txt): the generator
# configuration that src/protocol_emulator_core.v is generated from.
#
# Usage: scripts/design_selection.sh [--repo DIR] FIELD
#   FIELD
#     config    the configuration, relative to the repository:
#               configs/instruction-sram-32.json or configs/variants/NAME.json
#     variant   base for configs/instruction-sram-32.json, NAME for
#               configs/variants/NAME.json (PE_VARIANT of test/Makefile,
#               --variant of formal/run.sh and formal_eq/eq_check.py)
#     env       PE_DESIGN_CONFIG=<config> and PE_DESIGN_VARIANT=<variant>,
#               one per line (for $GITHUB_ENV)
#     check     validate only; prints one line describing the selection
#   --repo DIR  read DIR/configs/design-selection.txt and check that the named
#               configuration exists under DIR (default: this script's
#               repository). The file is read as data, never executed.
#
# The file holds exactly one line that is not empty and does not start with
# '#'. That line must be configs/instruction-sram-32.json or
# configs/variants/NAME.json (NAME: 1 to 32 lower-case letters, digits and
# '_', starting with a letter or digit, not "base"), with no surrounding
# white space, and name an existing refinement configuration. Anything else
# is an error (exit status 2). .github/workflows/equiv.yaml applies the same
# rules to the file of a built commit.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
FIELD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) [ $# -ge 2 ] || { echo "design_selection.sh: --repo needs a directory" >&2; exit 2; }
            REPO=$2; shift 2 ;;
    -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "design_selection.sh: unknown option $1" >&2; exit 2 ;;
    *) [ -z "$FIELD" ] || { echo "design_selection.sh: more than one field given" >&2; exit 2; }
       FIELD=$1; shift ;;
  esac
done
case "$FIELD" in
  config|variant|env|check) ;;
  "") echo "design_selection.sh: no field given (config, variant, env or check)" >&2; exit 2 ;;
  *) echo "design_selection.sh: unknown field $FIELD (config, variant, env or check)" >&2; exit 2 ;;
esac

FILE=$REPO/configs/design-selection.txt
fail() { echo "design_selection.sh: $FILE: $1" >&2; exit 2; }
[ -f "$FILE" ] || fail "not found"

selection="" count=0
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    "" | "#"*) continue ;;
  esac
  count=$((count + 1))
  selection=$line
done < "$FILE"
[ "$count" -eq 1 ] || fail "expected exactly one selection line, found $count"

variant_re='^configs/variants/([a-z0-9][a-z0-9_]{0,31})\.json$'
if [ "$selection" = configs/instruction-sram-32.json ]; then
  variant=base
elif [[ $selection =~ $variant_re ]]; then
  variant=${BASH_REMATCH[1]}
  [ "$variant" != base ] || fail "select configs/instruction-sram-32.json for the base design, not configs/variants/base.json"
else
  fail "the selection line must be configs/instruction-sram-32.json or configs/variants/NAME.json, not '$selection'"
fi
[ -f "$REPO/$selection" ] || fail "$selection does not exist"
grep -q '"protocol-emulator.refinement.v1"' "$REPO/$selection" \
  || fail "$selection is not a refinement configuration (protocol-emulator.refinement.v1)"

case "$FIELD" in
  config) printf '%s\n' "$selection" ;;
  variant) printf '%s\n' "$variant" ;;
  env) printf 'PE_DESIGN_CONFIG=%s\nPE_DESIGN_VARIANT=%s\n' "$selection" "$variant" ;;
  check) printf 'design selection: %s (variant %s)\n' "$selection" "$variant" ;;
esac
