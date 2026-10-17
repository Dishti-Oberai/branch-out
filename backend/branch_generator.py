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
    intent: str = "auto",
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

    `intent` (2026-10-17 amendment) — one of "auto"/"question"/"code_change":
    decides what instruction the model is given, see `_generate_single_branch`.
    """
    tasks = [
        _generate_and_notify(
            prompt, context_summary, user_preferences, llm_client, i, beam_width, on_result, codebase_context, intent
        )
        for i in range(beam_width)
    ]
    results = await asyncio.gather(*tasks)
    return [b for b in results if b is not None]


async def _generate_and_notify(
    prompt, context, preferences, client, index, beam_width, on_result: Optional[OnResult], codebase_context, intent
) -> Optional[Branch]:
    try:
        branch = await _generate_single_branch(
            prompt, context, preferences, client, index, beam_width, codebase_context, intent
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


_QUESTION_INSTRUCTION = """You are a coding assistant generating approach {approach} of {beam_width}.
This is a QUESTION, not a change request. Put your full answer in "reasoning"
and set "code" to the existing code unchanged (from Context/Codebase above).
Return only JSON: {{"code": "...", "reasoning": "...", "is_code_change": false}}"""

_CODE_CHANGE_INSTRUCTION = """You are a coding assistant generating approach {approach} of {beam_width}.
Generate a MEANINGFULLY DIFFERENT approach from the others.
Make the requested change and explain it in "reasoning".
Return only JSON: {{"code": "...", "reasoning": "...", "is_code_change": true}}"""

# Amendment (2026-10-17): "auto" is the model's own honest classification
# instead of being told the answer in advance -- a real bug (found via a
# user's own manual testing) showed a forced "this is a question" framing
# overriding the model's own correct judgment: it explicitly noticed the
# mismatch ("this sounds like a code change request... however the system
# prompt classifies this as a QUESTION") and obeyed the wrong framing
# anyway. Deliberately no bias toward either answer in "auto"; the
# orchestrator decides what to do with the classification (keeps
# target_file only when this comes back true). "question"/"code_change"
# are explicit overrides -- the user's own stated intent always wins over
# the model's guess, since it's the cheaper, more reliable signal.
_AUTO_INSTRUCTION = """You are a coding assistant generating approach {approach} of {beam_width}.
Generate a MEANINGFULLY DIFFERENT approach from the others.
Decide for yourself whether the Task below is actually asking for a code
change, or is a question, explanation, or discussion that doesn't need one.
If it's a question: set "code" to the existing code unchanged (from
Context/Codebase above) and put your full answer in "reasoning".
If it's a change: make the change and explain it in "reasoning".
Return only JSON: {{"code": "...", "reasoning": "...", "is_code_change": true|false}}"""

_INSTRUCTIONS = {"question": _QUESTION_INSTRUCTION, "code_change": _CODE_CHANGE_INSTRUCTION, "auto": _AUTO_INSTRUCTION}


async def _generate_single_branch(
    prompt, context, preferences, client, index, beam_width, codebase_context: Optional[str] = None,
    intent: str = "auto",
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
    instruction = _INSTRUCTIONS.get(intent, _AUTO_INSTRUCTION).format(approach=index + 1, beam_width=beam_width)
    system = codebase_context if codebase_context else None
    response = await client.complete(
        messages=[{
            "role": "user",
            "content": f"{instruction}\n\nContext: {context}{prefs}\n\nTask: {prompt}",
        }],
        system=system,
    )
    code, reasoning, is_code_change = _parse(response)
    return Branch(node_id="", code=code, reasoning=reasoning, is_code_change=is_code_change)


def _parse(response: str):
    """Returns (code, reasoning, is_code_change). `is_code_change` defaults
    to False whenever it's missing or the response doesn't parse at all —
    conservative by design (2026-10-17 amendment): when uncertain whether
    this was actually a code change, don't let a node claim `target_file`
    it may not deserve, same principle as every other "conflict ambiguous?
    don't silently proceed" rule in this codebase.
    """
    text = response.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
        return data["code"], data.get("reasoning", ""), bool(data.get("is_code_change", False))
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
        return data["code"], data.get("reasoning", ""), bool(data.get("is_code_change", False))
    except (json.JSONDecodeError, KeyError, TypeError):
        # Model ignored the format entirely; keep its output rather than
        # failing the turn. Unparseable -> conservative default False.
        return response, "(unparsed model output)", False


def branch_label(index: int) -> str:
    # A..Z, then AA, AB, ... so labels stay unique for wide fan-outs
    s = ""
    i = index + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s
