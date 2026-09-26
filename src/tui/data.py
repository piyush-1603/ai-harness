from typing import Optional

from src.memory.manager import MemoryManager
from src.context.diagnostics import ContextDiagnosticsEngine
from src.context.scanner import RepositoryIndex
from src.tui.models import (
    OverviewData, RelevantFileData, ActivityItem,
    ContextData, ContextCandidateData,
    MemoryData, MemoryItemData,
    RepositoryData, RepositoryFileData,
    EventItemData
)

class TUIDataProvider:
    def __init__(
        self,
        memory_manager: MemoryManager,
        diagnostics_engine: Optional[ContextDiagnosticsEngine] = None,
        repository_index: Optional[RepositoryIndex] = None,
    ):
        self.memory = memory_manager
        self.diagnostics = diagnostics_engine
        self.repo = repository_index

    def get_overview_data(self) -> OverviewData:
        state = self.memory.get_state()
        
        pressure_level = "UNKNOWN"
        pressure_score = 0
        repo_scope = "UNKNOWN"
        ctx_tokens = 0
        ctx_limit = 0
        
        if self.diagnostics:
            diag = self.diagnostics.explain(self.memory, repository_index=self.repo)
            if diag:
                pressure_level = diag.policy.pressure_level
                pressure_score = diag.policy.pressure_score
                repo_scope = diag.policy.repository_scope
                ctx_tokens = diag.budget.estimated_tokens_after
                ctx_limit = diag.budget.max_tokens

        active_failure = None
        active_failure_count = 0
        
        # Pick the most relevant unresolved failure
        unresolved = [f for f in state.failures if not f.resolved]
        if unresolved:
            # Sort by occurrence_count desc, then first_seen
            best_failure = sorted(unresolved, key=lambda x: (x.occurrence_count, x.first_seen), reverse=True)[0]
            active_failure = best_failure.error_signature
            active_failure_count = best_failure.occurrence_count
            
        relevant_files = []
        seen = set()
        for f in state.touched_files:
            if f not in seen:
                relevant_files.append(RelevantFileData(path=f, status="touched"))
                seen.add(f)
        for f in state.relevant_files:
            if f not in seen:
                relevant_files.append(RelevantFileData(path=f, status="relevant"))
                seen.add(f)
                
        # recent activity
        events = self.memory.get_events()[-10:] if hasattr(self.memory, 'get_events') else []
        activities = []
        for e in events:
            # Convert timestamp assuming iso format "YYYY-MM-DDTHH:MM:SS"
            ts = e.timestamp.split("T")[-1][:8] if "T" in e.timestamp else e.timestamp
            activities.append(ActivityItem(
                timestamp=ts,
                kind=e.type,
                label=e.metadata.get("action", "") or e.type.lower(),
                description=e.summary
            ))

        return OverviewData(
            task_id=state.task_id,
            task=state.task,
            phase=state.phase.name if hasattr(state.phase, "name") else str(state.phase),
            status=state.status.name if hasattr(state.status, "name") else str(state.status),
            iteration=state.iteration,
            model_calls=state.model_calls,
            tool_calls=state.tool_calls,
            pressure_level=pressure_level,
            pressure_score=pressure_score,
            repository_scope=repo_scope,
            context_tokens=ctx_tokens,
            context_limit=ctx_limit,
            active_failure=active_failure,
            active_failure_count=active_failure_count,
            relevant_files=relevant_files,
            recent_activity=activities
        )

    def get_context_data(self) -> ContextData:
        if not self.diagnostics:
            return ContextData("UNKNOWN", "UNKNOWN", 0, 0, [], [], [])
            
        diag = self.diagnostics.explain(self.memory, repository_index=self.repo)
        if not diag:
            return ContextData("UNKNOWN", "UNKNOWN", 0, 0, [], [], [])
            
        selected = []
        skipped = []
        
        for c in diag.candidates:
            cd = ContextCandidateData(
                id=c.id,
                category=c.category,
                priority=c.priority_label,
                score=c.score,
                estimated_tokens=c.estimated_tokens,
                files=c.files,
                decision=c.decision_reason,
                reasons=c.scoring_reasons,
                selected=c.selected
            )
            if c.selected:
                selected.append(cd)
            else:
                skipped.append(cd)
                
        return ContextData(
            pressure_level=diag.policy.pressure_level,
            repository_scope=diag.policy.repository_scope,
            max_tokens=diag.budget.max_tokens,
            final_tokens=diag.budget.estimated_tokens_after,
            reductions=diag.budget.reductions,
            selected_candidates=selected,
            skipped_candidates=skipped
        )

    def get_memory_data(self) -> MemoryData:
        state = self.memory.get_state()
        
        observations = []
        artifacts = []
        for o in state.recent_observations:
            observations.append(MemoryItemData(
                kind="Observation",
                label=o.type,
                summary=o.summary
            ))
            if o.output_ref:
                exists = False
                if hasattr(self.memory, 'has_artifact'):
                    exists = self.memory.has_artifact(o.output_ref)
                
                artifacts.append(MemoryItemData(
                    kind="Artifact",
                    label=o.output_ref,
                    summary="Stored externally",
                    ref=o.output_ref,
                    original_chars=o.raw_output_chars,
                    preview=o.raw_output,
                    exists=exists
                ))
                
        attempts = [MemoryItemData("Attempt", a.action, a.result) for a in state.attempts]
        failures = [MemoryItemData("Failure", f.error_signature, f.summary, count=f.occurrence_count) for f in state.failures]
        discoveries = [MemoryItemData("Discovery", "fact", d.statement) for d in state.discoveries]
        
        return MemoryData(
            observations=observations,
            attempts=attempts,
            failures=failures,
            discoveries=discoveries,
            artifacts=artifacts
        )

    def get_repository_data(self) -> RepositoryData:
        if not self.repo:
            return RepositoryData([])

        state = self.memory.get_state()
        files = []

        for path in self.repo.discovered_files:
            touched = path in state.touched_files
            relevant = path in state.relevant_files
            fs = self.repo.file_symbols.get(path)
            role = self.repo.file_roles.get(path, "source")
            files.append(RepositoryFileData(
                path=path,
                role=role,
                touched=touched,
                relevant=relevant,
                symbols=[f"{s.name}()" if s.kind in ("function", "method") else s.name for s in fs.symbols] if fs else [],
                imports=fs.local_imports if fs else [],
            ))

        return RepositoryData(files)


    def get_events_data(self, limit: int = 100) -> list[EventItemData]:
        if not hasattr(self.memory, 'get_events'):
            return []
            
        events = self.memory.get_events()[-limit:]
        return [
            EventItemData(
                timestamp=e.timestamp.split("T")[-1][:8] if "T" in e.timestamp else e.timestamp,
                kind=e.type,
                summary=e.summary
            ) for e in events
        ]
