# deep-review

A [Claude Code skill](https://code.claude.com/docs/en/skills): repeatable multi-agent code review across a fixed taxonomy of 9 dimensions, producing a severity-ranked, self-contained HTML report. Review only — it never modifies code.

Lives at `~/.claude/skills/deep-review`; invoke with `/deep-review [branch|working|staged|pr <num>|all|<path>]`.

Requires `git`, Python 3, and — for the `pr` target — the `gh` CLI.

## How a run works

```
scope.py  →  review-workflow.js  →  merge.py  →  render.py
 target       9 agents + synthesis    plan →      findings →
 → args       → raw findings           findings    report
```

Agents make two judgments: what is wrong with the code, and which findings are the same issue. Everything else — scope resolution, deduplication, severity and confidence resolution, file:line verification, sorting, counting, layout — is done by scripts, so two runs over the same code produce the same shape of report.

Findings are never retyped after the agent that found them writes them down. The synthesis agent emits a plan of which ids merge into which, not a rewritten copy of the findings, so nothing can be paraphrased, truncated, or silently dropped between review and report. A failed synthesis costs deduplication, not findings.

See [SKILL.md](SKILL.md) for the workflow, [DIMENSIONS.md](DIMENSIONS.md) for the dimension briefs and severity/confidence taxonomy.

## Output

Each run writes to `.claude/reviews/` in the repo under review (worth gitignoring):

| file | |
|---|---|
| `<stamp>-<slug>.html` | the report |
| `<stamp>-<slug>.findings.json` | machine-readable twin, consumed by `fix-review` |
| `<stamp>-<slug>.raw/` | scope list, per-dimension raw findings, merge plan |
