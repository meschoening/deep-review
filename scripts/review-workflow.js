// deep-review orchestration. Invoked by SKILL.md via Workflow({scriptPath, args}).
//
// args (all required, assembled by the dispatcher per SKILL.md):
//   skillDir     absolute path to the deep-review skill directory
//   repoRoot     absolute path to the repo under review
//   source       human source string from scope.py (e.g. "branch diff vs origin/main")
//   scope        one-line scope summary (e.g. "1247 LOC, 14 files")
//   timestamp    "YYYY-MM-DD HH:MM:SS" local time (Date is unavailable in workflow scripts)
//   dimensions   array of dimension names, grepped from DIMENSIONS.md headings
//   files        array of repo-relative paths in scope (scope.py FILES section)
//   diffCmd      command that shows the change under review; "" for non-diff targets
//   findingsPath absolute path where the synthesis agent writes the findings JSON
//   model        OPTIONAL, smoke-testing only: tier override (e.g. "sonnet") to exercise
//                the pipeline cheaply. Real reviews omit it — agents inherit the session model.
//
// No model overrides in real runs: every agent inherits the session model.
// Severity/confidence taxonomy and dimension briefs live ONLY in DIMENSIONS.md;
// agents read that file themselves so nothing is duplicated here.

export const meta = {
  name: 'deep-review',
  description: 'Fixed-dimension review agents in parallel, then one synthesis agent merges findings mechanically',
  phases: [
    { title: 'Review', detail: 'one agent per dimension, each over the full scope' },
    { title: 'Synthesize', detail: 'dedupe + severity sanity-check, write findings JSON' },
  ],
}

const FINDINGS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['dimension', 'findings'],
  properties: {
    dimension: { type: 'string', description: 'the dimension you were assigned' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['file', 'line', 'severity', 'confidence', 'dimension', 'description', 'suggestion'],
        properties: {
          file: { type: 'string', description: 'repo-relative path' },
          line: { type: 'integer', minimum: 0, description: 'line in the current version of the file; 0 for a file-level finding' },
          severity: { enum: ['critical', 'high', 'medium', 'low', 'nit'] },
          confidence: { enum: ['high', 'medium', 'low'], description: 'how sure you are the finding is real' },
          dimension: { type: 'string', description: 'dimension this finding belongs to — yours, unless reporting a cross-dimension find' },
          description: { type: 'string', description: '1-2 sentences, plain prose, no markup' },
          suggestion: { type: 'string', description: 'concrete fix, plain prose, no markup' },
        },
      },
    },
  },
}

const SYNTH_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['total', 'merged', 'droppedOutOfScope'],
  properties: {
    total: { type: 'integer', description: 'findings written to the JSON file' },
    merged: { type: 'integer', description: 'raw findings merged away as duplicates' },
    droppedOutOfScope: { type: 'integer', description: 'findings dropped because their file was not in scope' },
  },
}

let a = args
if (typeof a === 'string') {
  // Dispatchers sometimes pass args JSON-encoded; tolerate both forms.
  try { a = JSON.parse(a) } catch (e) { throw new Error('deep-review workflow: args arrived as an unparseable string') }
}
const missing = ['skillDir', 'repoRoot', 'source', 'scope', 'timestamp', 'dimensions', 'files', 'findingsPath']
  .filter((k) => a == null || a[k] == null || a[k] === '')
if (missing.length) throw new Error(`deep-review workflow: missing args: ${missing.join(', ')}`)
if (!Array.isArray(a.dimensions) || !a.dimensions.length) throw new Error('deep-review workflow: args.dimensions must be a non-empty array')
if (!Array.isArray(a.files) || !a.files.length) throw new Error('deep-review workflow: args.files must be a non-empty array')

const modelOpt = a.model ? { model: a.model } : {}
const fileList = a.files.join('\n')
const diffNote = a.diffCmd
  ? `This is a diff-based review. See what changed with:\n  ${a.diffCmd}\nFindings must target lines that exist in the current version of each file. Read surrounding context — bugs often live just outside the diff.`
  : 'This is a full review of the files in scope, not a diff.'

phase('Review')
const reviews = await parallel(a.dimensions.map((dim) => () =>
  agent(
    `You are a code reviewer focused on ONE dimension: ${dim}.

Working directory: ${a.repoRoot}
Scope: ${a.scope}. Source: ${a.source}.

Files in scope:
${fileList}

${diffNote}

First read ${a.skillDir}/DIMENSIONS.md. Its "Severity taxonomy" section defines the severity levels and the confidence field — apply them exactly. Your brief is the numbered section "${dim}": apply its checklist to every file in scope.

Rules:
- Severity rates the impact assuming the finding is real; confidence rates how sure you are it is real. Never downgrade severity to hedge — hedge with confidence.
- If you find a real issue outside your dimension, report it anyway and set its dimension field to the dimension it belongs to (use the exact names from DIMENSIONS.md). Do not assume a parallel agent saw it.
- Be specific: file, line, what is wrong, why it matters, and a concrete fix.
- description and suggestion are plain prose — no markdown, no HTML, no backticks. Refer to symbols and paths as bare text; everything is escaped and rendered verbatim downstream.
- Read-only review: do not modify any files or run state-changing commands.
- If there is genuinely nothing to flag, return an empty findings array. Do not invent findings to fill quota.`,
    { label: `review:${dim}`, phase: 'Review', schema: FINDINGS_SCHEMA, ...modelOpt },
  )))

const ok = []
const failures = []
reviews.forEach((r, i) => {
  if (r && Array.isArray(r.findings)) ok.push({ dimension: a.dimensions[i], findings: r.findings })
  else failures.push(a.dimensions[i])
})
if (!ok.length) throw new Error('deep-review workflow: every dimension agent failed')
const totalRaw = ok.reduce((n, r) => n + r.findings.length, 0)
log(`${ok.length}/${a.dimensions.length} dimension agents returned, ${totalRaw} raw findings${failures.length ? ` (failed: ${failures.join(', ')})` : ''}`)

phase('Synthesize')
const synth = await agent(
  `You are merging code-review findings from ${ok.length} parallel dimension agents into one findings file. You do not review code yourself, and you do NOT judge whether findings are correct — verification happens downstream, outside this workflow.

Raw findings, grouped by reporting agent:

${JSON.stringify({ agents: ok }, null, 1)}

Files in scope (a finding about any other file is out of scope):
${fileList}

Read the "Severity taxonomy" section of ${a.skillDir}/DIMENSIONS.md before applying rule 2.

Merge rules — mechanical only:
1. Dedupe: findings that reference the same file and line and describe the same underlying issue (even from different agents) merge into ONE finding — union their dimension tags, keep the clearest description and suggestion, and keep the higher severity and higher confidence. Findings on the same line that flag genuinely distinct issues stay separate.
2. Severity sanity-check: if a finding's severity plainly contradicts the taxonomy (e.g. a style preference marked critical), adjust it to the matching level. Do not otherwise second-guess severities.
3. Scope check: drop findings whose file is not in the scope list above, and count how many you dropped.
4. No judgment drops: never drop a finding because you think it is wrong, intentional, or unimportant.

Then use the Write tool to write ${a.findingsPath} with exactly this JSON shape:

{
  "meta": {
    "source": ${JSON.stringify(a.source)},
    "generated": ${JSON.stringify(a.timestamp)},
    "scope": ${JSON.stringify(a.scope)},
    "dimensions": ${JSON.stringify(a.dimensions)},
    "failures": ${JSON.stringify(failures)}
  },
  "findings": [
    { "file": "...", "line": 123, "severity": "...", "confidence": "...", "dimensions": ["..."], "description": "...", "suggestion": "..." }
  ]
}

Copy the meta block verbatim as given. Every finding carries all seven fields, with dimensions as an array of one or more dimension names. Order does not matter — the renderer sorts. After writing the file, report your counts.`,
  { label: 'synthesize', phase: 'Synthesize', schema: SYNTH_SCHEMA, ...modelOpt },
)
if (!synth) throw new Error('deep-review workflow: synthesis agent failed')
log(`synthesis: ${synth.total} findings written (${synth.merged} merged as duplicates, ${synth.droppedOutOfScope} dropped as out-of-scope)`)

return { findingsPath: a.findingsPath, totalFindings: synth.total, agentFailures: failures }
