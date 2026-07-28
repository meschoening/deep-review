---
name: deep-review
description: Use when the user runs /deep-review or asks for a thorough, deep, detailed, or multi-agent code review of a branch, PR, staged changes, a path, or the whole repo. Review only — it produces a report; never use it to apply fixes (that is fix-review's job).
---

# Deep Review

Repeatable, thorough code review. The same 9 dimensions every run (`DIMENSIONS.md`), every dimension reviewing the full scope. Consistency comes from the fixed dimension list and from scripts that own every mechanical step — resolving scope, merging, sorting, counting, rendering — not from anyone's judgment on the day. Agents make exactly two judgments: what is wrong with the code, and which two findings are the same issue.

This skill only reports. It never modifies code, and it never judges whether its own findings are correct — verification belongs to `fix-review`.

## Invocation

`/deep-review [target]`

| target               | meaning                                        |
|----------------------|------------------------------------------------|
| (omitted) / `branch` | committed diff vs auto-detected base           |
| `working`            | uncommitted work: worktree vs HEAD + untracked |
| `staged`             | `git diff --cached`                            |
| `pr <num>`           | the PR's diff — must be checked out locally    |
| `all`                | entire repo                                    |
| `<path>`             | a file or directory under the repo             |

`<skill_dir>` below is the directory containing this `SKILL.md`. Use `python3`, falling back to `python` if it isn't on PATH.

## Workflow

1. **Resolve scope.** Run `python3 <skill_dir>/scripts/scope.py <target> [args]`. It prints one JSON object and creates the run's raw directory. Print one line to chat: `scope: <scope field>`. If it exits nonzero, surface its stderr and stop — in particular `pr <num>` fails unless local HEAD matches the PR head, and reviewing a PR against the wrong checkout is worse than not reviewing it.

2. **Check before dispatching.**
   - `fileCount == 0` → stop with `no changes to review`. If `dirty` is true, add: uncommitted work exists — offer `/deep-review working`.
   - `oversized` is true → report `loc` and `fileCount` and ask the user to confirm or narrow the target before dispatching. Nine agents over a whole large repo is expensive and shallow.
   - Any nonzero `skipped` counts → mention them in the scope line so nobody assumes coverage that didn't happen.

3. **Run the review workflow.** Call the Workflow tool with `scriptPath: <skill_dir>/scripts/review-workflow.js` and `args` set to **scope.py's JSON object verbatim** — don't rebuild, trim, or re-key it. This skill instructing the call is the user's multi-agent opt-in. The script runs one agent per dimension (each writing its own raw findings file), retries any that fail, then one synthesis agent that writes a dedupe plan. Agents inherit the session model. Do not inline, edit, or re-derive the script.

   *If the Workflow tool is unavailable*, fan out the same work as parallel read-only Agent dispatches: one per dimension with the script's review prompt, then one synthesis dispatch. Same prompts, same output files.

4. **Merge.** `python3 <skill_dir>/scripts/merge.py --raw-dir <rawDir> -o <findingsPath>`. It applies the plan, filters to scope, verifies each cited file:line, and detects which dimensions produced nothing. Run it even if the workflow reported `planWritten: false` or errored outright — whatever reached disk is still a review, and without a plan every raw finding passes through unmerged. Only give up if merge.py itself reports no raw files at all.

5. **Render.** `python3 <skill_dir>/scripts/render.py <findingsPath> -o <reportPath>`.

6. **One-line chat summary.** Use render.py's counts: `Deep review complete: 2 critical, 5 high, 11 medium, 8 low, 3 nits → .claude/reviews/2026-05-06-1430-branch.html`. Append `(agent failures: <names>)` from merge.py's `dimension agents with no output` line — that reflects what actually reached the report, which the workflow's own return value can't. Append `(unmerged — synthesis failed)` if merge.py ran without a plan. Do not paste findings into chat — the report is the deliverable.

## Constraints

- **Strictly report.** Never edit code, open PRs, or modify the working tree.
- **Same dimensions every run**, whatever the diff looks like. An agent with nothing to flag writes an empty findings array.
- **Never hand-write or hand-edit** `plan.json`, the findings JSON, or the HTML. If a script rejects its input, fix the input or the script — don't route around it.
- **Don't consult prior reviews** in `.claude/reviews/` to seed findings. Fresh every invocation.
- If `.claude/reviews/` isn't gitignored, mention it once after the first run.

## Red Flags — STOP

- About to fix something you spotted, even a one-liner. → Report it. The user decides.
- About to skip a dimension because "this diff has no security/tests/concurrency angle." → Run all 9. That judgment is the bias the fixed list exists to remove.
- About to add, drop, or swap a dimension for this run. → Propose it for `DIMENSIONS.md` *after* the review.
- About to drop, rewrite, or re-severity a finding yourself. → merge.py owns that, and it cannot drop on judgment by design.
- About to summarize the findings in chat because "the user will want the gist." → One line, then the report path.

## Common Mistakes

| Rationalization | Reality |
|---|---|
| "This finding is obviously a false positive, I'll drop it" | You have not verified it either. Unverified in, unverified out — `fix-review` triages. |
| "The diff is tiny, one agent covering everything is enough" | The dimension list is the product. A tiny diff just makes the run cheap. |
| "I'll write the findings JSON myself, it's faster than fixing the plan" | Then the report reflects your judgment, not the review's. Every hand-written field is an untracked edit. |
| "The synthesis agent failed, so the run is lost" | Run merge.py anyway. Raw findings are on disk; you lose deduplication, not findings. |
| "Nine agents on `all` will be thorough" | Nine agents over a whole repo produce shallow coverage and a huge bill. Confirm or narrow first. |
| "scope.py said 0 files but I can see changes" | You have uncommitted work, or the files are binary/oversized. Check `dirty` and `skipped`, don't dispatch on a scope you distrust. |

## Files

- `DIMENSIONS.md` — the 9 dimension briefs and the severity/confidence taxonomy. Single source: agents read it directly; nothing in the prompts duplicates it.
- `scripts/scope.py` — target → workflow args JSON; owns timestamps, slugs, paths, the scope list, and binary/size filtering.
- `scripts/review-workflow.js` — dimension agents in parallel (with one retry), then a synthesis agent that writes the dedupe plan.
- `scripts/merge.py` — applies the plan deterministically; scope filter, dimension union, severity/confidence resolution, file:line verification.
- `scripts/render.py` — findings JSON → HTML; sorts, numbers, counts, escapes, and prints the severity counts.
