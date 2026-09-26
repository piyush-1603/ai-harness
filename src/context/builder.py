"""Deterministic context builder deriving model-ready context bundles from TaskState."""

from __future__ import annotations

from typing import Optional, Union
import uuid

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
    VerificationSnapshot,
)

from src.context.relevance import ContextCandidate, RelevanceScorer
from src.context.packing import ContextPacker

def _format_symbols_compact(fs: FileSymbols) -> list[str]:
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
    if not fs.local_imports:
        return []
    lines: list[str] = [fs.path, "  local imports:"]
    for imp in sorted(fs.local_imports):
        lines.append(f"  - {imp}")
    return lines

class ContextBuilder:
    def __init__(
        self,
        config: Optional[ContextConfig] = None,
        repository_index: Optional[RepositoryIndex] = None,
    ) -> None:
        self.config = config or ContextConfig()
        self.repository_index = repository_index
        self.scorer = RelevanceScorer()
        self.packer = ContextPacker()

    def build(
        self,
        state_or_manager: Union[TaskState, MemoryManager],
        config: Optional[ContextConfig] = None,
        repository_index: Optional[RepositoryIndex] = None,
        max_tokens: Optional[int] = None,
    ) -> ContextBundle:
        from src.context.budget import estimate_tokens, DEFAULT_MAX_CONTEXT_TOKENS, DEFAULT_CHARS_PER_TOKEN
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
        
        target_max = max_tokens if max_tokens is not None else (cfg.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS)
        chars_per_token = getattr(cfg, "chars_per_token", DEFAULT_CHARS_PER_TOKEN)

        # 1. Base Task & Lifecycle (core)
        task = state.task
        phase = state.phase
        status = state.status
        plan = list(state.plan)
        current_hypothesis = state.current_hypothesis
        relevant_files = list(state.relevant_files)
        touched_files = list(state.touched_files)

        # Gather eligible items per B4 config
        disc_slice = state.discoveries[-cfg.max_discoveries:] if cfg.max_discoveries > 0 else []
        obs_slice = state.recent_observations[-cfg.max_observations:] if cfg.max_observations > 0 else []
        
        all_failed = [a for a in state.attempts if not a.success]
        failed_slice = all_failed[-cfg.max_attempts:] if (cfg.include_failed_attempts and cfg.max_attempts > 0) else []
        
        all_successful = [a for a in state.attempts if a.success]
        successful_slice = all_successful[-cfg.max_attempts:] if (cfg.include_successful_attempts and cfg.max_attempts > 0) else []

        if cfg.include_repeated_failures:
            filtered_failures = [f for f in state.failures if f.occurrence_count >= cfg.min_failure_occurrences]
            sorted_failures = sorted(filtered_failures, key=lambda f: (1 if getattr(f, "resolved", False) else 0))
        else:
            sorted_failures = []
            
        current_errors = list(state.current_errors)
        
        latest_verification = state.verification if cfg.include_verification else None
        
        # Repository overview & symbols
        repository_overview: Optional[str] = None
        repository_symbols_dict: dict[str, str] = {}
        repository_local_imports_dict: dict[str, str] = {}

        if repo is not None:
            scope_val = cfg.repository_scope
            try:
                scope = RepositoryScope(scope_val) if isinstance(scope_val, str) else scope_val or RepositoryScope.FOCUSED
            except ValueError:
                scope = RepositoryScope.FOCUSED

            if cfg.include_repository_overview:
                overview_lines = []
                if repo.source_directories:
                    overview_lines.append(f"Source Directories: {', '.join(sorted(repo.source_directories))}")
                if repo.test_directories:
                    overview_lines.append(f"Test Directories: {', '.join(sorted(repo.test_directories))}")
                if repo.important_files:
                    overview_lines.append(f"Important Files: {', '.join(sorted(repo.important_files))}")
                if repo.documentation_files:
                    overview_lines.append(f"Documentation Files: {', '.join(sorted(repo.documentation_files))}")
                if repo.source_files or repo.test_files:
                    overview_lines.append(f"Files: {len(repo.source_files)} source, {len(repo.test_files)} test")
                if overview_lines:
                    repository_overview = "\n".join(overview_lines)

            expansion_plan_files = []
            if cfg.include_symbols or cfg.include_local_imports:
                from src.context.expansion import FailureAwareExpander
                from src.context.policy import PressureLevel

                try:
                    pressure = PressureLevel(cfg.pressure_level)
                except ValueError:
                    pressure = PressureLevel.LOW

                expander = FailureAwareExpander(max_expanded_files=getattr(cfg, "max_expanded_files", 20))
                expansion_plan = expander.expand(state, repo, pressure)
                expansion_plan_files = expansion_plan.expanded_files

            focus_files = []
            for f in relevant_files + touched_files + expansion_plan_files:
                if f not in focus_files:
                    focus_files.append(f)

            local_import_neighbors = []
            for f in focus_files:
                fs = repo.file_symbols.get(f)
                if fs and fs.local_imports:
                    if cfg.include_local_imports:
                        formatted_loc = _format_local_imports_compact(fs)
                        if formatted_loc:
                            repository_local_imports_dict[fs.path] = "\n".join(formatted_loc)
                    for imp in fs.local_imports:
                        if imp not in local_import_neighbors and imp not in focus_files:
                            local_import_neighbors.append(imp)

            if cfg.include_symbols:
                eligible_symbol_files = []
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
                            repository_symbols_dict[fs.path] = "\n".join(sym_lines)

        # Build Core Bundle to estimate core tokens
        core_bundle = ContextBundle(
            task=task,
            phase=phase,
            status=status,
            plan=plan,
            current_hypothesis=current_hypothesis,
            relevant_files=relevant_files,
            touched_files=touched_files,
            include_failed_attempts=False,
            include_repeated_failures=False,
            include_verification=False,
            include_telemetry=False,
        )
        core_text = core_bundle.render_text()
        core_tokens = estimate_tokens(core_text, chars_per_token=chars_per_token)

        # Generate ContextCandidates
        candidates = []
        
        for i, d in enumerate(disc_slice):
            evidence_str = f" evidence: {d.evidence}" if d.evidence else ""
            text = f"- [{d.confidence:.2f}] {d.statement}{evidence_str}"
            candidates.append(ContextCandidate(
                id=f"disc_{i}",
                category="discovery",
                text=text,
                estimated_tokens=estimate_tokens(text, chars_per_token=chars_per_token),
                files=list(d.files),
                recency_index=i,
                metadata={"obj": d, "confidence": d.confidence}
            ))
            
        for i, o in enumerate(obs_slice):
            text = f"[{o.type} | {o.source}] {o.summary}"
            if cfg.include_raw_output and o.raw_output:
                text += f"\n{o.raw_output}"
            candidates.append(ContextCandidate(
                id=f"obs_{i}",
                category="observation",
                text=text,
                estimated_tokens=estimate_tokens(text, chars_per_token=chars_per_token),
                files=list(o.files),
                recency_index=i,
                metadata={"obj": o}
            ))
            
        for i, a in enumerate(failed_slice):
            text = f"Attempt {a.id}: {a.action} -> {a.result}"
            candidates.append(ContextCandidate(
                id=f"fail_att_{a.id}",
                category="failed_attempt",
                text=text,
                estimated_tokens=estimate_tokens(text, chars_per_token=chars_per_token),
                files=list(a.files_touched),
                recency_index=i,
                metadata={"obj": a}
            ))
            
        for i, a in enumerate(successful_slice):
            text = f"Attempt {a.id}: {a.action} -> {a.result}"
            candidates.append(ContextCandidate(
                id=f"succ_att_{a.id}",
                category="successful_attempt",
                text=text,
                estimated_tokens=estimate_tokens(text, chars_per_token=chars_per_token),
                files=list(a.files_touched),
                recency_index=i,
                metadata={"obj": a}
            ))
            
        for i, f in enumerate(sorted_failures):
            status_str = " (resolved)" if getattr(f, "resolved", False) else ""
            text = f"[x{f.occurrence_count}] {f.error_signature}: {f.summary}{status_str}"
            candidates.append(ContextCandidate(
                id=f"rep_fail_{i}",
                category="repeated_failure",
                text=text,
                estimated_tokens=estimate_tokens(text, chars_per_token=chars_per_token),
                files=list(f.files),
                metadata={"obj": f, "resolved": getattr(f, "resolved", False)}
            ))
            
        for i, err in enumerate(current_errors):
            candidates.append(ContextCandidate(
                id=f"curr_err_{i}",
                category="current_error",
                text=err,
                estimated_tokens=estimate_tokens(err, chars_per_token=chars_per_token),
                metadata={"obj": err}
            ))
            
        if latest_verification:
            text = f"Success: {latest_verification.success} {latest_verification.summary}"
            candidates.append(ContextCandidate(
                id="verif_0",
                category="verification_result",
                text=text,
                estimated_tokens=estimate_tokens(text, chars_per_token=chars_per_token),
                metadata={"obj": latest_verification, "success": latest_verification.success}
            ))
            
        if repository_overview:
            candidates.append(ContextCandidate(
                id="repo_overview",
                category="repository_overview",
                text=repository_overview,
                estimated_tokens=estimate_tokens(repository_overview, chars_per_token=chars_per_token),
                metadata={"obj": repository_overview}
            ))
            
        if cfg.include_telemetry:
            eff_lines = [
                f"Iteration: {state.iteration}",
                f"Model Calls: {state.model_calls}",
                f"Tool Calls: {state.tool_calls}",
                f"Token Usage: {state.token_usage.total_tokens} total",
            ]
            text = "\n".join(eff_lines)
            candidates.append(ContextCandidate(
                id="telemetry_0",
                category="telemetry",
                text=text,
                estimated_tokens=estimate_tokens(text, chars_per_token=chars_per_token),
                metadata={"obj": "telemetry"}
            ))

        # Symbols
        for i, (path, sym_text) in enumerate(repository_symbols_dict.items()):
            candidates.append(ContextCandidate(
                id=f"sym_{i}",
                category="symbols",
                text=sym_text,
                estimated_tokens=estimate_tokens(sym_text, chars_per_token=chars_per_token),
                files=[path],
                metadata={"path": path, "text": sym_text}
            ))
            
        # Local Imports
        for i, (path, imp_text) in enumerate(repository_local_imports_dict.items()):
            candidates.append(ContextCandidate(
                id=f"imp_{i}",
                category="local_imports",
                text=imp_text,
                estimated_tokens=estimate_tokens(imp_text, chars_per_token=chars_per_token),
                files=[path],
                metadata={"path": path, "text": imp_text}
            ))

        # Score candidates
        scored_candidates = self.scorer.score_candidates(state, candidates)
        
        # Pack candidates
        selected, report = self.packer.pack(core_tokens, scored_candidates, target_max)
        
        # Reconstruct Bundle from selected candidates
        out_disc = [c.metadata["obj"] for c in selected if c.category == "discovery"]
        # Maintain chronological order by sorting by recency_index
        out_disc.sort(key=lambda c: (candidates.index(next(x for x in candidates if x.id == c.id)) if hasattr(c, "id") else 0)) 
        # Wait, the sorting of original items is easier:
        out_disc = [d for d in disc_slice if any(c.category == "discovery" and c.metadata["obj"] is d for c in selected)]
        out_obs = [o for o in obs_slice if any(c.category == "observation" and c.metadata["obj"] is o for c in selected)]
        out_fail_att = [a for a in failed_slice if any(c.category == "failed_attempt" and c.metadata["obj"] is a for c in selected)]
        out_succ_att = [a for a in successful_slice if any(c.category == "successful_attempt" and c.metadata["obj"] is a for c in selected)]
        out_rep_fail = [f for f in sorted_failures if any(c.category == "repeated_failure" and c.metadata["obj"] is f for c in selected)]
        out_err = [e for e in current_errors if any(c.category == "current_error" and c.metadata["obj"] == e for c in selected)]
        
        out_verif = None
        if any(c.category == "verification_result" for c in selected):
            out_verif = VerificationSnapshot(
                success=latest_verification.success,
                summary=latest_verification.summary,
                tests_passed=latest_verification.tests_passed,
                tests_failed=latest_verification.tests_failed,
                details=latest_verification.details,
                timestamp=latest_verification.timestamp,
            )
            
        out_repo_ov = None
        if any(c.category == "repository_overview" for c in selected):
            out_repo_ov = repository_overview
            
        out_syms = [c.metadata["text"] for c in selected if c.category == "symbols"]
        # keep deterministic order
        out_syms.sort()
        
        out_imps = [c.metadata["text"] for c in selected if c.category == "local_imports"]
        out_imps.sort()
        
        inc_telemetry = any(c.category == "telemetry" for c in selected)
        if inc_telemetry:
            out_iteration = state.iteration
            out_model = state.model_calls
            out_tool = state.tool_calls
            out_token = TokenUsage(
                input_tokens=state.token_usage.input_tokens,
                output_tokens=state.token_usage.output_tokens,
                total_tokens=state.token_usage.total_tokens,
            )
        else:
            out_iteration = 0
            out_model = 0
            out_tool = 0
            out_token = TokenUsage()
            
        # Map attempts and discoveries appropriately
        important_discoveries = [
            Discovery(
                statement=d.statement,
                evidence=d.evidence,
                files=list(d.files),
                confidence=d.confidence,
                timestamp=d.timestamp,
            ) for d in out_disc
        ]
        
        recent_observations = [
            Observation(
                type=o.type,
                source=o.source,
                summary=o.summary,
                raw_output=o.raw_output if cfg.include_raw_output else None,
                output_ref=o.output_ref if cfg.include_raw_output else None,
                raw_output_chars=o.raw_output_chars,
                files=list(o.files),
                timestamp=o.timestamp,
            ) for o in out_obs
        ]
        
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
            ) for a in out_fail_att
        ]
        
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
            ) for a in out_succ_att
        ]
        
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
            ) for f in out_rep_fail
        ]

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
            current_errors=out_err,
            latest_verification_result=out_verif,
            iteration_count=out_iteration,
            model_call_count=out_model,
            tool_call_count=out_tool,
            token_usage=out_token,
            repository_overview=out_repo_ov,
            repository_symbols=out_syms,
            repository_local_imports=out_imps,
            include_failed_attempts=cfg.include_failed_attempts,
            include_repeated_failures=cfg.include_repeated_failures,
            include_verification=cfg.include_verification,
            include_telemetry=inc_telemetry,
        )

    def render_text(
        self,
        state_or_manager: Union[TaskState, MemoryManager],
        config: Optional[ContextConfig] = None,
        repository_index: Optional[RepositoryIndex] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        bundle = self.build(state_or_manager, config=config, repository_index=repository_index, max_tokens=max_tokens)
        return bundle.render_text()
