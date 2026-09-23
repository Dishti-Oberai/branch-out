"""Step 2.3 — hybrid evaluator: test-gate first, LLM judge fallback.

Deviation from ROADMAP.md's literal snippet, documented in ROADMAP.md and
CLAUDE.md: retroactive rescoring requires mutating `score`/`score_source`
on already-created nodes, which the append-only section's original wording
("only status and children_ids") didn't list. Treated as a second mutable
metadata field alongside status — see tree_state.py's set_score().
"""
from __future__ import annotations

import logging
from typing import List

from models import Branch, TreeNode
from score_normaliser import rank_branches
from test_runner import has_test_imports, run_tests
from tree_state import TreeStateManager

logger = logging.getLogger(__name__)


def _check_for_tests(branches: List[Branch]) -> bool:
    return any(has_test_imports(b.code) for b in branches)


async def evaluate_branches(branches: List[Branch], llm_client) -> List[Branch]:
    """Test-gate first. LLM judge fallback if no tests. Returns ranked branches."""
    if not branches:
        return branches

    if _check_for_tests(branches):
        for b in branches:
            try:
                result = run_tests(b.code)
                b.score = result.pass_rate
            except Exception as e:  # noqa: BLE001 — one broken branch must not sink the rest
                logger.error("Test run failed for a branch, scoring it 0.0: %s", e)
                b.score = 0.0
            b.score_source = "tests"
    else:
        branches = await rank_branches(branches, llm_client)

    return sorted(branches, key=lambda b: b.score or 0, reverse=True)


def rescore_with_tests(node_ids: List[str], tree: TreeStateManager) -> List[TreeNode]:
    """Retroactive re-scoring: re-runs the test runner on each given node's
    `code_snapshot` and updates its score/score_source in place. Returns the
    updated nodes (for the caller to broadcast `score.ready` on) — never
    touches nodes not named in `node_ids`.

    Synchronous and blocking (subprocess.run underneath) — callers on the
    event loop should run this via asyncio.to_thread, not await it directly.
    """
    updated: List[TreeNode] = []
    for node_id in node_ids:
        node = tree.get_node(node_id)
        try:
            result = run_tests(node.code_snapshot)
        except Exception as e:  # noqa: BLE001 — one broken node must not sink the rest
            logger.error("Retroactive rescoring failed for %s: %s", node_id, e)
            continue
        tree.set_score(node_id, result.pass_rate, "tests")
        updated.append(tree.get_node(node_id))
    return updated
