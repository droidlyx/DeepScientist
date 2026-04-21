"""Post-run advisory audit policy.

When the ``startup_contract.audit_policy`` for a quest is enabled, the
state-changing artifact tools ``record_main_experiment``, ``submit_paper_bundle``
and ``confirm_baseline`` are intercepted after their artifact is built:
their ``guidance_vm.recommended_skill`` is rewritten to the ``audit-numbers``
companion skill, so a fresh subprocess runs a numeric audit against the
declared source files before the original next route is taken.

The audit skill emits an ``audit_report`` (report_type) artifact carrying a
structured ``claims`` list; this module's ``validate_audit_report_payload``
re-checks every claim against the cited source file so that the audit
subprocess cannot self-confirm its own hallucinations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..shared import read_yaml

AUDIT_POLICY_MODES = {"off", "advisory"}
AUDIT_POLICY_RUNNERS: tuple[str, ...] = (
    "inherit",
    "codex",
    "claude",
    "opencode",
    "kimi",
)

# Tool name -> (target kind + record-match predicate) for identifying when
# a given artifact payload is the product of one of the three triggers.
AUDIT_TRIGGERS: dict[str, dict[str, Any]] = {
    "record_main_experiment": {
        "kind": "run",
        "match": lambda r: str(r.get("run_kind") or "").strip() == "main_experiment",
        "label": "main_experiment",
        "fail_recommended_skill": "decision",
    },
    "submit_paper_bundle": {
        "kind": "report",
        "match": lambda r: str(r.get("report_type") or "").strip() == "paper_bundle",
        "label": "paper_bundle",
        "fail_recommended_skill": "write",
    },
    "confirm_baseline": {
        "kind": "baseline",
        "match": lambda r: (
            str(r.get("flow_type") or "").strip() == "baseline_gate"
            and str(r.get("protocol_step") or "").strip() == "confirm"
        ),
        "label": "baseline_confirm",
        "fail_recommended_skill": "baseline",
    },
}

DEFAULT_AUDIT_TRIGGERS: tuple[str, ...] = (
    "submit_paper_bundle",
    "record_main_experiment",
    "confirm_baseline",
)


def coerce_audit_policy(value: Any, *, field_name: str = "audit_policy") -> dict[str, Any]:
    """Validate + normalize a user-supplied ``audit_policy`` dict.

    Raises ``ValueError`` on malformed input. Used both by the MCP form-patch
    sanitizer and by any code path that accepts an audit_policy from untrusted
    input (e.g. the quest creation REST endpoint, if it ever starts validating).
    """
    if not isinstance(value, dict):
        raise ValueError(f"`{field_name}` must be an object with at least a `mode` key.")
    mode = str(value.get("mode") or "").strip().lower()
    if mode not in AUDIT_POLICY_MODES:
        raise ValueError(
            f"`{field_name}.mode` must be one of {sorted(AUDIT_POLICY_MODES)!r}."
        )
    out: dict[str, Any] = {"mode": mode}
    if mode == "off":
        return out
    triggers_raw = value.get("triggers")
    if triggers_raw is not None:
        if not isinstance(triggers_raw, list):
            raise ValueError(f"`{field_name}.triggers` must be a list when provided.")
        triggers = [
            str(item).strip()
            for item in triggers_raw
            if str(item).strip() in AUDIT_TRIGGERS
        ]
        if triggers:
            out["triggers"] = triggers
    runner_raw = value.get("runner")
    if runner_raw is not None and str(runner_raw).strip():
        runner = str(runner_raw).strip().lower()
        if runner not in AUDIT_POLICY_RUNNERS:
            raise ValueError(
                f"`{field_name}.runner` must be one of {list(AUDIT_POLICY_RUNNERS)!r} when provided."
            )
        out["runner"] = runner
    return out


def read_audit_policy(quest_root: Path) -> dict[str, Any]:
    """Read a normalized ``audit_policy`` dict from the quest's startup_contract.

    Returns ``{"mode": "off", ...}`` whenever the policy is absent, malformed,
    or explicitly disabled. Unknown triggers are silently dropped.
    """
    try:
        quest_yaml = read_yaml(quest_root / "quest.yaml", {})
    except Exception:
        quest_yaml = {}
    default = {"mode": "off", "triggers": list(DEFAULT_AUDIT_TRIGGERS), "runner": "inherit"}
    if not isinstance(quest_yaml, dict):
        return default
    startup_contract = quest_yaml.get("startup_contract")
    if not isinstance(startup_contract, dict):
        return default
    raw = startup_contract.get("audit_policy")
    if not isinstance(raw, dict):
        return default
    mode = str(raw.get("mode") or "off").strip().lower()
    if mode not in AUDIT_POLICY_MODES:
        mode = "off"
    triggers_raw = raw.get("triggers")
    if isinstance(triggers_raw, list) and triggers_raw:
        triggers = [str(t).strip() for t in triggers_raw if str(t).strip() in AUDIT_TRIGGERS]
        if not triggers:
            triggers = list(DEFAULT_AUDIT_TRIGGERS)
    else:
        triggers = list(DEFAULT_AUDIT_TRIGGERS)
    runner = str(raw.get("runner") or "inherit").strip().lower() or "inherit"
    return {"mode": mode, "triggers": triggers, "runner": runner}


def _match_trigger(record: dict[str, Any], triggers: list[str]) -> dict[str, Any] | None:
    kind = str(record.get("kind") or "").strip().lower()
    for tool_name in triggers:
        spec = AUDIT_TRIGGERS.get(tool_name)
        if not spec or spec["kind"] != kind:
            continue
        try:
            if spec["match"](record):
                return {**spec, "tool_name": tool_name}
        except Exception:
            continue
    return None


def is_audit_report(record: dict[str, Any]) -> bool:
    return (
        str(record.get("kind") or "").strip().lower() == "report"
        and str(record.get("report_type") or "").strip().lower() == "audit_report"
    )


def maybe_inject_audit_override(
    quest_root: Path,
    record: dict[str, Any],
    guidance_vm: dict[str, Any],
) -> dict[str, Any]:
    """Rewrite ``guidance_vm`` to route to the ``audit-numbers`` companion skill.

    Returns the (possibly replaced) ``guidance_vm``. When the policy does not
    match, the original ``guidance_vm`` is returned untouched. ``record`` is
    mutated in place to carry a ``pending_audit`` marker so that the audit
    skill can discover its target by reading only the target artifact.

    Audit-report artifacts themselves are never re-audited (loop guard).
    """
    if is_audit_report(record):
        return guidance_vm
    policy = read_audit_policy(quest_root)
    if policy.get("mode") != "advisory":
        return guidance_vm
    spec = _match_trigger(record, policy.get("triggers") or [])
    if not spec:
        return guidance_vm
    original = dict(guidance_vm) if isinstance(guidance_vm, dict) else {}
    pre_skill = str(original.get("recommended_skill") or "").strip() or None
    pre_action = str(original.get("recommended_action") or "").strip() or None
    target_artifact_id = str(record.get("artifact_id") or record.get("id") or "").strip() or None
    target_kind = str(record.get("kind") or "").strip().lower() or None
    record["pending_audit"] = {
        "trigger_label": spec["label"],
        "trigger_tool": spec["tool_name"],
        "policy_mode": policy.get("mode"),
        "pre_audit_recommended_skill": pre_skill,
        "pre_audit_recommended_action": pre_action,
        "audit_runner": policy.get("runner", "inherit"),
        "fail_recommended_skill": spec.get("fail_recommended_skill"),
    }
    overridden = dict(original)
    overridden["recommended_skill"] = "audit-numbers"
    overridden["recommended_action"] = "audit"
    overridden["summary"] = (
        f"[audit advisory] A `{spec['label']}` artifact (`{target_artifact_id or 'unknown'}`) "
        "was just recorded. Before continuing to "
        f"`{pre_skill or 'the next skill'}`, the `audit-numbers` companion skill must "
        "run in a fresh subprocess and verify every quantitative claim in this "
        "artifact against its declared source files."
    )
    overridden["why_now"] = (
        "The quest has audit_policy.mode=advisory. Fresh-context audits are scheduled "
        "automatically after state-changing tools to catch numeric hallucinations that "
        "the generating skill cannot self-detect."
    )
    overridden["pre_audit_guidance"] = {
        "recommended_skill": pre_skill,
        "recommended_action": pre_action,
        "summary": original.get("summary"),
        "why_now": original.get("why_now"),
        "complete_when": original.get("complete_when") or [],
        "suggested_artifact_calls": original.get("suggested_artifact_calls") or [],
        "alternative_routes": original.get("alternative_routes") or [],
    }
    overridden["pending_audit"] = {
        "target_artifact_id": target_artifact_id,
        "target_artifact_kind": target_kind,
        "trigger_label": spec["label"],
        "trigger_tool": spec["tool_name"],
        "fail_recommended_skill": spec.get("fail_recommended_skill"),
    }
    overridden["complete_when"] = [
        "An audit_report artifact is produced referencing the target artifact.",
        "Every quantitative claim in the audit_report specifies source_path and source_field.",
        "If the audit verdict is pass, the original recommended skill runs next.",
        "If the audit verdict is fail, the quest routes back to correct the underlying artifact.",
    ]
    overridden["suggested_artifact_calls"] = [
        {
            "name": "artifact.record(kind='report', report_type='audit_report', ...)",
            "purpose": "Persist the audit findings with structured claims and pre_audit_recommended_skill.",
        }
    ]
    overridden["alternative_routes"] = []
    overridden["stage_status"] = "pending_audit"
    return overridden


def _resolve_source_value(
    quest_root: Path, source_path: str, source_field: str | None
) -> Any:
    """Resolve a claim's cited location (path + optional dotted field).

    JSON files support a dotted-path lookup into nested dicts or list indices.
    Non-JSON files are returned as text; the caller decides between substring
    matching or a more specific check.
    """
    raw_path = source_path.strip()
    if not raw_path:
        raise ValueError("empty source_path")
    path = Path(raw_path)
    if not path.is_absolute():
        path = (quest_root / raw_path).resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"source_path not found: {source_path}")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"invalid JSON at {source_path}: {exc}") from exc
        if not source_field:
            return data
        value: Any = data
        for segment in source_field.split("."):
            segment = segment.strip()
            if not segment:
                continue
            if isinstance(value, dict):
                value = value.get(segment)
            elif isinstance(value, list):
                try:
                    value = value[int(segment)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
            if value is None:
                return None
        return value
    return path.read_text(encoding="utf-8", errors="replace")


def _values_match(stated: Any, actual: Any, *, rel_tol: float = 0.005, abs_tol: float = 0.01) -> bool:
    if stated is None and actual is None:
        return True
    if stated is None or actual is None:
        return False
    if isinstance(stated, bool) or isinstance(actual, bool):
        return stated == actual
    if isinstance(stated, (int, float)) and isinstance(actual, (int, float)):
        if stated == actual:
            return True
        return abs(float(stated) - float(actual)) <= max(abs_tol, rel_tol * abs(float(stated) or 1.0))
    try:
        sf = float(stated)
        af = float(actual)
        if sf == af:
            return True
        return abs(sf - af) <= max(abs_tol, rel_tol * abs(sf or 1.0))
    except (TypeError, ValueError):
        pass
    return str(stated).strip() == str(actual).strip()


def validate_audit_report_payload(
    quest_root: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    """Objectively re-check every claim in an audit_report payload.

    Mutates ``payload`` in place: each claim gets a ``validator_verdict``,
    and the top-level ``audit_verdict`` is forced to ``"fail"`` if any
    mismatch or IO error is found. Returns a diagnostics summary.

    Claim shape required by this validator::

        {
          "quote": str,              # the text being audited (optional)
          "stated_value": Any,       # the value asserted by the audit agent
          "source_path": str,        # file path, absolute or relative to quest_root
          "source_field": str|null,  # dotted path into JSON, or None for text
        }
    """
    claims = payload.get("claims")
    if not isinstance(claims, list):
        claims = []
    mismatches: list[dict[str, Any]] = []
    io_errors: list[dict[str, Any]] = []
    checked = 0
    for idx, raw in enumerate(claims):
        if not isinstance(raw, dict):
            continue
        source_path = str(raw.get("source_path") or "").strip()
        source_field = str(raw.get("source_field") or "").strip() or None
        stated_value = raw.get("stated_value")
        if not source_path:
            raw["validator_verdict"] = "unverifiable"
            io_errors.append(
                {
                    "claim_index": idx,
                    "quote": raw.get("quote"),
                    "reason": "missing source_path",
                }
            )
            continue
        if stated_value is None:
            raw["validator_verdict"] = "unverifiable"
            io_errors.append(
                {
                    "claim_index": idx,
                    "quote": raw.get("quote"),
                    "source_path": source_path,
                    "reason": "missing stated_value",
                }
            )
            continue
        checked += 1
        try:
            actual = _resolve_source_value(quest_root, source_path, source_field)
        except Exception as exc:
            raw["validator_verdict"] = "io_error"
            io_errors.append(
                {
                    "claim_index": idx,
                    "quote": raw.get("quote"),
                    "source_path": source_path,
                    "source_field": source_field,
                    "reason": str(exc),
                }
            )
            continue
        if source_field is None and isinstance(actual, str):
            if str(stated_value).strip() and str(stated_value).strip() in actual:
                raw["validator_verdict"] = "match"
            else:
                raw["validator_verdict"] = "mismatch"
                mismatches.append(
                    {
                        "claim_index": idx,
                        "quote": raw.get("quote"),
                        "source_path": source_path,
                        "source_field": None,
                        "stated_value": stated_value,
                        "actual_value": None,
                        "reason": "stated_value not found in source text",
                    }
                )
            continue
        if _values_match(stated_value, actual):
            raw["validator_verdict"] = "match"
        else:
            raw["validator_verdict"] = "mismatch"
            mismatches.append(
                {
                    "claim_index": idx,
                    "quote": raw.get("quote"),
                    "source_path": source_path,
                    "source_field": source_field,
                    "stated_value": stated_value,
                    "actual_value": actual,
                }
            )
    diagnostics = {
        "checked_claim_count": checked,
        "match_count": max(0, checked - len(mismatches)),
        "mismatch_count": len(mismatches),
        "io_error_count": len(io_errors),
        "mismatches": mismatches,
        "io_errors": io_errors,
        "total_claim_count": len(claims),
    }
    agent_verdict = str(payload.get("audit_verdict") or "").strip().lower() or None
    if mismatches or io_errors:
        payload["audit_verdict"] = "fail"
    elif checked > 0:
        if agent_verdict not in {"pass", "fail"}:
            payload["audit_verdict"] = "pass"
    elif agent_verdict not in {"pass", "fail"}:
        payload["audit_verdict"] = "inconclusive"
    payload["validator_diagnostics"] = diagnostics
    return diagnostics
