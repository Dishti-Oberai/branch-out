import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from runner import TaskResult, load_tasks, summarize  # noqa: E402


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
