import asyncio
import json

import pytest

from orchestrator import Orchestrator
from session import load_session, save_session
from tests.conftest import FakeLLM


def test_round_trip_with_branch_and_orphan(tmp_path):
    o = Orchestrator(FakeLLM())
    branches = asyncio.run(o.branch("root", "task", beam_width=3))
    asyncio.run(o.backtrack(branches[0].node_id))  # orphans branches[1], branches[2]
    asyncio.run(o.branch(branches[0].node_id, "step 2", beam_width=1))

    before = {n.id: n for n in o.tree.all_nodes()}
    path = str(tmp_path / "session.json")
    save_session(o.tree, path)
    loaded = load_session(path)
    after = {n.id: n for n in loaded.all_nodes()}

    assert before.keys() == after.keys()
    for nid in before:
        assert before[nid] == after[nid]
    assert loaded.active_id == o.tree.active_id


def test_save_to_bad_path_raises_and_is_recoverable(tmp_path):
    o = Orchestrator(FakeLLM())
    bad_path = "/root/definitely-not-writable/session.json"
    with pytest.raises(OSError):
        save_session(o.tree, bad_path)
    # session still usable afterwards
    assert o.tree.node_count() == 1


def test_load_malformed_file_raises_value_error(tmp_path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json")
    with pytest.raises(ValueError):
        load_session(str(bad_file))


def test_load_pre_phase3_session_file_without_target_file_keys(tmp_path):
    # A real session.json saved before Phase 3 existed has no
    # target_file/target_range keys anywhere. Must still load cleanly.
    old_format = {
        "nodes": [{
            "id": "root", "parent_id": None, "children_ids": [],
            "code_snapshot": "", "context_summary": "", "user_preferences": [],
            "score": None, "score_source": None, "status": "active",
            "query_history": [], "branch_label": "Root",
            "created_at": "2026-01-01T00:00:00",
        }]
    }
    path = tmp_path / "old.json"
    path.write_text(json.dumps(old_format))

    tree = load_session(str(path))
    node = tree.get_node("root")
    assert node.target_file is None
    assert node.target_range is None
