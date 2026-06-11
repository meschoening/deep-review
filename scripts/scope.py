#!/usr/bin/env python3
"""scope.py — resolve a /deep-review target into LOC and a file list.

Usage:
    scope.py <target> [args]

Targets:
    branch              diff vs auto-detected base (origin/main, origin/master, main, master)
    staged              git diff --cached
    pr <num>            gh pr diff <num>
    all                 entire repo (git ls-files)
    <path>              a file or directory inside the repo

Output:
    Header of KEY=value lines (TARGET, SOURCE, BASE, DIFF_CMD, LOC, FILE_COUNT),
    then a FILES section listing one path per line. DIFF_CMD is the command
    that shows the change under review; empty for non-diff targets (all, path).

For the pr target, the local HEAD must match the PR head commit (run
`gh pr checkout <num>` first) — otherwise agents would review the wrong code.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import NoReturn


def err(msg: str, code: int = 1) -> NoReturn:
    print(f"scope.py: {msg}", file=sys.stderr)
    sys.exit(code)


def run(cmd: list[str], check: bool = True) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        err(f"command failed: {' '.join(cmd)}\n{res.stderr.strip()}")
    return res.stdout


def detect_base() -> str:
    for ref in ("origin/main", "origin/master", "main", "master"):
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            capture_output=True,
        )
        if r.returncode == 0:
            return ref
    err("could not detect base branch (no origin/main, origin/master, main, or master)")


def file_loc(path: str) -> int:
    try:
        with open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def diff_files_loc(diff_args: list[str]) -> tuple[list[str], int]:
    files = run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRTUXB", *diff_args]
    ).splitlines()
    files = [f for f in files if f]
    numstat = run(["git", "diff", "--numstat", *diff_args]).splitlines()
    loc = 0
    for line in numstat:
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            loc += int(parts[0]) + int(parts[1])
    return files, loc


def main() -> None:
    args = sys.argv[1:]
    target = args[0] if args else "branch"

    in_repo = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if in_repo.returncode != 0:
        err("not in a git repository")

    if target in ("branch", ""):
        base = detect_base()
        files, loc = diff_files_loc([f"{base}...HEAD"])
        source = f"branch diff vs {base}"
        base_label = base
        diff_cmd = f"git diff {base}...HEAD"
    elif target == "staged":
        files, loc = diff_files_loc(["--cached"])
        source = "staged changes"
        base_label = "(staged)"
        diff_cmd = "git diff --cached"
    elif target == "pr":
        if len(args) < 2:
            err("pr target requires a PR number: scope.py pr <num>")
        pr_num = args[1]
        if not shutil.which("gh"):
            err("gh CLI not found; needed for pr target")
        info = json.loads(
            run(["gh", "pr", "view", pr_num, "--json", "headRefOid,headRefName"])
        )
        local_head = run(["git", "rev-parse", "HEAD"]).strip()
        if local_head != info["headRefOid"]:
            err(
                f"local HEAD {local_head[:9]} does not match PR #{pr_num} head "
                f"{info['headRefOid'][:9]} ({info['headRefName']}); "
                f"run: gh pr checkout {pr_num}"
            )
        files = [
            f for f in run(["gh", "pr", "diff", pr_num, "--name-only"]).splitlines() if f
        ]
        diff_text = run(["gh", "pr", "diff", pr_num])
        loc = sum(
            1
            for ln in diff_text.splitlines()
            if re.match(r"^[+-]", ln) and not re.match(r"^[+-]{3}", ln)
        )
        source = f"PR #{pr_num}"
        base_label = f"(pr #{pr_num})"
        diff_cmd = f"gh pr diff {pr_num}"
    elif target == "all":
        files = [f for f in run(["git", "ls-files"]).splitlines() if f]
        loc = sum(file_loc(f) for f in files if os.path.isfile(f))
        source = "full repo"
        base_label = "(full repo)"
        diff_cmd = ""
    elif os.path.exists(target):
        if os.path.isdir(target):
            files = [f for f in run(["git", "ls-files", "--", target]).splitlines() if f]
        else:
            files = [target]
        loc = sum(file_loc(f) for f in files if os.path.isfile(f))
        source = f"path: {target}"
        base_label = "(path)"
        diff_cmd = ""
    else:
        err(f"unknown target: {target} (expected branch|staged|pr <num>|all|<path>)")

    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        shown = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        print(
            f"scope.py: warning: {len(missing)} scoped file(s) missing from "
            f"worktree (skipped): {shown}",
            file=sys.stderr,
        )
    files = [f for f in files if os.path.exists(f)]

    print(f"TARGET={target}")
    print(f"SOURCE={source}")
    print(f"BASE={base_label}")
    print(f"DIFF_CMD={diff_cmd}")
    print(f"LOC={loc}")
    print(f"FILE_COUNT={len(files)}")
    print()
    print("FILES")
    for f in files:
        print(f)


if __name__ == "__main__":
    main()
