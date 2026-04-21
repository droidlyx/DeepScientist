---
name: audit-numbers
description: Automatically scheduled in fresh subprocess context after record_main_experiment, submit_paper_bundle, or confirm_baseline when audit_policy.mode=advisory. Verify every quantitative claim in the target artifact against its declared source files.
skill_role: companion
skill_order: 115
---

# Audit Numbers

This skill runs **only** as a post-run advisory audit, scheduled automatically
by the quest's audit policy after one of three state-changing tools recorded
an artifact:

- `artifact.record_main_experiment(...)` → target kind `run`
- `artifact.submit_paper_bundle(...)` → target kind `report` (report_type=paper_bundle)
- `artifact.confirm_baseline(...)` → target kind `baseline`

The defining property is that this skill runs in a **fresh subprocess with no
prior context from the generating skill**. You have never seen the numbers
being audited. Treat every quantitative claim as unverified until you re-derive
it from a specific source file.

Do not invoke this skill manually. The runtime schedules it via the
`guidance_vm.pending_audit` marker on the just-recorded target artifact.

## Non-goals

- You are **not** reviewing narrative quality, novelty, or methodology — that
  is the `review` skill.
- You are **not** re-running experiments. If source files are missing or
  inconclusive, mark those specific claims as `unverifiable` and proceed.
- You do **not** make route decisions. The `audit_report` artifact you produce
  is consumed by the guidance layer, which will either forward the quest to
  the pre-audit recommended skill (on pass) or route back to the remediation
  skill (on fail).

## How to find your target

1. Read the current `guidance_vm`; it contains `pending_audit.target_artifact_id`
   and `pending_audit.target_artifact_kind`. The audit override also copies
   `pre_audit_recommended_skill` into the target artifact's `pending_audit`
   field — you **must** propagate this into the audit_report payload.
2. Read the target artifact JSON under `artifacts/<kind>s/<artifact_id>.json`.
   Everything you need to audit is reachable from that record's `paths`,
   `evidence_paths`, `metrics_summary`, `metric_rows`, `baseline_comparisons`,
   and `details`.

## What counts as a "quantitative claim"

Every concrete number that could be wrong. In practice:

- Metric values: `mean_score`, `std_score`, `best_score`, `delta_vs_baseline`,
  `relative_improvement_pct`, primary metric values.
- Training counts: `total_timesteps`, step budgets quoted in prose.
- Wall-clock time: `train_time_seconds`, minute/hour quotations anywhere in
  the draft.
- Throughput: fps / samples-per-second / steps-per-second quoted anywhere.
- Percentages and ratios that summarize experiment outcomes.
- Cited baseline/reference numbers that the paper leans on.

Dates, version numbers, ArXiv IDs, section numbers, hyperparameter constants
that are static configuration (e.g. `batch_size=32`) and not experimental
outcomes do **not** need to be audited unless they appear as the basis of a
claim.

When a claim is derived (e.g. "72 min" = `train_time_seconds / 60`), encode
the derivation as a `source_field` using the raw field and add a
`derivation` note. The validator will fetch the raw field; you are
responsible for the arithmetic and must show your work in `derivation`.

## Output contract

Emit **exactly one** artifact at the end:

```
artifact.record(
  kind = "report",
  report_type = "audit_report",
  status = "completed",
  audit_target = {
    "artifact_id": "<the target artifact_id from pending_audit>",
    "kind":        "<target_artifact_kind>",
    "trigger_label": "<main_experiment | paper_bundle | baseline_confirm>",
  },
  pre_audit_recommended_skill = "<copy from target artifact's pending_audit.pre_audit_recommended_skill>",
  pre_audit_recommended_action = "<copy from target artifact's pending_audit.pre_audit_recommended_action>",
  fail_recommended_skill = "<copy from target artifact's pending_audit.fail_recommended_skill>",
  audit_verdict = "pass" | "fail",   # the validator may override this
  summary = "<one-paragraph human summary of findings>",
  claims = [
    {
      "quote":        "mean=17.48",                 # verbatim text from the audited artifact/draft
      "stated_value": 17.48,                        # what the artifact claims
      "source_path":  "experiments/results/ppo_symbolic_seed42/json/results.json",
      "source_field": "mean_score",                 # dotted path; null for free-text source
      "derivation":   null,                         # e.g. "divide raw seconds by 60 for minutes"
    },
    ...
  ],
  paths = {"audit_report_md": "<optional human-readable report path>"},
)
```

## How the validator uses your output

Immediately after you call `artifact.record(...)`, a pure-Python validator
re-opens every `source_path`, extracts the `source_field` value, and
compares it to your `stated_value` with a small numerical tolerance
(default 2% relative, 0.05 absolute). If any claim fails the comparison,
the validator **overrides** your `audit_verdict` to `"fail"` and attaches
`validator_diagnostics.mismatches` describing what broke.

This is deliberate: your `pass` cannot be trusted unless it holds up against
an independent re-reading of the source files. Write your claims in a way
that the validator can actually re-verify — that is the only way the audit
has epistemic value.

### Practical implications

- `source_path` must be a real path; the validator will `stat` it.
- `source_field` must be the actual dotted field name inside the JSON, not
  a slug you invented.
- For numbers quoted in prose that are derived (e.g. minutes from seconds),
  choose the **raw** source field and encode the derivation honestly.
- For text-only sources (logs, markdown), set `source_field = null`; the
  validator will do a substring match of `stated_value` against the text.

## Coverage discipline

- Read `paths.run_md`, `paths.result_json`, `paths.draft_path`, and any
  linked evidence ledger to enumerate claims. Do not rely on `summary`
  text alone.
- For paper bundles: audit the quantitative claims inside
  `paths.draft_path` (the full draft markdown), not only the manifest's
  summary.
- If you cannot find the source for a quoted number, mark that claim with
  `source_path = "UNKNOWN"` and set `validator_verdict`-relevant fields
  to force an `io_error`. A missing source is as bad as a wrong value and
  must fail the audit.
- Include every suspicious number; missing a claim is an audit failure
  mode that nothing downstream can catch.

## Tool discipline

- **Do not use native `shell_command` / `command_execution` in this skill.**
- Any file read for auditing must go through `bash_exec(...)` with `cat`,
  `jq`, or `grep`, or through the runtime's file-reading MCP tools where
  available.
- Do not edit the target artifact. Auditing is read-only.

## When to use

- Always, and only, when scheduled by the runtime via the `pending_audit`
  marker on a just-recorded target artifact.

## When not to use

- When a human operator asks for a qualitative review of the paper — that is
  `review`.
- When no state-changing target artifact has just been recorded — this skill
  has nothing to do without a target.
