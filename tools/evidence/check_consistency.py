#!/usr/bin/env python3
"""Fail-closed consistency check between the documentation and the evidence.

Checks (see tools/evidence/README.md for the exact rules):

  ledger       docs/bug-ledger.md: a row marked Open that names a fix commit
               present in history; a fix commit that does not resolve; a
               details heading whose status contradicts the table.
  phrases      banned stale phrases (tools/evidence/consistency.json).
  timing       pe_timing counts claimed in the docs against
               tools/timing/report/checks.json.
  certs        certificate results against the committed firmware images
               (warning-only until the results record image hashes or the
               configuration sets "enforce").
  links        relative Markdown links (and links to this repository on
               GitHub) that point to missing files; missing anchors warn.
  paths        backticked repository paths and workflow names in the strict
               docs that do not exist.
  ci-claims    "not in CI" style claims about something a workflow that runs
               automatically does run.
  status-runs  a run id cited as the current status (a sentence or table
               cell marked "(current)") whose commit has a different design
               (src/, info.yaml; variants6x4/ for the 6x4 build) than the
               working tree; a run said to be in progress (a warning offline;
               with --online an error once GitHub reports it completed); with
               --online also a run whose commit disagrees with the GitHub API.
  citations    citations of results.md rows marked Superseded (warning).

Exit status: 0 when there is no error, 1 when there is at least one error,
2 on a usage or configuration problem. Warnings do not fail the check unless
--strict is given. Python standard library only.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable, Iterator

ERROR = "error"
WARNING = "warning"

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent.parent
DEFAULT_CONFIG = "tools/evidence/consistency.json"

HEX_TOKEN = re.compile(r"`([0-9a-f]{7,40})`")
RUN_ID = re.compile(r"(?<![\w.])(\d{10,12})(?![\w])")
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
REF_DEF = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(<[^>]*>|\S+)")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
TIMING_CLAIM = re.compile(
    r"(\d[\d,]*)\s+PASS,\s*(\d[\d,]*)\s+FAIL"
    r"(?:,\s*(\d[\d,]*)\s+WARN)?(?:,\s*(\d[\d,]*)\s+INFO)?")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[*`(\"])")
TOKEN = re.compile(r"`([^`\n]+)`")


@dataclasses.dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    severity: str
    check: str
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.severity}: [{self.check}] {self.message}"

    def github(self) -> str:
        kind = "error" if self.severity == ERROR else "warning"
        msg = self.message.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        return f"::{kind} file={self.path},line={self.line},title={self.check}::{msg}"


@dataclasses.dataclass
class Unit:
    """A piece of Markdown that claims are read from: a sentence of a
    paragraph or list item, or one cell of a table row."""
    path: str
    line: int
    text: str
    section: str
    kind: str  # "prose" or "cell"
    row: str = ""  # the whole row, for table cells


@dataclasses.dataclass
class Block:
    path: str
    line: int
    text: str
    section: str
    kind: str  # "paragraph", "row" or "heading"
    lines: list[tuple[int, str]]


class ConfigError(Exception):
    pass


# --------------------------------------------------------------------------
# Repository access


class Repo:
    def __init__(self, root: Path):
        self.root = root
        self._commit_cache: dict[str, str | None] = {}
        self._files: set[str] | None = None
        self._dirs: set[str] | None = None
        self.is_git = self._git_ok("rev-parse", "--git-dir")
        self.shallow = self.is_git and self._git("rev-parse", "--is-shallow-repository").strip() == "true"

    def _git(self, *args: str, binary: bool = False):
        proc = subprocess.run(["git", "-C", str(self.root), *args],
                              capture_output=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode(errors="replace").strip())
        return proc.stdout if binary else proc.stdout.decode(errors="replace")

    def _git_ok(self, *args: str) -> bool:
        try:
            self._git(*args)
            return True
        except (RuntimeError, OSError):
            return False

    def resolve_commit(self, rev: str) -> str | None:
        if not self.is_git:
            return None
        if rev not in self._commit_cache:
            try:
                out = self._git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").strip()
                self._commit_cache[rev] = out or None
            except RuntimeError:
                self._commit_cache[rev] = None
        return self._commit_cache[rev]

    def show(self, rev: str, path: str) -> bytes | None:
        try:
            return self._git("show", f"{rev}:{path}", binary=True)
        except RuntimeError:
            return None

    def changed_since(self, rev: str, paths: list[str]) -> list[str]:
        out = self._git("diff", "--name-only", rev, "--", *paths)
        return [p for p in out.splitlines() if p]

    def _index(self) -> None:
        files: set[str] = set()
        if self.is_git:
            out = self._git("ls-files", "-co", "--exclude-standard", "-z")
            files = {p for p in out.split("\0") if p}
            files = {p for p in files if (self.root / p).exists()}
        else:
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = [d for d in dirnames if d != ".git"]
                for name in filenames:
                    rel = os.path.relpath(os.path.join(dirpath, name), self.root)
                    files.add(rel.replace(os.sep, "/"))
        dirs: set[str] = set()
        for f in files:
            parts = f.split("/")
            for i in range(1, len(parts)):
                dirs.add("/".join(parts[:i]))
        self._files, self._dirs = files, dirs

    def exists(self, rel: str) -> bool:
        """Whether a repository path (file or directory) exists, as git would
        commit it: tracked, or untracked and not ignored."""
        if self._files is None:
            self._index()
        rel = rel.strip("/")
        if rel in ("", "."):
            return True
        return rel in self._files or rel in self._dirs  # type: ignore[operator]

    def read_text(self, rel: str) -> str | None:
        p = self.root / rel
        try:
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None


# --------------------------------------------------------------------------
# Markdown helpers


def blank_code_spans(line: str) -> str:
    return INLINE_CODE.sub(lambda m: " " * len(m.group(0)), line)


def split_cells(row: str) -> list[str]:
    """Split a Markdown table row into cells; '|' inside code spans or
    escaped as '\\|' does not split."""
    s = row.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, in_code = [], [], False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def is_separator_row(row: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", row)) and "-" in row


def iter_lines(text: str) -> Iterator[tuple[int, str, bool]]:
    """Yield (line number, line, inside a fenced code block)."""
    fence: str | None = None
    for no, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if m:
            if fence is None:
                fence = m.group(1)[0] * 3
                yield no, line, True
                continue
            if m.group(1).startswith(fence):
                fence = None
                yield no, line, True
                continue
        yield no, line, fence is not None


def blocks(path: str, text: str) -> list[Block]:
    """Paragraphs, list items, table rows and headings, outside code fences."""
    out: list[Block] = []
    section = ""
    cur: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal cur
        if cur:
            joined = " ".join(l.strip() for _, l in cur)
            out.append(Block(path, cur[0][0], joined, section, "paragraph", cur))
            cur = []

    for no, line, fenced in iter_lines(text):
        if fenced:
            flush()
            continue
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if re.match(r"^#{1,6}\s", stripped):
            flush()
            section = stripped
            out.append(Block(path, no, stripped, section, "heading", [(no, line)]))
            continue
        if stripped.startswith("|"):
            flush()
            if not is_separator_row(stripped):
                out.append(Block(path, no, stripped, section, "row", [(no, line)]))
            continue
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", line):
            flush()
        cur.append((no, line))
    flush()
    return out


def units_of(block: Block) -> list[Unit]:
    if block.kind == "row":
        return [Unit(block.path, block.line, c, block.section, "cell", block.text)
                for c in split_cells(block.text)]
    if block.kind == "heading":
        return [Unit(block.path, block.line, block.text, block.section, "prose")]
    units: list[Unit] = []
    # Map sentence offsets back to source lines.
    starts: list[tuple[int, int]] = []
    pos = 0
    for no, l in block.lines:
        starts.append((pos, no))
        pos += len(l.strip()) + 1
    text = block.text
    last = 0
    pieces = []
    for m in SENTENCE_END.finditer(blank_code_spans(text)):
        pieces.append((last, m.start()))
        last = m.end()
    pieces.append((last, len(text)))
    for a, b in pieces:
        line = block.line
        for off, no in starts:
            if off <= a:
                line = no
        units.append(Unit(block.path, line, text[a:b], block.section, "prose"))
    return units


def github_slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    counts: collections.Counter[str] = collections.Counter()
    for _, line, fenced in iter_lines(text):
        if fenced:
            continue
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(1))
        title = re.sub(r"<[^>]+>", "", title)
        slug = title.strip().lower()
        slug = re.sub(r"[^\w\- ]", "", slug)
        slug = slug.replace(" ", "-")
        n = counts[slug]
        counts[slug] += 1
        slugs.add(slug if n == 0 else f"{slug}-{n}")
    for m in re.finditer(r"<a\s+(?:name|id)=\"([^\"]+)\"", text):
        slugs.add(m.group(1))
    return slugs


# --------------------------------------------------------------------------
# The checker


class Checker:
    def __init__(self, root: Path, config: dict, online: bool = False,
                 token: str | None = None, api_url: str = "https://api.github.com"):
        self.root = root
        self.cfg = config
        self.repo = Repo(root)
        self.online = online
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.findings: list[Finding] = []
        self._history = [re.compile(p) for p in config.get("history_markers", [])]
        self._md_cache: dict[str, str] = {}
        self._blocks_cache: dict[str, list[Block]] = {}
        self._run_cache: dict[str, dict | None] = {}

    # ---- utilities

    def add(self, check: str, severity: str, path: str, line: int, message: str) -> None:
        self.findings.append(Finding(path, line, severity, check, message))

    def is_history(self, *texts: str) -> bool:
        return any(p.search(t) for p in self._history for t in texts if t)

    def markdown_files(self) -> list[str]:
        roots = self.cfg.get("markdown_roots", ["README.md", "docs"])
        excl = tuple(self.cfg.get("markdown_exclude", []))
        files: list[str] = []
        for r in roots:
            p = self.root / r
            if p.is_file() and r.endswith(".md"):
                files.append(r)
            elif p.is_dir():
                for f in sorted(p.rglob("*.md")):
                    rel = f.relative_to(self.root).as_posix()
                    if not rel.startswith(excl) and self.repo.exists(rel):
                        files.append(rel)
        return files

    def md(self, rel: str) -> str:
        if rel not in self._md_cache:
            self._md_cache[rel] = self.repo.read_text(rel) or ""
        return self._md_cache[rel]

    def doc_blocks(self, rel: str) -> list[Block]:
        if rel not in self._blocks_cache:
            self._blocks_cache[rel] = blocks(rel, self.md(rel))
        return self._blocks_cache[rel]

    def strict(self, rel: str) -> bool:
        return rel in set(self.cfg.get("strict_docs", []))

    def load_json(self, rel: str):
        text = self.repo.read_text(rel)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{rel}: invalid JSON: {exc}") from exc

    # ---- ledger

    def check_ledger(self) -> None:
        rel = self.cfg.get("bug_ledger", "docs/bug-ledger.md")
        text = self.repo.read_text(rel)
        if text is None:
            self.add("ledger", ERROR, rel, 1, "bug ledger not found")
            return
        if self.repo.is_git and self.repo.shallow:
            self.add("ledger", ERROR, rel, 1,
                     "the checkout is shallow; fix commits cannot be resolved (use fetch-depth: 0)")
            return
        rows: dict[str, tuple[int, str, list[str]]] = {}
        header: list[str] | None = None
        for b in self.doc_blocks(rel):
            if b.kind != "row":
                header = None  # a table ends at the first non-row block
                continue
            cells = split_cells(b.text)
            lowered = [c.strip("* ").lower() for c in cells]
            if "id" in lowered and any(c.startswith("status") for c in lowered) and "fix" in lowered:
                header = lowered
                continue
            if header is None or len(cells) != len(header):
                continue
            rid = cells[header.index("id")].strip("* ")
            if not re.fullmatch(r"BL-\d+", rid):
                continue
            fix = cells[header.index("fix")]
            status = cells[[i for i, c in enumerate(header) if c.startswith("status")][0]]
            commits = []
            for tok in HEX_TOKEN.findall(fix):
                if not self.repo.is_git:
                    continue
                if self.repo.resolve_commit(tok):
                    commits.append(tok)
                elif re.search(r"[a-f]", tok):
                    self.add("ledger", ERROR, rel, b.line,
                             f"{rid}: fix commit `{tok}` does not resolve in this repository's history")
            lead = self.status_word(status)
            rows[rid] = (b.line, lead, commits)
            if lead == "open" and commits:
                self.add("ledger", ERROR, rel, b.line,
                         f"{rid} is marked Open, but its Fix column names commit(s) "
                         f"{', '.join(commits)} present in history; update the status "
                         "(Fixed / Partly fixed) or remove the commit")
        if not rows:
            self.add("ledger", ERROR, rel, 1, "no summary table with ID, Fix and Status columns found")
            return
        for b in self.doc_blocks(rel):
            if b.kind != "heading":
                continue
            m = re.match(r"^#{2,6}\s+(BL-\d+)(?:\s+(?:to|and)\s+(BL-\d+))?:.*\(([^()]*)\)\s*$", b.text)
            if not m:
                continue
            first, last, paren = int(m.group(1)[3:]), int((m.group(2) or m.group(1))[3:]), m.group(3).lower()
            words = set(re.findall(r"[a-z]+", paren))
            says = "open" if "open" in words and not {"fixed", "partly"} & words else (
                "fixed" if "fixed" in words and "partly" not in words else None)
            if says is None:
                continue
            for n in range(first, last + 1):
                rid = f"BL-{n}"
                if rid not in rows:
                    continue
                lead = rows[rid][1]
                if says == "open" and lead in ("fixed", "resolved"):
                    self.add("ledger", ERROR, rel, b.line,
                             f"heading says {rid} is open, the summary table says {lead}")
                if says == "fixed" and lead == "open":
                    self.add("ledger", ERROR, rel, b.line,
                             f"heading says {rid} is fixed, the summary table says open")

    @staticmethod
    def status_word(status: str) -> str:
        s = re.sub(r"[*_`]", "", status).strip().lower()
        s = re.split(r"[:;(,.]", s, maxsplit=1)[0].strip()
        for word in ("partly fixed", "fixed in part", "fixed", "open", "resolved",
                     "documented", "upstream not fixed", "worked around"):
            if s.startswith(word):
                return "partly" if word in ("partly fixed", "fixed in part") else word
        return s

    # ---- banned phrases

    def check_phrases(self) -> None:
        phrases = [(re.compile(p["pattern"]), p["reason"]) for p in self.cfg.get("banned_phrases", [])]
        if not phrases:
            return
        for rel in self.markdown_files():
            for b in self.doc_blocks(rel):
                texts = [(u.line, u.text) for u in units_of(b)]
                if b.kind == "row":
                    texts.append((b.line, b.text))  # a pattern may span cells
                for pat, reason in phrases:
                    for line, text in texts:
                        if pat.search(text) and not self.is_history(text):
                            self.add("phrases", ERROR, rel, line,
                                     f"stale phrase {pat.pattern!r}: {reason}")
                            break

    # ---- pe_timing counts

    def timing_counts(self) -> collections.Counter[str] | None:
        data = self.load_json(self.cfg.get("timing_report", "tools/timing/report/checks.json"))
        if data is None:
            return None
        counts: collections.Counter[str] = collections.Counter()

        def walk(o) -> None:
            if isinstance(o, dict):
                if isinstance(o.get("status"), str):
                    counts[o["status"]] += 1
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(data)
        return counts

    def check_timing(self) -> None:
        counts = self.timing_counts()
        rel_report = self.cfg.get("timing_report", "tools/timing/report/checks.json")
        if counts is None:
            self.add("timing", ERROR, rel_report, 1, "timing report not found")
            return
        now = f"{counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['WARN']} WARN, {counts['INFO']} INFO"
        for rel in self.markdown_files():
            for b in self.doc_blocks(rel):
                for u in units_of(b):
                    for m in TIMING_CLAIM.finditer(u.text):
                        vals = [None if g is None else int(g.replace(",", "")) for g in m.groups()]
                        want = [counts["PASS"], counts["FAIL"], counts["WARN"], counts["INFO"]]
                        wrong = [k for k, v, w in zip(("PASS", "FAIL", "WARN", "INFO"), vals, want)
                                 if v is not None and v != w]
                        if not wrong:
                            continue
                        if vals[1] == 0 and counts["FAIL"] > 0:
                            self.add("timing", ERROR, rel, u.line,
                                     f"claims 0 FAIL, but {rel_report} has {counts['FAIL']} FAIL ({now})")
                            continue
                        if self.is_history(u.text, u.row):
                            continue
                        sev = ERROR if self.strict(rel) else WARNING
                        self.add("timing", sev, rel, u.line,
                                 f"claims {m.group(0)!r}; {rel_report} has {now} "
                                 "(mark the claim Superseded or update it)")

    # ---- certificates

    def check_certs(self) -> None:
        c = self.cfg.get("certificates") or {}
        rel = c.get("summary")
        if not rel:
            return
        data = self.load_json(rel)
        if data is None:
            self.add("certs", ERROR, rel, 1, "certificate summary not found")
            return
        images = data.get("images") if isinstance(data, dict) else None
        if not isinstance(images, list):
            self.add("certs", ERROR, rel, 1, "certificate summary has no 'images' list")
            return
        fw = c.get("firmware_dir", "firmware")
        keys = c.get("hash_keys", ["bytecode_sha256"])
        recorded = any(any(k in im for k in keys) for im in images if isinstance(im, dict))
        enforce = bool(c.get("enforce")) or recorded
        sev = ERROR if enforce else WARNING
        baseline = data.get("commit") if isinstance(data, dict) else None
        baseline = baseline or c.get("certified_commit")
        certified: set[str] = set()
        stale: list[str] = []
        for im in images:
            if not isinstance(im, dict) or "image" not in im:
                continue
            name = im["image"]
            certified.add(name)
            img_rel = f"{fw}/{name}.image.json"
            cur = self.load_json(img_rel)
            if cur is None:
                self.add("certs", ERROR, rel, 1, f"certified image {name} has no {img_rel}")
                continue
            now = cur.get("bytecode_sha256")
            key = next((k for k in keys if k in im), None)
            if key is not None:
                if im[key] != now:
                    self.add("certs", ERROR, img_rel, 1,
                             f"{name}: certificate results record {key} {str(im[key])[:12]}…, "
                             f"the committed image has {str(now)[:12]}…; re-run the certificates")
                    stale.append(name)
                continue
            if baseline and self.repo.is_git and self.repo.resolve_commit(baseline):
                old = self.repo.show(baseline, img_rel)
                old_hash = None
                if old is not None:
                    try:
                        old_hash = json.loads(old).get("bytecode_sha256")
                    except json.JSONDecodeError:
                        old_hash = None
                if old_hash != now:
                    stale.append(name)
                    self.add("certs", sev, img_rel, 1,
                             f"{name}: certified at `{baseline}`, but the image changed since "
                             "(the results record no image hash); re-certify it")
            else:
                self.add("certs", WARNING, rel, 1,
                         f"{name}: results record no image hash and no certified commit resolves")
        for f in sorted((self.root / fw).glob("*.image.json")):
            name = f.name[: -len(".image.json")]
            if not self.repo.exists(f"{fw}/{f.name}"):
                continue  # ignored by git, so not committed
            if name not in certified:
                self.add("certs", sev, f"{fw}/{f.name}", 1,
                         f"{name}: no certificate results for this committed image")
        totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
        self.check_cert_wording(sorted(set(stale)), baseline, totals.get("segments"))

    def check_cert_wording(self, stale: list[str], baseline: str | None,
                           segments: int | None = None) -> None:
        """While certified images are stale, a headline claim must carry its
        qualification; once none is stale, the qualification is itself stale.
        "{segments}" in the headline pattern stands for the certified total."""
        c = self.cfg.get("certificates") or {}
        head_pat = c.get("headline_pattern")
        if head_pat and "{segments}" in head_pat:
            head_pat = head_pat.replace("{segments}", str(segments)) if segments else None
        head = re.compile(head_pat) if head_pat else None
        qual = re.compile(c["qualification_pattern"]) if c.get("qualification_pattern") else None
        if head is None and qual is None:
            return
        for rel in self.markdown_files():
            for b in self.doc_blocks(rel):
                for u in units_of(b):
                    if self.is_history(u.text):
                        continue
                    context = u.text + " " + (u.row or b.text)
                    if stale and head is not None and head.search(u.text):
                        qualified = (qual is not None and qual.search(context)) or \
                            (baseline is not None and baseline in context) or \
                            any(re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(name), context)
                                for name in stale)
                        if not qualified:
                            self.add("certs", ERROR if self.strict(rel) else WARNING, rel, u.line,
                                     f"certificate headline without its scope: {', '.join(stale)} "
                                     f"changed since `{baseline}` and await re-certification")
                    if not stale and qual is not None and qual.search(u.text):
                        self.add("certs", ERROR if self.strict(rel) else WARNING, rel, u.line,
                                 "says images await re-certification, but every certified image "
                                 "matches the committed firmware")

    # ---- links

    def check_links(self) -> None:
        repo_url = (self.cfg.get("repo_url") or "").rstrip("/")
        blob = re.compile(re.escape(repo_url) + r"/(?:blob|tree)/[^/]+/([^#?]*)(?:#.*)?$") if repo_url else None
        for rel in self.markdown_files():
            text = self.md(rel)
            base = Path(rel).parent
            for no, line, fenced in iter_lines(text):
                if fenced:
                    continue
                scan = blank_code_spans(line)
                targets = [m.group(3) for m in LINK.finditer(scan)]
                m = REF_DEF.match(scan)
                if m:
                    targets.append(m.group(2))
                for t in targets:
                    self.check_target(rel, no, base, t.strip("<>"), blob)

    def check_target(self, rel: str, no: int, base: Path, target: str, blob) -> None:
        if blob is not None:
            m = blob.match(target)
            if m:
                path = urllib.parse.unquote(m.group(1)).strip("/")
                if path and not self.repo.exists(path):
                    self.add("links", ERROR, rel, no, f"link to {target} names a missing path {path}")
                return
        if SCHEME.match(target) or target.startswith("//"):
            return
        path_part, _, frag = target.partition("#")
        path_part = urllib.parse.unquote(path_part.split("?", 1)[0])
        if not path_part:
            if frag and frag not in github_slugs(self.md(rel)):
                self.add("links", WARNING, rel, no, f"anchor #{frag} not found in {rel}")
            return
        if path_part.startswith("/"):
            resolved = os.path.normpath(path_part.lstrip("/"))
        else:
            resolved = os.path.normpath((base / path_part).as_posix())
        resolved = resolved.replace(os.sep, "/")
        if resolved.startswith(".."):
            # Above the repository root GitHub resolves a relative link against
            # the web UI (for example ../../workflows/gds/badge.svg from the
            # README); there is no file to check.
            return
        if not self.repo.exists(resolved):
            self.add("links", ERROR, rel, no, f"link {target} points to a missing file ({resolved})")
            return
        if frag and resolved.endswith(".md") and (self.root / resolved).is_file():
            if frag not in github_slugs(self.md(resolved)):
                self.add("links", WARNING, rel, no, f"anchor #{frag} not found in {resolved}")

    # ---- backticked paths and workflow names

    def workflows(self) -> dict[str, dict]:
        if hasattr(self, "_workflows"):
            return self._workflows  # type: ignore[has-type]
        wf: dict[str, dict] = {}
        d = self.root / ".github" / "workflows"
        roots = self.cfg.get("path_roots", [])
        path_re = re.compile(r"(?<![\w./-])((?:%s)/[\w./-]*)" % "|".join(re.escape(r) for r in roots)) if roots else None
        for f in sorted(list(d.glob("*.yaml")) + list(d.glob("*.yml"))) if d.is_dir() else []:
            text = f.read_text(encoding="utf-8", errors="replace")
            name_m = re.search(r"^name:\s*['\"]?([^'\"\n#]+?)['\"]?\s*$", text, re.M)
            triggers = self.triggers(text)
            jobs = set()
            jm = re.search(r"^jobs:\s*$", text, re.M)
            if jm:
                for line in text[jm.end():].splitlines():
                    if re.match(r"^\S", line):
                        break
                    k = re.match(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$", line)
                    if k:
                        jobs.add(k.group(1))
                    n = re.match(r"^    name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", line)
                    if n and "${{" not in n.group(1):
                        jobs.add(n.group(1).strip())
            refs: set[str] = set()
            for line in text.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                if path_re:
                    refs.update(m.group(1) for m in path_re.finditer(line))
                for m in re.finditer(r"\bcd\s+([\w./-]+)", line):
                    refs.add(m.group(1).rstrip("/") + "/")
            wf[f.name] = {
                "name": name_m.group(1).strip() if name_m else f.stem,
                "triggers": triggers,
                "automatic": bool(triggers & {"push", "pull_request", "pull_request_target",
                                               "workflow_run", "schedule", "merge_group"}),
                "jobs": jobs,
                "refs": refs,
            }
        self._workflows = wf
        return wf

    @staticmethod
    def triggers(text: str) -> set[str]:
        m = re.search(r"^(?:on|\"on\"|'on'):[ \t]*(.*)$", text, re.M)
        if not m:
            return set()
        inline = m.group(1).split("#", 1)[0].strip()
        if inline:
            return {t.strip(" '\"") for t in inline.strip("[]").split(",") if t.strip()}
        out = set()
        for line in text[m.end():].splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if re.match(r"^\S", line):
                break
            k = re.match(r"^  ([A-Za-z_]+):", line)
            if k:
                out.add(k.group(1))
            k = re.match(r"^  -\s*([A-Za-z_]+)", line)
            if k:
                out.add(k.group(1))
        return out

    def path_candidate(self, tok: str) -> str | None:
        tok = tok.strip()
        if any(ch in tok for ch in "<>*{}$ …") or "..." in tok or "=" in tok:
            return None
        tok = re.sub(r":\d+(?:-\d+)?$", "", tok).split("#", 1)[0]
        roots = self.cfg.get("path_roots", [])
        files = self.cfg.get("path_root_files", [])
        first = tok.split("/", 1)[0]
        if tok in files:
            return tok
        if first not in roots or "/" not in tok:
            return None
        if any(tok.startswith(g) or tok.rstrip("/") == g.rstrip("/")
               for g in self.cfg.get("generated_paths", [])):
            return None
        return tok

    def check_paths(self) -> None:
        wf = self.workflows()
        known = set()
        for fname, w in wf.items():
            known.update({w["name"], fname.rsplit(".", 1)[0]}, w["jobs"])
        mention = re.compile(r"`([A-Za-z0-9_-]+)`\s+(?:workflows?|actions?)\b|\bworkflows?\s+`([A-Za-z0-9_-]+)`")
        wf_file = re.compile(r"\.github/workflows/([\w.-]+\.ya?ml)")
        for rel in self.markdown_files():
            strict = self.strict(rel)
            for b in self.doc_blocks(rel):
                for u in units_of(b):
                    hist = self.is_history(u.text, u.row)
                    if not strict or hist:
                        continue
                    for m in wf_file.finditer(u.text):
                        if m.group(1) not in wf:
                            self.add("paths", ERROR, rel, u.line,
                                     f".github/workflows/{m.group(1)} does not exist")
                    for m in mention.finditer(u.text):
                        name = m.group(1) or m.group(2)
                        if name not in known:
                            self.add("paths", ERROR, rel, u.line,
                                     f"workflow `{name}` is not a workflow or job in .github/workflows")
                    for tok in TOKEN.findall(u.text):
                        if wf_file.fullmatch(tok):
                            continue  # reported above
                        p = self.path_candidate(tok)
                        if p and not self.repo.exists(p):
                            self.add("paths", ERROR, rel, u.line, f"`{tok}` does not exist in the repository")

    # ---- "not in CI" claims

    def ci_subject_prefixes(self, subject: str) -> list[str]:
        aliases = (self.cfg.get("ci_claims") or {}).get("aliases", {})
        if subject in aliases:
            return list(aliases[subject])
        p = self.path_candidate(subject)
        if p:
            return [p]
        return []

    def in_ci(self, prefixes: list[str]) -> list[str]:
        hits = []
        for fname, w in self.workflows().items():
            if not w["automatic"]:
                continue
            for ref in w["refs"]:
                for pre in prefixes:
                    pre_n = pre.rstrip("/")
                    if ref == pre or ref.rstrip("/") == pre_n or ref.startswith(pre_n + "/") \
                            or (pre.endswith("/") and ref.startswith(pre)) \
                            or (not pre.endswith("/") and ref.startswith(pre_n)):
                        hits.append(fname)
        return sorted(set(hits))

    def check_ci_claims(self) -> None:
        cc = self.cfg.get("ci_claims") or {}
        around = [re.compile(p) for p in cc.get("around_patterns", [])]
        after = [re.compile(p) for p in cc.get("after_patterns", [])]
        aliases = list((cc.get("aliases") or {}).keys())
        alias_re = re.compile(r"\b(%s)\b" % "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))) if aliases else None
        for rel in self.markdown_files():
            for b in self.doc_blocks(rel):
                for u in units_of(b):
                    if self.is_history(u.text):
                        continue
                    subjects = self.subjects(u.text, alias_re)
                    for pat in around:
                        for m in pat.finditer(u.text):
                            before = [s for s in subjects if s[0] < m.start()]
                            chosen = [before[-1]] if before else [s for s in subjects if s[0] >= m.end()][:1]
                            self.report_ci(rel, u, m.group(0), chosen)
                    for pat in after:
                        for m in pat.finditer(u.text):
                            chosen = [s for s in subjects if s[0] >= m.end()]
                            self.report_ci(rel, u, m.group(0), chosen)

    def subjects(self, text: str, alias_re) -> list[tuple[int, str]]:
        found = [(m.start(), m.group(1)) for m in TOKEN.finditer(text)]
        if alias_re is not None:
            plain = blank_code_spans(text)
            found += [(m.start(), m.group(1)) for m in alias_re.finditer(plain)]
        return sorted(found)

    def report_ci(self, rel: str, u: Unit, phrase: str, chosen: list[tuple[int, str]]) -> None:
        for _, subj in chosen:
            prefixes = self.ci_subject_prefixes(subj)
            if not prefixes:
                continue
            hits = self.in_ci(prefixes)
            if hits:
                self.add("ci-claims", ERROR, rel, u.line,
                         f"says {subj!r} is {phrase!r}, but {', '.join(hits)} runs it on push, "
                         "pull request or workflow_run")

    # ---- status runs

    def variant_paths(self, text: str) -> list[str]:
        sr = self.cfg.get("status_runs") or {}
        for pat, paths in (sr.get("variant_design_paths") or {}).items():
            if re.search(pat, text):
                return list(paths)
        return list(sr.get("design_paths", ["src", "info.yaml"]))

    def design_changed(self, rev: str, paths: list[str] | None = None) -> list[str]:
        if paths is None:
            paths = (self.cfg.get("status_runs") or {}).get("design_paths", ["src", "info.yaml"])
        changed = []
        for p in self.repo.changed_since(rev, paths):
            if p.endswith(".json"):
                old, new = self.repo.show(rev, p), self.repo.read_text(p)
                if old is not None and new is not None:
                    try:
                        hook = lambda pairs: {k: v for k, v in pairs if not str(k).startswith("//")}
                        if json.loads(old, object_pairs_hook=hook) == json.loads(new, object_pairs_hook=hook):
                            continue
                    except json.JSONDecodeError:
                        pass
            changed.append(p)
        return changed

    def api_run(self, run_id: str) -> dict | None:
        if run_id in self._run_cache:
            return self._run_cache[run_id]
        repo_url = (self.cfg.get("repo_url") or "").rstrip("/")
        slug = repo_url.split("github.com/", 1)[-1]
        url = f"{self.api_url}/repos/{slug}/actions/runs/{run_id}"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "pe-consistency-check"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            self.add("status-runs", WARNING, "-", 0, f"GitHub API: run {run_id}: {exc}")
            data = None
        self._run_cache[run_id] = data
        return data

    def check_status_runs(self) -> None:
        sr = self.cfg.get("status_runs") or {}
        current_re = re.compile(sr.get("current_marker", r"(?i)\(current\)"))
        progress_re = re.compile(sr.get("progress_pattern", r"(?i)\b(?:in progress|still running)\b"))
        if not self.repo.is_git:
            return
        for rel in self.markdown_files():
            for b in self.doc_blocks(rel):
                if b.kind == "heading":
                    continue
                for u in units_of(b):
                    runs = [(m.start(), m.group(1)) for m in RUN_ID.finditer(u.text)]
                    if not runs:
                        continue
                    if progress_re.search(u.text) and not self.is_history(u.text):
                        for _, rid in runs:
                            self.progress_claim(rel, u, rid)
                    # Only a unit (a sentence or a table cell) that presents
                    # a run as the current status is checked for design drift.
                    if not current_re.search(u.text) or self.is_history(u.text):
                        continue
                    design_paths = self.variant_paths(u.text)
                    commits = [(m.start(), m.group(1)) for m in HEX_TOKEN.finditer(u.text)
                               if self.repo.resolve_commit(m.group(1))]
                    for pos, rid in runs:
                        commit = min(commits, key=lambda c: abs(c[0] - pos))[1] if commits else None
                        api = self.api_run(rid) if self.online else None
                        if api and commit:
                            full = self.repo.resolve_commit(commit) or commit
                            if api.get("head_sha") and api["head_sha"] != full:
                                self.add("status-runs", ERROR, rel, u.line,
                                         f"run {rid} is of commit {api['head_sha'][:7]}, not `{commit}`")
                                continue
                        if commit is None and api and api.get("head_sha") and \
                                self.repo.resolve_commit(api["head_sha"]):
                            commit = api["head_sha"][:7]
                        if commit is None:
                            continue
                        changed = self.design_changed(commit, design_paths)
                        if changed:
                            self.add("status-runs", ERROR, rel, u.line,
                                     f"cites run {rid} (`{commit}`) as the current status, but "
                                     f"{', '.join(changed)} changed since `{commit}`")

    def progress_claim(self, rel: str, u: Unit, rid: str) -> None:
        if not self.online:
            self.add("status-runs", WARNING, rel, u.line,
                     f"says run {rid} is in progress; check it (--online checks it against GitHub)")
            return
        api = self.api_run(rid)
        if api and api.get("status") == "completed":
            self.add("status-runs", ERROR, rel, u.line,
                     f"says run {rid} is in progress, but it completed ({api.get('conclusion')})")

    # ---- citations of superseded results rows

    def check_citations(self) -> None:
        rel_res = self.cfg.get("results_doc", "docs/results.md")
        sup: dict[str, set[str]] = {}  # superseded row -> the rows that replace it
        for b in self.doc_blocks(rel_res):
            if b.kind != "row":
                continue
            cells = split_cells(b.text)
            if cells and re.fullmatch(r"R\d+[a-z]?", cells[0].strip("* ")):
                marked = [c for c in cells[1:3] if re.search(r"\*\*Superseded", c)]
                if marked:
                    by = re.search(r"Superseded by ((?:R\d+[a-z]?(?:,\s*|\s+and\s+)?)+)", marked[0])
                    sup[cells[0].strip("* ")] = set(re.findall(r"R\d+[a-z]?", by.group(1))) if by else set()
        if not sup:
            return
        cite = re.compile(r"\b(R\d+[a-z]?)\b")
        for rel in self.markdown_files():
            if rel == rel_res:
                continue
            for b in self.doc_blocks(rel):
                for u in units_of(b):
                    if self.is_history(u.text, u.row):
                        continue
                    cited = set(cite.findall(blank_code_spans(u.text)))
                    for m in cite.finditer(blank_code_spans(u.text)):
                        # Citing the replacement next to the old row is history.
                        if m.group(1) in sup and not (sup[m.group(1)] & cited):
                            self.add("citations", WARNING, rel, u.line,
                                     f"cites {m.group(1)}, which {rel_res} marks Superseded")

    # ---- driver

    CHECKS: dict[str, str] = {
        "ledger": "check_ledger",
        "phrases": "check_phrases",
        "timing": "check_timing",
        "certs": "check_certs",
        "links": "check_links",
        "paths": "check_paths",
        "ci-claims": "check_ci_claims",
        "status-runs": "check_status_runs",
        "citations": "check_citations",
    }

    def run(self, only: Iterable[str] | None = None) -> list[Finding]:
        for name, meth in self.CHECKS.items():
            if only and name not in only:
                continue
            getattr(self, meth)()
        # One finding per (path, line, check, message).
        self.findings = sorted(set(self.findings))
        return self.findings


def load_config(root: Path, rel: str | None) -> dict:
    path = Path(rel) if rel and os.path.isabs(rel) else root / (rel or DEFAULT_CONFIG)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration {path} not found") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"configuration {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="repository root (default: this checkout)")
    ap.add_argument("--config", default=None, help=f"configuration file (default: {DEFAULT_CONFIG})")
    ap.add_argument("--only", action="append", choices=sorted(Checker.CHECKS), help="run only these checks")
    ap.add_argument("--strict", action="store_true", help="warnings fail the check too")
    ap.add_argument("--online", action="store_true",
                    help="also check cited run ids against the GitHub API (GITHUB_TOKEN or GH_TOKEN if set)")
    ap.add_argument("--format", choices=("text", "github", "json"), default="text")
    ap.add_argument("--summary", default=None, help="append a Markdown summary to this file")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        cfg = load_config(root, args.config)
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        chk = Checker(root, cfg, online=args.online, token=token)
        findings = chk.run(args.only)
    except ConfigError as exc:
        print(f"check_consistency: {exc}", file=sys.stderr)
        return 2
    errors = [f for f in findings if f.severity == ERROR]
    warnings = [f for f in findings if f.severity == WARNING]
    if args.format == "json":
        print(json.dumps([dataclasses.asdict(f) for f in findings], indent=1))
    else:
        for f in findings:
            print(f.github() if args.format == "github" else f.render())
    failed = bool(errors) or (args.strict and bool(warnings))
    verdict = "FAIL" if failed else "PASS"
    print(f"consistency: {verdict} ({len(errors)} errors, {len(warnings)} warnings)")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(f"## Evidence consistency: {verdict}\n\n{len(errors)} errors, {len(warnings)} warnings\n\n")
            for f in findings:
                fh.write(f"- `{f.path}:{f.line}` {f.severity} [{f.check}] {f.message}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
