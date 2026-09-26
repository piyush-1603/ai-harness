from dataclasses import dataclass, field
import datetime

from src.memory.models import (
    TaskState,
    TaskStatus,
    Phase,
    Failure,
    Attempt,
    Discovery,
    Observation,
    VerificationSnapshot,
)
from src.context.scanner import RepositoryIndex
from src.context.symbols import FileSymbols, SymbolRecord

@dataclass
class Scenario:
    name: str
    description: str
    state: TaskState
    repo: RepositoryIndex
    ground_truth_relevant: list[str]
    max_tokens: int = 8000

def _get_timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def build_noisy_repository() -> RepositoryIndex:
    # Construct an auth-focused repository graph plus many unrelated files

    fs_routes = FileSymbols(path="src/routes.py", language="python", local_imports=["src/auth.py"], symbols=[SymbolRecord(name="dummy", kind="function", file="dummy")])
    fs_auth = FileSymbols(path="src/auth.py", language="python", local_imports=["src/token.py"], symbols=[SymbolRecord(name="dummy", kind="function", file="dummy")])
    fs_token = FileSymbols(path="src/token.py", language="python", local_imports=["src/crypto.py"], symbols=[SymbolRecord(name="dummy", kind="function", file="dummy")])
    fs_crypto = FileSymbols(path="src/crypto.py", language="python", local_imports=[], symbols=[SymbolRecord(name="dummy", kind="function", file="dummy")])

    noisy_files = ["src/profile.py", "src/payments.py", "src/search.py", "src/email.py", "src/analytics.py"]
    fs_noise = {f: FileSymbols(path=f, language="python", local_imports=[], symbols=[SymbolRecord(name="dummy", kind="function", file="dummy")]) for f in noisy_files}

    file_symbols = {
        "src/routes.py": fs_routes,
        "src/auth.py": fs_auth,
        "src/token.py": fs_token,
        "src/crypto.py": fs_crypto,
        **fs_noise,
    }

    return RepositoryIndex(
        root_dir="/tmp/bench",
        file_count=len(file_symbols),
        source_directories=["src"],
        source_files=list(file_symbols.keys()),
        file_symbols=file_symbols,
    )

def create_scenarios() -> list[Scenario]:
    scenarios = []

    # Base noisy repo
    repo = build_noisy_repository()

    # Scenario 1: Normal execution
    s1_state = TaskState(
        task_id="s1",
        task="Normal task execution",
        phase=Phase.EXECUTE,
        status=TaskStatus.RUNNING,
        touched_files=["src/auth.py"],
        relevant_files=["src/token.py"],
        discoveries=[Discovery(statement="Found auth", evidence="Found auth", timestamp=_get_timestamp(), files=["src/auth.py"])],
        recent_observations=[Observation(type="log", source="stdout", summary="Normal log", timestamp=_get_timestamp(), files=[])],
    )
    scenarios.append(Scenario(
        name="Scenario 1: Normal execution",
        description="phase = EXECUTE, pressure LOW, no errors",
        state=s1_state,
        repo=repo,
        ground_truth_relevant=["src/auth.py", "src/token.py"],
    ))

    # Scenario 2: First failure
    s2_state = TaskState(
        task_id="s2",
        task="Fix auth failure",
        phase=Phase.EXECUTE,
        status=TaskStatus.RUNNING,
        touched_files=["src/auth.py"],
        relevant_files=["src/token.py"],
        attempts=[Attempt(id="a1", hypothesis="H1", action="A", result="Fail", success=False, files_touched=["src/auth.py"])],
        current_errors=["ExpectedError: Invalid token"],
        verification=VerificationSnapshot(success=False, summary="Test failed", timestamp=_get_timestamp()),
    )
    scenarios.append(Scenario(
        name="Scenario 2: First failure",
        description="verification fails, current error appears, one failed attempt",
        state=s2_state,
        repo=repo,
        ground_truth_relevant=["src/auth.py", "src/token.py"],
    ))

    # Scenario 3: Repeated failure
    s3_state = TaskState(
        task_id="s3",
        task="Fix auth failure again",
        phase=Phase.EXECUTE,
        status=TaskStatus.RUNNING,
        touched_files=["src/auth.py"],
        relevant_files=["src/token.py"],
        attempts=[
            Attempt(id="a1", hypothesis="H1", action="A", result="Fail", success=False, files_touched=["src/auth.py"]),
            Attempt(id="a2", hypothesis="H1", action="A2", result="Fail", success=False, files_touched=["src/auth.py"]),
        ],
        current_errors=["ExpectedError: Invalid token"],
        verification=VerificationSnapshot(success=False, summary="Test failed", timestamp=_get_timestamp()),
        failures=[Failure(error_signature="ExpectedError", summary="Fails on auth", action="A", occurrence_count=2, files=["src/auth.py"])],
    )
    scenarios.append(Scenario(
        name="Scenario 3: Repeated failure",
        description="repeated unresolved failure, HIGH pressure",
        state=s3_state,
        repo=repo,
        # Expansion goes 2 levels: auth -> routes (reverse), auth -> token -> crypto (forward)
        ground_truth_relevant=["src/auth.py", "src/token.py", "src/crypto.py", "src/routes.py"],
    ))

    # Scenario 4: Recovery contraction
    s4_state = TaskState(
        task_id="s4",
        task="Recovered from failure",
        phase=Phase.EXECUTE,
        status=TaskStatus.RUNNING,
        touched_files=["src/auth.py"],
        relevant_files=["src/token.py"],
        attempts=[
            Attempt(id="a1", hypothesis="H1", action="A", result="Fail", success=False, files_touched=["src/auth.py"]),
            Attempt(id="a2", hypothesis="H2", action="A2", result="Fail", success=False, files_touched=["src/auth.py"]),
            Attempt(id="a3", hypothesis="H3", action="A3", result="Success", success=True, files_touched=["src/auth.py"]),
        ],
        current_errors=[],
        verification=VerificationSnapshot(success=True, summary="Test passed", timestamp=_get_timestamp()),
        failures=[Failure(error_signature="ExpectedError", summary="Fails on auth", action="A", occurrence_count=2, files=["src/auth.py"], resolved=True, resolved_at=_get_timestamp())],
    )
    scenarios.append(Scenario(
        name="Scenario 4: Recovery contraction",
        description="contracts after recovery",
        state=s4_state,
        repo=repo,
        ground_truth_relevant=["src/auth.py", "src/token.py"],
    ))

    # Scenario 5: Tight token budget
    s5_state = TaskState(
        task_id="s5",
        task="Tight budget test",
        phase=Phase.EXECUTE,
        status=TaskStatus.RUNNING,
        touched_files=["src/auth.py"],
        relevant_files=["src/token.py"],
        attempts=[
            Attempt(id="a1", hypothesis="H1", action="A", result="Fail", success=False, files_touched=["src/auth.py"]),
            Attempt(id="a2", hypothesis="H2", action="A2", result="Fail", success=False, files_touched=["src/auth.py"]),
        ],
        current_errors=["ExpectedError: Tight budget error"],
        verification=VerificationSnapshot(success=False, summary="Test failed", timestamp=_get_timestamp()),
        failures=[Failure(error_signature="ExpectedError", summary="Fails on auth", action="A", occurrence_count=2, files=["src/auth.py"])],
        discoveries=[Discovery(statement="Discovery", evidence="Evidence", files=["src/auth.py"], timestamp=_get_timestamp()) for _ in range(20)],
        recent_observations=[Observation(type="log", source="stdout", summary="Log", timestamp=_get_timestamp()) for _ in range(20)],
    )
    scenarios.append(Scenario(
        name="Scenario 5: Tight token budget",
        description="deliberately constrained budget",
        state=s5_state,
        repo=repo,
        ground_truth_relevant=["src/auth.py", "src/token.py", "src/crypto.py", "src/routes.py"],
        max_tokens=150, # Extremely tight
    ))

    # Scenario 6: Noisy repository
    # We already built the noisy repo, we just want to check irrelevant files are filtered.
    s6_state = TaskState(
        task_id="s6",
        task="Noisy repo execution",
        phase=Phase.EXECUTE,
        status=TaskStatus.RUNNING,
        touched_files=["src/auth.py"],
        relevant_files=[],
        attempts=[],
        current_errors=["ExpectedError: Auth fails in noise"],
        failures=[Failure(error_signature="ExpectedError", summary="Auth fails", action="A", occurrence_count=1, files=["src/auth.py"])],
    )
    scenarios.append(Scenario(
        name="Scenario 6: Noisy repository",
        description="measure relevant vs irrelevant repository context",
        state=s6_state,
        repo=repo,
        ground_truth_relevant=["src/auth.py", "src/token.py", "src/routes.py"], # Depth 1 because occurence=1
    ))

    return scenarios

def create_scenarios_extended(tmp_path=None) -> list[Scenario]:
    scenarios = create_scenarios()
    repo = scenarios[0].repo

    # Scenario 7: Large tool output
    # This scenario simulates a huge log output.
    # For Mode A/B it is kept fully inline (simulated by not using MemoryManager in benchmarks if we just pass state directly).
    # Wait, Mode C requires externalization. We can do that by creating an observation.

    # Let's create an observation with a huge text
    large_text = "VERBOSE_LOG_LINE\\n" * 2000 # 34,000 chars

    s7_state = TaskState(
        task_id="s7",
        task="Analyze huge log",
        phase=Phase.EXECUTE,
        status=TaskStatus.RUNNING,
        touched_files=["src/auth.py"],
        relevant_files=["src/token.py"],
        attempts=[],
        current_errors=[],
        discoveries=[],
        recent_observations=[Observation(type="log", source="stdout", summary="Huge log", raw_output=large_text, timestamp=_get_timestamp())],
    )

    # If tmp_path is provided, we can pass it through a MemoryManager to get the Mode C behavior!
    if tmp_path:
        from src.memory.manager import MemoryManager
        manager = MemoryManager(base_dir=tmp_path)
        manager._state = s7_state # wait, better to initialize task and add observation
        manager.initialize_task("s7", "Analyze huge log")
        manager.get_state().touched_files = ["src/auth.py"]
        manager.get_state().relevant_files = ["src/token.py"]

        # Add the huge observation using the manager so it gets externalized!
        manager.add_observation(Observation(type="log", source="stdout", summary="Huge log", raw_output=large_text, timestamp=_get_timestamp()))

        s7_state = manager.get_state()

    scenarios.append(Scenario(
        name="Scenario 7: Large tool output",
        description="huge raw output testing artifact bounds",
        state=s7_state,
        repo=repo,
        ground_truth_relevant=["src/auth.py"],
        max_tokens=2000, # Large output will exceed this if inline
    ))

    return scenarios
