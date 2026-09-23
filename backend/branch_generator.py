import asyncio
import json
import logging
import re
from typing import List

from models import DEFAULT_BEAM_WIDTH, Branch

logger = logging.getLogger(__name__)


async def generate_branches(
    prompt: str,
    context_summary: str,
    user_preferences: List[str],
    llm_client,
    beam_width: int = DEFAULT_BEAM_WIDTH,
) -> List[Branch]:
    tasks = [
        _generate_single_branch(prompt, context_summary, user_preferences, llm_client, i, beam_width)
        for i in range(beam_width)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    branches: List[Branch] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            # Fail-open: one branch's LLM call failing must never fail the rest.
            logger.error("Branch %d generation failed: %s", i, r)
            continue
        branches.append(r)
    return branches


async def _generate_single_branch(
    prompt, context, preferences, client, index, beam_width
) -> Branch:
    prefs = f"\nUser preferences: {', '.join(preferences)}" if preferences else ""
    system = f"""You are a coding assistant generating approach {index + 1} of {beam_width}.
Generate a MEANINGFULLY DIFFERENT approach from the others.
Return only JSON: {{"code": "...", "reasoning": "..."}}"""
    response = await client.complete(
        messages=[{"role": "user", "content": f"Context: {context}{prefs}\n\nTask: {prompt}"}],
        system=system,
    )
    code, reasoning = _parse(response)
    return Branch(node_id="", code=code, reasoning=reasoning)


def _parse(response: str):
    text = response.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
        return data["code"], data.get("reasoning", "")
    except (json.JSONDecodeError, KeyError, TypeError):
        # Model ignored the format; keep its output rather than failing the turn.
        return response, "(unparsed model output)"


def branch_label(index: int) -> str:
    # A..Z, then AA, AB, ... so labels stay unique for wide fan-outs
    s = ""
    i = index + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s
