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
    codebase_context: Optional[str] = None,
) -> List[Branch]:
    """Generate `beam_width` branches in parallel.

    `on_result`, if given, is awaited with `(index, branch_or_None)` the
    moment EACH individual call resolves — not batched after the whole
    gather completes. Step 2.4 (`branch.ready` firing per branch as it
    completes) depends on this; `continue_from`/`edit_prompt` don't pass it
    and see no behavior change.

    `codebase_context` (Phase 1 amendment, 2026-09-27): a static codebase
    snapshot, included as its own labeled section when given. The caller
    (Orchestrator) is responsible for deciding whether to pass it at all —
    this function doesn't know about `include_codebase`, it just includes
    whatever it's handed or omits the section entirely when it's None.
    Tokenetics (claude_client.py) is what's supposed to keep this from
    being a token-cost problem as it grows; this is the first real exercise
    of that on something other than ordinary conversational growth.
    """
    tasks = [
        _generate_and_notify(
            prompt, context_summary, user_preferences, llm_client, i, beam_width, on_result, codebase_context
        )
        for i in range(beam_width)
    ]
    results = await asyncio.gather(*tasks)
    return [b for b in results if b is not None]


async def _generate_and_notify(
    prompt, context, preferences, client, index, beam_width, on_result: Optional[OnResult], codebase_context
) -> Optional[Branch]:
    try:
        branch = await _generate_single_branch(
            prompt, context, preferences, client, index, beam_width, codebase_context
        )
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
    prompt, context, preferences, client, index, beam_width, codebase_context: Optional[str] = None
) -> Branch:
    """Caching fix (2026-09-27): `codebase_context`, when present, now goes
    into `system` instead of the per-call message — it's the same static
    text on every call within a session, and Tokenetics' cache-breakpoint
    stage can only ever anchor a cache_control marker on `system`/`tools`,
    never on `messages` (confirmed by reading the stage itself; it had no
    way to cache this before). The per-call-varying "approach N of M"
    instruction moves the other way, into the message, since it changes
    every single call anyway and was never a real caching candidate —
    mixing it into `system` would have made system differ call-to-call,
    breaking the exact-prefix match caching depends on.
    """
    prefs = f"\nUser preferences: {', '.join(preferences)}" if preferences else ""
    instruction = f"""You are a coding assistant generating approach {index + 1} of {beam_width}.
Generate a MEANINGFULLY DIFFERENT approach from the others.
Return only JSON: {{"code": "...", "reasoning": "..."}}"""
    system = codebase_context if codebase_context else None
    response = await client.complete(
        messages=[{
            "role": "user",
            "content": f"{instruction}\n\nContext: {context}{prefs}\n\nTask: {prompt}",
        }],
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
