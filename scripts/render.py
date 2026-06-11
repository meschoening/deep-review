#!/usr/bin/env python3
"""render.py — deterministic HTML renderer for deep-review findings JSON.

Usage:
    render.py <findings.json> -o <report.html>

Input shape (written by the synthesis agent in review-workflow.js):
{
  "meta": {
    "source": str, "generated": str, "scope": str,
    "dimensions": [str, ...], "failures": [str, ...]
  },
  "findings": [
    {"file": str, "line": int, "severity": str, "confidence": str,
     "dimensions": [str, ...], "description": str, "suggestion": str}
  ]
}

All sorting, numbering, counting, HTML escaping, and layout happen here —
never in an agent — so every report is structurally identical. Prints the
severity counts to stdout for the dispatcher's one-line chat summary.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from typing import NoReturn

SEVERITIES = ["critical", "high", "medium", "low", "nit"]
CONFIDENCES = {"high", "medium", "low"}
FINDING_FIELDS = {
    "file",
    "line",
    "severity",
    "confidence",
    "dimensions",
    "description",
    "suggestion",
}

CSS = """\
  :root {
    --fg: #1a1a1a;
    --muted: #5e5e5e;
    --rule: #e1e1e1;
    --bg: #ffffff;
    --bg-soft: #f7f7f7;
    --c-critical: #b00020; --bg-critical: #fde8ea;
    --c-high:     #c25a00; --bg-high:     #fdecdc;
    --c-medium:   #8a6d00; --bg-medium:   #fdf6d6;
    --c-low:      #2f6f3a; --bg-low:      #e6f2e6;
    --c-nit:      #555555; --bg-nit:      #ececec;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --fg: #ececec; --muted: #a5a5a5; --rule: #2e2e2e;
      --bg: #161616; --bg-soft: #1d1d1d;
      --bg-critical: #3a1417; --bg-high: #3a2410;
      --bg-medium: #3a3110; --bg-low: #11331a; --bg-nit: #2a2a2a;
    }
  }
  html, body { background: var(--bg); color: var(--fg); }
  body {
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    max-width: 920px;
    margin: 2.5rem auto;
    padding: 0 1.25rem 4rem;
  }
  h1 { font-size: 1.75rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
  h2 { font-size: 1.2rem; margin: 2.25rem 0 .75rem; padding-bottom: .35rem; border-bottom: 1px solid var(--rule); }
  h3 { font-size: .8rem; margin: 1.5rem 0 .5rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }
  code, .path { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; font-size: .92em; }
  .meta { color: var(--muted); margin: .25rem 0 1.5rem; }
  .meta div { margin: .15rem 0; }
  .meta strong { color: var(--fg); font-weight: 600; }
  table.summary { border-collapse: collapse; margin: .25rem 0 1rem; min-width: 220px; }
  table.summary th, table.summary td { padding: .35rem .85rem; border: 1px solid var(--rule); text-align: left; }
  table.summary th { background: var(--bg-soft); font-weight: 600; }
  table.summary tr.total td { font-weight: 700; }
  .finding { border: 1px solid var(--rule); border-left-width: 4px; border-radius: 5px; padding: .75rem 1rem; margin: .65rem 0; background: var(--bg-soft); }
  .finding.critical { border-left-color: var(--c-critical); }
  .finding.high     { border-left-color: var(--c-high); }
  .finding.medium   { border-left-color: var(--c-medium); }
  .finding.low      { border-left-color: var(--c-low); }
  .finding.nit      { border-left-color: var(--c-nit); }
  .finding > header { display: flex; flex-wrap: wrap; gap: .5rem; align-items: baseline; margin-bottom: .35rem; }
  .finding h4 { margin: 0; font-size: .98rem; font-weight: 600; }
  .finding h4 .num { color: var(--muted); margin-right: .4rem; font-weight: 500; }
  .sev { display: inline-block; padding: .08rem .55rem; border-radius: 3px; font-size: .72rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; }
  .sev.critical { background: var(--bg-critical); color: var(--c-critical); }
  .sev.high     { background: var(--bg-high);     color: var(--c-high); }
  .sev.medium   { background: var(--bg-medium);   color: var(--c-medium); }
  .sev.low      { background: var(--bg-low);      color: var(--c-low); }
  .sev.nit      { background: var(--bg-nit);      color: var(--c-nit); }
  .dim { color: var(--muted); font-size: .8rem; }
  .conf { display: inline-block; padding: .08rem .45rem; border: 1px solid var(--rule); border-radius: 3px; font-size: .72rem; color: var(--muted); }
  .finding p { margin: .35rem 0; }
  .fix { margin-top: .5rem; }
  .fix strong { color: var(--muted); font-weight: 600; }
  .empty { color: var(--muted); font-style: italic; }
  .coverage p { margin: .35rem 0; }
"""


def die(msg: str) -> NoReturn:
    print(f"render.py: {msg}", file=sys.stderr)
    sys.exit(1)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def validate(data: dict) -> tuple[dict, list[dict]]:
    meta = data.get("meta")
    findings = data.get("findings")
    if not isinstance(meta, dict) or not isinstance(findings, list):
        die("input must have object 'meta' and array 'findings'")
    for key in ("source", "generated", "scope", "dimensions", "failures"):
        if key not in meta:
            die(f"meta missing key: {key}")
    for i, f in enumerate(findings):
        missing = FINDING_FIELDS - set(f)
        if missing:
            die(f"finding {i} missing fields: {', '.join(sorted(missing))}")
        if f["severity"] not in SEVERITIES:
            die(f"finding {i} has unknown severity: {f['severity']!r}")
        if f["confidence"] not in CONFIDENCES:
            die(f"finding {i} has unknown confidence: {f['confidence']!r}")
        if not isinstance(f["line"], int) or f["line"] < 0:
            die(f"finding {i} has invalid line: {f['line']!r}")
        if not isinstance(f["dimensions"], list) or not f["dimensions"]:
            die(f"finding {i} has invalid dimensions: {f['dimensions']!r}")
    return meta, findings


def render_finding(f: dict, num: int) -> str:
    sev = f["severity"]
    loc = f["file"] if f["line"] == 0 else f"{f['file']}:{f['line']}"
    conf = (
        f'\n    <span class="conf">{esc(f["confidence"])} confidence</span>'
        if f["confidence"] != "high"
        else ""
    )
    return f"""\
<article class="finding {sev}" id="f{num}">
  <header>
    <span class="sev {sev}">{sev}</span>
    <h4><span class="num">{num}.</span><code class="path">{esc(loc)}</code></h4>
    <span class="dim">{esc(", ".join(f["dimensions"]))}</span>{conf}
  </header>
  <p>{esc(f["description"])}</p>
  <p class="fix"><strong>Fix:</strong> {esc(f["suggestion"])}</p>
</article>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("findings_json")
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args()

    try:
        with open(args.findings_json, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        die(f"cannot read findings JSON: {e}")

    meta, findings = validate(data)
    findings.sort(key=lambda f: (SEVERITIES.index(f["severity"]), f["file"], f["line"]))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    total = len(findings)

    failures = list(meta["failures"])
    covered = [d for d in meta["dimensions"] if d not in set(failures)]
    agents_line = f"{len(meta['dimensions'])} dimension agents + 1 synthesis pass"

    if total == 0:
        findings_html = '<p class="empty">No actionable findings.</p>'
    else:
        parts = []
        num = 0
        for sev in SEVERITIES:
            bucket = [f for f in findings if f["severity"] == sev]
            if not bucket:
                continue
            parts.append(f"<h3>{sev.capitalize()}</h3>")
            for f in bucket:
                num += 1
                parts.append(render_finding(f, num))
        findings_html = "\n".join(parts)

    summary_rows = "\n".join(
        f"    <tr><td>{s}</td><td>{counts[s]}</td></tr>" for s in SEVERITIES
    )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Code Review — {esc(meta["source"])}</title>
<style>
{CSS}</style>
</head>
<body>
<h1>Code Review — {esc(meta["source"])}</h1>
<div class="meta">
  <div><strong>Generated:</strong> {esc(meta["generated"])}</div>
  <div><strong>Scope:</strong> {esc(meta["scope"])}</div>
  <div><strong>Agents:</strong> {esc(agents_line)}</div>
</div>

<h2>Summary</h2>
<table class="summary">
  <thead><tr><th>Severity</th><th>Count</th></tr></thead>
  <tbody>
{summary_rows}
    <tr class="total"><td>total</td><td>{total}</td></tr>
  </tbody>
</table>

<h2>Findings</h2>
{findings_html}

<h2>Coverage</h2>
<section class="coverage">
  <p><strong>Dimensions covered:</strong> {esc(", ".join(covered) if covered else "(none)")}</p>
  <p><strong>Agent failures:</strong> {esc(", ".join(failures) if failures else "(none)")}</p>
</section>
</body>
</html>
"""

    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(doc)
    except OSError as e:
        die(f"cannot write report: {e}")

    print(" ".join(f"{s}={counts[s]}" for s in SEVERITIES) + f" total={total}")
    print(f"report: {args.out}")


if __name__ == "__main__":
    main()
