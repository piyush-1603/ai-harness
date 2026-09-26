import pytest
import copy

from src.context.diagnostics import ContextDiagnosticsEngine, get_priority_label
from src.context.policy import AdaptiveContextPolicy, RepositoryScope
from src.context.budget import ContextBudgeter
from src.context.builder import ContextBuilder
from src.memory.manager import MemoryManager
from src.memory.models import Observation, Failure, VerificationSnapshot

def setup_base_manager(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "diagnostics test")
    return manager

def test_policy_diagnostics(tmp_path):
    manager = setup_base_manager(tmp_path)

    # Add failures to increase pressure
    manager.add_failure(Failure(error_signature="sig1", summary="err1", action="act1", occurrence_count=2))
    manager.add_failure(Failure(error_signature="sig2", summary="err2", action="act2", occurrence_count=3))
    manager.add_failure(Failure(error_signature="sig3", summary="err3", action="act3", occurrence_count=4))

    engine = ContextDiagnosticsEngine()
    report = engine.explain(manager)

    assert report.policy.pressure_score > 0
    assert report.policy.pressure_level in ["MEDIUM", "HIGH", "CRITICAL"]
    assert len(report.policy.pressure_reasons) > 0
    assert "repeated failure sig3 x4" in report.policy.pressure_reasons[0] or "sig2" in report.policy.pressure_reasons[0] or any("repeated failure" in r for r in report.policy.pressure_reasons)

def test_candidate_selected_explanation(tmp_path):
    manager = setup_base_manager(tmp_path)
    manager.add_relevant_file("app/main.py")

    # Create touched-file observation
    obs = Observation(type="t", source="s", summary="sum", raw_output="hello", files=["app/main.py"])
    manager.add_observation(obs)

    engine = ContextDiagnosticsEngine()
    report = engine.explain(manager)

    # Find observation in candidates
    obs_cand = next((c for c in report.candidates if c.category == "observation"), None)
    assert obs_cand is not None
    assert obs_cand.selected is True
    assert "HIGH" in obs_cand.priority_label or "VERY_HIGH" in obs_cand.priority_label
    assert any("relevant-file overlap" in r for r in obs_cand.scoring_reasons)

def test_candidate_skipped_explanation(tmp_path):
    manager = setup_base_manager(tmp_path)
    # Add current errors with large text to exceed budget.
    # The budgeter cannot clear current errors, so they will reach the packer and be skipped.
    text = "A" * 3000
    manager.set_current_errors([text] * 15)

    engine = ContextDiagnosticsEngine()
    # tiny max_context_tokens
    report = engine.explain(manager, max_context_tokens=100)

    # At least one current_error should be skipped
    err_skipped = [c for c in report.candidates if c.category == "current_error" and not c.selected]
    assert len(err_skipped) > 0
    obs_cand = err_skipped[0]
    assert obs_cand.decision_reason in ["insufficient_remaining_budget", "no_optional_budget"]

def test_failed_verification_is_critical(tmp_path):
    manager = setup_base_manager(tmp_path)
    manager.set_phase("RECOVER")
    manager.set_verification(VerificationSnapshot(success=False, summary="failed", tests_passed=0, tests_failed=1))

    engine = ContextDiagnosticsEngine()
    report = engine.explain(manager)

    verif_cand = next((c for c in report.candidates if c.category == "verification_result"), None)
    assert verif_cand is not None
    assert verif_cand.priority_label == "CRITICAL"

def test_repeated_active_failure_is_critical(tmp_path):
    manager = setup_base_manager(tmp_path)
    manager.set_phase("RECOVER")
    manager.add_failure(Failure(error_signature="sig", summary="sum", action="act", occurrence_count=2))

    engine = ContextDiagnosticsEngine()
    report = engine.explain(manager)

    fail_cand = next((c for c in report.candidates if c.category == "repeated_failure"), None)
    assert fail_cand is not None
    assert fail_cand.priority_label == "CRITICAL"

def test_successful_verification_is_low(tmp_path):
    manager = setup_base_manager(tmp_path)
    manager.set_phase("VERIFY")
    manager.set_verification(VerificationSnapshot(success=True, summary="passed", tests_passed=1, tests_failed=0))

    engine = ContextDiagnosticsEngine()
    report = engine.explain(manager)

    verif_cand = next((c for c in report.candidates if c.category == "verification_result"), None)
    assert verif_cand is not None
    assert verif_cand.priority_label == "LOW"

def test_budget_reductions_appear(tmp_path):
    manager = setup_base_manager(tmp_path)
    manager.set_phase("RECOVER")
    from src.memory.models import Discovery
    # Add large telemetry by recording many attempts/discoveries to increase budget size
    for i in range(15):
        manager.add_discovery(Discovery(statement=f"disc_{i}", evidence="A" * 3000, confidence=1.0))
        manager.add_failure(Failure(error_signature=f"sig_{i}", summary="sum", action="act", occurrence_count=2))

    engine = ContextDiagnosticsEngine()
    report = engine.explain(manager, max_context_tokens=1000)

    assert report.budget.was_reduced is True
    assert len(report.budget.reductions) > 0

def test_final_config_exists_and_corresponds(tmp_path):
    manager = setup_base_manager(tmp_path)
    manager.set_phase("RECOVER")
    from src.memory.models import Discovery
    # Add multiple discoveries so we trigger reductions
    for i in range(15):
        manager.add_discovery(Discovery(statement=f"disc_{i}", evidence="A" * 3000, confidence=1.0))
        manager.add_failure(Failure(error_signature=f"sig_{i}", summary="sum", action="act", occurrence_count=2))

    engine = ContextDiagnosticsEngine()
    report = engine.explain(manager, max_context_tokens=100)

    # Check that reductions include 'disable_telemetry' and skipped candidates include telemetry with reason
    assert "disable_telemetry" in report.budget.reductions
    assert not any(c.category == "telemetry" for c in report.candidates) # It's not generated if it's disabled in final config

def test_hard_truncation_diagnostics(tmp_path):
    manager = setup_base_manager(tmp_path)
    manager.add_observation(Observation(type="t", source="s", summary="small", raw_output="hello"))

    engine = ContextDiagnosticsEngine()
    report = engine.explain(manager, max_context_tokens=10) # very small budget

    assert report.budget.hard_truncated is True
    assert report.budget.hard_truncation_note is not None

def test_diagnostic_determinism(tmp_path):
    manager = setup_base_manager(tmp_path)
    manager.add_observation(Observation(type="t", source="s", summary="sum", raw_output="hello", files=["app/main.py"]))
    manager.add_failure(Failure(error_signature="sig", summary="sum", action="act", occurrence_count=2))

    engine = ContextDiagnosticsEngine()
    report1 = engine.explain(manager)
    report2 = engine.explain(manager)

    assert report1.to_dict() == report2.to_dict()
    assert report1.render_text() == report2.render_text()

def test_diagnostics_are_read_only(tmp_path):
    manager = setup_base_manager(tmp_path)
    manager.add_observation(Observation(type="t", source="s", summary="sum", raw_output="hello", files=["app/main.py"]))

    before_state = copy.deepcopy(manager.get_state().to_dict())

    engine = ContextDiagnosticsEngine()
    engine.explain(manager)

    after_state = manager.get_state().to_dict()
    assert before_state == after_state

def test_diagnostics_do_not_append_events(tmp_path):
    manager = setup_base_manager(tmp_path)
    manager.add_observation(Observation(type="t", source="s", summary="sum", raw_output="hello", files=["app/main.py"]))

    before_events = len(manager.get_events())

    engine = ContextDiagnosticsEngine()
    engine.explain(manager)

    after_events = len(manager.get_events())
    assert before_events == after_events

def test_build_output_unchanged(tmp_path):
    manager = setup_base_manager(tmp_path)
    manager.add_observation(Observation(type="t", source="s", summary="sum", raw_output="hello"))

    builder = ContextBuilder()
    bundle_a = builder.build(manager)
    bundle_b, _ = builder.build_with_report(manager)

    assert bundle_a.render_text() == bundle_b.render_text()

def test_artifact_backed_observation_diagnostics(tmp_path):
    manager = setup_base_manager(tmp_path)
    from src.memory.manager import INLINE_OUTPUT_MAX_CHARS
    large_text = "B" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = manager.add_observation(Observation(type="t", source="s", summary="large", raw_output=large_text))

    engine = ContextDiagnosticsEngine()
    report = engine.explain(manager)

    obs_cand = next((c for c in report.candidates if c.category == "observation"), None)
    assert obs_cand is not None
    assert obs_cand.output_ref == obs.output_ref
    assert obs_cand.raw_output_chars == len(large_text)

    rendered = report.render_text()
    assert f"Artifact: {obs.output_ref}" in rendered
    assert f"Original chars: {len(large_text)}" in rendered
    assert "Context uses bounded preview only" in rendered

def test_observation_window_artifact_mapping(tmp_path):
    manager = setup_base_manager(tmp_path)
    from src.memory.manager import INLINE_OUTPUT_MAX_CHARS
    manager.set_phase("RECOVER")

    # Create 10 large observations so they get artifact refs
    for i in range(10):
        manager.add_observation(
            Observation(type="t", source="s", summary=f"obs_{i}", raw_output=f"OBS_{i}_" + "X" * (INLINE_OUTPUT_MAX_CHARS + 10))
        )

    # The default config in RECOVER has max_observations=12 or similar,
    # but let's constrain the max tokens severely so max_observations gets halved/reduced.
    engine = ContextDiagnosticsEngine()
    report = engine.explain(manager, max_context_tokens=1000)

    # The first observation in the candidate list must map to the exact observation in the final slice
    obs_candidates = [c for c in report.candidates if c.category == "observation"]
    obs_candidates.sort(key=lambda c: int(c.id.split("_")[1])) # sort by ID "obs_0", "obs_1"...

    state = manager.get_state()
    # The ContextBuilder generates ids like obs_0, obs_1 for the slice
    # So candidate 'obs_0' corresponds to slice[0].
    # Which corresponds to state.recent_observations[-len(slice)]

    # Let's find candidate obs_0
    obs_0_cand = next((c for c in obs_candidates if c.id == "obs_0"), None)
    assert obs_0_cand is not None

    # Check its output_ref
    # Since obs_0 is the first in the slice, it should map to state.recent_observations[-len(obs_candidates)]
    expected_obs = state.recent_observations[-len(obs_candidates)]
    assert obs_0_cand.output_ref == expected_obs.output_ref

def test_diagnostics_do_not_read_artifacts(tmp_path, monkeypatch):
    manager = setup_base_manager(tmp_path)
    from src.memory.manager import INLINE_OUTPUT_MAX_CHARS
    large_text = "A" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = manager.add_observation(Observation(type="t", source="s", summary="huge", raw_output=large_text))

    get_text_called = False
    def mock_get_text(self, ref):
        nonlocal get_text_called
        get_text_called = True
        return large_text

    import src.memory.artifacts
    monkeypatch.setattr(src.memory.artifacts.ArtifactStore, "get_text", mock_get_text)

    engine = ContextDiagnosticsEngine()
    report = engine.explain(manager)

    assert not get_text_called

    obs_cand = next((c for c in report.candidates if c.category == "observation"), None)
    assert obs_cand is not None
    assert obs_cand.output_ref == obs.output_ref
    assert obs_cand.raw_output_chars == len(large_text)
