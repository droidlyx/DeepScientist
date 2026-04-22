---
name: pilot
description: Use before an expensive full run to test cheaply whether the method is obviously broken, saving compute when the mechanism is wrong rather than when the final metric is disappointing.
skill_role: companion
---

# Pilot

Use this skill to spend a small fraction of a planned full-run budget
catching failures that would have made the full run a waste.

A pilot does not predict the final metric. A pilot is usually too short
to tell a good method from a mediocre one; training curves, convergence,
and many real effects only show up at scale. But a pilot **is** long
enough to catch:

- wiring bugs (bad data pipeline, wrong loss, broken eval)
- catastrophic training dynamics (loss explodes, no learning signal,
  reward degrades monotonically, NaNs)
- obviously-wrong baselines (the pilot is worse than a random policy
  or the published number is unreachable from this pipeline)
- mechanism falsification (the causal story behind the method predicts
  a specific short-horizon observable; if that observable is absent at
  pilot scale, the story is wrong and the full run will not fix it)

A pilot is a cheap filter against **broken methods**, not a cheap
forecast of final quality.

## Match signals

Use `pilot` when:

- the planned full run is expensive enough relative to the quest's
  compute budget that a wasted run hurts
- the run cannot be resumed from a checkpoint if it goes wrong midway
- the configuration has changed along an axis the last successful run
  did not cover (new algorithm family, new observation encoding, new
  reward shape, new network scale, etc.)
- the mechanism behind the method predicts an observable short-horizon
  signal that a pilot can actually check

Do not use `pilot` when:

- the pipeline is so unverified that only a smoke test makes sense
  (run the smoke first, then decide if a pilot is the right next step)
- the full run itself is already small enough that a pilot would have
  no meaningful cost advantage
- the pilot budget would be so small that no interesting signal is
  reachable (e.g., an RL pilot at 1% of steps where the agent has not
  exited warm-up — in that case, pick a larger pilot or skip the pilot
  and budget the full run as the experiment)

The agent is expected to judge "expensive enough" and "meaningful
signal reachable" from the current quest context, not to apply a
fixed percentage or wall-clock threshold.

## One-sentence summary

Spend a small, judged fraction of the planned budget to falsify a
broken method cheaply, then route from whether the pilot's causal
signal appeared or did not.

## Control workflow

1. State the causal intuition.
   Write the mechanism by which this change might move the metric. The
   claim does not have to include a predicted number. It does have to
   name the short-horizon observable that would have to be true at
   pilot scale for the mechanism to be alive — for example, "the new
   reward shaping should make episode reward trend upward within
   100k steps" or "the new attention head should reduce loss on this
   slice within one epoch."
2. Choose a pilot scale that can observe that signal.
   Pick a reduced budget small enough to be cheap, large enough to
   reach the named observable. If no budget setting satisfies both,
   document that no meaningful pilot exists and either run the smoke
   test instead or treat the full run as the experiment.
3. Run the pilot with the exact full-run code path.
   Only the budget variable (steps, epochs, data fraction) may differ
   from the planned full run. Any other change invalidates the pilot.
4. Check the causal signal, not the final metric.
   The pilot's question is "is the mechanism alive?" — is the named
   observable present, absent, or inverted. Final metric quality is
   almost never answerable at pilot scale.
5. Route.
   Pick one of `{go, revise, stop}` with a reason tied to the observed
   signal. `go` means no showstopper was found at pilot scale; it does
   not mean the full run will succeed. `revise` means the pilot
   surfaced a specific fixable issue. `stop` means the mechanism is
   falsified or the pipeline is too broken to scale up.

## Constraints

- **Pilot scale is a judgment, not a threshold.** Do not encode a fixed
  percentage or wall-clock minimum — what counts as "cheap enough" and
  "large enough to see the signal" depends on the method. Justify the
  chosen scale in one sentence.
- **Causal-intuition-first.** A pilot with no written mechanism and no
  named short-horizon observable is not a pilot; it is a truncated
  training run. Redo it with the mechanism recorded.
- **Identical variables.** Only the budget-controlling variable may
  change between pilot and full run. Any other change invalidates the
  pilot.
- **No silent expansion.** Extending a pilot mid-run to "see more" is
  a new pilot; record it with a new prediction of what the extension
  should reveal, not as "the same pilot with more data".
- **Stale pilot.** A pilot older than the most recent code or config
  change on the run surface is stale and must be redone.
- **A pass does not promise success.** A `go` verdict says "no
  showstopper was visible at pilot scale" — not "the full run will
  beat baseline." Communicate this honestly to downstream stages.

## AVOID / pitfalls

- Do not treat a smoke-test "it runs" outcome as pilot evidence. Smoke
  measures pipeline; pilot measures whether the mechanism is alive.
- Do not reuse a pilot from a different config. Each config gets its
  own pilot.
- Do not invent a numeric prediction to "comply" with the pilot skill.
  If the honest prediction is qualitative, write the qualitative
  statement and the observable that would refute it.
- Do not wrap the pilot around code the full run will not use. Pilot
  code must be a budget-reduced version of the exact full-run code.
- Do not declare a pilot pass on a short-horizon observable that was
  never reached inside the pilot's budget. If the signal window was
  not entered, the pilot is inconclusive, not a pass.
- Do not record a pilot as `main_experiment`. Pilots are a separate
  evidence tier.

## Validation

Before `pilot` can end, all applicable checks should be true:

- the causal intuition is written in `PLAN.md` or the pilot record
- the named short-horizon observable is explicit
- the pilot was executed at a scale the agent justified in one sentence
- command, config, seed, and environment are durably captured
- the observed evidence on the named observable is recorded
- the route verdict is exactly one of `{go, revise, stop}`, with a
  reason pointing to the observed evidence on the observable
- if the verdict is `go`, the full-run plan references this pilot id
- the pilot artifact is not recorded as `main_experiment`

## Interaction discipline

Follow the shared interaction contract injected by the system prompt.
Pilot updates should be terser than main-run updates; the pilot is
plumbing for the full run, not the evidence. A concise progress update
after the pilot result exists and before the full run launches is
usually enough.

## Required durable outputs

A meaningful pilot pass should leave behind:

- the causal intuition and the named short-horizon observable
- what was observed on that observable
- the route verdict and reason
- command, config, and seed
- a pointer from the planned full run back to this pilot

## Exit criteria

Exit the pilot stage once one of the following is durably true:

- a `go` verdict is recorded and the full run can reference this pilot
- a `revise` verdict is recorded and the next action is either a new
  pilot or a return to `idea` / `decision`
- a `stop` verdict is recorded and the direction is abandoned

A good pilot pass leaves one clear mechanism-level verdict, not a
vague "looked okay" or "not enough steps to tell."
