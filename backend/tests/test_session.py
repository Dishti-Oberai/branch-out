import asyncio

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
