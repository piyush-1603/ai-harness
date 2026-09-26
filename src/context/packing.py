from dataclasses import dataclass, field


from src.context.relevance import ContextCandidate

@dataclass
class ContextPackingReport:
    candidate_count: int
    selected_count: int
    skipped_count: int
    token_budget: int
    core_tokens: int
    optional_tokens_used: int
    total_estimated_tokens: int
    selected: list[dict]
    skipped: list[dict]

class ContextPacker:
    def pack(
        self,
        core_tokens: int,
        candidates: list[ContextCandidate],
        max_tokens: int,
    ) -> tuple[list[ContextCandidate], ContextPackingReport]:
        
        candidate_budget = max_tokens - core_tokens
        
        # Deterministic sort:
        # priority tier DESC
        # score DESC
        # recency DESC
        # category ASC
        # candidate id ASC
        def sort_key(c: ContextCandidate):
            return (
                -c.priority_tier,
                -c.score,
                -(c.recency_index if c.recency_index is not None else -1),
                c.category,
                c.id
            )
            
        sorted_candidates = sorted(candidates, key=sort_key)
        
        selected_candidates = []
        skipped_candidates = []
        optional_tokens_used = 0
        
        for c in sorted_candidates:
            if candidate_budget > 0 and (optional_tokens_used + c.estimated_tokens <= candidate_budget):
                selected_candidates.append(c)
                optional_tokens_used += c.estimated_tokens
            else:
                skipped_candidates.append(c)
                
        # Re-sort selected back to their original sequence if needed? 
        # The prompt says: "packing report... selected optional tokens never exceed candidate budget"
        # B5.2 should not unnecessarily drop content.
        # It's up to ContextBuilder to re-sort or just render them in category groupings.
                
        report = ContextPackingReport(
            candidate_count=len(candidates),
            selected_count=len(selected_candidates),
            skipped_count=len(skipped_candidates),
            token_budget=max_tokens,
            core_tokens=core_tokens,
            optional_tokens_used=optional_tokens_used,
            total_estimated_tokens=core_tokens + optional_tokens_used,
            selected=[{
                "id": c.id,
                "category": c.category,
                "score": c.score,
                "priority_tier": c.priority_tier,
                "estimated_tokens": c.estimated_tokens,
                "files": list(c.files),
                "recency_index": c.recency_index,
                "scoring_reasons": list(c.scoring_reasons),
                "decision_reason": "selected_within_budget"
            } for c in selected_candidates],
            skipped=[{
                "id": c.id,
                "category": c.category,
                "score": c.score,
                "priority_tier": c.priority_tier,
                "estimated_tokens": c.estimated_tokens,
                "files": list(c.files),
                "recency_index": c.recency_index,
                "scoring_reasons": list(c.scoring_reasons),
                "decision_reason": "no_optional_budget" if candidate_budget <= 0 else "insufficient_remaining_budget"
            } for c in skipped_candidates],
        )
        
        return selected_candidates, report
