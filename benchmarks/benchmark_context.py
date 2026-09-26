import os
import json
import hashlib
import datetime
from pathlib import Path

from src.context.builder import ContextBuilder
from src.context.config import ContextConfig
from src.context.policy import RepositoryScope
from src.context.budget import estimate_tokens, DEFAULT_CHARS_PER_TOKEN, ContextBudgeter
from benchmarks.baseline import StaticContextBaseline
from benchmarks.scenarios import create_scenarios
from benchmarks.metrics import calculate_metrics, BenchmarkMetrics, AggregateMetrics

def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def run_benchmark():
    baseline_impl = StaticContextBaseline()
    
    # Adaptive config should mirror the features enabled in baseline but allow B4 and B6 to work
    adaptive_config = ContextConfig(
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
    )
    
    results = []
    
    scenarios_list = create_scenarios()
    
    for scenario in scenarios_list:
        print(f"Running {scenario.name}...")
        
        repo_files = scenario.repo.source_files
        has_curr_err = len(scenario.state.current_errors) > 0
        has_failed_verif = scenario.state.verification and not scenario.state.verification.success
        has_failed_att = any(not a.success for a in scenario.state.attempts)
        has_rep_fail = len([f for f in scenario.state.failures if not getattr(f, "resolved", False) and f.occurrence_count >= adaptive_config.min_failure_occurrences]) > 0
        
        # We run each twice to verify determinism
        
        # --- STATIC BASELINE ---
        s1_static = create_scenarios()[scenarios_list.index(scenario)]
        static_final_text_1 = baseline_impl.build_context(s1_static.state, s1_static.repo, max_tokens=scenario.max_tokens)
        hash_static_1 = hash_text(static_final_text_1)
        
        s2_static = create_scenarios()[scenarios_list.index(scenario)]
        static_final_text_2 = baseline_impl.build_context(s2_static.state, s2_static.repo, max_tokens=scenario.max_tokens)
        hash_static_2 = hash_text(static_final_text_2)
        
        if hash_static_1 != hash_static_2:
            print(f"  [ERROR] Static Baseline nondeterministic in {scenario.name}")
            
        static_tokens = estimate_tokens(static_final_text_1, chars_per_token=DEFAULT_CHARS_PER_TOKEN)
        
        static_metrics = calculate_metrics(
            scenario_name=scenario.name,
            mode="Static",
            text=static_final_text_1,
            budget_limit=scenario.max_tokens,
            estimated_tokens=static_tokens,
            repo_files=repo_files,
            ground_truth_relevant=scenario.ground_truth_relevant,
            has_current_error=has_curr_err,
            has_failed_verification=has_failed_verif,
            has_failed_attempt=has_failed_att,
            has_repeated_failure=has_rep_fail,
            output_hash=hash_static_1,
        )
        static_metrics.estimated_tokens_before = static_tokens
        
        # --- ADAPTIVE PIPELINE ---
        budgeter = ContextBudgeter()
        
        s1_adaptive = create_scenarios()[scenarios_list.index(scenario)]
        budget_res_1 = budgeter.fit(state=s1_adaptive.state, config=adaptive_config, repository_index=s1_adaptive.repo, max_context_tokens=scenario.max_tokens)
        adapt_final_text_1 = budget_res_1.text
        hash_adapt_1 = hash_text(adapt_final_text_1)
        
        s2_adaptive = create_scenarios()[scenarios_list.index(scenario)]
        budget_res_2 = budgeter.fit(state=s2_adaptive.state, config=adaptive_config, repository_index=s2_adaptive.repo, max_context_tokens=scenario.max_tokens)
        adapt_final_text_2 = budget_res_2.text
        hash_adapt_2 = hash_text(adapt_final_text_2)
        
        if hash_adapt_1 != hash_adapt_2:
            print(f"  [ERROR] Adaptive Pipeline nondeterministic in {scenario.name}")
            
        adapt_tokens = estimate_tokens(adapt_final_text_1, chars_per_token=DEFAULT_CHARS_PER_TOKEN)
        
        adapt_metrics = calculate_metrics(
            scenario_name=scenario.name,
            mode="Adaptive",
            text=adapt_final_text_1,
            budget_limit=scenario.max_tokens,
            estimated_tokens=adapt_tokens,
            repo_files=repo_files,
            ground_truth_relevant=scenario.ground_truth_relevant,
            has_current_error=has_curr_err,
            has_failed_verification=has_failed_verif,
            has_failed_attempt=has_failed_att,
            has_repeated_failure=has_rep_fail,
            output_hash=hash_adapt_1,
        )
        adapt_metrics.estimated_tokens_before = budget_res_1.estimated_tokens_before
        
        results.append((static_metrics, adapt_metrics))
        
    write_reports(results)

def write_reports(results):
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    # Generate JSON
    json_data = {
        "benchmark_timestamp": timestamp,
        "scenarios": [],
        "aggregate_metrics": {}
    }
    
    total_scenarios = len(results)
    
    static_agg = AggregateMetrics()
    adapt_agg = AggregateMetrics()
    
    def update_agg(agg: AggregateMetrics, m: BenchmarkMetrics):
        agg.average_estimated_tokens += m.estimated_tokens
        if m.estimated_tokens_before is not None:
            agg.average_estimated_tokens_before += m.estimated_tokens_before
        else:
            agg.average_estimated_tokens_before += m.estimated_tokens
        agg.average_relevant_file_recall += m.relevant_file_recall
        agg.total_irrelevant_files_included += m.irrelevant_files_included
        
        crit_retained = 0
        crit_total = 0
        if m.current_error_retained is not None: crit_retained += int(m.current_error_retained); crit_total += 1
        if m.failed_verification_retained is not None: crit_retained += int(m.failed_verification_retained); crit_total += 1
        if m.failed_attempt_retained is not None: crit_retained += int(m.failed_attempt_retained); crit_total += 1
        if m.repeated_failure_retained is not None: crit_retained += int(m.repeated_failure_retained); crit_total += 1
        
        if crit_total > 0:
            agg.critical_evidence_retention_rate += (crit_retained / crit_total)
        else:
            agg.critical_evidence_retention_rate += 1.0 # 100% if nothing to retain
            
        if not m.budget_respected:
            agg.number_of_budget_violations += 1
            
    for s_metric, a_metric in results:
        update_agg(static_agg, s_metric)
        update_agg(adapt_agg, a_metric)
        
        json_data["scenarios"].append({
            "scenario": s_metric.scenario,
            "static": s_metric.__dict__,
            "adaptive": a_metric.__dict__
        })
        
    if total_scenarios > 0:
        for agg in (static_agg, adapt_agg):
            agg.average_estimated_tokens /= total_scenarios
            agg.average_estimated_tokens_before /= total_scenarios
            agg.average_relevant_file_recall /= total_scenarios
            agg.critical_evidence_retention_rate /= total_scenarios
        
    json_data["aggregate_metrics"] = {
        "static": static_agg.__dict__,
        "adaptive": adapt_agg.__dict__
    }
    
    out_dir = Path("benchmark_results")
    out_dir.mkdir(exist_ok=True)
    
    with open(out_dir / "latest.json", "w") as f:
        json.dump(json_data, f, indent=2)
        
    # Generate Markdown
    md_lines = [
        "# Context Builder Benchmark Results",
        f"Generated at: {timestamp}\\n",
        "## Aggregate Metrics\\n",
        "| Metric | Static | Adaptive |",
        "|---|---:|---:|",
        f"| Average Estimated Tokens (Final) | {static_agg.average_estimated_tokens:.1f} | {adapt_agg.average_estimated_tokens:.1f} |",
        f"| Average Estimated Tokens (Before) | {static_agg.average_estimated_tokens_before:.1f} | {adapt_agg.average_estimated_tokens_before:.1f} |",
        f"| Average Relevant File Recall | {static_agg.average_relevant_file_recall:.1%} | {adapt_agg.average_relevant_file_recall:.1%} |",
        f"| Total Irrelevant Files | {static_agg.total_irrelevant_files_included} | {adapt_agg.total_irrelevant_files_included} |",
        f"| Critical Evidence Retention | {static_agg.critical_evidence_retention_rate:.1%} | {adapt_agg.critical_evidence_retention_rate:.1%} |",
        f"| Budget Violations | {static_agg.number_of_budget_violations} | {adapt_agg.number_of_budget_violations} |",
        "\\n## Scenario Breakdown\\n"
    ]
    
    for s, a in results:
        md_lines.extend([
            f"### {s.scenario}",
            "| Metric | Static | Adaptive |",
            "|---|---:|---:|",
            f"| Estimated tokens (Final) | {s.estimated_tokens} | {a.estimated_tokens} |",
            f"| Estimated tokens (Before)| {s.estimated_tokens_before or s.estimated_tokens} | {a.estimated_tokens_before or a.estimated_tokens} |",
            f"| Relevant file recall | {s.relevant_file_recall:.1%} | {a.relevant_file_recall:.1%} |",
            f"| Irrelevant files | {s.irrelevant_files_included} | {a.irrelevant_files_included} |",
            f"| Current error retained | {s.current_error_retained} | {a.current_error_retained} |",
            f"| Failed verification retained | {s.failed_verification_retained} | {a.failed_verification_retained} |",
            f"| Budget respected | {s.budget_respected} | {a.budget_respected} |",
            "\\n"
        ])
        
    with open(out_dir / "latest.md", "w") as f:
        f.write("\\n".join(md_lines))
        
    print(f"\\nBenchmark complete. Wrote {out_dir / 'latest.json'} and {out_dir / 'latest.md'}")
    print(f"Static Avg Tokens: {static_agg.average_estimated_tokens:.1f} | Adaptive Avg Tokens: {adapt_agg.average_estimated_tokens:.1f}")
    print(f"Static Avg Recall: {static_agg.average_relevant_file_recall:.1%} | Adaptive Avg Recall: {adapt_agg.average_relevant_file_recall:.1%}")

if __name__ == "__main__":
    run_benchmark()
