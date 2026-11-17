// Reference attempt at a suite, as a Claude Code Workflow script: per unit a
// solver agent (sets the unit up, works, never sees a validator) and then a
// separate grader agent (runs `reference_attempt.py grade`, which appends the
// ledger row). Variants of one task run sequentially: they share /tmp fixtures.
// From the 2026-11-16 Claude Opus 5.5 v5 reference (71/71; 68/71 inside the
// strict 1x wall; integrity scan clean).
//
// args: the args.json evals/reference/prepare.py writes:
//   { groups: {task: [unit ids]}, solver: {unit: prompt}, grader: {unit: prompt}, ... }
// After it finishes, run evals/reference/integrity_scan.py on the run's
// subagent transcripts. See evals/reference/README.md.

export const meta = {
  name: 'eval-reference-attempt',
  description: 'Reference model attempt at every suite unit: per-unit solver agent, separate grader, the solver never sees validator output',
  phases: [{ title: 'Solve+Grade', detail: 'per task group, variants sequential (shared fixtures)' }],
}

const GRADE = { type: 'object', properties: {
  unit: { type: 'string' }, passed: { type: 'boolean' }, indeterminate: { type: 'boolean' },
  elapsed_s: { type: 'number' }, over_budget: { type: 'boolean' }, raw: { type: 'string' } },
  required: ['unit', 'passed', 'raw'] }

const groups = args.groups || {}
const solver = args.solver || {}
const grader = args.grader || {}

const out = await pipeline(
  Object.entries(groups),
  async ([task, units]) => {
    const rows = []
    for (const u of units) {
      const solved = await agent(solver[u], { label: `solve:${u}`, phase: 'Solve+Grade' })
      const g = await agent(grader[u], { label: `grade:${u}`, phase: 'Solve+Grade', schema: GRADE, effort: 'low' })
      rows.push({ unit: u, solver_summary: (solved || '').slice(0, 300), grade: g })
      log(`${u}: ${g ? (g.passed ? 'PASS' : 'FAIL') : 'no grade'}`)
    }
    return rows
  },
)
return out.filter(Boolean).flat()
