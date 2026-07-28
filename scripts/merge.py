#!/usr/bin/env python3
"""merge.py — deterministically assemble deep-review findings from raw agent output.

Usage:
    merge.py --raw-dir <run>.raw -o <run>.findings.json [--plan PATH]

Reads, from the run's raw directory:
    meta.json     written by scope.py
    files.txt     the scope list, written by scope.py
    NN.json       one per dimension agent: {index, dimension, findings: [...]}
    plan.json     optional, written by the synthesis agent: which findings are
                  duplicates of which, plus any severity corrections

Everything except "are these two findings the same issue?" happens here rather
than in an agent: scope filtering, dimension unioning, severity/confidence
resolution, file:line verification, and failure detection. Findings are moved,
never retyped, so no prose can drift or be truncated between review and report.

Fail-open by design. A finding no plan group mentions passes through untouched,
and with no plan at all every raw finding still lands in the report (unmerged) —
a failed synthesis costs deduplication, never findings.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import NoReturn

SEVERITIES = ["critical", "high", "medium", "low", "nit"]
CONFIDENCES = ["high", "medium", "low"]
REQUIRED = {"file", "line", "severity", "confidence", "description", "suggestion"}


def die(msg: str) -> NoReturn:
    print(f"merge.py: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"merge.py: {msg}", file=sys.stderr)


def norm(path: str) -> str:
    return re.sub(r"^\./", "", str(path).replace("\\", "/")).strip()


def load_json(path: Path) -> object:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        die(f"cannot read {path}: {e}")


def load_raw(raw_dir: Path, dimensions: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Return (id -> finding, failed dimension names). id is '<dim#>#<position>'."""
    findings: dict[str, dict] = {}
    seen_indexes: set[int] = set()
    malformed = 0

    for path in sorted(raw_dir.glob("[0-9][0-9].json")):
        data = load_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
            warn(f"{path.name}: not a valid raw findings file, ignoring")
            continue
        try:
            index = int(data.get("index", int(path.stem)))
        except (TypeError, ValueError):
            warn(f"{path.name}: unusable index, ignoring")
            continue
        if not 1 <= index <= len(dimensions):
            warn(f"{path.name}: index {index} outside the dimension list, ignoring")
            continue
        seen_indexes.add(index)
        own = data.get("dimension") or dimensions[index - 1]
        for pos, f in enumerate(data["findings"]):
            if not isinstance(f, dict) or (REQUIRED - set(f)):
                malformed += 1
                continue
            f = dict(f)
            f["file"] = norm(f["file"])
            f["_dims"] = [f.get("dimension") or own]
            findings[f"{index}#{pos}"] = f

    if malformed:
        warn(f"{malformed} raw finding(s) dropped: missing required fields")
    failures = [d for i, d in enumerate(dimensions, 1) if i not in seen_indexes]
    return findings, failures


def most_severe(values: list[str]) -> str:
    known = [v for v in values if v in SEVERITIES]
    return min(known, key=SEVERITIES.index) if known else "low"


def highest_confidence(values: list[str]) -> str:
    known = [v for v in values if v in CONFIDENCES]
    return min(known, key=CONFIDENCES.index) if known else "low"


def apply_plan(findings: dict[str, dict], plan: dict) -> tuple[list[dict], int, int]:
    """Fold each plan group into its keeper. Unmentioned findings pass through."""
    consumed: set[str] = set()
    merged = 0
    overrides = 0
    out: list[dict] = []

    for group in plan.get("groups") or []:
        keep_id = group.get("keep")
        keeper = findings.get(keep_id)
        if keeper is None:
            warn(f"plan references unknown finding id {keep_id!r}, skipping group")
            continue
        if keep_id in consumed:
            warn(f"plan uses {keep_id!r} in more than one group, skipping the later one")
            continue
        dup_ids = [d for d in (group.get("duplicates") or []) if d != keep_id]
        dups = []
        for d in dup_ids:
            if d not in findings:
                warn(f"plan references unknown finding id {d!r}, ignoring")
            elif d in consumed:
                warn(f"finding {d!r} claimed by two groups, ignoring the later claim")
            else:
                dups.append(findings[d])
                consumed.add(d)
        consumed.add(keep_id)
        merged += len(dups)

        member_dims = [dim for m in [keeper, *dups] for dim in m["_dims"]]
        keeper["_dims"] = list(dict.fromkeys(member_dims))
        keeper["confidence"] = highest_confidence(
            [m["confidence"] for m in [keeper, *dups]]
        )
        computed = most_severe([m["severity"] for m in [keeper, *dups]])
        override = group.get("severity")
        if override in SEVERITIES and override != computed:
            keeper["severity"] = override
            overrides += 1
        else:
            keeper["severity"] = computed
        out.append(keeper)

    for fid, f in findings.items():
        if fid not in consumed:
            out.append(f)
    return out, merged, overrides


def resolve(f: dict, repo_root: Path) -> bool:
    """True if the cited file exists and the cited line is inside it."""
    target = repo_root / f["file"]
    if not target.is_file():
        return False
    if f["line"] == 0:
        return True
    try:
        with open(target, "rb") as fh:
            return f["line"] <= sum(1 for _ in fh)
    except OSError:
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--plan")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    if not raw_dir.is_dir():
        die(f"raw directory not found: {raw_dir}")
    meta = load_json(raw_dir / "meta.json")
    if not isinstance(meta, dict) or not isinstance(meta.get("dimensions"), list):
        die("meta.json is missing its dimensions list")
    dimensions = meta["dimensions"]

    try:
        scope_files = {
            norm(l) for l in (raw_dir / "files.txt").read_text(encoding="utf-8").splitlines() if l.strip()
        }
    except OSError as e:
        die(f"cannot read files.txt: {e}")
    by_basename: dict[str, list[str]] = {}
    for p in scope_files:
        by_basename.setdefault(os.path.basename(p), []).append(p)

    findings, failures = load_raw(raw_dir, dimensions)
    if not findings and failures == dimensions:
        die("no raw findings files found: every dimension agent failed")

    plan_path = Path(args.plan) if args.plan else raw_dir / "plan.json"
    plan = load_json(plan_path) if plan_path.is_file() else None
    if plan is None:
        warn("no plan.json: writing every raw finding through unmerged")
        merged_list, merged, overrides = list(findings.values()), 0, 0
    elif not isinstance(plan, dict):
        die(f"{plan_path} is not a JSON object")
    else:
        merged_list, merged, overrides = apply_plan(findings, plan)

    repo_root = Path(meta.get("repoRoot") or ".")
    out = []
    out_of_scope = 0
    repaired = 0
    unresolved = 0
    for f in merged_list:
        path = f["file"]
        if path not in scope_files:
            # Agents sometimes cite a bare filename; repair it when the scope
            # list makes the intent unambiguous, drop it only when it doesn't.
            candidates = by_basename.get(os.path.basename(path), [])
            if len(candidates) == 1:
                path = candidates[0]
                repaired += 1
            else:
                out_of_scope += 1
                continue
        ok = resolve({**f, "file": path}, repo_root)
        unresolved += not ok
        out.append(
            {
                "file": path,
                "line": f["line"],
                "severity": f["severity"] if f["severity"] in SEVERITIES else "low",
                "confidence": f["confidence"] if f["confidence"] in CONFIDENCES else "low",
                "dimensions": f["_dims"],
                "title": f.get("title") or "",
                "description": f["description"],
                "suggestion": f["suggestion"],
                "resolved": ok,
            }
        )

    doc = {
        "meta": {
            "source": meta.get("source", ""),
            "generated": meta.get("generated", ""),
            "scope": meta.get("scope", ""),
            "dimensions": dimensions,
            "failures": failures,
            "base": meta.get("base", ""),
            "baseSha": meta.get("baseSha", ""),
            "headSha": meta.get("headSha", ""),
            "merged": plan is not None,
        },
        "findings": out,
    }
    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
            fh.write("\n")
    except OSError as e:
        die(f"cannot write findings JSON: {e}")

    print(
        f"raw={len(findings)} merged_away={merged} severity_overrides={overrides} "
        f"path_repaired={repaired} out_of_scope={out_of_scope} "
        f"unresolved_location={unresolved} written={len(out)}"
    )
    if failures:
        print(f"dimension agents with no output: {', '.join(failures)}")


if __name__ == "__main__":
    main()
