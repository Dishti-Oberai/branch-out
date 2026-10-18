import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from runner import TaskResult, _extract_code, load_tasks, summarize  # noqa: E402


def test_load_tasks_finds_all_real_task_files():
    tasks = load_tasks()
    assert len(tasks) == 15
    assert sum(1 for t in tasks if t.category == "standard") == 10
    assert sum(1 for t in tasks if t.category == "custom") == 5
    assert len({t.id for t in tasks}) == len(tasks)  # no duplicate ids


def test_every_task_has_all_required_fields_nonempty():
    for t in load_tasks():
        assert t.id and t.description and t.prompt and t.test_suite and t.category
        assert t.category in ("standard", "custom")


def test_summarize_is_per_mode_per_category_never_flat():
    results = [
        TaskResult("standard_001", "standard", "baseline-generic", 1.0, True),
        TaskResult("standard_002", "standard", "baseline-generic", 0.0, False),
        TaskResult("custom_001", "custom", "baseline-generic", 1.0, True),
        TaskResult("standard_001", "standard", "branchout", 1.0, True),
        TaskResult("standard_002", "standard", "branchout", 1.0, True),
        TaskResult("custom_001", "custom", "branchout", 0.5, False),
    ]
    summary = summarize(results)
    assert summary["baseline-generic"]["standard"]["n"] == 2
    assert summary["baseline-generic"]["standard"]["pass@1"] == 0.5
    assert summary["baseline-generic"]["custom"]["n"] == 1
    assert summary["branchout"]["standard"]["pass@1"] == 1.0
    assert summary["branchout"]["custom"]["pass@1"] == 0.0
    assert summary["branchout"]["custom"]["mean_score"] == 0.5
    # standard and custom are never merged into one number
    assert "standard" in summary["baseline-generic"] and "custom" in summary["baseline-generic"]


def test_summarize_excludes_errored_tasks_from_scores_but_counts_them():
    # 2026-10-18 fix: a crashed call (auth/network/rate-limit) must never
    # count as the model trying and scoring 0 -- this is the exact bug
    # that produced a fully-populated, entirely fake "0% across the board"
    # benchmark result on this runner's actual first real-API run.
    results = [
        TaskResult("standard_001", "standard", "baseline-generic", 1.0, True),
        TaskResult("standard_002", "standard", "baseline-generic", 0.0, False, error="auth failed"),
    ]
    summary = summarize(results)
    cat = summary["baseline-generic"]["standard"]
    assert cat["n"] == 1  # only the one that actually ran
    assert cat["errored"] == 1
    assert cat["pass@1"] == 1.0  # not dragged down by the crash
    assert cat["mean_score"] == 1.0


def test_extract_code_finds_a_fenced_block_preceded_by_prose():
    # Real bug (2026-10-18): a response that leads with prose/headers before
    # the fence (Claude's normal, unsteered behavior) used to fail entirely
    # -- the old check only stripped a fence at the very START of the text.
    response = (
        "## FizzBuzz in Python\n\n"
        "```python\ndef fizzbuzz(n):\n    return n\n```\n\n"
        "### How it works:\n| Condition | Output |\n"
    )
    assert _extract_code(response) == "def fizzbuzz(n):\n    return n"


def test_extract_code_still_handles_a_fence_at_the_very_start():
    response = "```python\nx = 1\n```"
    assert _extract_code(response) == "x = 1"


def test_extract_code_still_handles_plain_json():
    response = '{"code": "x = 1", "reasoning": "r"}'
    assert _extract_code(response) == "x = 1"


def test_extract_code_falls_back_to_raw_text_with_no_fence_or_json():
    assert _extract_code("just plain code, no markdown") == "just plain code, no markdown"


def test_summarize_reports_none_scores_when_every_task_in_a_category_errored():
    results = [TaskResult("standard_001", "standard", "baseline-generic", 0.0, False, error="auth failed")]
    summary = summarize(results)
    cat = summary["baseline-generic"]["standard"]
    assert cat["n"] == 0
    assert cat["errored"] == 1
    assert cat["pass@1"] is None
    assert cat["mean_score"] is None
