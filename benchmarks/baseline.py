from src.memory.models import TaskState
from src.context.scanner import RepositoryIndex
from src.context.config import ContextConfig
from src.context.policy import RepositoryScope
from src.context.builder import ContextBuilder

class StaticContextBaseline:
    """
    Fair static-context baseline against the adaptive Person B pipeline.
    Uses identical task state, repository data, and token budgets.
    Does NOT use: pressure-dependent behavior, AdaptiveContextPolicy, FailureAwareExpander.
    """
    
    def __init__(self):
        self.config = ContextConfig(
            max_observations=8,
            max_attempts=5,
            max_discoveries=10,
            repository_scope=RepositoryScope.FOCUSED,
            include_symbols=True,
            include_local_imports=True,
            include_repository_overview=False,
            include_successful_attempts=True,
            include_failed_attempts=True,
            include_repeated_failures=True,
            include_verification=True,
            include_telemetry=True,
            # No pressure dependent config!
        )
        
    def build_context(self, state: TaskState, repo: RepositoryIndex, max_tokens: int) -> str:
        # We use the existing ContextBuilder, but bypass the adaptive policy and expander
        # Since ContextBuilder internally calls FailureAwareExpander if include_symbols=True,
        # we need to provide a clean builder that doesn't use the expander for the baseline.
        
        # Wait, if ContextBuilder internally instantiates FailureAwareExpander...
        # We need to temporarily patch or avoid it.
        # But wait, the requirements say:
        # "Static baseline must NOT use: ... FailureAwareExpander ... relevance scoring ... token-aware candidate ranking"
        # Since we must avoid ContextBuilder's RelevanceScorer, ContextPacker, and FailureAwareExpander,
        # we should build the baseline bundle directly!
        
        # Or, we can just use the naive baseline logic.
        from src.context.bundle import ContextBundle
        from src.memory.models import TokenUsage
        from src.context.budget import estimate_tokens
        
        # 1. Base Task & Lifecycle (core)
        task = state.task
        phase = state.phase
        status = state.status
        plan = list(state.plan)
        current_hypothesis = state.current_hypothesis
        relevant_files = list(state.relevant_files)
        touched_files = list(state.touched_files)
        
        focus_files = []
        for f in relevant_files + touched_files:
            if f not in focus_files:
                focus_files.append(f)
                
        # Discoveries, observations, attempts
        disc_slice = state.discoveries[-self.config.max_discoveries:] if self.config.max_discoveries > 0 else []
        obs_slice = state.recent_observations[-self.config.max_observations:] if self.config.max_observations > 0 else []
        
        all_failed = [a for a in state.attempts if not a.success]
        failed_slice = all_failed[-self.config.max_attempts:] if self.config.max_attempts > 0 else []
        
        all_successful = [a for a in state.attempts if a.success]
        successful_slice = all_successful[-self.config.max_attempts:] if self.config.max_attempts > 0 else []
        
        repeated_failures = state.failures # Just include all
        
        current_errors = list(state.current_errors)
        latest_verification = state.verification
        
        # We will build the repository context without expansion
        repository_symbols_dict = {}
        repository_local_imports_dict = {}
        
        if repo:
            # Reimplement _format_symbols_compact and _format_local_imports_compact
            from src.context.builder import _format_symbols_compact, _format_local_imports_compact
            
            local_import_neighbors = []
            for f in focus_files:
                fs = repo.file_symbols.get(f)
                if fs and fs.local_imports:
                    formatted_loc = _format_local_imports_compact(fs)
                    if formatted_loc:
                        repository_local_imports_dict[fs.path] = "\\n".join(formatted_loc)
                    for imp in fs.local_imports:
                        if imp not in local_import_neighbors and imp not in focus_files:
                            local_import_neighbors.append(imp)
                            
            eligible_symbol_files = list(focus_files)
            for f in local_import_neighbors:
                if f not in eligible_symbol_files:
                    eligible_symbol_files.append(f)
                    
            for f in eligible_symbol_files:
                fs = repo.file_symbols.get(f)
                if fs:
                    sym_lines = _format_symbols_compact(fs)
                    if sym_lines:
                        repository_symbols_dict[fs.path] = "\\n".join(sym_lines)

        # Baseline doesn't do token-aware candidate ranking, it just naive renders everything it can.
        # But it must obey max_context_tokens. A typical baseline truncates the end.
        
        bundle = ContextBundle(
            task=task,
            phase=phase,
            status=status,
            plan=plan,
            current_hypothesis=current_hypothesis,
            relevant_files=relevant_files,
            touched_files=touched_files,
            important_discoveries=disc_slice,
            recent_observations=obs_slice,
            recent_attempts=successful_slice,
            failed_attempts=failed_slice,
            repeated_failures=repeated_failures,
            current_errors=current_errors,
            latest_verification_result=latest_verification,
            iteration_count=state.iteration,
            model_call_count=state.model_calls,
            tool_call_count=state.tool_calls,
            token_usage=TokenUsage(), # mock
            repository_overview=None,
            repository_symbols=list(repository_symbols_dict.values()),
            repository_local_imports=list(repository_local_imports_dict.values()),
            include_failed_attempts=True,
            include_repeated_failures=True,
            include_verification=True,
            include_telemetry=True,
        )
        
        text = bundle.render_text()
        
        # Hard truncate to max_tokens for baseline
        chars_per_token = 4
        if len(text) > max_tokens * chars_per_token:
            text = text[:max_tokens * chars_per_token]
            
        return text
