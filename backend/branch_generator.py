import asyncio
import json
import logging
import re
from typing import Awaitable, Callable, List, Optional

from models import DEFAULT_BEAM_WIDTH, Branch

logger = logging.getLogger(__name__)

OnResult = Callable[[int, Optional[Branch]], Awaitable[None]]


async def generate_branches(
    prompt: str,
    context_summary: str,
    user_preferences: List[str],
    llm_client,
    beam_width: int = DEFAULT_BEAM_WIDTH,
    on_result: Optional[OnResult] = None,
) -> List[Branch]:
    """Generate `beam_width` branches in parallel.

    `on_result`, if given, is awaited with `(index, branch_or_None)` the
    moment EACH individual call resolves — not batched after the whole
    gather completes. Step 2.4 (`branch.ready` firing per branch as it
    completes) depends on this; `continue_from`/`edit_prompt` don't pass it
    and see no behavior change.
    """
    tasks = [
        _generate_and_notify(prompt, context_summary, user_preferences, llm_client, i, beam_width, on_result)
        for i in range(beam_width)
    ]
    results = await asyncio.gather(*tasks)
    return [b for b in results if b is not None]


async def _generate_and_notify(
    prompt, context, preferences, client, index, beam_width, on_result: Optional[OnResult]
) -> Optional[Branch]:
    try:
        branch = await _generate_single_branch(prompt, context, preferences, client, index, beam_width)
    except Exception as e:
        # Fail-open: one branch's LLM call failing must never fail the rest.
        logger.error("Branch %d generation failed: %s", index, e)
        if on_result:
            await on_result(index, None)
        return None
    if on_result:
        await on_result(index, branch)
    return branch


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
        pass

    # Real, reproducible model quirk (found via a real-API check, 2026-09-26):
    # the model occasionally drops the `n` in a `\n` escape inside the JSON
    # string, emitting a bare `\` followed by whitespace — invalid JSON,
    # always right where a newline+indent was clearly intended. Without
    # this repair, every branch that hits it silently stores the entire
    # unparsed JSON wrapper as "code" (guaranteed to fail every test run,
    # scored 0.0 for a parsing bug, not a bad solution). Repairing is safe:
    # a bare backslash is invalid JSON regardless, so this only ever fixes
    # a document that would otherwise be unusable anyway.
    repaired = re.sub(r"\\(?=[ \t])", r"\\n", text)
    try:
        data = json.loads(repaired)
        return data["code"], data.get("reasoning", "")
    except (json.JSONDecodeError, KeyError, TypeError):
        # Model ignored the format entirely; keep its output rather than
        # failing the turn.
        return response, "(unparsed model output)"


def branch_label(index: int) -> str:
    # A..Z, then AA, AB, ... so labels stay unique for wide fan-outs
    s = ""
    i = index + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s
