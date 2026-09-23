import asyncio
import json

from models import Branch
from score_normaliser import rank_branches
from tests.conftest import FakeLLM


def make_branches(n=3):
    return [Branch(node_id=f"n{i}", code=f"code {i}", reasoning=f"reason {i}") for i in range(n)]


def test_valid_response_sets_rank_and_score_on_all():
    branches = make_branches(3)
    valid = json.dumps([
        {"branch_index": 0, "rank": 2, "score": 0.5, "reason": "ok"},
        {"branch_index": 1, "rank": 1, "score": 0.9, "reason": "best"},
        {"branch_index": 2, "rank": 3, "score": 0.2, "reason": "worst"},
    ])
    llm = FakeLLM(response_fn=lambda i: valid)
    result = asyncio.run(rank_branches(branches, llm))
    assert [b.rank for b in result] == [2, 1, 3]
    assert [b.score for b in result] == [0.5, 0.9, 0.2]
    assert all(b.score_source == "llm_judge" for b in result)


def test_malformed_json_leaves_scores_none():
    branches = make_branches(3)
    llm = FakeLLM(response_fn=lambda i: "not json at all")
    result = asyncio.run(rank_branches(branches, llm))
    assert all(b.rank is None for b in result)
    assert all(b.score is None for b in result)
    assert all(b.score_source is None for b in result)


def test_missing_branch_index_fails_open():
    branches = make_branches(2)
    # only one entry for 2 branches -> KeyError on branch_index 1
    incomplete = json.dumps([{"branch_index": 0, "rank": 1, "score": 0.9, "reason": "x"}])
    llm = FakeLLM(response_fn=lambda i: incomplete)
    result = asyncio.run(rank_branches(branches, llm))
    assert all(b.score is None for b in result)


def test_exactly_one_llm_call_regardless_of_beam_width():
    branches = make_branches(5)
    valid = json.dumps([{"branch_index": i, "rank": i + 1, "score": 1.0 - i * 0.1, "reason": "x"} for i in range(5)])
    llm = FakeLLM(response_fn=lambda i: valid)
    asyncio.run(rank_branches(branches, llm))
    assert llm.calls == 1


def test_empty_branch_list_is_a_noop():
    llm = FakeLLM()
    result = asyncio.run(rank_branches([], llm))
    assert result == []
    assert llm.calls == 0
