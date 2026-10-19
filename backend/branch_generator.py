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

_TARGET_FILE_GUIDANCE = """
If an "Other files that exist in this codebase" list is given above and the
Task is actually about one of those other files rather than the one shown
below, set "target_file" to that exact path from the list. Otherwise omit
it or set it to null — never invent a path that isn't in that list."""

# Phase 8 (2026-10-19): a request can genuinely need changes to MORE than
# one file (e.g. "change X in constants.py and Y in util.py") -- before
# this, the model had no way to express that at all, so it described the
# second file's change in prose and silently never produced real code for
# it. "code"/"target_file" stay the single primary file exactly as
# before; this is strictly additive for anything beyond that.
_ADDITIONAL_FILES_GUIDANCE = """
If the Task genuinely needs changes to files OTHER than the one you're
putting in "code" (not just the primary target), set "additional_files" to
an object mapping each such file's exact path (from the "Other files" list
if given, or any file you can see) to its FULL new content. Only ever
include a file there if you can actually see its real content somewhere
above -- never invent changes for a file you haven't been shown. Omit
"additional_files" or leave it empty for the normal, single-file case."""

_CODE_CHANGE_INSTRUCTION = """You are a coding assistant generating approach {approach} of {beam_width}.
Generate a MEANINGFULLY DIFFERENT approach from the others.
Make the requested change and explain it in "reasoning".""" + _TARGET_FILE_GUIDANCE + _ADDITIONAL_FILES_GUIDANCE + """
Return only JSON: {{"code": "...", "reasoning": "...", "is_code_change": true, "target_file": null, "additional_files": {{}}}}"""

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
#
# Real bug, found via manual testing (2026-10-17): "reasoning" is shown to
# the user as a standalone "just the answer" section (see frontend's
# deriveLatestAnswer), but the model was prefacing it with its own
# classification narration ("The user is asking a question about X, not
# requesting a code change...") before the actual answer -- because
# nothing told it to record that decision ONLY via is_code_change and
# otherwise keep quiet about it. Fixed by saying so explicitly.
_AUTO_INSTRUCTION = """You are a coding assistant generating approach {approach} of {beam_width}.
Generate a MEANINGFULLY DIFFERENT approach from the others.
Decide for yourself whether the Task below is actually asking for a code
change, or is a question, explanation, or discussion that doesn't need one.
Record that decision ONLY in "is_code_change" -- never narrate it in
"reasoning" (no "this is a question, not a code change" preamble). "reasoning"
must start directly with the substantive answer or explanation itself.
If it's a question: set "code" to the existing code unchanged (from
Context/Codebase above) and put your full answer in "reasoning".
If it's a change: make the change and explain it in "reasoning".
Even if the Task reads as feedback or conversation (e.g. "you should have
done X instead") rather than a direct instruction, you MUST still respond
with ONLY the JSON object below -- never plain prose with no JSON wrapper
at all.""" + _TARGET_FILE_GUIDANCE + _ADDITIONAL_FILES_GUIDANCE + """
Return only JSON: {{"code": "...", "reasoning": "...", "is_code_change": true|false, "target_file": null, "additional_files": {{}}}}"""

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
    code, reasoning, is_code_change, target_file, additional_files = _parse(response)
    return Branch(
        node_id="", code=code, reasoning=reasoning, is_code_change=is_code_change, target_file=target_file,
        additional_files=additional_files,
    )


def _extract_additional_files(data: dict) -> dict:
    """Phase 8 (2026-10-19): defensive extraction for "additional_files" --
    must be a dict of {str: str}, otherwise drop it entirely rather than
    let a malformed shape (e.g. a list, or non-string values) propagate
    into orchestrator._resolve_additional_edits as something it doesn't
    expect. Conservative by design, same as every other "uncertain? don't
    proceed" field here.
    """
    raw = data.get("additional_files")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def _parse(response: str):
    """Returns (code, reasoning, is_code_change, target_file,
    additional_files). `is_code_change` defaults to False whenever it's
    missing or the response doesn't parse at all — conservative by design
    (2026-10-17 amendment): when uncertain whether this was actually a
    code change, don't let a node claim `target_file` it may not deserve,
    same principle as every other "conflict ambiguous? don't silently
    proceed" rule in this codebase. `target_file` (Phase 7, 2026-10-17)
    defaults to None the same way -- None means "no redirect", i.e. use
    whatever target was already given. `additional_files` (Phase 8,
    2026-10-19) defaults to `{}` the same way -- empty means no files
    beyond the primary one.
    """
    text = response.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
        return (
            data["code"], data.get("reasoning", ""), bool(data.get("is_code_change", False)),
            data.get("target_file") or None, _extract_additional_files(data),
        )
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
        return (
            data["code"], data.get("reasoning", ""), bool(data.get("is_code_change", False)),
            data.get("target_file") or None, _extract_additional_files(data),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        # Model ignored the format entirely -- keep its output rather than
        # failing the turn, and is_code_change/target_file stay
        # conservative (False/None) so nothing gets misapplied to a file.
        #
        # Real bug, found via manual testing (2026-10-17): this used to put
        # the placeholder "(unparsed model output)" in "reasoning" (what
        # the UI shows as the node's standalone Response/answer) and dump
        # the model's ENTIRE raw text into "code" instead -- so when the
        # model answered in plain prose rather than JSON (which happens
        # more than it should when a prompt reads as feedback/conversation
        # rather than an explicit instruction, e.g. "you should have done
        # X instead"), its real, useful answer was hidden from the user
        # entirely unless they went digging in the Code snapshot section.
        # The raw text almost always IS the intended answer in this case --
        # surface it as the answer, not as code.
        return response, response, False, None, {}


def branch_label(index: int) -> str:
    # A..Z, then AA, AB, ... so labels stay unique for wide fan-outs
    s = ""
    i = index + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s
