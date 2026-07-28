#!/usr/bin/env python3
"""scope.py — resolve a /deep-review target into a ready-to-use workflow args JSON.

Usage:
    scope.py [target] [args]

Targets:
    branch              committed diff vs auto-detected base (origin/main, main, ...)
    working             uncommitted work: working tree vs HEAD, plus untracked files
    staged              git diff --cached
    pr <num>            a PR's diff — local HEAD must match the PR head commit
    all                 entire repo (tracked + untracked, minus ignored)
    <path>              a file or directory inside the repo

Prints one JSON object to stdout: the exact args the review workflow takes,
plus the fields the dispatcher needs to decide whether to run at all. Errors go
to stderr with a nonzero exit.

Side effects (only when there is something to review): creates the run's raw
directory and writes `meta.json` (consumed by merge.py) and `files.txt` (the
scope list, read by the review agents and by merge.py's scope filter).

Everything mechanical lives here rather than in the skill's prose — timestamps,
slugs, paths, the dimension list — so the dispatcher never derives a value by
hand and the same target always produces the same run layout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import NoReturn

# A file over this size is machine-generated, vendored, or a blob; nine agents
# reading it would burn the run's context for nothing.
MAX_FILE_BYTES = 1 << 20
BINARY_SNIFF_BYTES = 8000  # how much git itself reads before calling a file binary

# Above either threshold a full review stops being meaningful (and gets expensive),
# so the dispatcher confirms with the user first.
OVERSIZED_LOC = 25_000
OVERSIZED_FILES = 300

# This skill's own output. It is untracked (or committed) inside the repo under
# review, so without this every run after the first would review its predecessors.
REVIEW_OUTPUT = ".claude/reviews/"

SKILL_DIR = Path(__file__).resolve().parent.parent


def err(msg: str, code: int = 1) -> NoReturn:
    print(f"scope.py: {msg}", file=sys.stderr)
    sys.exit(code)


def run(cmd: list[str], check: bool = True) -> str:
    res = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and res.returncode != 0:
        err(f"command failed: {' '.join(cmd)}\n{res.stderr.strip()}")
    return res.stdout


def verify_rev(ref: str) -> str | None:
    res = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    return res.stdout.strip() or None


def zsplit(out: str) -> list[str]:
    """Split NUL-delimited git output. -z also stops git from quoting non-ASCII
    paths, which would otherwise make them unopenable."""
    return [t for t in out.split("\0") if t]


def dedupe(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def detect_base() -> str:
    for ref in ("origin/main", "origin/master", "main", "master"):
        if verify_rev(ref):
            return ref
    err("could not detect base branch (no origin/main, origin/master, main, or master)")


def diff_files(diff_args: list[str]) -> list[str]:
    return zsplit(
        run(["git", "diff", "--name-only", "-z", "--diff-filter=ACMRTUXB", *diff_args])
    )


def diff_numstat(diff_args: list[str]) -> dict[str, int]:
    """Map path -> changed lines. Binary files report '-' and count as 0.

    With -z, a rename is emitted as `add\\tdel\\t` followed by two extra
    NUL-separated tokens (old path, new path) rather than one inline path.
    """
    toks = run(["git", "diff", "--numstat", "-z", *diff_args]).split("\0")
    stats: dict[str, int] = {}
    i = 0
    while i < len(toks):
        m = re.match(r"^(\d+|-)\t(\d+|-)\t(.*)$", toks[i], re.S)
        if not m:
            i += 1
            continue
        added, deleted, path = m.groups()
        if path == "":
            path = toks[i + 2] if i + 2 < len(toks) else ""
            i += 3
        else:
            i += 1
        if path:
            stats[path] = 0 if "-" in (added, deleted) else int(added) + int(deleted)
    return stats


def ls_files(pathspec: str | None = None, untracked: bool = True) -> list[str]:
    cmd = ["git", "ls-files", "-z", "--cached"]
    if untracked:
        cmd += ["--others", "--exclude-standard"]
    if pathspec:
        cmd += ["--", pathspec]
    return dedupe(zsplit(run(cmd)))


def untracked_files(pathspec: str | None = None) -> list[str]:
    cmd = ["git", "ls-files", "-z", "--others", "--exclude-standard"]
    if pathspec:
        cmd += ["--", pathspec]
    return zsplit(run(cmd))


def classify(path: str) -> str | None:
    """None if the file is worth handing to a reviewer, else why it isn't."""
    try:
        if not os.path.isfile(path):
            return "missing"
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return "large"
        with open(path, "rb") as fh:
            if b"\0" in fh.read(BINARY_SNIFF_BYTES):
                return "binary"
    except OSError:
        return "missing"
    return None


def file_loc(path: str) -> int:
    try:
        with open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def read_dimensions() -> list[str]:
    md = SKILL_DIR / "DIMENSIONS.md"
    try:
        text = md.read_text(encoding="utf-8")
    except OSError as e:
        err(f"cannot read {md}: {e}")
    dims = re.findall(r"^### \d+\. (.+)$", text, re.M)
    if not dims:
        err(f"no '### N. Name' dimension headings found in {md}")
    return [d.strip() for d in dims]


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9]+", "-", text)).strip("-").lower()


def resolve_pr(pr_num: str) -> tuple[list[str], dict[str, int], str, str, int | None]:
    """(files, numstat, diff_cmd, base_sha, fallback_loc) for a checked-out PR.

    fallback_loc is set only when the base commit isn't available locally and the
    per-file breakdown had to be given up for gh's whole-diff line count.
    """
    if not shutil.which("gh"):
        err("gh CLI not found; needed for pr target")
    info = json.loads(
        run(["gh", "pr", "view", pr_num, "--json", "headRefOid,headRefName,baseRefOid,baseRefName"])
    )
    local_head = run(["git", "rev-parse", "HEAD"]).strip()
    if local_head != info["headRefOid"]:
        err(
            f"local HEAD {local_head[:9]} does not match PR #{pr_num} head "
            f"{info['headRefOid'][:9]} ({info['headRefName']}); "
            f"run: gh pr checkout {pr_num}"
        )
    diff_cmd = f"gh pr diff {pr_num}"
    for ref in (info.get("baseRefOid"), f"origin/{info.get('baseRefName')}"):
        sha = verify_rev(ref) if ref else None
        if sha:
            spec = f"{ref}...HEAD"
            return diff_files([spec]), diff_numstat([spec]), diff_cmd, sha, None
    # Base commit was never fetched locally; fall back to gh's own diff.
    files = [f for f in run(["gh", "pr", "diff", pr_num, "--name-only"]).splitlines() if f]
    text = run(["gh", "pr", "diff", pr_num])
    total = sum(
        1
        for ln in text.splitlines()
        if re.match(r"^[+-]", ln) and not re.match(r"^[+-]{3}", ln)
    )
    return files, {}, diff_cmd, info.get("baseRefOid", ""), total


def main() -> None:
    ap = argparse.ArgumentParser(description="resolve a /deep-review target")
    ap.add_argument("target", nargs="?", default="branch")
    ap.add_argument("rest", nargs="*")
    opts = ap.parse_args()
    target = opts.target or "branch"

    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if top.returncode != 0:
        err("not in a git repository")
    repo_root = top.stdout.strip()
    # Every git path below is repo-root-relative, so resolve them from there
    # rather than from wherever the dispatcher happened to be invoked.
    os.chdir(repo_root)

    head_sha = run(["git", "rev-parse", "HEAD"], check=False).strip()
    dirty = bool(run(["git", "status", "--porcelain"], check=False).strip())

    base_label = ""
    base_sha = ""
    numstat: dict[str, int] = {}
    full_file_loc = False  # count whole files rather than changed lines
    fallback_loc: int | None = None

    if target in ("branch", ""):
        target = "branch"
        base = detect_base()
        base_sha = run(["git", "merge-base", base, "HEAD"], check=False).strip()
        spec = f"{base}...HEAD"
        files = diff_files([spec])
        numstat = diff_numstat([spec])
        source = f"branch diff vs {base}"
        base_label = base
        diff_cmd = f"git diff {spec}"
    elif target == "working":
        files = diff_files(["HEAD"])
        numstat = diff_numstat(["HEAD"])
        new = untracked_files()
        files = dedupe(files + new)
        for f in new:
            numstat.setdefault(f, file_loc(f))
        source = "uncommitted changes (working tree vs HEAD)"
        base_label = "HEAD"
        base_sha = head_sha
        diff_cmd = "git diff HEAD"
    elif target == "staged":
        files = diff_files(["--cached"])
        numstat = diff_numstat(["--cached"])
        source = "staged changes"
        base_label = "(staged)"
        base_sha = head_sha
        diff_cmd = "git diff --cached"
    elif target == "pr":
        if not opts.rest:
            err("pr target requires a PR number: scope.py pr <num>")
        pr_num = opts.rest[0]
        files, numstat, diff_cmd, base_sha, fallback_loc = resolve_pr(pr_num)
        source = f"PR #{pr_num}"
        base_label = f"(pr #{pr_num})"
    elif target == "all":
        files = ls_files()
        source = "full repo"
        base_label = "(full repo)"
        diff_cmd = ""
        full_file_loc = True
    elif os.path.exists(target):
        files = ls_files(target) if os.path.isdir(target) else [target]
        source = f"path: {target}"
        base_label = "(path)"
        diff_cmd = ""
        full_file_loc = True
    else:
        err(
            f"unknown target: {target} "
            f"(expected branch|working|staged|pr <num>|all|<path>)"
        )

    skipped = {"binary": 0, "large": 0, "missing": 0}
    kept = []
    for f in files:
        if f.replace("\\", "/").startswith(REVIEW_OUTPUT):
            continue
        reason = classify(f)
        if reason:
            skipped[reason] += 1
        else:
            kept.append(f)
    files = kept

    # LOC counts only the files that survived, so the number always describes
    # what the agents actually see (deleted and binary files never inflate it).
    if fallback_loc is not None:
        loc = fallback_loc  # PR with no local base: whole-diff total, unsplittable
    elif full_file_loc:
        loc = sum(file_loc(f) for f in files)
    else:
        loc = sum(numstat.get(f, 0) for f in files)

    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d-%H%M")
    slug = {"branch": "branch", "working": "working", "staged": "staged", "all": "all"}.get(
        target, f"pr-{opts.rest[0]}" if target == "pr" else slugify(target)
    )
    reviews = Path(repo_root) / ".claude" / "reviews"
    stem = f"{stamp}-{slug}"
    raw_dir = reviews / f"{stem}.raw"
    dimensions = read_dimensions()

    payload = {
        "target": target,
        "source": source,
        "scope": f"{loc} LOC, {len(files)} files",
        "loc": loc,
        "fileCount": len(files),
        "dirty": dirty,
        "oversized": loc > OVERSIZED_LOC or len(files) > OVERSIZED_FILES,
        "skipped": skipped,
        "repoRoot": repo_root,
        "skillDir": str(SKILL_DIR),
        "base": base_label,
        "baseSha": base_sha,
        "headSha": head_sha,
        "diffCmd": diff_cmd,
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "dimensions": dimensions,
        "rawDir": str(raw_dir),
        "filesPath": str(raw_dir / "files.txt"),
        "findingsPath": str(reviews / f"{stem}.findings.json"),
        "reportPath": str(reviews / f"{stem}.html"),
    }

    if files:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "files.txt").write_text("\n".join(files) + "\n", encoding="utf-8")
        (raw_dir / "meta.json").write_text(
            json.dumps(
                {
                    "source": source,
                    "generated": payload["timestamp"],
                    "scope": payload["scope"],
                    "dimensions": dimensions,
                    "base": base_label,
                    "baseSha": base_sha,
                    "headSha": head_sha,
                    "repoRoot": repo_root,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
