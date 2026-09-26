import os
from dataclasses import dataclass, field
from typing import Optional

from src.memory.models import TaskState, Attempt, Failure, Observation
from src.context.scanner import RepositoryIndex
from src.context.policy import PressureLevel

@dataclass
class ExpansionPlan:
    seed_files: list[str]
    expanded_files: list[str]
    depth: int
    pressure_level: str
    reasons: dict[str, str] = field(default_factory=dict)
    
class FailureAwareExpander:
    def __init__(self, max_expanded_files: int = 20):
        self.max_expanded_files = max_expanded_files
        
    def _normalize_path(self, path: str) -> str:
        return os.path.normpath(path)

    def _determine_seeds(self, state: TaskState) -> list[str]:
        seeds: list[str] = []
        
        def add_seed(path: str):
            norm = self._normalize_path(path)
            if norm not in seeds:
                seeds.append(norm)

        # 1. Unresolved failures
        for failure in state.failures:
            if not getattr(failure, "resolved", False):
                for f in failure.files:
                    add_seed(f)
                    
        # 2. Recent failed attempts
        for attempt in reversed(state.attempts):
            if not attempt.success:
                for f in attempt.files_touched:
                    add_seed(f)
                    
        # 3. Observations referencing errors (heuristic: type 'error' or 'exception', or just use recent observations)
        for obs in reversed(state.recent_observations):
            if obs.type in ("error", "exception") or "error" in obs.summary.lower() or "fail" in obs.summary.lower():
                for f in obs.files:
                    add_seed(f)
                    
        # 4. Touched files
        for f in state.touched_files:
            add_seed(f)
            
        # 5. Relevant files
        for f in state.relevant_files:
            add_seed(f)
            
        return seeds

    def _build_graph(self, repo_index: RepositoryIndex) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        forward_edges: dict[str, list[str]] = {}
        reverse_edges: dict[str, list[str]] = {}
        
        for path, fs in repo_index.file_symbols.items():
            norm_path = self._normalize_path(path)
            if norm_path not in forward_edges:
                forward_edges[norm_path] = []
            if norm_path not in reverse_edges:
                reverse_edges[norm_path] = []
                
            for imp in fs.local_imports:
                norm_imp = self._normalize_path(imp)
                
                # forward
                if norm_imp not in forward_edges[norm_path]:
                    forward_edges[norm_path].append(norm_imp)
                
                # reverse
                if norm_imp not in reverse_edges:
                    reverse_edges[norm_imp] = []
                if norm_path not in reverse_edges[norm_imp]:
                    reverse_edges[norm_imp].append(norm_path)
                    
        # Sort for determinism
        for edges in (forward_edges, reverse_edges):
            for k in edges:
                edges[k].sort()
                
        return forward_edges, reverse_edges

    def expand(
        self,
        state: TaskState,
        repo_index: RepositoryIndex,
        pressure_level: PressureLevel,
    ) -> ExpansionPlan:
        
        # Determine depth based on pressure
        if pressure_level == PressureLevel.LOW:
            depth = 0
        elif pressure_level == PressureLevel.MEDIUM:
            depth = 1
        elif pressure_level == PressureLevel.HIGH:
            depth = 2
        else:
            depth = 0
            
        seeds = self._determine_seeds(state)
        
        forward_edges, reverse_edges = self._build_graph(repo_index)
        
        expanded_files: list[str] = []
        reasons: dict[str, str] = {}
        
        def add_file(f: str, reason: str):
            if len(expanded_files) >= self.max_expanded_files:
                return False
            if f not in expanded_files:
                expanded_files.append(f)
                reasons[f] = reason
            return True
            
        # Level 0: Seeds
        for f in seeds:
            if not add_file(f, "failure seed"):
                break
                
        current_level_files = list(expanded_files)
        
        # Traverse for up to `depth` hops
        for current_hop in range(1, depth + 1):
            next_level_files = []
            # Order: direct dependencies first, then direct importers
            
            # Dependencies
            for node in current_level_files:
                for dep in forward_edges.get(node, []):
                    if dep not in expanded_files:
                        if add_file(dep, f"imported by {node} (hop {current_hop})"):
                            next_level_files.append(dep)
            
            # Importers
            for node in current_level_files:
                for imp in reverse_edges.get(node, []):
                    if imp not in expanded_files:
                        if add_file(imp, f"imports {node} (hop {current_hop})"):
                            next_level_files.append(imp)
                            
            current_level_files = next_level_files
            
        return ExpansionPlan(
            seed_files=seeds,
            expanded_files=expanded_files,
            depth=depth,
            pressure_level=pressure_level.value,
            reasons=reasons,
        )
