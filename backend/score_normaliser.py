"""Step 2.2 — LLM-judge ranking, used only when no branch has tests.

One LLM call ranks every branch at once (never one call per branch — that
would be N calls for N branches, and relative ranking needs to see all of
them together anyway). Fail-open: any failure here leaves every branch's
rank/score as None rather than guessing.
"""
from __future__ import annotations

import json
import logging
from typing import List

from models import Branch

logger = logging.getLogger(__name__)

_SYSTEM_TEMPLATE = """You are ranking {n} candidate solutions to the same coding task, best to worst.
Judge on correctness, clarity, and idiomatic style.
Return ONLY a JSON array, one object per candidate, in exactly this shape:
[{{"branch_index": 0, "rank": 1, "score": 0.87, "reason": "..."}}, ...]
rank 1 is best. score is a float from 0.0 to 1.0. Include every branch_index exactly once, no extras."""


async def rank_branches(branches: List[Branch], llm_client) -> List[Branch]:
    """Send all branches to the LLM in a single call for relative ranking.

    Returns the same branches with `rank` (1 = best) and `score` (0.0-1.0)
    set on each. On any failure — LLM error, malformed JSON, a missing or
    out-of-range branch_index — every branch's rank/score is left as None.
    Never raises: evaluation is informational, not load-bearing.
    """
    if not branches:
        return branches

    system = _SYSTEM_TEMPLATE.format(n=len(branches))
    candidates = "\n\n".join(
        f"--- Candidate {i} ---\nCode:\n{b.code}\nReasoning: {b.reasoning}"
        for i, b in enumerate(branches)
    )

    try:
        response = await llm_client.complete(
            messages=[{"role": "user", "content": candidates}],
            system=system,
        )
        results = _parse(response)
        by_index = {int(item["branch_index"]): item for item in results}
        for i, b in enumerate(branches):
            item = by_index[i]  # KeyError -> caught below, fails the whole batch open
            b.rank = int(item["rank"])
            b.score = float(item["score"])
            b.score_source = "llm_judge"
    except Exception as e:  # noqa: BLE001 — deliberately broad: fail-open, never raise
        logger.error("Score normalisation failed, leaving ranks/scores unset: %s", e)
        for b in branches:
            b.rank = None
            b.score = None
            b.score_source = None
    return branches


def _parse(response: str):
    text = response.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text)
