import os
import copy
from typing import Optional

from src.context.relevance import ContextCandidate, RelevanceScorer, TIER_CRITICAL, TIER_VERY_HIGH, TIER_HIGH, TIER_MEDIUM, TIER_LOW, TIER_VERY_LOW
from src.context.packing import ContextPacker
from src.context.builder import ContextBuilder
from src.context.config import ContextConfig
from src.context.budget import ContextBudgeter, ContextBudgetConfig
from src.memory.models import TaskState, Phase, TaskStatus, Discovery, Observation, Attempt, Failure, VerificationSnapshot, TokenUsage
from src.context.scanner import RepositoryIndex, FileRole
from src.context.symbols import FileSymbols, SymbolRecord

def test_priority_tiers():
    state = TaskState(task_id="t1", task="test")
    scorer = RelevanceScorer()

    c_err = ContextCandidate(id="err1", category="current_error", text="error", estimated_tokens=10, metadata={"obj": "err"})
    c_tel = ContextCandidate(id="tel1", category="telemetry", text="tel", estimated_tokens=10, metadata={"obj": "tel"})
    c_ver = ContextCandidate(id="ver1", category="verification_result", text="fail", estimated_tokens=10, metadata={"obj": {}, "success": False})
    c_repo = ContextCandidate(id="repo1", category="repository_overview", text="repo", estimated_tokens=10, metadata={"obj": "repo"})
    c_act = ContextCandidate(id="fail1", category="repeated_failure", text="fail", estimated_tokens=10, metadata={"obj": {}, "resolved": False})
    c_succ = ContextCandidate(id="succ1", category="successful_attempt", text="succ", estimated_tokens=10, metadata={"obj": {}})
    c_att = ContextCandidate(id="att1", category="failed_attempt", text="att", estimated_tokens=10, metadata={"obj": {}})

    scored = scorer.score_candidates(state, [c_err, c_tel, c_ver, c_repo, c_act, c_succ, c_att])

    err_tier = next(c.priority_tier for c in scored if c.id == "err1")
    tel_tier = next(c.priority_tier for c in scored if c.id == "tel1")
    assert err_tier == TIER_CRITICAL
    assert tel_tier == TIER_VERY_LOW
    assert err_tier > tel_tier

    ver_tier = next(c.priority_tier for c in scored if c.id == "ver1")
    repo_tier = next(c.priority_tier for c in scored if c.id == "repo1")
    assert ver_tier == TIER_CRITICAL
    assert repo_tier == TIER_LOW
    assert ver_tier > repo_tier

    act_tier = next(c.priority_tier for c in scored if c.id == "fail1")
    succ_tier = next(c.priority_tier for c in scored if c.id == "succ1")
    assert act_tier == TIER_CRITICAL
    assert succ_tier == TIER_LOW
    assert act_tier > succ_tier

    att_tier = next(c.priority_tier for c in scored if c.id == "att1")
    assert att_tier == TIER_VERY_HIGH
    assert att_tier > succ_tier

def test_file_relevance():
    state = TaskState(
        task_id="t1",
        task="test",
        touched_files=["src/touched.py"],
        relevant_files=["src/relevant.py"]
    )
    scorer = RelevanceScorer()

    c1 = ContextCandidate(id="obs1", category="observation", text="obs1", estimated_tokens=10, files=["src/touched.py"])
    c2 = ContextCandidate(id="obs2", category="observation", text="obs2", estimated_tokens=10, files=["src/unrelated.py"])
    c3 = ContextCandidate(id="obs3", category="observation", text="obs3", estimated_tokens=10, files=["src/./relevant.py"])

    scored = scorer.score_candidates(state, [c1, c2, c3])
    sc1 = next(c for c in scored if c.id == "obs1")
    sc2 = next(c for c in scored if c.id == "obs2")
    sc3 = next(c for c in scored if c.id == "obs3")

    assert sc1.score > sc2.score
    assert sc3.score > sc2.score
    assert sc1.score > sc3.score  # touched gets higher boost than relevant

def test_recency():
    state = TaskState(task_id="t1", task="test")
    scorer = RelevanceScorer()
    packer = ContextPacker()

    c1 = ContextCandidate(id="obs1", category="observation", text="old", estimated_tokens=10, recency_index=0)
    c2 = ContextCandidate(id="obs2", category="observation", text="new", estimated_tokens=10, recency_index=1)
    
    scored = scorer.score_candidates(state, [c1, c2])
    selected, rep = packer.pack(0, scored, 20)
    # newer observation should be selected first, so it appears first in `selected` because sorting is descending on recency
    assert selected[0].id == "obs2"
    assert selected[1].id == "obs1"

def test_confidence():
    state = TaskState(task_id="t1", task="test")
    scorer = RelevanceScorer()

    c1 = ContextCandidate(id="disc1", category="discovery", text="d1", estimated_tokens=10, metadata={"confidence": 0.5})
    c2 = ContextCandidate(id="disc2", category="discovery", text="d2", estimated_tokens=10, metadata={"confidence": 0.9})
    c3 = ContextCandidate(id="err", category="current_error", text="e", estimated_tokens=10, metadata={"obj": "e"})

    scored = scorer.score_candidates(state, [c1, c2, c3])
    sc1 = next(c for c in scored if c.id == "disc1")
    sc2 = next(c for c in scored if c.id == "disc2")
    sc3 = next(c for c in scored if c.id == "err")

    assert sc2.score > sc1.score
    assert sc3.priority_tier > sc2.priority_tier  # confidence doesn't cross tier

def test_packing_logic():
    packer = ContextPacker()
    # 5 items, each 10 tokens
    c1 = ContextCandidate(id="c1", category="observation", text="1", estimated_tokens=10, priority_tier=5)
    c2 = ContextCandidate(id="c2", category="observation", text="2", estimated_tokens=20, priority_tier=4)
    c3 = ContextCandidate(id="c3", category="observation", text="3", estimated_tokens=10, priority_tier=3)
    c4 = ContextCandidate(id="c4", category="observation", text="4", estimated_tokens=10, priority_tier=2)

    # Core is 20 tokens, max is 50. Budget = 30.
    # c1 (10) fits. Remaining = 20.
    # c2 (20) fits. Remaining = 0.
    # c3, c4 skip.
    selected, report = packer.pack(20, [c4, c2, c3, c1], 50)
    assert len(selected) == 2
    assert selected[0].id == "c1"
    assert selected[1].id == "c2"
    assert report.optional_tokens_used == 30
    assert report.skipped_count == 2
    
    # Core is 20 tokens, max is 45. Budget = 25.
    # c1 (10) fits. Remaining = 15.
    # c2 (20) too large, skipped.
    # c3 (10) fits. Remaining = 5.
    # c4 (10) too large, skipped.
    selected2, report2 = packer.pack(20, [c4, c2, c3, c1], 45)
    assert len(selected2) == 2
    assert selected2[0].id == "c1"
    assert selected2[1].id == "c3"
    assert report2.optional_tokens_used == 20

def test_determinism_and_immutability():
    state = TaskState(
        task_id="t1",
        task="test",
        touched_files=["src/auth.py"],
        current_errors=["Error A"],
        failures=[Failure("sig1", "sum1", "act1", ["src/auth.py"], occurrence_count=5)]
    )
    original_state = copy.deepcopy(state)
    
    config = ContextConfig(max_context_tokens=1000)
    original_config = copy.deepcopy(config)
    
    builder = ContextBuilder(config=config)
    
    bundle1 = builder.build(state)
    text1 = bundle1.render_text()
    
    bundle2 = builder.build(state)
    text2 = bundle2.render_text()
    
    assert text1 == text2
    assert state == original_state
    assert config == original_config

def test_b5_1_integration():
    state = TaskState(
        task_id="t1",
        task="Fix authentication failures",
        touched_files=["src/auth.py"],
        relevant_files=["src/session.py"],
        phase=Phase.RECOVER,
        status=TaskStatus.RUNNING,
        current_errors=["AuthError: invalid token"],
        failures=[Failure("sig", "summary", "action", ["src/auth.py"], occurrence_count=4)],
        attempts=[
            Attempt("succ1", "hyp", "act", ["src/old.py"], "res", True, 1),
            Attempt("fail1", "hyp", "act", ["src/auth.py"], "res", False, 2),
        ],
        discoveries=[
            Discovery("auth flow requires token", "see docs", ["src/auth.py"], 0.9),
            Discovery("repo uses pytest", "see setup", [], 0.5)
        ],
        verification=VerificationSnapshot(False, "tests failed", 10, 2, "details")
    )
    
    repo = RepositoryIndex(root_dir=".")
    repo.file_symbols["src/auth.py"] = FileSymbols("src/auth.py", "python", [], ["os", "src.session"])
    repo.file_symbols["src/session.py"] = FileSymbols("src/session.py", "python", [], [])
    
    # Large budget -> everything included
    large_config = ContextConfig(max_context_tokens=100000)
    budgeter = ContextBudgeter(config=ContextBudgetConfig(max_context_tokens=100000))
    res_large = budgeter.fit(state, config=large_config, repository_index=repo)
    assert not res_large.was_reduced
    
    # Constrained budget
    # Try an extremely constrained budget that fits core + a few items
    small_budget = 200 # approx tokens
    res_small = budgeter.fit(state, config=ContextConfig(), repository_index=repo, max_context_tokens=small_budget)
    
    assert res_small.estimated_tokens_after <= small_budget
    # If core + any item doesn't exceed small_budget, it uses normal packing.
    # If core alone exceeds small_budget, B5.1 truncates.
    
    # Build manually to verify priorities
    builder = ContextBuilder(config=ContextConfig(max_context_tokens=300), repository_index=repo)
    bundle = builder.build(state)
    assert len(bundle.current_errors) > 0 and "AuthError" in bundle.current_errors[0]
    assert bundle.include_telemetry is False or bundle.iteration_count == 0  # Likely skipped telemetry due to low priority
