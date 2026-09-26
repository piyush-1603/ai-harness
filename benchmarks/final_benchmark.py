import os
import json
import hashlib
import datetime
import tempfile
import copy
from pathlib import Path

from src.context.builder import ContextBuilder
from src.context.config import ContextConfig
from src.context.policy import RepositoryScope
from src.context.budget import estimate_tokens, DEFAULT_CHARS_PER_TOKEN, ContextBudgeter
from benchmarks.baseline import StaticContextBaseline
from benchmarks.scenarios import create_scenarios, create_scenarios_extended
from benchmarks.metrics import calculate_metrics, BenchmarkMetrics, AggregateMetrics

def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def run_benchmark():
    baseline_impl = StaticContextBaseline()
    
    # Adaptive config for Mode B and C
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
    
    # We need a temporary directory for Mode C (MemoryManager artifacts)
    with tempfile.TemporaryDirectory() as tmp_dir:
        # We need three sets of scenarios to avoid mutating states
        scenarios_static = create_scenarios_extended()
        scenarios_adaptive = create_scenarios_extended()
        scenarios_artifact = create_scenarios_extended(tmp_path=tmp_dir)
        
        for idx in range(len(scenarios_static)):
            scenario_name = scenarios_static[idx].name
            print(f"Running {scenario_name}...")
            
            repo_files = scenarios_static[idx].repo.source_files
            max_tokens = scenarios_static[idx].max_tokens
            ground_truth = scenarios_static[idx].ground_truth_relevant
            
            # --- STATIC BASELINE ---
            s_static = scenarios_static[idx]
            s_static_2 = copy.deepcopy(s_static)
            
            has_curr_err = len(s_static.state.current_errors) > 0
            has_failed_verif = s_static.state.verification and not s_static.state.verification.success
            has_failed_att = any(not a.success for a in s_static.state.attempts)
            has_rep_fail = len([f for f in s_static.state.failures if not getattr(f, "resolved", False) and f.occurrence_count >= adaptive_config.min_failure_occurrences]) > 0
            
            static_final_text_1 = baseline_impl.build_context(s_static.state, s_static.repo, max_tokens=max_tokens)
            hash_static_1 = hash_text(static_final_text_1)
            
            static_final_text_2 = baseline_impl.build_context(s_static_2.state, s_static_2.repo, max_tokens=max_tokens)
            hash_static_2 = hash_text(static_final_text_2)
            
            if hash_static_1 != hash_static_2:
                print(f"  [ERROR] Static Baseline nondeterministic in {scenario_name}")
                
            static_tokens = estimate_tokens(static_final_text_1, chars_per_token=DEFAULT_CHARS_PER_TOKEN)
            
            static_metrics = calculate_metrics(
                scenario_name=scenario_name,
                mode="Static",
                text=static_final_text_1,
                budget_limit=max_tokens,
                estimated_tokens=static_tokens,
                repo_files=repo_files,
                ground_truth_relevant=ground_truth,
                has_current_error=has_curr_err,
                has_failed_verification=has_failed_verif,
                has_failed_attempt=has_failed_att,
                has_repeated_failure=has_rep_fail,
                output_hash=hash_static_1,
            )
            static_metrics.estimated_tokens_before = static_tokens
            
            # --- ADAPTIVE PIPELINE ---
            budgeter = ContextBudgeter()
            
            s_adapt = scenarios_adaptive[idx]
            s_adapt_2 = copy.deepcopy(s_adapt)
            
            budget_res_1 = budgeter.fit(state=s_adapt.state, config=adaptive_config, repository_index=s_adapt.repo, max_context_tokens=max_tokens)
            adapt_final_text_1 = budget_res_1.text
            hash_adapt_1 = hash_text(adapt_final_text_1)
            
            budget_res_2 = budgeter.fit(state=s_adapt_2.state, config=adaptive_config, repository_index=s_adapt_2.repo, max_context_tokens=max_tokens)
            adapt_final_text_2 = budget_res_2.text
            hash_adapt_2 = hash_text(adapt_final_text_2)
            
            if hash_adapt_1 != hash_adapt_2:
                print(f"  [ERROR] Adaptive Pipeline nondeterministic in {scenario_name}")
                
            adapt_tokens = estimate_tokens(adapt_final_text_1, chars_per_token=DEFAULT_CHARS_PER_TOKEN)
            
            adapt_metrics = calculate_metrics(
                scenario_name=scenario_name,
                mode="Adaptive",
                text=adapt_final_text_1,
                budget_limit=max_tokens,
                estimated_tokens=adapt_tokens,
                repo_files=repo_files,
                ground_truth_relevant=ground_truth,
                has_current_error=has_curr_err,
                has_failed_verification=has_failed_verif,
                has_failed_attempt=has_failed_att,
                has_repeated_failure=has_rep_fail,
                output_hash=hash_adapt_1,
            )
            adapt_metrics.estimated_tokens_before = budget_res_1.estimated_tokens_before
            
            # --- ADAPTIVE + ARTIFACT MEMORY ---
            s_artifact = scenarios_artifact[idx]
            s_artifact_2 = copy.deepcopy(s_artifact)
            
            budget_res_art_1 = budgeter.fit(state=s_artifact.state, config=adaptive_config, repository_index=s_artifact.repo, max_context_tokens=max_tokens)
            art_final_text_1 = budget_res_art_1.text
            hash_art_1 = hash_text(art_final_text_1)
            
            budget_res_art_2 = budgeter.fit(state=s_artifact_2.state, config=adaptive_config, repository_index=s_artifact_2.repo, max_context_tokens=max_tokens)
            art_final_text_2 = budget_res_art_2.text
            hash_art_2 = hash_text(art_final_text_2)
            
            if hash_art_1 != hash_art_2:
                print(f"  [ERROR] Artifact Pipeline nondeterministic in {scenario_name}")
                
            art_tokens = estimate_tokens(art_final_text_1, chars_per_token=DEFAULT_CHARS_PER_TOKEN)
            
            art_metrics = calculate_metrics(
                scenario_name=scenario_name,
                mode="Adaptive + Artifact",
                text=art_final_text_1,
                budget_limit=max_tokens,
                estimated_tokens=art_tokens,
                repo_files=repo_files,
                ground_truth_relevant=ground_truth,
                has_current_error=has_curr_err,
                has_failed_verification=has_failed_verif,
                has_failed_attempt=has_failed_att,
                has_repeated_failure=has_rep_fail,
                output_hash=hash_art_1,
            )
            art_metrics.estimated_tokens_before = budget_res_art_1.estimated_tokens_before
            
            # Save metrics
            results.append((static_metrics, adapt_metrics, art_metrics))
            
            # Additional validation for Scenario 7 (Large Output)
            if "Scenario 7" in scenario_name:
                orig_chars = len("VERBOSE_LOG_LINE\\n" * 2000)
                # Ensure artifact text does not contain full log
                full_log_part = "VERBOSE_LOG_LINE\\n" * 50
                if full_log_part in art_final_text_1:
                    print("  [ERROR] Artifact context contains too much raw output!")
                # Ensure baseline contains it or gets truncated
                if adapt_metrics.estimated_tokens > max_tokens:
                    print("  [INFO] Adaptive (inline) exceeded budget due to huge output.")
                if art_metrics.estimated_tokens > max_tokens:
                    print("  [ERROR] Adaptive+Artifact exceeded budget!")
            
    write_reports(results)

def write_reports(results):
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    json_data = {
        "benchmark_timestamp": timestamp,
        "scenarios": [],
        "aggregate_metrics": {}
    }
    
    total_scenarios = len(results)
    
    static_agg = AggregateMetrics()
    adapt_agg = AggregateMetrics()
    art_agg = AggregateMetrics()
    
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
            agg.critical_evidence_retention_rate += 1.0
            
        if not m.budget_respected:
            agg.number_of_budget_violations += 1
            
    for s_metric, a_metric, art_metric in results:
        update_agg(static_agg, s_metric)
        update_agg(adapt_agg, a_metric)
        update_agg(art_agg, art_metric)
        
        json_data["scenarios"].append({
            "scenario": s_metric.scenario,
            "static": s_metric.__dict__,
            "adaptive": a_metric.__dict__,
            "artifact": art_metric.__dict__,
        })
        
    if total_scenarios > 0:
        for agg in (static_agg, adapt_agg, art_agg):
            agg.average_estimated_tokens /= total_scenarios
            agg.average_estimated_tokens_before /= total_scenarios
            agg.average_relevant_file_recall /= total_scenarios
            agg.critical_evidence_retention_rate /= total_scenarios
        
    json_data["aggregate_metrics"] = {
        "static": static_agg.__dict__,
        "adaptive": adapt_agg.__dict__,
        "artifact": art_agg.__dict__,
    }
    
    out_dir = Path("benchmarks/results")
    out_dir.mkdir(exist_ok=True, parents=True)
    
    with open(out_dir / "final_results.json", "w") as f:
        json.dump(json_data, f, indent=2)
        
    # Generate Markdown
    md_lines = [
        "# Person B Final Context Benchmark",
        f"Generated at: {timestamp}\n",
        "Compared:",
        "- Static Context",
        "- Adaptive Context",
        "- Adaptive + Artifact Memory\n",
        "## Aggregate Metrics\n",
        "| Metric | Static | Adaptive | Adaptive + Artifact |",
        "|---|---:|---:|---:|",
        f"| Average Estimated Tokens (Final) | {static_agg.average_estimated_tokens:.1f} | {adapt_agg.average_estimated_tokens:.1f} | {art_agg.average_estimated_tokens:.1f} |",
        f"| Average Estimated Tokens (Before) | {static_agg.average_estimated_tokens_before:.1f} | {adapt_agg.average_estimated_tokens_before:.1f} | {art_agg.average_estimated_tokens_before:.1f} |",
        f"| Average Relevant File Recall | {static_agg.average_relevant_file_recall:.1%} | {adapt_agg.average_relevant_file_recall:.1%} | {art_agg.average_relevant_file_recall:.1%} |",
        f"| Total Irrelevant Files | {static_agg.total_irrelevant_files_included} | {adapt_agg.total_irrelevant_files_included} | {art_agg.total_irrelevant_files_included} |",
        f"| Critical Evidence Retention | {static_agg.critical_evidence_retention_rate:.1%} | {adapt_agg.critical_evidence_retention_rate:.1%} | {art_agg.critical_evidence_retention_rate:.1%} |",
        f"| Budget Violations | {static_agg.number_of_budget_violations} | {adapt_agg.number_of_budget_violations} | {art_agg.number_of_budget_violations} |",
        "\n## Scenario Breakdown\n"
    ]
    
    for s, a, art in results:
        md_lines.extend([
            f"### {s.scenario}",
            "| Metric | Static | Adaptive | Adaptive + Artifact |",
            "|---|---:|---:|---:|",
            f"| Estimated tokens (Final) | {s.estimated_tokens} | {a.estimated_tokens} | {art.estimated_tokens} |",
            f"| Estimated tokens (Before)| {s.estimated_tokens_before or s.estimated_tokens} | {a.estimated_tokens_before or a.estimated_tokens} | {art.estimated_tokens_before or art.estimated_tokens} |",
            f"| Relevant file recall | {s.relevant_file_recall:.1%} | {a.relevant_file_recall:.1%} | {art.relevant_file_recall:.1%} |",
            f"| Irrelevant files | {s.irrelevant_files_included} | {a.irrelevant_files_included} | {art.irrelevant_files_included} |",
            f"| Current error retained | {s.current_error_retained} | {a.current_error_retained} | {art.current_error_retained} |",
            f"| Failed verification retained | {s.failed_verification_retained} | {a.failed_verification_retained} | {art.failed_verification_retained} |",
            f"| Budget respected | {s.budget_respected} | {a.budget_respected} | {art.budget_respected} |",
            "\n"
        ])
        
    with open(out_dir / "final_report.md", "w") as f:
        f.write("\n".join(md_lines))
        
    print(f"\nBenchmark complete. Wrote {out_dir / 'final_results.json'} and {out_dir / 'final_report.md'}")
    
    # Assertions
    # Budget violation checks
    assert adapt_agg.number_of_budget_violations == 0, "Adaptive baseline violated budget!"
    assert art_agg.number_of_budget_violations == 0, "Artifact mode violated budget!"

if __name__ == "__main__":
    run_benchmark()
