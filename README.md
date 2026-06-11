# deep-review

A [Claude Code skill](https://code.claude.com/docs/en/skills): repeatable multi-agent code review across a fixed taxonomy of 9 dimensions, producing a severity-ranked, self-contained HTML report. Review only — it never modifies code.

Lives at `~/.claude/skills/deep-review`; invoke with `/deep-review [branch|staged|pr <num>|all|<path>]`.

See [SKILL.md](SKILL.md) for the workflow, [DIMENSIONS.md](DIMENSIONS.md) for the dimension briefs and severity/confidence taxonomy.
