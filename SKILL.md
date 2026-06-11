---
name: deep-review
description: Use when the user runs /deep-review or asks for a thorough, deep, detailed, or multi-agent code review of a branch, PR, staged changes, a path, or the whole repo. Review only — it produces a report; never use it to apply fixes (that is fix-review's job).
---

# Deep Review

Repeatable, thorough code review. The same 9 dimensions every run (`DIMENSIONS.md`), every dimension reviewing the full scope. Findings come back as schema-validated structured output and a deterministic script renders the report, so consistency comes from the fixed dimension list and the fixed renderer — not from anyone's judgment on the day. This skill only reports: it never modifies code, and it never judges its own findings (verification is a downstream skill's job).

## Invocation

`/deep-review [target]`

| target               | meaning                                       |
|----------------------|-----------------------------------------------|
| (omitted) / `branch` | diff vs auto-detected base (main/master)      |
| `staged`             | `git diff --cached`                           |
| `pr <num>`           | the PR's diff — must be checked out locally   |
| `all`                | entire repo                                   |
| `<path>`             | a file or directory under the repo            |

The skill directory is the directory containing this `SKILL.md`. Refer to it as `<skill_dir>` below.

## Workflow

1. **Resolve scope.** Run `python3 <skill_dir>/scripts/scope.py <target> [args]` from the repo root. Parse the output: a header of `KEY=value` lines (`TARGET`, `SOURCE`, `BASE`, `DIFF_CMD`, `LOC`, `FILE_COUNT`), then a `FILES` section listing one path per line. Print one line to chat: `scope: 1247 LOC, 14 files`.

2. **Abort on empty or error.** If `LOC == 0` or `FILE_COUNT == 0`, abort with `no changes to review`. If the script errored, surface its message and stop — in particular, `pr <num>` errors unless the local HEAD matches the PR head commit (it tells the user to run `gh pr checkout <num>` first). Never silently dispatch on empty scope, and never review a PR against the wrong checkout.

3. **Gather workflow inputs.**
   - Dimension names: `grep -E '^### ' <skill_dir>/DIMENSIONS.md | sed -E 's/^### [0-9]+\. //'`
   - Timestamps: `date "+%Y-%m-%d %H:%M:%S"` (report header) and `date "+%Y-%m-%d-%H%M"` (file stamp).
   - Slug: `branch`, `staged`, `pr-<num>`, `all`, or for paths replace `/` and `.` with `-` (e.g. `engine-cli-py`).
   - `mkdir -p <repo_root>/.claude/reviews`; findings path is `<repo_root>/.claude/reviews/<stamp>-<slug>.findings.json`, report path the same with `.html`.

4. **Run the review workflow.** Call the Workflow tool with `scriptPath: <skill_dir>/scripts/review-workflow.js` and args `{ skillDir, repoRoot, source, scope, timestamp, dimensions, files, diffCmd, findingsPath }` — `files` is the `FILES` list verbatim, `scope` is the same one-liner printed in step 1, `diffCmd` is `DIFF_CMD` (empty string for `all`/path targets). This skill instructing the call is the user's multi-agent opt-in. The script dispatches one agent per dimension plus a synthesis agent that writes the findings JSON; all agents inherit the session model. Do not inline, edit, or re-derive the script, and do not review or merge findings yourself — the workflow doing it the same way each run is the point.

5. **Render.** Run `python3 <skill_dir>/scripts/render.py <findingsPath> -o <reportPath>`. The script sorts, numbers, counts, HTML-escapes, and lays out the report deterministically, then prints the severity counts.

6. **One-line chat summary.** Format: `Deep review complete: 2 critical, 5 high, 11 medium, 8 low, 3 nits → .claude/reviews/2026-05-06-1430-branch.html`, using render.py's counts. If any dimension agents failed, append `(agent failures: <names>)`. Do not paste findings into chat.

## Constraints

- **Strictly report.** Never edit code, open PRs, or modify the working tree. Even obvious-looking fixes — the user decides what to act on.
- **Same dimensions every run.** Don't skip dimensions because "this PR doesn't seem to involve security" or "no tests touched". Consistency is the point. A dimension agent returns an empty findings list if there's genuinely nothing to flag.
- **Don't consult prior reviews** in `.claude/reviews/` to seed findings. Do the work fresh each invocation.
- **Don't merge, filter, or verify findings yourself.** The workflow's synthesis agent merges mechanically (dedupe, severity sanity-check, scope check) and nothing in this skill judges whether a finding is correct — verification belongs to a downstream skill.
- **Don't add a custom dimension** because the diff "feels like it needs one". If a real gap exists, propose adding it to `DIMENSIONS.md` *after* the review — never mid-run.

## Files

- `DIMENSIONS.md` — the 9 dimension briefs and the severity/confidence taxonomy. Single source: workflow agents read it directly; nothing in the prompts duplicates it.
- `scripts/scope.py` — resolves a target into LOC, file list, and the diff command; enforces the PR-checkout match.
- `scripts/review-workflow.js` — the Workflow script: schema-validated dimension agents in parallel, then one synthesis agent that writes the findings JSON.
- `scripts/render.py` — deterministic findings-JSON → HTML renderer; prints the severity counts for the chat summary.
