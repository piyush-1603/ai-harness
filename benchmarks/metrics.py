from dataclasses import dataclass, field
from typing import Optional

@dataclass
class BenchmarkMetrics:
    scenario: str
    mode: str

    estimated_tokens: int
    budget_limit: int
    budget_respected: bool

    relevant_files_expected: int
    relevant_files_included: int
    relevant_file_recall: float

    irrelevant_files_included: int

    current_error_retained: bool
    failed_verification_retained: bool
    failed_attempt_retained: bool
    repeated_failure_retained: bool

    repository_files_in_context: list[str] = field(default_factory=list)
    output_hash: str = ""
    estimated_tokens_before: Optional[int] = None

@dataclass
class AggregateMetrics:
    average_estimated_tokens: float = 0.0
    average_estimated_tokens_before: float = 0.0
    average_relevant_file_recall: float = 0.0
    total_irrelevant_files_included: int = 0
    critical_evidence_retention_rate: float = 0.0
    number_of_budget_violations: int = 0
    determinism_failures: int = 0

def extract_files_from_context(text: str, repo_files: list[str]) -> list[str]:
    """
    Extracts files that actually appear in the final text.
    For structural information, we look for the exact file path or class/function names.
    Since path is the most reliable, we check if the path is in the text.
    """
    return [f for f in repo_files if f in text]

def calculate_metrics(
    scenario_name: str,
    mode: str,
    text: str,
    budget_limit: int,
    estimated_tokens: int,
    repo_files: list[str],
    ground_truth_relevant: list[str],
    has_current_error: bool,
    has_failed_verification: bool,
    has_failed_attempt: bool,
    has_repeated_failure: bool,
    output_hash: str,
) -> BenchmarkMetrics:
    
    files_in_context = extract_files_from_context(text, repo_files)
    
    relevant_included = [f for f in files_in_context if f in ground_truth_relevant]
    irrelevant_included = [f for f in files_in_context if f not in ground_truth_relevant]
    
    expected_count = len(ground_truth_relevant)
    recall = len(relevant_included) / expected_count if expected_count > 0 else 1.0
    
    # Critical evidence retention checking
    # This is a bit heuristic, but we check if keywords like "Error:", "Success: False", or specific attempt strings exist
    curr_err_retained = False
    if has_current_error:
        curr_err_retained = "curr_err_" in text or "Exception" in text or "Error" in text
        
    failed_verif_retained = False
    if has_failed_verification:
        failed_verif_retained = "Success: False" in text or "Success: False" in text
        
    failed_att_retained = False
    if has_failed_attempt:
        failed_att_retained = "Attempt" in text and "False" in text or "Fail" in text
        
    rep_fail_retained = False
    if has_repeated_failure:
        rep_fail_retained = "[x" in text # repeated failure notation e.g. [x2]
        
    return BenchmarkMetrics(
        scenario=scenario_name,
        mode=mode,
        estimated_tokens=estimated_tokens,
        budget_limit=budget_limit,
        budget_respected=estimated_tokens <= budget_limit,
        relevant_files_expected=expected_count,
        relevant_files_included=len(relevant_included),
        relevant_file_recall=recall,
        irrelevant_files_included=len(irrelevant_included),
        current_error_retained=curr_err_retained if has_current_error else True,
        failed_verification_retained=failed_verif_retained if has_failed_verification else True,
        failed_attempt_retained=failed_att_retained if has_failed_attempt else True,
        repeated_failure_retained=rep_fail_retained if has_repeated_failure else True,
        repository_files_in_context=files_in_context,
        output_hash=output_hash,
    )
