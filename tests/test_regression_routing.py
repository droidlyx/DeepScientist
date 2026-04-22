from __future__ import annotations

from pathlib import Path

import yaml

from deepscientist.artifact.guidance import build_guidance_for_record
from deepscientist.artifact.careful_mode import (
    _is_agent_flagged_regression,
    coerce_careful_mode,
    is_careful_mode_on,
    maybe_inject_careful_mode_regression_routing,
    read_careful_mode,
)


def _write_quest_yaml(quest_root: Path, careful_mode: dict | None) -> None:
    quest_root.mkdir(parents=True, exist_ok=True)
    payload: dict = {"quest_id": "test", "startup_contract": {}}
    if careful_mode is not None:
        payload["startup_contract"]["careful_mode"] = careful_mode
    (quest_root / "quest.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")


def _run_record(**overrides: object) -> dict:
    record: dict = {
        "kind": "run",
        "run_kind": "main_experiment",
        "artifact_id": "run-test",
    }
    record.update(overrides)
    return record


# ---- classifier: only agent-flagged refuted runs count as regressions ----


def test_refuted_verdict_is_a_regression() -> None:
    assert _is_agent_flagged_regression(_run_record(verdict="refuted")) is True


def test_supported_verdict_is_not_a_regression() -> None:
    assert _is_agent_flagged_regression(_run_record(verdict="supported")) is False


def test_inconclusive_verdict_is_not_a_regression() -> None:
    assert _is_agent_flagged_regression(_run_record(verdict="inconclusive")) is False


def test_numeric_negative_delta_alone_is_not_a_regression() -> None:
    # Intentionally conservative: careful mode does NOT auto-classify from
    # numeric signals, only from the agent's own verdict. Seed noise and
    # intentionally-regressing ablations should not trip the router.
    record = _run_record(details={"delta_vs_baseline": -0.5})
    assert _is_agent_flagged_regression(record) is False


def test_beats_baseline_false_alone_is_not_a_regression() -> None:
    record = _run_record(details={"beats_baseline": False})
    assert _is_agent_flagged_regression(record) is False


def test_non_main_run_kind_is_not_a_regression() -> None:
    record = _run_record(run_kind="analysis-campaign", verdict="refuted")
    assert _is_agent_flagged_regression(record) is False


# ---- careful_mode config helper ----


def test_careful_mode_defaults_to_off_when_missing(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, None)
    assert read_careful_mode(tmp_path)["mode"] == "off"
    assert is_careful_mode_on(tmp_path) is False


def test_careful_mode_accepts_structured_on(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, {"mode": "on"})
    assert read_careful_mode(tmp_path)["mode"] == "on"
    assert is_careful_mode_on(tmp_path) is True


def test_careful_mode_accepts_plain_string_on(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, "on")
    assert read_careful_mode(tmp_path)["mode"] == "on"
    assert is_careful_mode_on(tmp_path) is True


def test_careful_mode_malformed_value_falls_back_to_off(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, {"mode": "loud"})
    assert read_careful_mode(tmp_path)["mode"] == "off"


def test_coerce_careful_mode_rejects_garbage() -> None:
    for bad in [None, 0, [], {"mode": "foo"}, {"other": "on"}, 3.14]:
        try:
            coerce_careful_mode(bad)
        except ValueError:
            continue
        raise AssertionError(f"coerce_careful_mode should have rejected {bad!r}")


def test_coerce_careful_mode_accepts_structured_and_string() -> None:
    assert coerce_careful_mode({"mode": "on"}) == {"mode": "on"}
    assert coerce_careful_mode("off") == {"mode": "off"}


# ---- routing override: gated on careful_mode AND agent-flagged refuted ----


def test_override_not_applied_when_careful_mode_off(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, {"mode": "off"})
    record = _run_record(verdict="refuted")
    base = build_guidance_for_record(record)
    out = maybe_inject_careful_mode_regression_routing(tmp_path, record, base)
    # Careful mode off -> guidance must be untouched.
    assert out is base


def test_override_not_applied_when_run_is_not_refuted(tmp_path: Path) -> None:
    _write_quest_yaml(tmp_path, {"mode": "on"})
    record = _run_record(verdict="supported", details={"delta_vs_baseline": -0.3})
    base = build_guidance_for_record(record)
    out = maybe_inject_careful_mode_regression_routing(tmp_path, record, base)
    assert out is base


def test_override_routes_to_analysis_campaign_when_both_conditions_hold(
    tmp_path: Path,
) -> None:
    _write_quest_yaml(tmp_path, {"mode": "on"})
    record = _run_record(verdict="refuted")
    base = build_guidance_for_record(record)
    out = maybe_inject_careful_mode_regression_routing(tmp_path, record, base)
    assert out is not base
    assert out["recommended_skill"] == "analysis-campaign"
    assert out["recommended_action"] == "launch_analysis_campaign"
    assert out["careful_mode"] is True
    # Should carry the previous route for context, not erase it.
    assert out["previous_recommended_skill"] == base.get("recommended_skill")


def test_default_main_run_guidance_is_unchanged_without_careful_mode(
    tmp_path: Path,
) -> None:
    # Without touching careful_mode, build_guidance_for_record should keep
    # its original behavior: main run -> decision.
    record = _run_record(verdict="refuted")
    guidance = build_guidance_for_record(record)
    assert guidance["recommended_skill"] == "decision"
