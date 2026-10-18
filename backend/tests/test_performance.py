"""Phase 6.2 (2026-10-18): "test performance at 50 nodes" from the ROADMAP's
risk register — large sessions were never actually measured before. Builds
a real 50-node tree (via FakeLLM, no real API cost) with a realistic mix of
branching/continuing/backtracking, then times the operations an active
session repeatedly exercises: activation (backtrack's orphan/revive sweep,
which walks every node), and full-session serialization (what GET /session
does on every poll). Generous ceilings -- this isn't a tight perf budget,
it's a regression trip-wire for an accidental O(n^2) creeping into
tree_state.py or node_to_dict.
"""
import asyncio
import time

from models import node_to_dict
from orchestrator import Orchestrator
from tests.conftest import FakeLLM


def _build_50_node_tree() -> Orchestrator:
    llm = FakeLLM(response_fn=lambda i: '{"code": "x", "reasoning": "r", "is_code_change": true}')
    o = Orchestrator(llm)
    current = "root"
    count = 1
    while count < 50:
        if count % 5 == 0:
            # Every 5th step, fan out 3 siblings and continue down one of them.
            branches = asyncio.run(o.branch(current, f"task {count}", beam_width=min(3, 50 - count)))
            count += len(branches)
            current = branches[0].node_id if branches else current
        else:
            node = asyncio.run(o.continue_from(current, f"task {count}"))
            current = node.id
            count += 1
    return o


def test_50_node_tree_builds_and_serializes_within_a_generous_time_budget():
    start = time.monotonic()
    o = _build_50_node_tree()
    build_elapsed = time.monotonic() - start
    all_nodes = o.all_nodes()
    assert len(all_nodes) >= 50

    start = time.monotonic()
    serialized = [node_to_dict(n) for n in all_nodes]
    serialize_elapsed = time.monotonic() - start
    assert len(serialized) == len(all_nodes)

    # Backtrack to root and back to the deepest node -- exercises the
    # orphan/revive sweep (_activate_unlocked), which walks every node on
    # every call, twice.
    start = time.monotonic()
    asyncio.run(o.backtrack("root"))
    asyncio.run(o.backtrack(all_nodes[-1].id))
    activate_elapsed = time.monotonic() - start

    # Generous ceilings (FakeLLM has no real network latency, so this is
    # measuring OUR code's overhead, not the LLM's) -- these catch an
    # accidental O(n^2)/O(n^3), not a tight budget.
    assert build_elapsed < 5.0, f"building 50 nodes took {build_elapsed:.3f}s"
    assert serialize_elapsed < 0.5, f"serializing 50 nodes took {serialize_elapsed:.3f}s"
    assert activate_elapsed < 0.5, f"two activations over 50 nodes took {activate_elapsed:.3f}s"
