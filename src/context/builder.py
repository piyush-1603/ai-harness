"""Deterministic context builder deriving model-ready context bundles from TaskState."""

from __future__ import annotations

from typing import Optional, Union

from src.context.bundle import ContextBundle
from src.context.config import ContextConfig
from src.context.policy import RepositoryScope
from src.context.scanner import RepositoryIndex
from src.context.symbols import FileSymbols
from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Discovery,
    Failure,
    Observation,
    TaskState,
    TokenUsage,
    VerificationResult,
)


def _format_symbols_compact(fs: FileSymbols) -> list[str]:
    """
    Format symbols of a file into compact, deterministic lines without imports.
    """
    lines: list[str] = [fs.path]
    classes: dict[str, list[str]] = {}
    top_level_fns: list[str] = []
    other_syms: list[str] = []

    for s in fs.symbols:
        if s.kind == "class":
            if s.name not in classes:
                classes[s.name] = []
        elif s.kind in ("method", "async_method"):
            if s.parent:
                classes.setdefault(s.parent, []).append(s.name)
            else:
                top_level_fns.append(s.name)
        elif s.kind in ("function", "async_function"):
            top_level_fns.append(s.name)
        else:
            other_syms.append(s.name)

    for cls_name in sorted(classes.keys()):
        lines.append(f"  class: {cls_name}")
        methods = classes[cls_name]
        if methods:
            lines.append(f"  methods: {', '.join(methods)}")

    if top_level_fns:
        lines.append(f"  functions: {', '.join(top_level_fns)}")

    if other_syms:
        lines.append(f"  symbols: {', '.join(other_syms)}")

    if len(lines) == 1:
        return []
    return lines


def _format_local_imports_compact(fs: FileSymbols) -> list[str]:
    """
    Format resolved local imports for a file into bulleted lines.
    """
    if not fs.local_imports:
        return []
    lines: list[str] = [fs.path, "  local imports:"]
    for imp in sorted(fs.local_imports):
        lines.append(f"  - {imp}")
    return lines


class ContextBuilder:
    """
    Constructs a deterministic, compact ContextBundle from TaskState or MemoryManager.
    Never mutates the underlying TaskState.
    """

    def __init__(
        self,
        config: Optional[ContextConfig] = None,
        repository_index: Optional[RepositoryIndex] = None,
    ) -> None:
        self.config = config or ContextConfig()
        self.repository_index = repository_index

    def build(
        self,
        state_or_manager: Union[TaskState, MemoryManager],
        config: Optional[ContextConfig] = None,
        repository_index: Optional[RepositoryIndex] = None,
    ) -> ContextBundle:
        """
        Derives a model-ready ContextBundle from the given state.
        Guarantees that state_or_manager is not mutated.
        """
        if hasattr(state_or_manager, "get_state"):
            state = state_or_manager.get_state()
        elif isinstance(state_or_manager, TaskState):
            state = state_or_manager
        else:
            raise TypeError(
                f"Expected TaskState or MemoryManager, got {type(state_or_manager).__name__}"
            )

        cfg = config or self.config
        repo = repository_index if repository_index is not None else self.repository_index

        # 1. Base Task & Lifecycle (copies of primitives/collections)
        task = state.task
        phase = state.phase
        status = state.status
        plan = list(state.plan)
        current_hypothesis = state.current_hypothesis
        relevant_files = list(state.relevant_files)
        touched_files = list(state.touched_files)

        # 2. Discoveries (stable learned facts, bounded by max_discoveries)
        disc_slice = (
            state.discoveries[-cfg.max_discoveries :]
            if cfg.max_discoveries > 0
            else []
        )
        important_discoveries = [
            Discovery(
                statement=d.statement,
                evidence=d.evidence,
                files=list(d.files),
                confidence=d.confidence,
                timestamp=d.timestamp,
            )
            for d in disc_slice
        ]

        # 3. Recent Observations (recency limited, raw_output omitted by default)
        obs_slice = (
            state.recent_observations[-cfg.max_observations :]
            if cfg.max_observations > 0
            else []
        )
        recent_observations = [
            Observation(
                type=o.type,
                source=o.source,
                summary=o.summary,
                raw_output=o.raw_output if cfg.include_raw_output else None,
                files=list(o.files),
                timestamp=o.timestamp,
            )
            for o in obs_slice
        ]

        # 4. Attempts (independent bounded recency for failed vs successful attempts)
        if cfg.include_failed_attempts and cfg.max_attempts > 0:
            all_failed = [a for a in state.attempts if not a.success]
            failed_slice = all_failed[-cfg.max_attempts :]
            failed_attempts = [
                Attempt(
                    id=a.id,
                    hypothesis=a.hypothesis,
                    action=a.action,
                    files_touched=list(a.files_touched),
                    result=a.result,
                    success=a.success,
                    iteration=a.iteration,
                    timestamp=a.timestamp,
                )
                for a in failed_slice
            ]
        else:
            failed_attempts = []

        if cfg.include_successful_attempts and cfg.max_attempts > 0:
            all_successful = [a for a in state.attempts if a.success]
            successful_slice = all_successful[-cfg.max_attempts :]
            recent_attempts = [
                Attempt(
                    id=a.id,
                    hypothesis=a.hypothesis,
                    action=a.action,
                    files_touched=list(a.files_touched),
                    result=a.result,
                    success=a.success,
                    iteration=a.iteration,
                    timestamp=a.timestamp,
                )
                for a in successful_slice
            ]
        else:
            recent_attempts = []

        # 5. Repeated Failures (occurrence_count >= min_failure_occurrences, active preferred)
        if cfg.include_repeated_failures:
            filtered_failures = [
                f for f in state.failures
                if f.occurrence_count >= cfg.min_failure_occurrences
            ]
            sorted_failures = sorted(
                filtered_failures,
                key=lambda f: (1 if getattr(f, "resolved", False) else 0)
            )
            repeated_failures = [
                Failure(
                    error_signature=f.error_signature,
                    summary=f.summary,
                    action=f.action,
                    files=list(f.files),
                    occurrence_count=f.occurrence_count,
                    first_seen=f.first_seen,
                    last_seen=f.last_seen,
                    resolved=getattr(f, "resolved", False),
                    resolved_at=getattr(f, "resolved_at", None),
                )
                for f in sorted_failures
            ]
        else:
            repeated_failures = []

        # 6. Current Active Errors
        current_errors = list(state.current_errors)

        # 7. Latest Verification Result
        latest_verification = None
        if cfg.include_verification and state.verification is not None:
            latest_verification = VerificationResult(
                success=state.verification.success,
                summary=state.verification.summary,
                tests_passed=state.verification.tests_passed,
                tests_failed=state.verification.tests_failed,
                details=state.verification.details,
                timestamp=state.verification.timestamp,
            )

        # 8. Telemetry & Budget
        if cfg.include_telemetry:
            iteration_count = state.iteration
            model_call_count = state.model_calls
            tool_call_count = state.tool_calls
            token_usage = TokenUsage(
                input_tokens=state.token_usage.input_tokens,
                output_tokens=state.token_usage.output_tokens,
                total_tokens=state.token_usage.total_tokens,
            )
        else:
            iteration_count = 0
            model_call_count = 0
            tool_call_count = 0
            token_usage = TokenUsage()

        # 9. Repository Overview, Symbols, Local Imports
        repository_overview: Optional[str] = None
        repository_symbols: list[str] = []
        repository_local_imports: list[str] = []

        if repo is not None:
            scope_val = cfg.repository_scope
            if isinstance(scope_val, str):
                try:
                    scope = RepositoryScope(scope_val)
                except ValueError:
                    scope = RepositoryScope.FOCUSED
            elif scope_val is not None:
                scope = scope_val
            else:
                scope = RepositoryScope.FOCUSED

            # Overview
            if cfg.include_repository_overview:
                overview_lines: list[str] = []
                if repo.source_directories:
                    src_dirs = ", ".join(sorted(repo.source_directories))
                    overview_lines.append(f"Source Directories: {src_dirs}")
                if repo.test_directories:
                    test_dirs = ", ".join(sorted(repo.test_directories))
                    overview_lines.append(f"Test Directories: {test_dirs}")
                if repo.important_files:
                    imp_files = ", ".join(sorted(repo.important_files))
                    overview_lines.append(f"Important Files: {imp_files}")
                if repo.documentation_files:
                    doc_files = ", ".join(sorted(repo.documentation_files))
                    overview_lines.append(f"Documentation Files: {doc_files}")
                if repo.source_files or repo.test_files:
                    overview_lines.append(f"Files: {len(repo.source_files)} source, {len(repo.test_files)} test")
                if overview_lines:
                    repository_overview = "\n".join(overview_lines)

            # Focus files (relevant + touched, deduplicated)
            focus_files = []
            for f in relevant_files + touched_files:
                if f not in focus_files:
                    focus_files.append(f)

            # Local-import neighbors
            local_import_neighbors: list[str] = []
            for f in focus_files:
                fs = repo.file_symbols.get(f)
                if fs and fs.local_imports:
                    if cfg.include_local_imports:
                        formatted_loc = _format_local_imports_compact(fs)
                        if formatted_loc:
                            repository_local_imports.append("\n".join(formatted_loc))
                    for imp in fs.local_imports:
                        if imp not in local_import_neighbors and imp not in focus_files:
                            local_import_neighbors.append(imp)

            # Symbols
            if cfg.include_symbols:
                eligible_symbol_files: list[str] = []
                # In all scopes, focus files come first
                for f in focus_files:
                    if f in repo.file_symbols and f not in eligible_symbol_files:
                        eligible_symbol_files.append(f)

                if scope in (RepositoryScope.FOCUSED, RepositoryScope.BROAD):
                    for f in local_import_neighbors:
                        if f in repo.file_symbols and f not in eligible_symbol_files:
                            eligible_symbol_files.append(f)

                if scope == RepositoryScope.BROAD:
                    for f in sorted(repo.file_symbols.keys()):
                        if f not in eligible_symbol_files:
                            eligible_symbol_files.append(f)
                        if len(eligible_symbol_files) >= 15:
                            break

                for f in eligible_symbol_files:
                    fs = repo.file_symbols.get(f)
                    if fs:
                        sym_lines = _format_symbols_compact(fs)
                        if sym_lines:
                            repository_symbols.append("\n".join(sym_lines))

        return ContextBundle(
            task=task,
            phase=phase,
            status=status,
            plan=plan,
            current_hypothesis=current_hypothesis,
            relevant_files=relevant_files,
            touched_files=touched_files,
            important_discoveries=important_discoveries,
            recent_observations=recent_observations,
            recent_attempts=recent_attempts,
            failed_attempts=failed_attempts,
            repeated_failures=repeated_failures,
            current_errors=current_errors,
            latest_verification_result=latest_verification,
            iteration_count=iteration_count,
            model_call_count=model_call_count,
            tool_call_count=tool_call_count,
            token_usage=token_usage,
            repository_overview=repository_overview,
            repository_symbols=repository_symbols,
            repository_local_imports=repository_local_imports,
            include_failed_attempts=cfg.include_failed_attempts,
            include_repeated_failures=cfg.include_repeated_failures,
            include_verification=cfg.include_verification,
            include_telemetry=cfg.include_telemetry,
        )

    def render_text(
        self,
        state_or_manager: Union[TaskState, MemoryManager],
        config: Optional[ContextConfig] = None,
        repository_index: Optional[RepositoryIndex] = None,
    ) -> str:
        """
        Builds a bundle and returns its deterministic text representation.
        """
        bundle = self.build(state_or_manager, config=config, repository_index=repository_index)
        return bundle.render_text()
