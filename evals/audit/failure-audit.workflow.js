// Failure audit of one eval run, as a Claude Code Workflow script.
// One auditor per failed task group, then one skeptic per group arguing the
// opposite direction. Generalized from the 2026-11-16 GLM-5.3-Flash v5 L0
// audit (45 failures: 43 TRUE_FAIL, 2 harness).
//
// args: the args.json evals/audit/prepare.py writes:
//   { groups: {task: [unit ids]},
//     auditor: {task: prompt}, skeptic: {task: prompt with {{AUDIT_JSON}}} }
// Returns [{task, audit, challenge}]; feed it to evals/audit/verdicts.py.
// Every path, model name and toolchain line lives in the prompts, so this
// script never changes between models. See evals/audit/README.md.

export const meta = {
  name: 'eval-failure-audit',
  description: 'Audit every failed unit of an eval run (true fail vs harness / prompt / validator / infra), then challenge each verdict adversarially',
  phases: [
    { title: 'Audit', detail: 'one auditor per failed task group' },
    { title: 'Challenge', detail: 'one skeptic per group, opposite direction' },
  ],
}

const VERDICTS = ['TRUE_FAIL', 'HARNESS', 'PROMPT', 'VALIDATOR_BUG', 'INFRA', 'UNCLEAR']

const VERDICT = {
  type: 'object',
  properties: {
    units: { type: 'array', items: { type: 'object', properties: {
      unit: { type: 'string' },
      verdict: { type: 'string', enum: VERDICTS },
      confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
      root_cause: { type: 'string' },
      evidence: { type: 'string' },
      replay: { type: 'string', description: 'result of replaying the reconstructed final solution through replay.py, or why not possible' },
      proposed_fix: { type: 'string', description: 'for non-TRUE_FAIL: exact change to task text / validator / harness; empty for TRUE_FAIL' },
    }, required: ['unit', 'verdict', 'confidence', 'root_cause', 'evidence', 'replay', 'proposed_fix'] } },
    systemic_note: { type: 'string' },
  },
  required: ['units', 'systemic_note'],
}

const CHALLENGE = {
  type: 'object',
  properties: {
    units: { type: 'array', items: { type: 'object', properties: {
      unit: { type: 'string' },
      agree: { type: 'boolean' },
      final_verdict: { type: 'string', enum: VERDICTS },
      reasoning: { type: 'string' },
    }, required: ['unit', 'agree', 'final_verdict', 'reasoning'] } },
  },
  required: ['units'],
}

const groups = args.groups || {}
const auditorPrompt = args.auditor || {}
const skepticPrompt = args.skeptic || {}

const results = await pipeline(
  Object.entries(groups),
  ([task]) => agent(auditorPrompt[task], { label: `audit:${task}`, phase: 'Audit', schema: VERDICT }),
  (audit, [task]) => audit && agent(
    skepticPrompt[task].split('{{AUDIT_JSON}}').join(JSON.stringify(audit, null, 1)),
    { label: `challenge:${task}`, phase: 'Challenge', schema: CHALLENGE },
  ).then(challenge => ({ task, audit, challenge })),
)
return results.filter(Boolean)
