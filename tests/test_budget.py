"""
Tests for Person B Phase B5.1: Deterministic Context Budget Enforcement.

Verifies:
- Deterministic token estimation (estimate_tokens)
- Budget configuration and environment variable fallback (HARNESS_CONTEXT_MAX_TOKENS)
- Under-budget text remains byte-for-byte identical
- Oversized context is reduced deterministically in stages
- Final context always fits configured budget
- Telemetry disappears before critical recovery evidence
- Successful attempts disappear before failed attempts
- BROAD scope contracts to FOCUSED, and FOCUSED contracts to MINIMAL
- Observations and discoveries shrink deterministically (recency preserved)
- Critical failure information survives longer than low-value sections
- Hard truncation handles an oversized core context
- Truncation marker fits within the limit
- ContextBudgetResult reports accurate metadata
- Deterministic reproducibility
- Immutability of TaskState, ContextConfig, ContextProfile, and RepositoryIndex
- End-to-end integration test: RECOVER + HIGH pressure under a tight budget
"""

import math
import os
from pathlib import Path
import pytest

from src.context.budget import (
    ContextBudgetConfig,
    ContextBudgetResult,
    ContextBudgeter,
    DEFAULT_CHARS_PER_TOKEN,
    DEFAULT_MAX_CONTEXT_TOKENS,
    TRUNCATION_MARKER,
    estimate_tokens,
    get_default_max_context_tokens,
)
from src.context.builder import ContextBuilder
from src.context.bundle import ContextBundle
from src.context.config import ContextConfig
from src.context.policy import (
    AdaptiveContextPolicy,
    ContextProfile,
    PressureLevel,
    RepositoryScope,
)
from src.context.scanner import RepositoryIndex
from src.context.symbols import FileSymbols, SymbolRecord
from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Discovery,
    Failure,
    Observation,
    Phase,
    TaskState,
    TaskStatus,
    TokenUsage,
    VerificationResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_test_repo_index() -> RepositoryIndex:
    """Helper to build a populated RepositoryIndex for tests."""
    fs_main = FileSymbols(
        path="src/main.py",
        language="Python",
        symbols=[
            SymbolRecord(name="MainApp", kind="class", file="src/main.py"),
            SymbolRecord(name="run", kind="method", file="src/main.py", parent="MainApp"),
        ],
        imports=["os", "src.helper"],
        local_imports=["src/helper.py"],
    )
    fs_helper = FileSymbols(
        path="src/helper.py",
        language="Python",
        symbols=[
            SymbolRecord(name="Helper", kind="class", file="src/helper.py"),
            SymbolRecord(name="assist", kind="method", file="src/helper.py", parent="Helper"),
        ],
        imports=["json"],
        local_imports=[],
    )
    return RepositoryIndex(
        root_dir="/workspace",
        discovered_files=["src/main.py", "src/helper.py", "tests/test_main.py", "README.md"],
        discovered_directories=["src", "tests"],
        file_count=4,
        detected_languages={"Python": 3, "Markdown": 1},
        detected_extensions={".py": 3, ".md": 1},
        important_files=["README.md"],
        test_files=["tests/test_main.py"],
        test_directories=["tests"],
        source_files=["src/main.py", "src/helper.py"],
        source_directories=["src"],
        documentation_files=["README.md"],
        file_roles={
            "src/main.py": "SOURCE",
            "src/helper.py": "SOURCE",
            "tests/test_main.py": "TEST",
            "README.md": "DOCUMENTATION",
        },
        file_symbols={
            "src/main.py": fs_main,
            "src/helper.py": fs_helper,
        },
    )


def _build_test_task_state(
    num_obs: int = 4,
    num_disc: int = 4,
    num_successful: int = 2,
    num_failed: int = 2,
    phase: Phase = Phase.EXECUTE,
) -> TaskState:
    """Build a comprehensive TaskState for budgeting tests."""
    observations = [
        Observation(
            type="tool_output",
            source="bash",
            summary=f"Ran diagnostic step {i} with detailed telemetry",
            raw_output=f"Output line {i} with additional verbose data",
            files=["src/main.py"],
        )
        for i in range(1, num_obs + 1)
    ]
    discoveries = [
        Discovery(
            statement=f"Discovered architectural pattern #{i} in auth subsystem",
            evidence=f"Referenced in file src/helper.py at line {i * 10}",
            files=["src/helper.py"],
            confidence=0.9,
        )
        for i in range(1, num_disc + 1)
    ]
    attempts: list[Attempt] = []
    for i in range(1, num_successful + 1):
        attempts.append(
            Attempt(
                id=f"att-succ-{i}",
                hypothesis=f"Successful hypothesis {i}",
                action=f"Applied fix step {i}",
                files_touched=["src/main.py"],
                result="Success",
                success=True,
                iteration=i,
            )
        )
    for i in range(1, num_failed + 1):
        attempts.append(
            Attempt(
                id=f"att-fail-{i}",
                hypothesis=f"Failing hypothesis {i}",
                action=f"Attempted risky refactor {i}",
                files_touched=["src/helper.py"],
                result="AssertionError: verification failed",
                success=False,
                iteration=num_successful + i,
            )
        )

    failures = [
        Failure(
            error_signature="ConnectionTimeout",
            summary="Timed out connecting to upstream service",
            action="call_remote_api",
            files=["src/main.py"],
            occurrence_count=3,
        )
    ]

    return TaskState(
        task_id="task-budget-test",
        task="Implement high-performance auth caching layer",
        phase=phase,
        status=TaskStatus.RUNNING,
        plan=["Audit existing auth code", "Implement Redis cache", "Verify performance"],
        current_hypothesis="Cache invalidation occurs too aggressively",
        relevant_files=["src/main.py", "src/helper.py"],
        touched_files=["src/main.py"],
        discoveries=discoveries,
        recent_observations=observations,
        attempts=attempts,
        failures=failures,
        current_errors=["ConnectionTimeout: upstream service took >500ms"],
        verification=VerificationResult(
            success=False,
            summary="2 tests passed, 1 timed out",
            tests_passed=["test_login", "test_logout"],
            tests_failed=["test_cache_hit_rate"],
        ),
        iteration=3,
        model_calls=4,
        tool_calls=12,
        token_usage=TokenUsage(input_tokens=1500, output_tokens=300),
    )


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------

def test_estimate_tokens() -> None:
    """Verify deterministic token estimation and boundary handling."""
    assert estimate_tokens("") == 0
    assert estimate_tokens("", chars_per_token=4) == 0
    assert estimate_tokens("abcd", chars_per_token=4) == 1
    assert estimate_tokens("abcde", chars_per_token=4) == 2
    assert estimate_tokens("a" * 100, chars_per_token=4) == 25
    assert estimate_tokens("a" * 101, chars_per_token=4) == 26

    # Custom chars_per_token
    assert estimate_tokens("123456", chars_per_token=3) == 2
    assert estimate_tokens("1234567", chars_per_token=3) == 3

    # Invalid chars_per_token rejection
    with pytest.raises(ValueError):
        estimate_tokens("hello", chars_per_token=0)
    with pytest.raises(ValueError):
        estimate_tokens("hello", chars_per_token=-1)


def test_environment_max_tokens_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify environment variable reading, fallbacks, and programmatic override."""
    # When unset: falls back to 24000
    monkeypatch.delenv("HARNESS_CONTEXT_MAX_TOKENS", raising=False)
    assert get_default_max_context_tokens() == 24000

    # When set to valid int:
    monkeypatch.setenv("HARNESS_CONTEXT_MAX_TOKENS", "12000")
    assert get_default_max_context_tokens() == 12000

    # When set to invalid non-int:
    monkeypatch.setenv("HARNESS_CONTEXT_MAX_TOKENS", "not_a_number")
    assert get_default_max_context_tokens() == 24000

    # When set to negative or zero:
    monkeypatch.setenv("HARNESS_CONTEXT_MAX_TOKENS", "-100")
    assert get_default_max_context_tokens() == 24000
    monkeypatch.setenv("HARNESS_CONTEXT_MAX_TOKENS", "0")
    assert get_default_max_context_tokens() == 24000

    # Explicit programmatic config overrides env var
    monkeypatch.setenv("HARNESS_CONTEXT_MAX_TOKENS", "12000")
    budgeter = ContextBudgeter(max_context_tokens=8000)
    assert budgeter.budget_config.max_context_tokens == 8000


def test_under_budget_text_remains_byte_for_byte_identical() -> None:
    """When context already fits within budget, return the rendered text byte-for-byte unchanged."""
    state = _build_test_task_state(num_obs=2, num_disc=2)
    cfg = ContextConfig()
    repo = _build_test_repo_index()

    normal_text = ContextBuilder(config=cfg, repository_index=repo).build(state).render_text()
    normal_tokens = estimate_tokens(normal_text, 4)

    # Configure a budget well above normal_tokens
    budgeter = ContextBudgeter(max_context_tokens=normal_tokens + 500)
    result = budgeter.fit(state=state, config=cfg, repository_index=repo)

    assert result.was_reduced is False
    assert result.hard_truncated is False
    assert result.reductions == []
    assert result.estimated_tokens_before == normal_tokens
    assert result.estimated_tokens_after == normal_tokens
    assert result.text == normal_text


def test_oversized_context_is_reduced() -> None:
    """When context exceeds budget, reduction stages are applied and tokens are decreased."""
    state = _build_test_task_state(num_obs=8, num_disc=8, num_successful=4, num_failed=4)
    cfg = ContextConfig(include_repository_overview=True, include_symbols=True, include_local_imports=True)
    repo = _build_test_repo_index()

    normal_text = ContextBuilder(config=cfg, repository_index=repo).build(state).render_text()
    initial_tokens = estimate_tokens(normal_text, 4)

    target_budget = initial_tokens - 80
    budgeter = ContextBudgeter(max_context_tokens=target_budget)
    result = budgeter.fit(state=state, config=cfg, repository_index=repo)

    assert result.was_reduced is True
    assert result.estimated_tokens_before == initial_tokens
    assert result.estimated_tokens_after <= target_budget
    assert len(result.reductions) > 0


def test_final_context_always_fits_configured_budget() -> None:
    """Guarantees estimate_tokens(result.text) <= max_tokens across various budget levels."""
    state = _build_test_task_state(num_obs=6, num_disc=6, num_successful=3, num_failed=3)
    repo = _build_test_repo_index()
    cfg = ContextConfig(include_repository_overview=True, include_symbols=True)

    for budget_limit in [500, 300, 200, 150, 100, 50, 20]:
        budgeter = ContextBudgeter(max_context_tokens=budget_limit)
        result = budgeter.fit(state=state, config=cfg, repository_index=repo)
        actual_tokens = estimate_tokens(result.text, chars_per_token=4)
        assert actual_tokens <= budget_limit, f"Failed for limit {budget_limit}: got {actual_tokens}"


def test_telemetry_disappears_before_critical_recovery_evidence() -> None:
    """Stage 1 disables telemetry before any critical recovery evidence is touched."""
    state = _build_test_task_state(num_obs=1, num_disc=1, num_successful=1, num_failed=2)
    cfg = ContextConfig(include_telemetry=True, include_failed_attempts=True, include_repeated_failures=True)

    normal_text = ContextBuilder(config=cfg).build(state).render_text()
    initial_tokens = estimate_tokens(normal_text, 4)

    # Shave off just enough tokens to trigger stage 1
    tight_budget = initial_tokens - 10
    budgeter = ContextBudgeter(max_context_tokens=tight_budget)
    result = budgeter.fit(state=state, config=cfg)

    assert "disable_telemetry" in result.reductions
    assert "## EFFICIENCY" not in result.text
    # Recovery evidence remains intact
    assert "## FAILED ATTEMPTS" in result.text
    assert "## REPEATED FAILURES" in result.text
    assert "## CURRENT ERRORS" in result.text


def test_successful_attempts_disappear_before_failed_attempts() -> None:
    """Successful attempts are dropped in Stage 2 before failed attempts."""
    state = _build_test_task_state(num_obs=1, num_disc=1, num_successful=3, num_failed=3)
    cfg = ContextConfig(include_telemetry=False, include_successful_attempts=True, include_failed_attempts=True)

    normal_text = ContextBuilder(config=cfg).build(state).render_text()
    initial_tokens = estimate_tokens(normal_text, 4)

    # Budget tight enough to disable successful attempts
    tight_budget = initial_tokens - 15
    budgeter = ContextBudgeter(max_context_tokens=tight_budget)
    result = budgeter.fit(state=state, config=cfg)

    assert "disable_successful_attempts" in result.reductions
    # Successful attempts collapsed to (None)
    assert "- Attempt att-succ-" not in result.text
    # Failed attempts still retain full details
    assert "- Attempt att-fail-" in result.text


def test_broad_scope_contracts_to_focused() -> None:
    """BROAD repository scope contracts to FOCUSED when over budget."""
    state = _build_test_task_state()
    repo = _build_test_repo_index()
    cfg = ContextConfig(
        repository_scope=RepositoryScope.BROAD,
        include_telemetry=False,
        include_successful_attempts=False,
        include_symbols=True,
    )

    normal_text = ContextBuilder(config=cfg, repository_index=repo).build(state).render_text()
    initial_tokens = estimate_tokens(normal_text, 4)

    budgeter = ContextBudgeter(max_context_tokens=initial_tokens - 5)
    result = budgeter.fit(state=state, config=cfg, repository_index=repo)

    assert "contract_repository_scope_to_focused" in result.reductions


def test_focused_scope_contracts_to_minimal() -> None:
    """FOCUSED repository scope contracts to MINIMAL when further reduction is needed."""
    state = _build_test_task_state(num_obs=1, num_disc=1)
    repo = _build_test_repo_index()
    cfg = ContextConfig(
        repository_scope=RepositoryScope.FOCUSED,
        include_telemetry=False,
        include_successful_attempts=False,
        include_symbols=True,
        max_discoveries=0,
        max_observations=0,
        max_attempts=0,
    )

    normal_text = ContextBuilder(config=cfg, repository_index=repo).build(state).render_text()
    initial_tokens = estimate_tokens(normal_text, 4)

    budgeter = ContextBudgeter(max_context_tokens=initial_tokens - 5)
    result = budgeter.fit(state=state, config=cfg, repository_index=repo)

    assert "contract_repository_scope_to_minimal" in result.reductions


def test_observations_and_discoveries_shrink_deterministically_preserving_recency() -> None:
    """Halving discovery and observation windows deterministically retains the most recent items."""
    observations = [
        Observation(type="log", source="app", summary=f"Obs item #{i}")
        for i in range(1, 7)  # items 1 through 6
    ]
    discoveries = [
        Discovery(statement=f"Disc item #{i}", evidence="proof")
        for i in range(1, 7)  # items 1 through 6
    ]
    state = TaskState(
        task_id="task-recency",
        task="Test recency preservation",
        recent_observations=observations,
        discoveries=discoveries,
    )
    cfg = ContextConfig(
        max_observations=6,
        max_discoveries=6,
        include_telemetry=False,
        include_successful_attempts=False,
    )

    normal_text = ContextBuilder(config=cfg).build(state).render_text()
    initial_tokens = estimate_tokens(normal_text, 4)

    budgeter = ContextBudgeter(max_context_tokens=initial_tokens - 30)
    result = budgeter.fit(state=state, config=cfg)

    # When window shrinks from 6 to 3, items 4, 5, 6 should survive; items 1, 2, 3 should be dropped
    assert "Obs item #6" in result.text
    assert "Obs item #5" in result.text
    assert "Disc item #6" in result.text
    assert "Disc item #5" in result.text


def test_critical_failure_information_survives_longer_than_low_value_sections() -> None:
    """Repeated failures and active errors survive while auxiliary sections are shed."""
    state = _build_test_task_state(num_obs=4, num_disc=4, num_successful=2, num_failed=2)
    repo = _build_test_repo_index()
    cfg = ContextConfig(
        include_telemetry=True,
        include_successful_attempts=True,
        include_repository_overview=True,
        include_symbols=True,
        include_local_imports=True,
        include_repeated_failures=True,
    )

    normal_text = ContextBuilder(config=cfg, repository_index=repo).build(state).render_text()
    initial_tokens = estimate_tokens(normal_text, 4)

    # Shave off about 50% of the token budget
    budgeter = ContextBudgeter(max_context_tokens=initial_tokens // 2)
    result = budgeter.fit(state=state, config=cfg, repository_index=repo)

    # Low-value sections shed
    assert "## EFFICIENCY" not in result.text
    assert "## REPOSITORY" not in result.text
    assert "## REPOSITORY SYMBOLS" not in result.text
    assert "## LOCAL IMPORTS" not in result.text

    # Critical failure info preserved
    assert "## REPEATED FAILURES" in result.text
    assert "ConnectionTimeout" in result.text
    assert "## CURRENT ERRORS" in result.text


def test_hard_truncation_handles_oversized_core_context() -> None:
    """When the irreducible core context exceeds budget, hard truncation is applied deterministically."""
    huge_task = "A" * 8000
    state = TaskState(task_id="task-huge", task=huge_task)
    cfg = ContextConfig()

    budget_limit = 50  # 50 tokens = 200 chars
    budgeter = ContextBudgeter(max_context_tokens=budget_limit)
    result = budgeter.fit(state=state, config=cfg)

    assert result.hard_truncated is True
    assert "hard_truncation" in result.reductions
    assert TRUNCATION_MARKER in result.text
    assert estimate_tokens(result.text, 4) <= budget_limit


def test_truncation_marker_fits_within_small_limits() -> None:
    """Marker itself must fit even when budget is very small."""
    huge_task = "X" * 1000
    state = TaskState(task_id="task-tiny", task=huge_task)

    for tiny_budget in [1, 2, 5, 8, 12]:
        budgeter = ContextBudgeter(max_context_tokens=tiny_budget)
        result = budgeter.fit(state=state)
        actual_tokens = estimate_tokens(result.text, chars_per_token=4)
        assert actual_tokens <= tiny_budget, f"Failed for tiny budget {tiny_budget}: got {actual_tokens}"


def test_context_budget_result_metadata() -> None:
    """Verify ContextBudgetResult structured fields and to_dict() serialization."""
    state = _build_test_task_state(num_obs=4, num_disc=4)
    cfg = ContextConfig()

    budgeter = ContextBudgeter(max_context_tokens=100)
    result = budgeter.fit(state=state, config=cfg)

    d = result.to_dict()
    assert "text" in d
    assert "estimated_tokens_before" in d
    assert "estimated_tokens_after" in d
    assert "max_tokens" in d
    assert "was_reduced" in d
    assert "hard_truncated" in d
    assert "reductions" in d
    assert isinstance(d["reductions"], list)
    assert d["max_tokens"] == 100


def test_deterministic_reproducibility() -> None:
    """Identical state, config, and budget produce identical output and reduction lists."""
    state = _build_test_task_state(num_obs=5, num_disc=5, num_successful=2, num_failed=2)
    cfg = ContextConfig(include_repository_overview=True, include_symbols=True)
    repo = _build_test_repo_index()

    budgeter = ContextBudgeter(max_context_tokens=250)
    res1 = budgeter.fit(state=state, config=cfg, repository_index=repo)
    res2 = budgeter.fit(state=state, config=cfg, repository_index=repo)

    assert res1.text == res2.text
    assert res1.reductions == res2.reductions
    assert res1.estimated_tokens_before == res2.estimated_tokens_before
    assert res1.estimated_tokens_after == res2.estimated_tokens_after


def test_immutability_of_inputs() -> None:
    """Budgeting must not mutate TaskState, ContextConfig, ContextProfile, or RepositoryIndex."""
    state = _build_test_task_state(num_obs=4, num_disc=4)
    original_state_dict = state.to_dict()

    cfg = ContextConfig(
        include_telemetry=True,
        include_successful_attempts=True,
        include_repository_overview=True,
        max_observations=8,
        max_attempts=5,
    )
    original_cfg_dict = cfg.to_dict()

    repo = _build_test_repo_index()
    original_repo_files = list(repo.discovered_files)

    budgeter = ContextBudgeter(max_context_tokens=120)
    budgeter.fit(state=state, config=cfg, repository_index=repo)

    # Assert completely unmodified
    assert state.to_dict() == original_state_dict
    assert cfg.to_dict() == original_cfg_dict
    assert repo.discovered_files == original_repo_files


# ---------------------------------------------------------------------------
# Integration Test
# ---------------------------------------------------------------------------

def test_recover_high_pressure_integration_with_small_budget() -> None:
    """
    End-to-end integration test specified in prompt:
    RECOVER + HIGH pressure + many observations + many discoveries + failed attempts
    + repository overview + symbols + local imports + deliberately small context budget.

    Verify:
    B4 requests expanded context -> B5 reduces lower-priority context -> recovery evidence remains -> final context fits budget.
    """
    repo = _build_test_repo_index()

    # Step 1: Create MemoryManager with state that produces HIGH pressure in RECOVER phase
    manager = MemoryManager()
    manager.initialize_task("task-recover-integ", "Fix critical regression in payment processing")
    manager.set_phase(Phase.RECOVER)

    # 4 active errors
    manager.set_current_errors([
        "PaymentGatewayTimeout: upstream timed out",
        "InvalidSignature: HMAC mismatch",
        "DatabaseLockError: row locked",
        "ConnectionReset: pipe broken",
    ])

    # Failed verification
    manager.set_verification({
        "success": False,
        "summary": "Payment suite failed with 4 errors",
        "tests_passed": ["test_init"],
        "tests_failed": ["test_charge", "test_refund"],
    })

    # Add 3 failed attempts
    for i in range(1, 4):
        manager.add_attempt(
            Attempt(
                id=f"att-{i}",
                action=f"Refactor step {i}",
                result="Failed with exception",
                success=False,
            )
        )

    # Repeated failure with 4 occurrences
    manager.add_failure(
        Failure(
            error_signature="PaymentGatewayTimeout",
            summary="Gateway timed out after 30s",
            action="charge",
            files=["src/main.py"],
            occurrence_count=4,
        )
    )

    # Add many observations and discoveries
    for i in range(1, 10):
        manager.add_observation(
            Observation(
                type="debug_log",
                source="pytest",
                summary=f"Verbose debug log line #{i} with detailed memory trace",
                raw_output=f"Traceback step {i} in payment gateway execution",
            )
        )
        manager.add_discovery(
            Discovery(
                statement=f"Payment architecture invariant #{i}",
                evidence=f"Confirmed in src/main.py line {i * 20}",
            )
        )

    state = manager.get_state()

    # Step 2: B4 policy evaluation
    policy = AdaptiveContextPolicy()
    profile = policy.evaluate(state, repo)

    # Verify B4 expanded context due to RECOVER + HIGH pressure
    assert profile.phase == "RECOVER"
    assert profile.pressure_level == PressureLevel.HIGH
    assert profile.include_symbols is True
    assert profile.include_local_imports is True
    assert profile.include_repository_overview is True
    assert profile.include_failed_attempts is True
    assert profile.include_repeated_failures is True

    # Step 3: Deliberately small budget (e.g. 250 tokens)
    small_budget = 250
    budgeter = ContextBudgeter(max_context_tokens=small_budget)
    result = budgeter.fit(state=state, config=profile, repository_index=repo)

    # Verify B5 behavior:
    # 1. Budget was reduced
    assert result.was_reduced is True

    # 2. Lower-priority context was reduced first (telemetry, repo overview, symbols, local imports)
    assert "disable_telemetry" in result.reductions
    assert "## EFFICIENCY" not in result.text
    assert "## REPOSITORY SYMBOLS" not in result.text
    assert "## LOCAL IMPORTS" not in result.text

    # 3. Critical recovery evidence remains in text
    assert "PaymentGatewayTimeout" in result.text or "Payment suite failed" in result.text
    assert "## CURRENT ERRORS" in result.text

    # 4. Final context fits budget
    assert result.estimated_tokens_after <= small_budget
    assert estimate_tokens(result.text, chars_per_token=4) <= small_budget
