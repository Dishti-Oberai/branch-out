import asyncio
import json
import time

from branch_generator import branch_label, generate_branches, _parse
from tests.conftest import FakeLLM

# Captured verbatim from a real Claude response during a real-API check
# (2026-09-26): the model dropped the `n` in a `\n` escape mid-string,
# producing a bare `\` followed by whitespace — invalid JSON. Without the
# repair in _parse(), this whole blob gets stored as literal "code",
# guaranteed to fail every test run. Truncated here to the part that
# reproduces the JSONDecodeError; the fix doesn't care about the rest.
REAL_MALFORMED_RESPONSE = (
    '{"code": "import unittest\\nimport math\\n\\ndef is_prime(n):\\    if n < 2:\\'
    '        return False\\    if n == 2:\\        return True\\    return n % 2 != 0", '
    '"reasoning": "Trial division."}'
)


def test_generate_branches_returns_one_per_beam_slot():
    llm = FakeLLM()
    branches = asyncio.run(generate_branches("task", "", [], llm, beam_width=3))
    assert len(branches) == 3
    assert all(b.code for b in branches)


def test_one_failed_call_still_returns_the_rest():
    llm = FakeLLM(fail_on={1})
    branches = asyncio.run(generate_branches("task", "", [], llm, beam_width=3))
    assert len(branches) == 2


def test_calls_are_concurrent_not_sequential():
    llm = FakeLLM(delay=0.2)
    start = time.time()
    asyncio.run(generate_branches("task", "", [], llm, beam_width=3))
    elapsed = time.time() - start
    assert elapsed < 0.2 * 3, "branch calls ran sequentially, not concurrently"


def test_malformed_json_keeps_raw_output_instead_of_failing():
    llm = FakeLLM(response_fn=lambda i: "not json")
    branches = asyncio.run(generate_branches("task", "", [], llm, beam_width=1))
    assert branches[0].code == "not json"


def test_parse_repairs_a_dropped_n_in_a_json_newline_escape():
    # This exact string previously raised JSONDecodeError and fell through
    # to the "unparsed model output" fallback, storing the raw JSON wrapper
    # (still containing literal backslashes, unresolved) as "code" — which
    # can never be valid Python. The repair must recover real code instead.
    code, reasoning, is_code_change, target_file = _parse(REAL_MALFORMED_RESPONSE)
    assert code.startswith("import unittest\nimport math\n\ndef is_prime(n):\n    if n < 2:")
    assert reasoning == "Trial division."
    compile(code, "<candidate>", "exec")  # must be valid Python, not just non-empty


def test_parse_still_falls_back_when_truly_unparseable():
    # 2026-10-17 fix: the raw text becomes BOTH code and reasoning now --
    # it's almost always the model's real (prose) answer when it ignores
    # the JSON format, so it must surface where the UI shows "the answer"
    # (reasoning), not be hidden behind a placeholder label.
    code, reasoning, is_code_change, target_file = _parse("this is not json and has no recoverable structure at all")
    assert code == "this is not json and has no recoverable structure at all"
    assert reasoning == "this is not json and has no recoverable structure at all"
    assert is_code_change is False  # conservative default when unparseable
    assert target_file is None  # conservative default when unparseable


def test_branch_label_sequence():
    assert [branch_label(i) for i in range(3)] == ["A", "B", "C"]
    assert branch_label(26) == "AA"


def test_on_result_fires_per_branch_as_it_completes_including_failures():
    llm = FakeLLM(fail_on={1})
    seen = []

    async def on_result(index, branch):
        seen.append((index, branch))

    asyncio.run(generate_branches("task", "", [], llm, beam_width=3, on_result=on_result))

    assert sorted(i for i, _ in seen) == [0, 1, 2]
    by_index = dict(seen)
    assert by_index[1] is None  # the failed one
    assert by_index[0] is not None and by_index[2] is not None


def test_parse_extracts_is_code_change_true():
    code, reasoning, is_code_change, target_file = _parse('{"code": "x = 1", "reasoning": "r", "is_code_change": true}')
    assert is_code_change is True


def test_parse_extracts_is_code_change_false():
    code, reasoning, is_code_change, target_file = _parse('{"code": "x = 1", "reasoning": "r", "is_code_change": false}')
    assert is_code_change is False


def test_parse_defaults_is_code_change_false_when_field_missing():
    # The model is asked for it, but if it ever omits the field entirely,
    # stay conservative rather than assuming a code change happened.
    code, reasoning, is_code_change, target_file = _parse('{"code": "x = 1", "reasoning": "r"}')
    assert is_code_change is False


def test_parse_extracts_target_file_when_model_declares_one():
    code, reasoning, is_code_change, target_file = _parse(
        '{"code": "x = 1", "reasoning": "r", "is_code_change": true, "target_file": "util.py"}'
    )
    assert target_file == "util.py"


def test_parse_defaults_target_file_none_when_missing():
    code, reasoning, is_code_change, target_file = _parse(
        '{"code": "x = 1", "reasoning": "r", "is_code_change": true}'
    )
    assert target_file is None


def test_parse_defaults_target_file_none_when_explicitly_null():
    code, reasoning, is_code_change, target_file = _parse(
        '{"code": "x = 1", "reasoning": "r", "is_code_change": true, "target_file": null}'
    )
    assert target_file is None
