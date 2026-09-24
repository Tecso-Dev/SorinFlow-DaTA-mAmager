#!/usr/bin/env python3
"""
Lint only the lines a change actually touches.

ruff and mypy both run clean on new code and both have hundreds of pre-existing
findings on the rest of this codebase — turning either gate on for the whole
tree would either block every unrelated commit until someone works through the
backlog, or (in practice) get the gate disabled. Comparing against a base ref
and keeping only the findings that land on an added or changed line lets the
gate be strict from day one without anyone first cleaning up code nobody
touched.

Usage:
    python scripts/lint_new_code.py                  # diff against origin/main
    python scripts/lint_new_code.py --base sorinflow-v2
    python scripts/lint_new_code.py --staged          # pre-commit: staged changes only

Prints "path:line: [tool] code message" for every finding on a touched line
and exits 1 if there is at least one. Nothing to report is a silent success,
same as the tools it wraps.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ESLINT_BIN = REPO_ROOT / "node_modules" / ".bin" / "eslint"

# Mirrors eslint.config.js's own ignores — skip these ourselves instead of
# shelling out to eslint, which refuses an explicitly-named ignored file.
JS_VENDOR_PREFIXES = ("frontend/vendor/", "frontend/js/vendor/")

HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")
# mypy's normal (non-JSON) output: "path/to/file.py:12: error: message  [code]"
# Only "error:" lines count — notes and warnings are noise for a merge gate.
MYPY_LINE = re.compile(r"^(?P<path>[^:\n]+):(?P<line>\d+):(?:\d+:)?\s*error:\s*(?P<message>.+)$")
MYPY_CODE = re.compile(r"\[([a-z0-9-]+)\]\s*$")


def parse_unified_diff(diff_text: str) -> dict[str, set[int]]:
    """Map each file the diff touches to the line numbers it added or changed,
    as they are numbered in the file's new (post-change) version.

    Built from ``git diff -U0`` (zero context lines) output: with no context,
    every line a hunk's "+" side reports is itself new or changed, so the
    hunk header — ``@@ -old_start,old_count +new_start,new_count @@`` — is all
    that is needed; there is no per-line +/- bookkeeping to do. A count of 1
    is omitted from the header by git, so it defaults to 1 when absent. A
    hunk with a new-side count of 0 is a pure deletion — nothing was added at
    that point, so it contributes no line numbers.

    A deleted file (``+++ /dev/null``) never gets an entry: there is no new
    file left to lint. A file touched only by a pure rename or a mode change
    has no hunks at all and also never gets an entry — both cases mean
    "nothing changed in this file's content", which callers treat the same
    way as a file with no changed lines.
    """
    changed: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current = None
        elif line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                current = None
                continue
            # "+++ b/<path>" (occasionally a bare path with no a/ b/ prefix
            # depending on diff.mnemonicPrefix). core.quotePath=false, set by
            # the caller, keeps non-ASCII paths here as literal UTF-8 rather
            # than octal-escaped and quoted.
            current = path[2:] if path[:2] in ("a/", "b/") else path
            changed.setdefault(current, set())
        elif line.startswith("@@ ") and current is not None:
            m = HUNK_HEADER.match(line)
            if not m:
                continue
            start = int(m.group("start"))
            count = int(m.group("count")) if m.group("count") is not None else 1
            if count == 0:
                continue
            changed[current].update(range(start, start + count))
    return changed


def _git_diff(args: list[str]) -> str:
    # -c core.quotePath=false: see parse_unified_diff. --find-renames: a
    # rename with edits must be read as a diff on the new path, not dropped
    # as an unreadable delete+add pair.
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--find-renames", "-U0", *args],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return proc.stdout


def _untracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [p for p in proc.stdout.splitlines() if p]


def changed_lines(base: str | None, staged: bool) -> dict[str, set[int]]:
    if staged:
        diff_text = _git_diff(["--cached"])
        changed = parse_unified_diff(diff_text)
    else:
        merge_base = subprocess.run(
            ["git", "merge-base", base or "origin/main", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        diff_text = _git_diff([merge_base])
        changed = parse_unified_diff(diff_text)
        # git diff never mentions a file nobody has `git add`ed yet, tracked
        # or not — a brand new file sitting in the working tree would
        # otherwise be invisible here (--staged mode does not have this
        # problem: by commit time it is staged, so git diff --cached already
        # shows it). Every line of it counts as changed.
        for path in _untracked_files():
            try:
                with open(REPO_ROOT / path, encoding="utf-8", errors="ignore") as fh:
                    line_count = sum(1 for _ in fh)
            except OSError:
                continue
            if line_count:
                changed[path] = set(range(1, line_count + 1))
    # Drop files a rename/mode-change left with no hunks at all (see above).
    return {path: lines for path, lines in changed.items() if lines}


def _relpath(raw: str) -> str:
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return raw


def run_ruff(files: list[str], changed: dict[str, set[int]]) -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--output-format=json", *files],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    out = proc.stdout.strip()
    if not out:
        return []
    try:
        diagnostics = json.loads(out)
    except json.JSONDecodeError:
        return [f"lint_new_code: ruff output was not JSON: {out[:200]!r}"]
    findings = []
    for d in diagnostics:
        path = _relpath(d.get("filename", ""))
        row = (d.get("location") or {}).get("row")
        if row in changed.get(path, ()):
            findings.append(f"{path}:{row}: [ruff] {d.get('code') or '?'} {d.get('message', '')}")
    return findings


def run_mypy(files: list[str], changed: dict[str, set[int]]) -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-error-summary", *files],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    findings = []
    for line in proc.stdout.splitlines():
        m = MYPY_LINE.match(line)
        if not m:
            continue
        path = _relpath(m.group("path"))
        lineno = int(m.group("line"))
        if lineno in changed.get(path, ()):
            message = m.group("message")
            code_m = MYPY_CODE.search(message)
            code = code_m.group(1) if code_m else "?"
            findings.append(f"{path}:{lineno}: [mypy] {code} {message}")
    return findings


def run_eslint(files: list[str], changed: dict[str, set[int]]) -> list[str]:
    if not ESLINT_BIN.exists():
        return [f"lint_new_code: eslint is not installed (run `npm install`) — "
                f"{len(files)} changed .js file(s) were not checked"]
    proc = subprocess.run(
        [str(ESLINT_BIN), "--format=json", *files],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    out = proc.stdout.strip()
    if not out:
        return []
    try:
        results = json.loads(out)
    except json.JSONDecodeError:
        return [f"lint_new_code: eslint output was not JSON: {out[:200]!r}"]
    findings = []
    for file_result in results:
        path = _relpath(file_result.get("filePath", ""))
        lines = changed.get(path)
        if not lines:
            continue
        for msg in file_result.get("messages", []):
            line = msg.get("line") or 0
            # line 0 is a whole-file message (e.g. a parse error) rather than
            # one this file's diff can place — report it since the file is
            # part of this change regardless.
            if line and line not in lines:
                continue
            rule = msg.get("ruleId") or ("error" if msg.get("severity") == 2 else "warning")
            findings.append(f"{path}:{line}: [eslint] {rule} {msg.get('message', '')}")
    return findings


def _is_vendor_js(path: str) -> bool:
    return path.endswith(".min.js") or any(path.startswith(p) for p in JS_VENDOR_PREFIXES)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=None,
                         help="ref to merge-base against HEAD (default: origin/main)")
    parser.add_argument("--staged", action="store_true",
                         help="lint staged changes only (git diff --cached), for the pre-commit hook")
    args = parser.parse_args()

    changed = changed_lines(args.base, args.staged)
    py_files = sorted(p for p in changed if p.endswith(".py"))
    js_files = sorted(p for p in changed if p.endswith(".js") and not _is_vendor_js(p))

    findings: list[str] = []
    if py_files:
        findings += run_ruff(py_files, changed)
        findings += run_mypy(py_files, changed)
    if js_files:
        findings += run_eslint(js_files, changed)

    for line in sorted(findings):
        print(line)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
