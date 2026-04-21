from __future__ import annotations

import json
from pathlib import Path

import yaml

from deepscientist.artifact.audit import (
    AUDIT_TRIGGERS,
    is_audit_report,
    maybe_inject_audit_override,
    read_audit_policy,
    validate_audit_report_payload,
)
from deepscientist.artifact.guidance import build_guidance_for_record


def _write_quest_yaml(quest_root: Path, audit_policy: dict | None) -> None:
    quest_root.mkdir(parents=True, exist_ok=True)
    payload: dict = {"quest_id": "test", "startup_contract": {}}
    if audit_policy is not None:
        payload["startup_contract"]["audit_policy"] = audit_policy
    (quest_root / "quest.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")


def test_read_audit_policy_defaults_to_off_when_missing(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, None)
    policy = read_audit_policy(tmp_path)
    assert policy["mode"] == "off"
    assert set(policy["triggers"]) == {
        "record_main_experiment",
        "submit_paper_bundle",
        "confirm_baseline",
    }


def test_read_audit_policy_advisory_round_trips(tmp_path: Path) -> None:
    _write_quest_yaml(
        tmp_path,
        {
            "mode": "advisory",
            "triggers": ["submit_paper_bundle"],
            "runner": "codex",
        },
    )
    policy = read_audit_policy(tmp_path)
    assert policy["mode"] == "advisory"
    assert policy["triggers"] == ["submit_paper_bundle"]
    assert policy["runner"] == "codex"


def test_read_audit_policy_drops_unknown_triggers(tmp_path: Path) -> None:
    _write_quest_yaml(
        tmp_path,
        {"mode": "advisory", "triggers": ["bogus_tool", "confirm_baseline"]},
    )
    policy = read_audit_policy(tmp_path)
    assert policy["triggers"] == ["confirm_baseline"]


def test_read_audit_policy_rejects_invalid_mode(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, {"mode": "blocking"})
    # blocking is reserved but not yet implemented — must be normalized to off
    policy = read_audit_policy(tmp_path)
    assert policy["mode"] == "off"


def _main_experiment_record() -> dict:
    return {
        "kind": "run",
        "run_kind": "main_experiment",
        "artifact_id": "run-deadbeef",
        "flow_type": "main_experiment",
        "protocol_step": "record",
        "paths": {"result_json": "experiments/main/r/RESULT.json"},
    }


def _paper_bundle_record() -> dict:
    return {
        "kind": "report",
        "report_type": "paper_bundle",
        "artifact_id": "report-abcd",
        "flow_type": "paper_bundle",
        "protocol_step": "submit",
        "paths": {},
    }


def _baseline_confirm_record() -> dict:
    return {
        "kind": "baseline",
        "status": "confirmed",
        "artifact_id": "baseline-beef",
        "flow_type": "baseline_gate",
        "protocol_step": "confirm",
        "paths": {},
    }


def test_override_is_noop_when_policy_off(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, None)
    record = _paper_bundle_record()
    original_guidance = build_guidance_for_record(record)
    result = maybe_inject_audit_override(tmp_path, record, original_guidance)
    assert result == original_guidance
    assert "pending_audit" not in record


def test_override_is_noop_on_audit_report(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, {"mode": "advisory"})
    record = {
        "kind": "report",
        "report_type": "audit_report",
        "artifact_id": "audit-001",
        "audit_verdict": "pass",
        "pre_audit_recommended_skill": "finalize",
        "audit_target": {"artifact_id": "report-abcd", "trigger_label": "paper_bundle"},
    }
    guidance = build_guidance_for_record(record)
    out = maybe_inject_audit_override(tmp_path, record, guidance)
    assert out is guidance
    # Audit-report should never be re-audited.
    assert out["recommended_skill"] != "audit-numbers"


def test_override_rewrites_paper_bundle_guidance(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, {"mode": "advisory"})
    record = _paper_bundle_record()
    original = build_guidance_for_record(record)
    out = maybe_inject_audit_override(tmp_path, record, original)
    assert out["recommended_skill"] == "audit-numbers"
    assert out["recommended_action"] == "audit"
    assert out["stage_status"] == "pending_audit"
    assert out["pending_audit"]["target_artifact_id"] == "report-abcd"
    assert out["pending_audit"]["trigger_label"] == "paper_bundle"
    assert out["pending_audit"]["fail_recommended_skill"] == "write"
    assert out["pre_audit_guidance"]["recommended_skill"] == original["recommended_skill"]
    assert record["pending_audit"]["pre_audit_recommended_skill"] == original["recommended_skill"]


def test_override_fires_for_all_three_triggers(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, {"mode": "advisory"})
    for record_factory, expected_label in (
        (_main_experiment_record, "main_experiment"),
        (_paper_bundle_record, "paper_bundle"),
        (_baseline_confirm_record, "baseline_confirm"),
    ):
        rec = record_factory()
        original = build_guidance_for_record(rec)
        out = maybe_inject_audit_override(tmp_path, rec, original)
        assert out["recommended_skill"] == "audit-numbers", expected_label
        assert out["pending_audit"]["trigger_label"] == expected_label


def test_override_respects_triggers_list(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, {"mode": "advisory", "triggers": ["submit_paper_bundle"]})
    # main_experiment is not in the configured triggers — do nothing
    rec = _main_experiment_record()
    original = build_guidance_for_record(rec)
    out = maybe_inject_audit_override(tmp_path, rec, original)
    assert out["recommended_skill"] == original["recommended_skill"]
    # paper_bundle is — divert
    rec2 = _paper_bundle_record()
    original2 = build_guidance_for_record(rec2)
    out2 = maybe_inject_audit_override(tmp_path, rec2, original2)
    assert out2["recommended_skill"] == "audit-numbers"


def test_is_audit_report_positive_and_negative() -> None:
    assert is_audit_report({"kind": "report", "report_type": "audit_report"}) is True
    assert is_audit_report({"kind": "report", "report_type": "paper_bundle"}) is False
    assert is_audit_report({"kind": "baseline"}) is False


def test_validator_flags_numeric_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "RESULT.json"
    source.write_text(json.dumps({"mean_score": 13.92}), encoding="utf-8")
    payload = {
        "kind": "report",
        "report_type": "audit_report",
        "audit_verdict": "pass",
        "claims": [
            {
                "quote": "mean=14.08",
                "stated_value": 14.08,
                "source_path": "RESULT.json",
                "source_field": "mean_score",
            },
        ],
    }
    diag = validate_audit_report_payload(tmp_path, payload)
    assert diag["mismatch_count"] == 1
    assert payload["audit_verdict"] == "fail"
    assert payload["claims"][0]["validator_verdict"] == "mismatch"


def test_validator_accepts_within_tolerance(tmp_path: Path) -> None:
    source = tmp_path / "RESULT.json"
    source.write_text(json.dumps({"mean_score": 17.48}), encoding="utf-8")
    payload = {
        "kind": "report",
        "report_type": "audit_report",
        "audit_verdict": "pass",
        "claims": [
            {
                "quote": "mean 17.5",
                "stated_value": 17.5,
                "source_path": "RESULT.json",
                "source_field": "mean_score",
            },
        ],
    }
    diag = validate_audit_report_payload(tmp_path, payload)
    assert diag["mismatch_count"] == 0
    assert payload["audit_verdict"] == "pass"
    assert payload["claims"][0]["validator_verdict"] == "match"


def test_validator_catches_missing_source(tmp_path: Path) -> None:
    payload = {
        "kind": "report",
        "report_type": "audit_report",
        "audit_verdict": "pass",
        "claims": [
            {
                "quote": "72 minutes",
                "stated_value": 72,
                "source_path": "does/not/exist.json",
                "source_field": "minutes",
            }
        ],
    }
    diag = validate_audit_report_payload(tmp_path, payload)
    assert diag["io_error_count"] == 1
    assert payload["audit_verdict"] == "fail"
    assert payload["claims"][0]["validator_verdict"] == "io_error"


def test_validator_supports_dotted_path(tmp_path: Path) -> None:
    source = tmp_path / "metric_contract.json"
    source.write_text(
        json.dumps(
            {
                "metrics_summary": {"train_time_seconds": 2982.0},
                "primary_metric": {"value": 15.88},
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "kind": "report",
        "report_type": "audit_report",
        "audit_verdict": "pass",
        "claims": [
            {
                "quote": "15.88",
                "stated_value": 15.88,
                "source_path": "metric_contract.json",
                "source_field": "primary_metric.value",
            },
            {
                "quote": "2982s",
                "stated_value": 2982.0,
                "source_path": "metric_contract.json",
                "source_field": "metrics_summary.train_time_seconds",
            },
        ],
    }
    diag = validate_audit_report_payload(tmp_path, payload)
    assert diag["mismatch_count"] == 0
    assert diag["match_count"] == 2
    assert payload["audit_verdict"] == "pass"


def test_validator_substring_match_for_plain_text(tmp_path: Path) -> None:
    source = tmp_path / "train.log"
    source.write_text("...\nfps | 519\n...", encoding="utf-8")
    payload = {
        "kind": "report",
        "report_type": "audit_report",
        "audit_verdict": "pass",
        "claims": [
            {
                "quote": "519 fps",
                "stated_value": "fps | 519",
                "source_path": "train.log",
                "source_field": None,
            }
        ],
    }
    diag = validate_audit_report_payload(tmp_path, payload)
    assert diag["mismatch_count"] == 0
    assert payload["audit_verdict"] == "pass"


def test_validator_forces_fail_even_when_agent_claims_pass(tmp_path: Path) -> None:
    source = tmp_path / "RESULT.json"
    source.write_text(json.dumps({"mean_score": 15.88}), encoding="utf-8")
    payload = {
        "kind": "report",
        "report_type": "audit_report",
        "audit_verdict": "pass",
        "claims": [
            {
                "quote": "mean=17.48",
                "stated_value": 17.48,
                "source_path": "RESULT.json",
                "source_field": "mean_score",
            }
        ],
    }
    validate_audit_report_payload(tmp_path, payload)
    assert payload["audit_verdict"] == "fail"


def test_audit_report_guidance_pass_routes_back() -> None:
    record = {
        "kind": "report",
        "report_type": "audit_report",
        "artifact_id": "audit-42",
        "audit_verdict": "pass",
        "pre_audit_recommended_skill": "finalize",
        "pre_audit_recommended_action": "finalize",
        "fail_recommended_skill": "write",
        "audit_target": {"artifact_id": "report-bundle", "trigger_label": "paper_bundle"},
        "validator_diagnostics": {
            "checked_claim_count": 12,
            "match_count": 12,
            "mismatch_count": 0,
            "io_error_count": 0,
        },
    }
    guidance = build_guidance_for_record(record)
    assert guidance["recommended_skill"] == "finalize"
    assert guidance["recommended_action"] == "finalize"
    assert guidance["stage_status"] == "audit_pass"


def test_audit_report_guidance_fail_routes_to_remediation() -> None:
    record = {
        "kind": "report",
        "report_type": "audit_report",
        "artifact_id": "audit-43",
        "audit_verdict": "fail",
        "pre_audit_recommended_skill": "finalize",
        "fail_recommended_skill": "write",
        "audit_target": {"artifact_id": "report-bundle", "trigger_label": "paper_bundle"},
        "validator_diagnostics": {
            "checked_claim_count": 12,
            "match_count": 9,
            "mismatch_count": 3,
            "io_error_count": 0,
        },
    }
    guidance = build_guidance_for_record(record)
    assert guidance["recommended_skill"] == "write"
    assert guidance["recommended_action"] == "revise"
    assert guidance["stage_status"] == "audit_fail"
    assert "3 numeric mismatch" in guidance["summary"]


def test_audit_report_guidance_fail_defaults_by_trigger_when_fail_skill_missing() -> None:
    record = {
        "kind": "report",
        "report_type": "audit_report",
        "artifact_id": "audit-44",
        "audit_verdict": "fail",
        "audit_target": {"artifact_id": "run-x", "trigger_label": "main_experiment"},
        "validator_diagnostics": {
            "checked_claim_count": 4,
            "mismatch_count": 1,
            "io_error_count": 0,
        },
    }
    guidance = build_guidance_for_record(record)
    assert guidance["recommended_skill"] == "decision"


def test_default_trigger_set_matches_three_tools() -> None:
    assert set(AUDIT_TRIGGERS.keys()) == {
        "record_main_experiment",
        "submit_paper_bundle",
        "confirm_baseline",
    }


# ---------------------------------------------------------------------------
# MCP form-patch sanitizer (Flow B: agent-mediated refinement)
# ---------------------------------------------------------------------------


import pytest

from deepscientist.artifact.audit import coerce_audit_policy


def test_coerce_accepts_advisory_with_full_config() -> None:
    out = coerce_audit_policy(
        {
            "mode": "advisory",
            "triggers": ["submit_paper_bundle"],
            "runner": "claude",
        }
    )
    assert out == {
        "mode": "advisory",
        "triggers": ["submit_paper_bundle"],
        "runner": "claude",
    }


def test_coerce_accepts_off() -> None:
    assert coerce_audit_policy({"mode": "off"}) == {"mode": "off"}


def test_coerce_off_drops_triggers_and_runner() -> None:
    out = coerce_audit_policy({"mode": "off", "triggers": ["submit_paper_bundle"], "runner": "claude"})
    assert out == {"mode": "off"}


def test_coerce_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        coerce_audit_policy({"mode": "blocking"})


def test_coerce_drops_unknown_triggers() -> None:
    out = coerce_audit_policy(
        {"mode": "advisory", "triggers": ["bogus", "confirm_baseline"]}
    )
    assert out["triggers"] == ["confirm_baseline"]


def test_coerce_rejects_non_dict_input() -> None:
    with pytest.raises(ValueError, match="object"):
        coerce_audit_policy("advisory")


def test_coerce_rejects_unknown_runner() -> None:
    with pytest.raises(ValueError, match="runner"):
        coerce_audit_policy({"mode": "advisory", "runner": "gpt5"})


def test_coerce_accepts_empty_runner_silently() -> None:
    # Empty string runner should not raise (treated as unset)
    out = coerce_audit_policy({"mode": "advisory", "runner": ""})
    assert "runner" not in out


def test_coerce_rejects_non_list_triggers() -> None:
    with pytest.raises(ValueError, match="triggers"):
        coerce_audit_policy({"mode": "advisory", "triggers": "submit_paper_bundle"})


def test_coerce_and_read_audit_policy_share_shape(tmp_path: Path) -> None:
    # A coerced dict written into startup_contract must round-trip through
    # read_audit_policy without change.
    policy = coerce_audit_policy(
        {"mode": "advisory", "triggers": ["submit_paper_bundle", "confirm_baseline"], "runner": "inherit"}
    )
    _write_quest_yaml(tmp_path, policy)
    read_back = read_audit_policy(tmp_path)
    assert read_back["mode"] == "advisory"
    assert read_back["runner"] == "inherit"
    assert set(read_back["triggers"]) == {"submit_paper_bundle", "confirm_baseline"}
