# tools/evidence: documentation consistency check

`check_consistency.py` fails when the documentation disagrees with the
repository in a way a program can decide: a status that the git history
contradicts, a count that the committed result file contradicts, a file or
workflow that does not exist, a "not in CI" claim about something a workflow
runs. It is fail-closed: an error exits 1, and the `consistency` workflow
([`.github/workflows/consistency.yaml`](../../.github/workflows/consistency.yaml))
runs it, with its unit tests, on every push and pull request.

It needs Python 3.10 or later and `git`, nothing else. It reads the checkout
and writes nothing.

```sh
python3 tools/evidence/check_consistency.py              # all checks, text output
python3 tools/evidence/check_consistency.py --only ledger --only links
python3 tools/evidence/check_consistency.py --strict     # warnings fail too
python3 tools/evidence/check_consistency.py --online     # also ask the GitHub API about cited runs
python3 -m unittest discover -s tools/evidence -p 'test_*.py'
```

Exit status: 0 without errors, 1 with at least one error (or a warning under
`--strict`), 2 for a missing or invalid configuration. `--format github`
prints workflow annotations, `--format json` a list of findings, and
`--summary FILE` appends a Markdown summary.

## What it reads

- **Documents.** `README.md` and every `docs/**/*.md` except `docs/notes/`.
  The *strict* documents (`strict_docs` in `consistency.json`: the README and
  the evidence pages) get the path and workflow checks, and their stale
  counts are errors rather than warnings.
- **Units.** A claim is read from one *unit*: a sentence of a paragraph or
  list item, or one cell of a table row. Fenced code blocks are skipped;
  links inside code spans are ignored.
- **History markers.** A unit that marks itself as history is exempt from
  the staleness checks, so superseded evidence can stay in place. The
  markers are in `history_markers`, for example "Superseded", "Resolved",
  "Obsolete", "as first written", "until `abc1234`", "removed in `abc1234`".
  A marker exempts only its own unit (its sentence or table cell), so a
  current claim next to a superseded one is still checked.

## The checks

| Check | Fails (error) when | Warns when |
|---|---|---|
| `ledger` | a row of the summary table of `docs/bug-ledger.md` whose status begins with "Open" names, in its Fix column, a commit that exists in this repository's history; a Fix commit does not resolve; a details heading `### BL-n: … (…, open)` or `(…, fixed …)` contradicts the table; the checkout is shallow | |
| `phrases` | a banned stale phrase (`banned_phrases`, each with the evidence that made it stale) appears outside a history unit | |
| `timing` | a strict document states `pe_timing` counts ("N PASS, M FAIL, K WARN, L INFO") that differ from `tools/timing/report/checks.json`, outside a history unit; any document claims 0 FAIL while the report has a FAIL | another document states different counts |
| `certs` | the certificate results record an image hash that differs from the committed `firmware/<image>.image.json`; with enforcement on, a certified image changed since the certified commit, or a committed image has no certificate; a strict document gives the certificate headline ("N of N" segments, with N the total of the results, today 368) without its scope while images are stale, or says images "await re-certification" when none is stale | the same image and headline findings while enforcement is off |
| `links` | a relative Markdown link, or a link to this repository's `blob`/`tree` URLs, names a file or directory that git would not commit (missing, or ignored) | an anchor (`#…`) matches no heading of the target |
| `paths` | in a strict document, a backticked repository path (a path under a top-level directory of the repository) does not exist, a `.github/workflows/*.yaml` mentioned does not exist, or "the `x` workflow / action" names no workflow or job | |
| `ci-claims` | a unit says something is "not in CI", "not run on GitHub" or "cluster-only" (or "CI does not run …") about a path that a workflow triggered by push, pull request, schedule or `workflow_run` references in its steps; workflows that run only on `workflow_dispatch` do not count | |
| `status-runs` | a unit marked "(current)" cites a run id next to a commit whose design differs from the checkout: `src/` or `info.yaml` for the 8x4 build, `variants6x4/`, `src/project.v` and `src/sram_pdn_cfg.tcl` for a unit about the 6x4 build (`//` comment keys of JSON files are ignored); with `--online`, the API gives the run another commit, or a unit calls a run "in progress" that has completed | offline, a unit calls a run "in progress" or "still running" |
| `citations` | | a document cites a row of `docs/results.md` that is marked **Superseded** (for example `R40`), outside a history unit |

**Certificates, and when enforcement starts.** `tools/timing/cert/results/summary.json`
lists the certified images. When its image entries carry a hash
(`bytecode_sha256`, or a key in `hash_keys`), each is compared with the
committed image and enforcement is on. Until then the check compares each
image at `certified_commit` (`c118027`) with the checkout and only warns; set
`"enforce": true` in `consistency.json` to make those findings errors.

**Run ids.** Official run ids are not required to be the latest run of
`main`: every push starts new runs, so a document that cites them could
never be current. What the check requires instead is that a run presented as
the current status (marked "(current)") built the design that is in the
checkout. A run of an older design must be marked as history.

## Changing what it checks

Everything that names documents, phrases, patterns or paths is in
[`consistency.json`](consistency.json). When a claim becomes stale, fix the
document and, if the stale wording is likely to come back, add it to
`banned_phrases` with the evidence in `reason`. When a new workflow runs a
tool that the documents call cluster-only, the `ci-claims` check fails until
the documents are updated; `ci_claims.aliases` maps plain-text names such as
"the host tests" to repository paths. A new test in
[`test_check_consistency.py`](test_check_consistency.py) should come with
each new rule.

## Limits

- The checks are text rules, not an understanding of the prose. A stale
  claim in words the rules do not match passes; the history markers exempt
  a unit that uses them, whatever it says.
- `--online` needs network access; without a token the GitHub API allows 60
  requests per hour. API errors are warnings, so the CI job does not use it.
- The ledger check needs the full history (`fetch-depth: 0` in the
  workflow).
