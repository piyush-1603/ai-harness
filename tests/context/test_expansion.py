import pytest

from src.context.expansion import FailureAwareExpander, ExpansionPlan
from src.context.policy import PressureLevel, ContextProfile, RepositoryScope
from src.context.scanner import RepositoryIndex, FileSymbols
from src.context.config import ContextConfig
from src.context.builder import ContextBuilder
from src.context.bundle import ContextBundle
from src.memory.models import TaskState, Failure, Attempt, Observation, Phase


@pytest.fixture
def repo_index() -> RepositoryIndex:
    # src/routes.py -> src/auth.py -> src/token.py -> src/crypto.py
    # src/profile.py is unrelated
    from src.context.symbols import SymbolRecord
    dummy = SymbolRecord(name="dummy", kind="function", file="dummy.py")
    
    fs_routes = FileSymbols(path="src/routes.py", language="python", local_imports=["src/auth.py"], symbols=[dummy])
    fs_auth = FileSymbols(path="src/auth.py", language="python", local_imports=["src/token.py"], symbols=[dummy])
    fs_token = FileSymbols(path="src/token.py", language="python", local_imports=["src/crypto.py"], symbols=[dummy])
    fs_crypto = FileSymbols(path="src/crypto.py", language="python", local_imports=[], symbols=[dummy])
    fs_profile = FileSymbols(path="src/profile.py", language="python", local_imports=[], symbols=[dummy])
    
    return RepositoryIndex(
        root_dir="/tmp",
        file_count=5,
        source_directories=["src"],
        source_files=["src/routes.py", "src/auth.py", "src/token.py", "src/crypto.py", "src/profile.py"],
        file_symbols={
            "src/routes.py": fs_routes,
            "src/auth.py": fs_auth,
            "src/token.py": fs_token,
            "src/crypto.py": fs_crypto,
            "src/profile.py": fs_profile,
        }
    )


@pytest.fixture
def base_state() -> TaskState:
    return TaskState(task_id="test1", task="Test Task")


def test_seed_selection_and_resolved_exclusion(base_state: TaskState):
    # Setup state with different sources of seeds
    base_state.failures = [
        Failure(error_signature="Err1", summary="Unresolved", action="A1", files=["src/auth.py"]),
        Failure(error_signature="Err2", summary="Resolved", action="A2", files=["src/profile.py"], resolved=True)
    ]
    base_state.attempts = [
        Attempt(id="A3", hypothesis="H1", action="Act1", files_touched=["src/token.py"], result="Fail", success=False)
    ]
    base_state.touched_files = ["src/routes.py"]
    
    expander = FailureAwareExpander()
    seeds = expander._determine_seeds(base_state)
    
    # Expected order: unresolved failures (src/auth.py), failed attempts (src/token.py), touched files (src/routes.py)
    # Profile should be excluded because it's resolved.
    assert seeds == ["src/auth.py", "src/token.py", "src/routes.py"]


def test_expansion_depths(base_state: TaskState, repo_index: RepositoryIndex):
    base_state.failures = [Failure(error_signature="E", summary="E", action="A", files=["src/auth.py"])]
    expander = FailureAwareExpander()
    
    # LOW -> depth 0
    plan_low = expander.expand(base_state, repo_index, PressureLevel.LOW)
    assert plan_low.depth == 0
    assert plan_low.expanded_files == ["src/auth.py"]
    
    # MEDIUM -> depth 1
    plan_med = expander.expand(base_state, repo_index, PressureLevel.MEDIUM)
    assert plan_med.depth == 1
    assert "src/auth.py" in plan_med.expanded_files
    assert "src/token.py" in plan_med.expanded_files  # forward
    assert "src/routes.py" in plan_med.expanded_files # reverse
    assert "src/crypto.py" not in plan_med.expanded_files
    
    # HIGH -> depth 2
    plan_high = expander.expand(base_state, repo_index, PressureLevel.HIGH)
    assert plan_high.depth == 2
    assert set(plan_high.expanded_files) == {"src/auth.py", "src/token.py", "src/routes.py", "src/crypto.py"}


def test_deterministic_ordering(base_state: TaskState, repo_index: RepositoryIndex):
    base_state.failures = [Failure(error_signature="E", summary="E", action="A", files=["src/auth.py"])]
    expander = FailureAwareExpander()
    
    plan_med1 = expander.expand(base_state, repo_index, PressureLevel.MEDIUM)
    plan_med2 = expander.expand(base_state, repo_index, PressureLevel.MEDIUM)
    
    assert plan_med1.expanded_files == plan_med2.expanded_files


def test_max_expansion_cap(base_state: TaskState, repo_index: RepositoryIndex):
    base_state.failures = [Failure(error_signature="E", summary="E", action="A", files=["src/auth.py"])]
    expander = FailureAwareExpander(max_expanded_files=2)
    
    plan = expander.expand(base_state, repo_index, PressureLevel.HIGH)
    # Should only expand 2 files despite HIGH pressure
    assert len(plan.expanded_files) == 2


def test_contraction_lifecycle(base_state: TaskState, repo_index: RepositoryIndex):
    base_state.failures = [Failure(error_signature="E", summary="E", action="A", files=["src/auth.py"])]
    expander = FailureAwareExpander()
    
    # Pressure increases -> expands
    plan_high = expander.expand(base_state, repo_index, PressureLevel.HIGH)
    assert len(plan_high.expanded_files) == 4
    
    # Pressure drops -> contracts
    plan_low = expander.expand(base_state, repo_index, PressureLevel.LOW)
    assert len(plan_low.expanded_files) == 1
    assert plan_low.expanded_files == ["src/auth.py"]


def test_builder_integration(base_state: TaskState, repo_index: RepositoryIndex):
    base_state.failures = [Failure(error_signature="E", summary="E", action="A", files=["src/auth.py"])]
    
    config = ContextConfig(include_symbols=True, pressure_level="HIGH", repository_scope=RepositoryScope.MINIMAL)
    builder = ContextBuilder(config=config, repository_index=repo_index)
    
    bundle = builder.build(base_state)
    syms_text = "".join(bundle.repository_symbols)
    
    # Even with MINIMAL scope, the HIGH pressure expansion should force the neighborhood into symbols
    assert "src/auth.py" in syms_text
    assert "src/token.py" in syms_text
    assert "src/routes.py" in syms_text
    assert "src/crypto.py" in syms_text


def test_budget_integration(base_state: TaskState, repo_index: RepositoryIndex):
    # Provide very small budget to ensure B5.1 packer still truncates expanded files if needed
    base_state.failures = [Failure(error_signature="E", summary="E", action="A", files=["src/auth.py"])]
    config = ContextConfig(include_symbols=True, pressure_level="HIGH")
    builder = ContextBuilder(config=config, repository_index=repo_index)
    
    # Force max tokens very low so it can't include all symbols
    bundle = builder.build(base_state, max_tokens=100) # Extremely low
    
    # It shouldn't crash, and should include limited symbols
    # We don't exactly know how many fit, but packer should respect the limit
    assert bundle is not None


def test_immutability(base_state: TaskState, repo_index: RepositoryIndex):
    base_state.failures = [Failure(error_signature="E", summary="E", action="A", files=["src/auth.py"])]
    base_state.plan = ["Step 1"]
    
    original_failures = list(base_state.failures)
    original_plan = list(base_state.plan)
    
    expander = FailureAwareExpander()
    expander.expand(base_state, repo_index, PressureLevel.HIGH)
    
    assert base_state.failures == original_failures
    assert base_state.plan == original_plan
