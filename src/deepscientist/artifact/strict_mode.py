"""Opt-in strict-mode switch for process-discipline enforcement.

When ``startup_contract.strict_mode`` is set to ``{"mode": "on"}`` on a
quest, the runtime turns on a small set of extra enforcement surfaces
that are not part of the default DeepScientist behavior:

- Rationalization / pilot / stale-state reminders are appended to the
  system prompt from ``src/prompts/strict_mode_addendum.md``.
- A regressed main experiment (explicitly marked by the agent with
  ``verdict == "refuted"``) is routed to ``analysis-campaign`` rather
  than to the default ``decision`` fallback.

When ``strict_mode.mode == "off"`` (the default), none of the above
fires and the quest sees zero token or behavioral overhead.

The default is ``off`` so that existing quests and opportunistic /
exploratory workflows are not penalized by the extra prompt budget.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..shared import read_yaml

STRICT_MODE_MODES = {"off", "on"}


def coerce_strict_mode(value: Any, *, field_name: str = "strict_mode") -> dict[str, Any]:
    """Normalize a user-supplied ``strict_mode`` value.

    Accepts either the structured form ``{"mode": "on"}`` or, for
    convenience, a bare string ``"on"`` / ``"off"``. Anything else is
    treated as a malformed input and rejected with ``ValueError`` so the
    MCP form-patch sanitizer can surface the error.
    """

    if isinstance(value, str):
        mode = value.strip().lower()
        if mode not in STRICT_MODE_MODES:
            raise ValueError(
                f"`{field_name}` must be one of {sorted(STRICT_MODE_MODES)!r}."
            )
        return {"mode": mode}
    if not isinstance(value, dict):
        raise ValueError(
            f"`{field_name}` must be an object with a `mode` key or a plain string."
        )
    mode = str(value.get("mode") or "").strip().lower()
    if mode not in STRICT_MODE_MODES:
        raise ValueError(
            f"`{field_name}.mode` must be one of {sorted(STRICT_MODE_MODES)!r}."
        )
    return {"mode": mode}


def read_strict_mode(quest_root: Path) -> dict[str, Any]:
    """Read a normalized ``strict_mode`` dict from the quest's startup_contract.

    Returns ``{"mode": "off"}`` whenever the field is absent, malformed,
    or explicitly disabled. This is the authoritative gate for the
    strict-mode behaviors; callers should not try to re-derive it from
    other quest fields.
    """

    try:
        quest_yaml = read_yaml(quest_root / "quest.yaml", {})
    except Exception:
        quest_yaml = {}
    if not isinstance(quest_yaml, dict):
        return {"mode": "off"}
    startup_contract = quest_yaml.get("startup_contract")
    if not isinstance(startup_contract, dict):
        return {"mode": "off"}
    raw = startup_contract.get("strict_mode")
    if isinstance(raw, str):
        mode = raw.strip().lower()
        if mode not in STRICT_MODE_MODES:
            mode = "off"
        return {"mode": mode}
    if not isinstance(raw, dict):
        return {"mode": "off"}
    mode = str(raw.get("mode") or "off").strip().lower()
    if mode not in STRICT_MODE_MODES:
        mode = "off"
    return {"mode": mode}


def is_strict_mode_on(quest_root: Path | None) -> bool:
    """Convenience: ``True`` iff the quest's strict_mode is explicitly on."""

    if quest_root is None:
        return False
    try:
        return read_strict_mode(quest_root).get("mode") == "on"
    except Exception:
        return False


def _is_agent_flagged_regression(record: dict[str, Any]) -> bool:
    """Return True only when the agent itself flagged the run as regressed.

    We deliberately do *not* auto-classify regressions from numeric deltas or
    from ``beats_baseline == False``: those are often noise, seed variance, or
    intentionally-negative ablations. Strict mode's regression routing should
    only fire when the agent explicitly recorded ``verdict == "refuted"``, so
    the re-route is a follow-through of the agent's own judgment rather than
    a second-guess of a numeric signal.
    """

    kind = str(record.get("kind") or "").strip().lower()
    run_kind = str(record.get("run_kind") or record.get("stage") or "").strip().lower()
    if kind != "run" or run_kind != "main_experiment":
        return False
    verdict = str(record.get("verdict") or "").strip().lower()
    return verdict == "refuted"


def _regression_guidance_override(
    record: dict[str, Any],
    existing_guidance: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a guidance_vm that routes a refuted main run to analysis-campaign."""

    artifact_id = str(
        record.get("artifact_id") or record.get("id") or ""
    ).strip() or None
    related_paths: list[str] = []
    paths = record.get("paths") if isinstance(record.get("paths"), dict) else {}
    for value in paths.values():
        if value is not None:
            related_paths.append(str(value))

    previous_skill = None
    previous_action = None
    if isinstance(existing_guidance, dict):
        previous_skill = existing_guidance.get("recommended_skill")
        previous_action = existing_guidance.get("recommended_action")

    return {
        "schema_version": 1,
        "current_anchor": "experiment",
        "recommended_skill": "analysis-campaign",
        "recommended_action": "launch_analysis_campaign",
        "summary": (
            "Main run was marked refuted. Strict mode routes through "
            "`analysis-campaign` to isolate the cause before the next full run."
        ),
        "why_now": (
            "The agent has already judged the main run as refuted. Strict mode "
            "follows through on that judgment by scheduling one root-cause pass "
            "before any further full-budget run is launched."
        ),
        "complete_when": [
            "A campaign report explains the refutation and names the most likely cause.",
            "A `decision` artifact records the chosen next route: revise, retry-with-change, branch, or stop.",
        ],
        "alternative_routes": [
            {
                "action": "continue",
                "label": "Record a decision first",
                "when": (
                    "The cause is already understood and analysis would not change the verdict."
                ),
                "tradeoff": (
                    "Skips extra analysis runs, but concedes the standard root-cause pass."
                ),
            },
            {
                "action": "branch",
                "label": "Branch a different direction",
                "when": (
                    "The current route is clearly exhausted and analysis cannot change that."
                ),
                "tradeoff": (
                    "Moves on faster, but leaves the refutation unexplained in the durable record."
                ),
            },
        ],
        "suggested_artifact_calls": [
            {
                "name": "artifact.create_analysis_campaign(...)",
                "purpose": "Spawn the structured analysis branches needed to explain the refutation.",
            },
            {
                "name": "artifact.record(kind='report', ...)",
                "purpose": "Synthesize the refutation diagnosis before the follow-up decision.",
            },
            {
                "name": "artifact.record(kind='decision', ...)",
                "purpose": "Record the post-refutation route (revise, retry-with-change, branch, stop).",
            },
        ],
        "source_artifact_kind": str(record.get("kind") or "run"),
        "source_artifact_id": artifact_id,
        "related_paths": related_paths,
        "previous_recommended_skill": previous_skill,
        "previous_recommended_action": previous_action,
        "strict_mode": True,
    }


def maybe_inject_strict_mode_regression_routing(
    quest_root: Path,
    record: dict[str, Any],
    guidance_vm: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Post-process guidance_vm to route refuted main runs to analysis-campaign.

    Only fires when all three hold:
      - the quest's ``strict_mode.mode`` is ``"on"``
      - the just-built record is a ``main_experiment`` run
      - the agent itself recorded ``verdict == "refuted"`` on that run

    Otherwise the existing ``guidance_vm`` is returned unchanged. This helper
    never auto-classifies regressions from numeric deltas.
    """

    if not is_strict_mode_on(quest_root):
        return guidance_vm
    if not _is_agent_flagged_regression(record):
        return guidance_vm
    return _regression_guidance_override(record, guidance_vm)
