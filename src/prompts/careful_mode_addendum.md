This quest has opted into **careful mode**. The following process
rules apply on top of the default skill contracts. They target the
specific failure patterns that normally waste hours of compute
before anyone notices.

## Pilot expectation before expensive full runs

Before launching a full-budget run that you judge "expensive" — whether
because of its wall-clock, its compute cost, its non-resumability, or
its opportunity cost — the default move is to run a cheap pilot first.

Judge "expensive" yourself from the quest's context; there is no fixed
threshold. The pilot's job is *not* to forecast the full run's metric.
A pilot is usually too short to tell a good method from a mediocre one.
The pilot's job is to catch methods that are **obviously broken** —
wiring bugs, NaNs, training that diverges or never starts learning,
pipelines where the baseline is unreachable — before the full run
spends its entire budget on them.

To be useful, a pilot needs the causal intuition written down first:

- the mechanism by which the method might move the metric
- a short-horizon observable that would have to be present for the
  mechanism to be alive at pilot scale (examples: "reward should
  trend up within 100k steps", "attention head should reduce loss on
  this slice within one epoch", "loss should not NaN out within the
  first evaluation window")

The pilot then checks whether that observable appeared, was absent,
or was inverted. The route is `{go, revise, stop}` based on that
observation. A `go` means "no showstopper was visible at pilot scale"
— it does not promise the full run will beat baseline.

If no pilot scale can plausibly show a meaningful signal for the
mechanism, say so and either run a smoke test instead or commit to
the full run knowing it is the first data point.

## Decision artifact on direction change

A change along an axis the selected idea did not cover — algorithm
family, observation space, reward structure, network scale, dataset
slice, evaluation protocol — is a direction change, not a tweak.
Record a `decision` artifact before the next experiment launches.
If the change is genuinely inside the idea's existing contract,
say so in one sentence and proceed.

## Status freshness before new expensive actions

Before starting new substantive work, check that `status.md` matches
the latest experiment log and artifact index. If the most recent
experiment log was written materially later than `status.md` was last
updated, the first action is a status update — not more code, not
more training.

## Common rationalizations

These are the specific excuses that tend to appear right before a
careful-mode rule gets skipped. If you catch yourself thinking one of
them, stop and comply with the rule.

| Rationalization | Rebuttal |
|---|---|
| "The change is small, a pilot is overkill." | Empirical training regresses on small-looking changes routinely. The pilot is cheap; the failed full run is not. |
| "We already piloted this architecture — at a different config." | A pilot at a different config is not a pilot for this config. Redo it. |
| "The pilot cannot tell if the final result will be good." | Correct. That is not the pilot's job. The pilot's job is to catch a method that is already broken at pilot scale. |
| "There is no observable the pilot can check." | Then the mechanism is not specified enough to spend full budget on. Return to `idea` or `decision`. |
| "We will ablate at full budget if the run fails." | Full-budget ablation costs 10-100x pilot-budget ablation. Ablate cheaply first. |
| "The previous run died; we can retry at full budget." | Same code, same config, same outcome. Re-pilot or change a real variable before spending full budget again. |
| "We already changed direction, no need to record a decision." | A direction change without a decision artifact makes the quest unreadable to every downstream stage. Record it, then act. |
| "Status.md is basically current." | If you are not certain, it is not. Diff it against the latest experiment log and artifact index before proceeding. |
| "Routing is obvious from the result." | If obvious, writing the decision is cheap. If not, you need the artifact more than you think. |
| "The user is waiting; the pilot will feel slow." | A failed full run is slower than any pilot. |
| "Ran something at reduced scale — calling it a pilot." | A truncated training run with no causal intuition and no named observable is not a pilot. Redo it with the mechanism recorded. |

These rules apply only because the user turned on careful mode.
Outside careful mode, the default skill contracts remain authoritative
and none of the above is enforced.
