import pytest
import os
import json
import copy

from benchmarks.metrics import calculate_metrics, extract_files_from_context, BenchmarkMetrics
from benchmarks.scenarios import create_scenarios
from benchmarks.baseline import StaticContextBaseline
from src.context.builder import ContextBuilder
from src.context.config import ContextConfig
from src.context.policy import RepositoryScope
from benchmarks.benchmark_context import run_benchmark, hash_text

def test_scenario_generation_deterministic():
    s1 = create_scenarios()
    s2 = create_scenarios()
    
    assert len(s1) == len(s2)
    for a, b in zip(s1, s2):
        assert a.name == b.name
        assert a.state.task_id == b.state.task_id
        assert a.ground_truth_relevant == b.ground_truth_relevant

def test_baseline_config_fixed():
    baseline = StaticContextBaseline()
    assert baseline.config.max_observations == 8
    assert baseline.config.include_symbols is True
    assert baseline.config.repository_scope == RepositoryScope.FOCUSED

def test_relevant_file_recall_and_irrelevant_counting():
    text = "this text contains src/auth.py and src/routes.py but not token"
    repo_files = ["src/auth.py", "src/token.py", "src/routes.py", "src/crypto.py"]
    ground_truth = ["src/auth.py", "src/token.py"]
    
    metrics = calculate_metrics(
        scenario_name="test",
        mode="test",
        text=text,
        budget_limit=1000,
        estimated_tokens=50,
        repo_files=repo_files,
        ground_truth_relevant=ground_truth,
        has_current_error=False,
        has_failed_verification=False,
        has_failed_attempt=False,
        has_repeated_failure=False,
        output_hash="abc",
    )
    
    assert metrics.relevant_files_expected == 2
    assert metrics.relevant_files_included == 1 # auth
    assert metrics.relevant_file_recall == 0.5
    assert metrics.irrelevant_files_included == 1 # routes

def test_critical_evidence_detection():
    text = "Here is an ExpectedError inside and Attempt 1 -> Fail and Success: False and [x3]"
    metrics = calculate_metrics(
        scenario_name="test",
        mode="test",
        text=text,
        budget_limit=1000,
        estimated_tokens=50,
        repo_files=[],
        ground_truth_relevant=[],
        has_current_error=True,
        has_failed_verification=True,
        has_failed_attempt=True,
        has_repeated_failure=True,
        output_hash="abc",
    )
    assert metrics.current_error_retained is True
    assert metrics.failed_verification_retained is True
    assert metrics.failed_attempt_retained is True
    assert metrics.repeated_failure_retained is True
    
    metrics2 = calculate_metrics(
        scenario_name="test",
        mode="test",
        text="Clean text",
        budget_limit=1000,
        estimated_tokens=50,
        repo_files=[],
        ground_truth_relevant=[],
        has_current_error=True,
        has_failed_verification=True,
        has_failed_attempt=True,
        has_repeated_failure=True,
        output_hash="abc",
    )
    assert metrics2.current_error_retained is False
    assert metrics2.failed_verification_retained is False
    assert metrics2.failed_attempt_retained is False
    assert metrics2.repeated_failure_retained is False

def test_budget_violations_detected():
    m = calculate_metrics(
        scenario_name="test",
        mode="test",
        text="test",
        budget_limit=1000,
        estimated_tokens=1500,
        repo_files=[],
        ground_truth_relevant=[],
        has_current_error=False,
        has_failed_verification=False,
        has_failed_attempt=False,
        has_repeated_failure=False,
        output_hash="abc",
    )
    assert m.budget_respected is False

def test_immutability():
    scenarios = create_scenarios()
    s = scenarios[0]
    
    orig_state = copy.deepcopy(s.state)
    orig_repo = copy.deepcopy(s.repo)
    
    baseline = StaticContextBaseline()
    baseline.build_context(s.state, s.repo, 8000)
    
    assert s.state == orig_state
    
    builder = ContextBuilder()
    builder.build(s.state, max_tokens=8000, repository_index=s.repo)
    
    assert s.state == orig_state
    
    # Check repo wasn't mutated
    assert len(s.repo.source_files) == len(orig_repo.source_files)

def test_report_generation(tmp_path):
    # patch out_dir in benchmark_context to use tmp_path
    import benchmarks.benchmark_context as bcontext
    orig_run = bcontext.run_benchmark
    
    # Just test write_reports
    bcontext.Path = lambda x: tmp_path # mock path
    
    try:
        bcontext.write_reports([])
        assert (tmp_path / "latest.json").exists()
        assert (tmp_path / "latest.md").exists()
        
        with open(tmp_path / "latest.json") as f:
            data = json.load(f)
            assert "benchmark_timestamp" in data
    finally:
        import pathlib
        bcontext.Path = pathlib.Path

def test_repeated_failure_occurrence_count():
    from src.memory.models import Failure, TaskState
    
    # 1 occurence -> NOT expected
    f1 = Failure(error_signature="E", summary="S", action="A", occurrence_count=1, files=["f"])
    s1 = TaskState(task_id="t1", task="t", failures=[f1])
    cfg = ContextConfig(min_failure_occurrences=2)
    has_rep_1 = len([f for f in s1.failures if not getattr(f, "resolved", False) and f.occurrence_count >= cfg.min_failure_occurrences]) > 0
    assert has_rep_1 is False

    # 2 occurences -> IS expected
    f2 = Failure(error_signature="E", summary="S", action="A", occurrence_count=2, files=["f"])
    s2 = TaskState(task_id="t2", task="t", failures=[f2])
    has_rep_2 = len([f for f in s2.failures if not getattr(f, "resolved", False) and f.occurrence_count >= cfg.min_failure_occurrences]) > 0
    assert has_rep_2 is True

def test_scenario_5_eviction_and_budget():
    from src.context.budget import estimate_tokens, DEFAULT_CHARS_PER_TOKEN, ContextBudgeter
    scenarios = create_scenarios()
    s5 = next(s for s in scenarios if "Scenario 5" in s.name)
    
    # Check max tokens
    assert s5.max_tokens == 150
    
    baseline = StaticContextBaseline()
    static_text = baseline.build_context(s5.state, s5.repo, s5.max_tokens)
    static_tokens = estimate_tokens(static_text, chars_per_token=DEFAULT_CHARS_PER_TOKEN)
    
    budgeter = ContextBudgeter()
    cfg = ContextConfig()
    res = budgeter.fit(s5.state, config=cfg, repository_index=s5.repo, max_context_tokens=s5.max_tokens)
    
    adapt_text = res.text
    adapt_tokens = estimate_tokens(adapt_text, chars_per_token=DEFAULT_CHARS_PER_TOKEN)
    
    # Verify we measure from final text
    assert adapt_tokens == res.estimated_tokens_after
    
    # Adaptive should strictly obey budget
    assert adapt_tokens <= s5.max_tokens
    
    # Static was hard-truncated, it should also be <= budget
    assert static_tokens <= s5.max_tokens
    
    # Ensure it was reduced
    assert res.was_reduced is True
    assert res.estimated_tokens_before > s5.max_tokens
    
    # Ensure metrics inspect final string
    metrics = calculate_metrics(
        scenario_name="test",
        mode="Adaptive",
        text=adapt_text,
        budget_limit=s5.max_tokens,
        estimated_tokens=adapt_tokens,
        repo_files=s5.repo.source_files,
        ground_truth_relevant=s5.ground_truth_relevant,
        has_current_error=True,
        has_failed_verification=True,
        has_failed_attempt=True,
        has_repeated_failure=True,
        output_hash=hash_text(adapt_text)
    )
    
    assert metrics.budget_respected is (metrics.estimated_tokens <= metrics.budget_limit)
    if metrics.budget_respected:
        assert metrics.estimated_tokens <= s5.max_tokens
