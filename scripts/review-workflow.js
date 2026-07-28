// deep-review orchestration. Invoked by SKILL.md via Workflow({scriptPath, args}).
//
// args: pass scope.py's JSON output verbatim. The fields used here are
//   repoRoot, skillDir, source, scope, diffCmd, dimensions, filesPath, rawDir
// plus an optional `model` (smoke-testing only — a tier override to exercise the
// pipeline cheaply). Real reviews omit it: every agent inherits the session model.
//
// Findings never travel through this script. Each dimension agent writes its own
// raw file and returns only a count; the synthesis agent reads those files and
// writes a *plan* — which findings duplicate which — rather than a rewritten copy
// of them. merge.py then assembles the report input deterministically. Nothing in
// the pipeline retypes a finding's prose, so nothing can paraphrase or truncate it.
//
// Severity/confidence taxonomy and dimension briefs live ONLY in DIMENSIONS.md;
// agents read that file themselves so nothing is duplicated here.

export const meta = {
  name: 'deep-review',
  description: 'Fixed-dimension review agents in parallel, then one synthesis agent plans the merge',
  phases: [
    { title: 'Review', detail: 'one agent per dimension, each over the full scope' },
    { title: 'Retry', detail: 'one more attempt for any dimension that failed' },
    { title: 'Synthesize', detail: 'read raw findings, write the dedupe plan' },
  ],
}

const REVIEW_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['index', 'dimension', 'count', 'wrote'],
  properties: {
    index: { type: 'integer', description: 'your dimension number' },
    dimension: { type: 'string', description: 'your dimension name' },
    count: { type: 'integer', minimum: 0, description: 'findings in the file you wrote' },
    wrote: { type: 'string', description: 'absolute path of the raw findings file you wrote' },
  },
}

const PLAN_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['groups', 'duplicatesMerged'],
  properties: {
    groups: { type: 'integer', minimum: 0, description: 'groups written to plan.json' },
    duplicatesMerged: { type: 'integer', minimum: 0, description: 'findings folded into a keeper' },
    severityOverrides: { type: 'integer', minimum: 0, description: 'severities corrected against the taxonomy' },
  },
}

let a = args
if (typeof a === 'string') {
  // Dispatchers sometimes pass args JSON-encoded; tolerate both forms.
  try { a = JSON.parse(a) } catch (e) { throw new Error('deep-review workflow: args arrived as an unparseable string') }
}
const missing = ['repoRoot', 'skillDir', 'source', 'scope', 'dimensions', 'filesPath', 'rawDir']
  .filter((k) => a == null || a[k] == null || a[k] === '')
if (missing.length) throw new Error(`deep-review workflow: missing args: ${missing.join(', ')}`)
if (!Array.isArray(a.dimensions) || !a.dimensions.length) throw new Error('deep-review workflow: args.dimensions must be a non-empty array')

const modelOpt = a.model ? { model: a.model } : {}
const pad = (n) => String(n).padStart(2, '0')
const diffNote = a.diffCmd
  ? `This is a diff-based review. See what changed with:\n  ${a.diffCmd}\nFindings must cite lines that exist in the current version of each file. Read the surrounding code too — bugs often live just outside the diff.`
  : 'This is a full review of the files in scope, not a diff.'

const reviewPrompt = (dim, i) => `You are a code reviewer focused on ONE dimension: ${dim}.

Working directory: ${a.repoRoot}
Scope: ${a.scope}. Source: ${a.source}.
The files in scope are listed one per line in ${a.filesPath} — read that file first. Review every file it lists.

${diffNote}

Read ${a.skillDir}/DIMENSIONS.md. Its "Severity taxonomy" section defines the severity levels and the confidence field — apply them exactly. Your brief is the numbered section "${i}. ${dim}": apply its checklist to every file in scope.

If the repo documents its own standards (CLAUDE.md, AGENTS.md, CONTRIBUTING.md, docs/ style or architecture guides), read what is relevant and hold the code to those documented rules. A violation of the repo's own written convention is a real finding; your personal preference is not.

Rules:
- Severity rates the impact assuming the finding is real; confidence rates how sure you are it is real. Never downgrade severity to hedge — hedge with confidence.
- If you find a real issue outside your dimension, report it anyway and set its dimension field to where it belongs (exact names from DIMENSIONS.md). Do not assume a parallel agent saw it.
- Be specific: file, line, what is wrong, why it matters, and a concrete fix.
- title, description and suggestion are plain prose — no markdown, no HTML, no backticks. Refer to symbols and paths as bare text; everything is escaped and rendered verbatim downstream.
- Read-only review: do not modify any file under ${a.repoRoot} and do not run state-changing commands.
- If there is genuinely nothing to flag, write an empty findings array. Do not invent findings to fill a quota.

Then use the Write tool to write ${a.rawDir}/${pad(i)}.json:

{
  "index": ${i},
  "dimension": ${JSON.stringify(dim)},
  "findings": [
    {
      "file": "repo-relative path",
      "line": 123,
      "severity": "critical|high|medium|low|nit",
      "confidence": "high|medium|low",
      "dimension": "which dimension this belongs to",
      "title": "short noun phrase naming the problem, under 60 characters",
      "description": "1-2 sentences",
      "suggestion": "the concrete fix"
    }
  ]
}

Use line 0 for a finding about the file as a whole. This file is the only record of your findings — nothing downstream re-reads your reply — so write it before you return.`

phase('Review')
let results = await parallel(a.dimensions.map((dim, idx) => () =>
  agent(reviewPrompt(dim, idx + 1), { label: `review:${dim}`, phase: 'Review', schema: REVIEW_SCHEMA, ...modelOpt })))

const allIdx = () => [...a.dimensions.keys()]
let failed = allIdx().filter((i) => !results[i] || typeof results[i].count !== 'number')
if (failed.length) {
  // One flake shouldn't permanently cost a dimension — the whole point is that
  // every run covers all of them.
  phase('Retry')
  log(`retrying ${failed.length} failed dimension agent(s): ${failed.map((i) => a.dimensions[i]).join(', ')}`)
  const retries = await parallel(failed.map((i) => () =>
    agent(reviewPrompt(a.dimensions[i], i + 1), { label: `retry:${a.dimensions[i]}`, phase: 'Retry', schema: REVIEW_SCHEMA, ...modelOpt })))
  failed.forEach((i, n) => { if (retries[n]) results[i] = retries[n] })
  failed = allIdx().filter((i) => !results[i] || typeof results[i].count !== 'number')
}

const ok = allIdx().filter((i) => !failed.includes(i))
if (!ok.length) throw new Error('deep-review workflow: every dimension agent failed')
const failures = failed.map((i) => a.dimensions[i])
const totalRaw = ok.reduce((n, i) => n + results[i].count, 0)
log(`${ok.length}/${a.dimensions.length} dimension agents returned, ${totalRaw} raw findings${failures.length ? ` (failed: ${failures.join(', ')})` : ''}`)

phase('Synthesize')
const rawList = ok.map((i) => `  ${a.rawDir}/${pad(i + 1)}.json  — ${a.dimensions[i]} (${results[i].count} findings)`).join('\n')
const plan = await agent(
  `You are deduplicating code-review findings produced by ${ok.length} parallel dimension agents. You do not review code, you do not rewrite findings, and you do NOT judge whether a finding is correct — verification happens downstream, outside this workflow.

Read these files:
${rawList}

Each holds {index, dimension, findings: [...]}. A finding's id is its file's index, then "#", then its 0-based position in that array — so the third finding in the file with index 4 is "4#2".

Your only job is to decide which findings are the SAME issue reported by different agents, and to correct any severity that plainly contradicts the taxonomy. Read the "Severity taxonomy" section of ${a.skillDir}/DIMENSIONS.md first.

Two findings are duplicates when they cite the same file and line AND describe the same underlying problem. Two distinct problems on one line are NOT duplicates. The same problem cited at slightly different lines IS a duplicate — pick whichever finding describes it best as the keeper.

Use the Write tool to write ${a.rawDir}/plan.json:

{
  "groups": [
    { "keep": "1#0", "duplicates": ["3#2", "7#1"] },
    { "keep": "2#4", "duplicates": [], "severity": "nit" }
  ]
}

- keep: the id of the clearest finding in the group; its title, description and suggestion become the merged finding's.
- duplicates: ids folded into it. Every id appears at most once across the whole plan.
- severity: OPTIONAL, only when the finding's own severity contradicts the taxonomy (a style preference marked critical, say). Omit it otherwise — severity, confidence and dimension tags are computed downstream.

Only list a finding if it merges with another or needs a severity correction. Anything you omit is kept exactly as written, so an incomplete plan loses nothing. Never list a finding to make it disappear: you cannot drop findings, and out-of-scope files are filtered downstream.`,
  { label: 'synthesize', phase: 'Synthesize', schema: PLAN_SCHEMA, ...modelOpt },
)
if (plan) log(`plan: ${plan.groups} groups, ${plan.duplicatesMerged} duplicates folded in`)
else log('synthesis agent failed — merge.py will pass every raw finding through unmerged')

return {
  rawDir: a.rawDir,
  planWritten: Boolean(plan),
  rawFindings: totalRaw,
  agentFailures: failures,
}
