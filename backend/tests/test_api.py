import json
import time

import pytest
from fastapi.testclient import TestClient

import main
from orchestrator import Orchestrator
from tests.conftest import FakeLLM


@pytest.fixture
def client():
    main._orchestrator = Orchestrator(FakeLLM())
    main._workspace_root = None
    main._source_snapshots.clear()
    main._file_last_synced.clear()
    main._active_editor_context = None
    return TestClient(main.app)


def test_branch_returns_branches(client):
    r = client.post("/branch", json={"node_id": "root", "prompt": "hi"})
    assert r.status_code == 200
    branches = r.json()["branches"]
    assert len(branches) == 3
    assert all(b["node_id"] for b in branches)


def test_backtrack_returns_target_node(client):
    branches = client.post("/branch", json={"node_id": "root", "prompt": "hi"}).json()["branches"]
    target = branches[0]["node_id"]
    r = client.post("/backtrack", json={"node_id": target})
    assert r.status_code == 200
    assert r.json()["node"]["id"] == target
    assert r.json()["node"]["status"] == "active"


def test_backtrack_unknown_node_is_404(client):
    assert client.post("/backtrack", json={"node_id": "nope"}).status_code == 404


def test_session_returns_all_nodes(client):
    client.post("/branch", json={"node_id": "root", "prompt": "hi"})
    r = client.get("/session")
    assert len(r.json()["nodes"]) == 4  # root + 3 branches


def test_save_and_load_round_trip(client, tmp_path):
    client.post("/branch", json={"node_id": "root", "prompt": "hi"})
    before = client.get("/session").json()["nodes"]

    path = str(tmp_path / "s.json")
    save = client.post("/session/save", json={"path": path})
    assert save.status_code == 200

    load = client.post("/session/load", json={"path": path})
    assert load.status_code == 200
    assert client.get("/session").json()["nodes"] == before


def test_continue_adds_single_child(client):
    r = client.post("/continue", json={"node_id": "root", "prompt": "hi"})
    assert r.status_code == 200
    assert r.json()["node"]["parent_id"] == "root"
    assert r.json()["node"]["status"] == "active"
    assert len(client.get("/session").json()["nodes"]) == 2


def test_asking_twice_without_reselecting_continues_the_thread(client):
    # Regression: the second Ask must target the node the first one just
    # created (now active), not fan out another child of the original node.
    first = client.post("/continue", json={"node_id": "root", "prompt": "hi"}).json()["node"]
    second = client.post("/continue", json={"node_id": first["id"], "prompt": "hi again"}).json()["node"]
    assert second["parent_id"] == first["id"]
    nodes = client.get("/session").json()["nodes"]
    root = next(n for n in nodes if n["id"] == "root")
    assert len(root["children_ids"]) == 1


def test_ws_accepts_connection(client):
    with client.websocket_connect("/ws") as ws:
        ws.close()


def test_edit_prompt_returns_new_node_and_stale_ids(client):
    node = client.post("/continue", json={"node_id": "root", "prompt": "original"}).json()["node"]

    r = client.post("/edit-prompt", json={"node_id": node["id"], "new_prompt": "revised"})
    assert r.status_code == 200
    body = r.json()
    assert body["new_node"]["parent_id"] == "root"
    assert body["stale_node_ids"] == [node["id"]]

    nodes = {n["id"]: n for n in client.get("/session").json()["nodes"]}
    assert nodes[node["id"]]["status"] == "stale"
    assert nodes[node["id"]]["code_snapshot"] == node["code_snapshot"]  # untouched


def test_edit_prompt_on_root_is_400(client):
    r = client.post("/edit-prompt", json={"node_id": "root", "new_prompt": "x"})
    assert r.status_code == 400


def test_continue_llm_failure_is_502_not_500(client):
    main._orchestrator = Orchestrator(FakeLLM(fail_on={0}))
    r = client.post("/continue", json={"node_id": "root", "prompt": "hi"})
    assert r.status_code == 502


# ---- Phase 2: hybrid evaluator + WebSocket wiring -------------------------

def test_branch_ready_events_fire_before_score_ready(client):
    with client.websocket_connect("/ws") as ws:
        r = client.post("/branch", json={"node_id": "root", "prompt": "hi"})
        assert r.status_code == 200

        events = [ws.receive_json() for _ in range(6)]  # 3 branch.ready + 3 score.ready

    kinds = [e["event"] for e in events]
    assert kinds.count("branch.ready") == 3
    assert kinds.count("score.ready") == 3
    last_branch_ready = max(i for i, k in enumerate(kinds) if k == "branch.ready")
    first_score_ready = min(i for i, k in enumerate(kinds) if k == "score.ready")
    assert last_branch_ready < first_score_ready

    node_ids = {b["node_id"] for b in r.json()["branches"]}
    branch_ready_events = [e for e in events if e["event"] == "branch.ready"]
    assert {e["branch"]["node_id"] for e in branch_ready_events} == node_ids
    score_ready_events = [e for e in events if e["event"] == "score.ready"]
    assert {e["node_id"] for e in score_ready_events} == node_ids


def test_score_ready_uses_test_scoring_when_code_has_tests(client):
    llm = FakeLLM(response_fn=lambda i: json.dumps({
        "code": "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(1+1, 2)",
        "reasoning": "r",
    }))
    main._orchestrator = Orchestrator(llm)
    with client.websocket_connect("/ws") as ws:
        client.post("/branch", json={"node_id": "root", "prompt": "hi", "beam_width": 1})
        events = [ws.receive_json() for _ in range(2)]  # 1 branch.ready + 1 score.ready

    score_event = next(e for e in events if e["event"] == "score.ready")
    assert score_event["score"] == 1.0
    assert score_event["score_source"] == "tests"


def test_session_load_triggers_retroactive_rescore_for_llm_judge_nodes_with_tests(client, tmp_path):
    session_data = {
        "nodes": [
            {
                "id": "root", "parent_id": None, "children_ids": ["a"],
                "code_snapshot": "", "context_summary": "", "user_preferences": [],
                "score": None, "score_source": None, "status": "normal",
                "query_history": [], "branch_label": "Root",
                "created_at": "2026-01-01T00:00:00",
            },
            {
                "id": "a", "parent_id": "root", "children_ids": [],
                "code_snapshot": (
                    "import unittest\nclass T(unittest.TestCase):\n"
                    "    def test_x(self):\n        self.assertEqual(1 + 1, 2)"
                ),
                "context_summary": "x", "user_preferences": [],
                "score": 0.5, "score_source": "llm_judge", "status": "active",
                "query_history": [], "branch_label": "Branch A",
                "created_at": "2026-01-01T00:00:00",
            },
        ]
    }
    path = tmp_path / "s.json"
    path.write_text(json.dumps(session_data))

    r = client.post("/session/load", json={"path": str(path)})
    assert r.status_code == 200

    for _ in range(40):
        nodes = {n["id"]: n for n in client.get("/session").json()["nodes"]}
        if nodes["a"]["score_source"] == "tests":
            break
        time.sleep(0.05)
    else:
        pytest.fail("retroactive rescore did not complete in time")

    assert nodes["a"]["score"] == 1.0


def test_session_load_does_not_touch_nodes_already_test_scored_or_without_tests(client, tmp_path):
    session_data = {
        "nodes": [
            {
                "id": "root", "parent_id": None, "children_ids": ["a", "b"],
                "code_snapshot": "", "context_summary": "", "user_preferences": [],
                "score": None, "score_source": None, "status": "active",
                "query_history": [], "branch_label": "Root",
                "created_at": "2026-01-01T00:00:00",
            },
            {  # already tests-scored -- must not be re-touched
                "id": "a", "parent_id": "root", "children_ids": [],
                "code_snapshot": "import unittest",
                "context_summary": "x", "user_preferences": [],
                "score": 0.42, "score_source": "tests", "status": "normal",
                "query_history": [], "branch_label": "Branch A",
                "created_at": "2026-01-01T00:00:00",
            },
            {  # llm_judge but no tests in the code -- not eligible for rescore
                "id": "b", "parent_id": "root", "children_ids": [],
                "code_snapshot": "def f(): pass",
                "context_summary": "x", "user_preferences": [],
                "score": 0.9, "score_source": "llm_judge", "status": "normal",
                "query_history": [], "branch_label": "Branch B",
                "created_at": "2026-01-01T00:00:00",
            },
        ]
    }
    path = tmp_path / "s.json"
    path.write_text(json.dumps(session_data))

    client.post("/session/load", json={"path": str(path)})
    time.sleep(0.2)  # give any (wrongly-fired) background task a chance to run
    nodes = {n["id"]: n for n in client.get("/session").json()["nodes"]}
    assert nodes["a"]["score"] == 0.42
    assert nodes["a"]["score_source"] == "tests"
    assert nodes["b"]["score"] == 0.9
    assert nodes["b"]["score_source"] == "llm_judge"


# ---- Phase 3: VS Code extension (schema + apply-to-file) ------------------

def test_branch_accepts_target_file_and_range(client):
    r = client.post("/branch", json={
        "node_id": "root", "prompt": "hi", "beam_width": 1,
        "target_file": "src/foo.py",
        "target_range": {"start_line": 1, "end_line": 5},
    })
    assert r.status_code == 200
    node = client.get("/session").json()["nodes"][1]
    assert node["target_file"] == "src/foo.py"
    assert node["target_range"] == {"start_line": 1, "end_line": 5}


def test_continue_accepts_target_file_and_range(client):
    r = client.post("/continue", json={
        "node_id": "root", "prompt": "hi",
        "target_file": "src/bar.py",
        "target_range": {"start_line": 2, "end_line": 4},
    })
    assert r.status_code == 200
    assert r.json()["node"]["target_file"] == "src/bar.py"


def test_branch_without_target_file_leaves_it_null(client):
    r = client.post("/branch", json={"node_id": "root", "prompt": "hi", "beam_width": 1})
    node = client.get("/session").json()["nodes"][1]
    assert node["target_file"] is None
    assert node["target_range"] is None


def test_set_workspace_rejects_non_directory(client, tmp_path):
    not_a_dir = tmp_path / "nope.txt"
    not_a_dir.write_text("x")
    r = client.post("/workspace", json={"root": str(not_a_dir)})
    assert r.status_code == 400


def test_apply_to_file_writes_code_snapshot(client, tmp_path):
    client.post("/workspace", json={"root": str(tmp_path)})
    node = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "target_file": "src/out.py",
    }).json()["node"]

    r = client.post("/apply-to-file", json={"node_id": node["id"]})
    assert r.status_code == 200
    written = tmp_path / "src" / "out.py"
    assert written.read_text() == node["code_snapshot"]
    assert r.json()["path"] == str(written)


def test_apply_to_file_also_writes_additional_edits(client, tmp_path):
    # Phase 8 (2026-10-19): a /branch result (not yet auto-activated) can
    # also carry additional_edits -- manually applying it via this
    # endpoint must write those too, not just the primary target_file.
    (tmp_path / "constants.py").write_text("MAX_RETRIES = 5\n")
    (tmp_path / "util.py").write_text("from constants import MAX_RETRIES\n")
    main._orchestrator = Orchestrator(FakeLLM(response_fn=lambda i: json.dumps({
        "code": "MAX_RETRIES = 7\n", "reasoning": "r", "is_code_change": True,
        "additional_files": {"util.py": "from constants import MAX_RETRIES\nprint(MAX_RETRIES)\n"},
    })))
    client.post("/workspace", json={"root": str(tmp_path)})

    branches = client.post("/branch", json={
        "node_id": "root", "prompt": "change retries and add a print in util.py", "beam_width": 1,
        "target_file": "constants.py",
    }).json()["branches"]
    assert (tmp_path / "util.py").read_text() == "from constants import MAX_RETRIES\n"  # not yet applied

    client.post("/apply-to-file", json={"node_id": branches[0]["node_id"]})
    assert (tmp_path / "constants.py").read_text() == "MAX_RETRIES = 7\n"
    assert (tmp_path / "util.py").read_text() == "from constants import MAX_RETRIES\nprint(MAX_RETRIES)\n"


def test_apply_to_file_with_target_range_splices_instead_of_overwriting(client, tmp_path):
    # Real bug (2026-09-27): a node created from a *selection* only ever
    # holds that selection's code — applying it as a full-file overwrite
    # silently destroyed everything else in the file. Fix: splice into just
    # target_range's lines when one is set.
    original = "line1\nline2\nline3\nline4\nline5\n"
    (tmp_path / "out.py").write_text(original)
    client.post("/workspace", json={"root": str(tmp_path)})

    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: json.dumps({"code": "REPLACED-2\nREPLACED-3\n", "reasoning": "r", "is_code_change": True})
    ))
    node = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "target_file": "out.py",
        "target_range": {"start_line": 2, "end_line": 3},
    }).json()["node"]

    r = client.post("/apply-to-file", json={"node_id": node["id"]})
    assert r.status_code == 200
    assert (tmp_path / "out.py").read_text() == "line1\nREPLACED-2\nREPLACED-3\nline4\nline5\n"


def test_apply_to_file_without_target_range_still_overwrites_whole_file(client, tmp_path):
    (tmp_path / "out.py").write_text("old content\n")
    client.post("/workspace", json={"root": str(tmp_path)})
    node = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "target_file": "out.py",
    }).json()["node"]

    r = client.post("/apply-to-file", json={"node_id": node["id"]})
    assert r.status_code == 200
    assert (tmp_path / "out.py").read_text() == node["code_snapshot"]


def test_apply_to_file_rejects_node_without_target_file(client, tmp_path):
    client.post("/workspace", json={"root": str(tmp_path)})
    node = client.post("/continue", json={"node_id": "root", "prompt": "hi"}).json()["node"]
    r = client.post("/apply-to-file", json={"node_id": node["id"]})
    assert r.status_code == 400


def test_apply_to_file_rejects_without_workspace_declared(client, tmp_path):
    node = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "target_file": "out.py",
    }).json()["node"]
    r = client.post("/apply-to-file", json={"node_id": node["id"]})
    assert r.status_code == 400


def test_apply_to_file_rejects_path_traversal_outside_workspace(client, tmp_path):
    client.post("/workspace", json={"root": str(tmp_path)})
    node = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "target_file": "../../etc/passwd",
    }).json()["node"]
    r = client.post("/apply-to-file", json={"node_id": node["id"]})
    assert r.status_code == 400


def test_apply_to_file_unknown_node_is_404(client, tmp_path):
    client.post("/workspace", json={"root": str(tmp_path)})
    r = client.post("/apply-to-file", json={"node_id": "nope"})
    assert r.status_code == 404


def test_cors_allows_arbitrary_origins_including_vscode_webview(client):
    # Regression: CORS used to allowlist only http://localhost:5173 (the
    # web app's dev-server origin), which silently rejected every request
    # from a VS Code webview (origin vscode-webview://<random-id>) — found
    # as a real "Failed to fetch" bug during the VS Code extension's first
    # real-world test. Any origin must be allowed now.
    webview_origin = "vscode-webview://abc123"
    r = client.get("/session", headers={"Origin": webview_origin})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") in ("*", webview_origin)


# ---- Phase 1/3 amendment (2026-09-27): codebase-aware context ------------

def test_workspace_gathers_codebase_context_onto_root(client, tmp_path):
    (tmp_path / "main.py").write_text("print('marker-xyz')\n")
    r = client.post("/workspace", json={"root": str(tmp_path)})
    assert r.status_code == 200
    assert r.json()["codebase_context_chars"] > 0

    root = next(n for n in client.get("/session").json()["nodes"] if n["id"] == "root")
    assert "marker-xyz" in root["codebase_context"]


def test_new_nodes_inherit_codebase_context_from_root(client, tmp_path):
    (tmp_path / "main.py").write_text("print('marker-xyz')\n")
    client.post("/workspace", json={"root": str(tmp_path)})
    node = client.post("/continue", json={"node_id": "root", "prompt": "hi"}).json()["node"]
    assert "marker-xyz" in node["codebase_context"]


def test_include_codebase_false_leaves_prompt_without_it_but_node_still_inherits(client, tmp_path):
    (tmp_path / "main.py").write_text("print('marker-xyz')\n")
    client.post("/workspace", json={"root": str(tmp_path)})
    node = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "include_codebase": False,
    }).json()["node"]
    # still inherited onto the node itself, just excluded from this prompt
    assert "marker-xyz" in node["codebase_context"]


def test_calling_workspace_again_refreshes_codebase_context(client, tmp_path):
    (tmp_path / "main.py").write_text("print('version-1')\n")
    client.post("/workspace", json={"root": str(tmp_path)})
    (tmp_path / "main.py").write_text("print('version-2')\n")
    client.post("/workspace", json={"root": str(tmp_path)})

    root = next(n for n in client.get("/session").json()["nodes"] if n["id"] == "root")
    assert "version-2" in root["codebase_context"]
    assert "version-1" not in root["codebase_context"]


# ---- Step 3.9: apply-to-file safety net ----------------------------------

def test_apply_to_file_succeeds_when_file_unchanged_since_generation(client, tmp_path):
    client.post("/workspace", json={"root": str(tmp_path)})
    node = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "target_file": "out.py",
    }).json()["node"]

    r = client.post("/apply-to-file", json={"node_id": node["id"]})
    assert r.status_code == 200


def test_apply_to_file_merges_cleanly_despite_an_unrelated_change_elsewhere(client, tmp_path):
    # Phase 5.5's actual improvement over Step 3.9's blind refusal: the
    # file genuinely changed since generation, but the change is unrelated
    # to what this node touched -- a real merge succeeds instead of
    # refusing outright. A large enough file with real separation between
    # the two edits (default diff context is 3 lines each side) so their
    # hunks don't overlap -- too close together and this would be a real
    # conflict, correctly, not a bug to work around.
    #
    # Node built directly via _add_node (not /continue) deliberately --
    # Phase 5.6 made /continue's auto-activate also auto-sync the file
    # immediately, which would overwrite this test's own "someone edited
    # it afterward" setup before apply-to-file is even called. This keeps
    # the test isolated to what /apply-to-file itself does.
    client.post("/workspace", json={"root": str(tmp_path)})
    original_lines = [f"line{i}\n" for i in range(1, 21)]  # 20-line file
    original = "".join(original_lines)
    (tmp_path / "out.py").write_text(original)

    _add_node("n1", "root", code_snapshot="REPLACED2\n",
              target_file="out.py", target_range={"start_line": 2, "end_line": 2})
    main._source_snapshots["n1"] = original

    # An unrelated edit far away (line 19) before applying.
    changed_lines = list(original_lines)
    changed_lines[18] = "CHANGED_ELSEWHERE\n"
    (tmp_path / "out.py").write_text("".join(changed_lines))

    r = client.post("/apply-to-file", json={"node_id": "n1"})
    assert r.status_code == 200
    content = (tmp_path / "out.py").read_text()
    assert "REPLACED2" in content          # this node's edit applied
    assert "CHANGED_ELSEWHERE" in content  # the unrelated edit survived


def test_apply_to_file_conflict_fires_conflict_detected_over_the_websocket(client, tmp_path):
    # See the note above -- built directly via _add_node, same reason.
    client.post("/workspace", json={"root": str(tmp_path)})
    _add_node("n1", "root", code_snapshot="print(1)\n", target_file="out.py")
    main._source_snapshots["n1"] = None  # file didn't exist at "generation" time
    (tmp_path / "out.py").write_text("# surprise content\n")

    with client.websocket_connect("/ws") as ws:
        r = client.post("/apply-to-file", json={"node_id": "n1"})
        assert r.status_code == 409
        event = ws.receive_json()

    assert event["event"] == "conflict.detected"
    assert event["node_id"] == "n1"
    assert event["diff_preview"]


def test_apply_to_file_refuses_when_file_changed_since_generation(client, tmp_path):
    # See the note above -- built directly via _add_node, same reason.
    client.post("/workspace", json={"root": str(tmp_path)})
    _add_node("n1", "root", code_snapshot="print(1)\n", target_file="out.py")
    main._source_snapshots["n1"] = None  # file didn't exist at "generation" time

    # someone/something creates it in the meantime
    (tmp_path / "out.py").write_text("# surprise content\n")

    r = client.post("/apply-to-file", json={"node_id": "n1"})
    assert r.status_code == 409


def test_apply_to_file_allows_reapplying_after_a_successful_apply(client, tmp_path):
    client.post("/workspace", json={"root": str(tmp_path)})
    node = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "target_file": "out.py",
    }).json()["node"]

    first = client.post("/apply-to-file", json={"node_id": node["id"]})
    assert first.status_code == 200
    # re-applying the same node right after its own successful apply must
    # not spuriously conflict with itself
    second = client.post("/apply-to-file", json={"node_id": node["id"]})
    assert second.status_code == 200


def test_apply_to_file_allows_writes_for_nodes_with_no_hash_history(client, tmp_path):
    # e.g. a node from a session saved before Step 3.9 existed — fail-open,
    # not fail-closed, for anything we have no record of.
    client.post("/workspace", json={"root": str(tmp_path)})
    node = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "target_file": "out.py",
    }).json()["node"]
    main._source_snapshots.pop(node["id"], None)

    r = client.post("/apply-to-file", json={"node_id": node["id"]})
    assert r.status_code == 200


# ---- Phase 4: cross-node querying -----------------------------------------

def test_query_returns_the_new_entry(client):
    main._orchestrator = Orchestrator(FakeLLM(response_fn=lambda i: "the answer"))
    r = client.post("/query", json={"node_id": "root", "question": "what is this?"})
    assert r.status_code == 200
    entry = r.json()["query"]
    assert entry["question"] == "what is this?"
    assert entry["answer"] == "the answer"
    assert entry["chained_from"] is None
    assert "id" in entry and "timestamp" in entry


def test_query_appears_in_the_nodes_query_history_via_session(client):
    main._orchestrator = Orchestrator(FakeLLM(response_fn=lambda i: "the answer"))
    client.post("/query", json={"node_id": "root", "question": "what is this?"})
    node = client.get("/session").json()["nodes"][0]
    assert len(node["query_history"]) == 1
    assert node["query_history"][0]["answer"] == "the answer"


def test_query_does_not_move_the_active_node(client):
    main._orchestrator = Orchestrator(FakeLLM())
    branches = client.post("/branch", json={"node_id": "root", "prompt": "task", "beam_width": 1}).json()["branches"]
    client.post("/continue", json={"node_id": branches[0]["node_id"], "prompt": "go on"})
    active_before = [n for n in client.get("/session").json()["nodes"] if n["status"] == "active"][0]

    client.post("/query", json={"node_id": "root", "question": "what happened at the start?"})

    active_after = [n for n in client.get("/session").json()["nodes"] if n["status"] == "active"][0]
    assert active_after["id"] == active_before["id"]


def test_query_unknown_node_is_404(client):
    r = client.post("/query", json={"node_id": "nope", "question": "what?"})
    assert r.status_code == 404


def test_query_llm_failure_returns_502_and_leaves_history_unchanged(client):
    main._orchestrator = Orchestrator(FakeLLM(fail_on={0}))
    r = client.post("/query", json={"node_id": "root", "question": "what is this?"})
    assert r.status_code == 502
    node = client.get("/session").json()["nodes"][0]
    assert node["query_history"] == []


def test_chained_query_via_api_includes_the_prior_answer(client):
    main._orchestrator = Orchestrator(FakeLLM(response_fn=lambda i: f"answer {i}"))
    first = client.post("/query", json={"node_id": "root", "question": "first question"}).json()["query"]
    client.post("/query", json={
        "node_id": "root", "question": "follow-up", "chain_from": first["id"],
    })
    node = client.get("/session").json()["nodes"][0]
    assert len(node["query_history"]) == 2
    assert node["query_history"][1]["chained_from"] == first["id"]


def test_chained_query_unknown_chain_from_is_404(client):
    main._orchestrator = Orchestrator(FakeLLM())
    r = client.post("/query", json={"node_id": "root", "question": "follow-up", "chain_from": "nope"})
    assert r.status_code == 404


# ---- Phase 5.2/5.3: /patch (Option A) and /merge (Option C) ---------------

def _add_node(node_id, parent_id, **overrides):
    from datetime import datetime
    import models
    defaults = dict(
        id=node_id, parent_id=parent_id, children_ids=[], code_snapshot="",
        context_summary="", user_preferences=[], score=None, score_source=None,
        status="normal", query_history=[], branch_label=node_id,
    )
    defaults.update(overrides)
    main._orchestrator.tree.add_node(models.TreeNode(**defaults))


def _add_query(node_id, question, answer, entry_id="q1"):
    from datetime import datetime
    import models
    main._orchestrator.tree.add_query_entry(node_id, models.QueryEntry(
        id=entry_id, question=question, answer=answer, timestamp=datetime.utcnow(),
    ))


def test_patch_clean_creates_new_active_node(client):
    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: '{"code": "line1\\nCHANGED\\n", "reasoning": "ok"}'
    ))
    _add_node("source", "root", code_snapshot="line1\nline2\n")
    _add_node("target", "root", code_snapshot="line1\nline2\n")
    _add_query("source", "what should line2 be?", "CHANGED")

    r = client.post("/patch", json={"source_node_id": "source", "query_id": "q1", "target_node_id": "target"})
    assert r.status_code == 200
    outcome = r.json()["outcome"]
    assert outcome["conflict"] is False
    assert outcome["node"]["code_snapshot"] == "line1\nCHANGED\n"
    assert outcome["node"]["parent_id"] == "target"

    active = [n for n in client.get("/session").json()["nodes"] if n["status"] == "active"][0]
    assert active["id"] == outcome["node"]["id"]


def test_patch_conflict_fires_conflict_detected_over_the_websocket(client):
    # CLAUDE.md: every WebSocket event needs an integration test proving
    # it fires at the right moment -- conflict.detected specifically fires
    # "the moment conflict detection runs in POST /patch".
    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: '{"code": "line1\\nCHANGED\\n", "reasoning": "ok"}'
    ))
    _add_node("source", "root", code_snapshot="line1\nline2\n")
    _add_node("target", "root", code_snapshot="totally different\nnot line1 or line2\n")
    _add_query("source", "what should line2 be?", "CHANGED")

    with client.websocket_connect("/ws") as ws:
        r = client.post("/patch", json={"source_node_id": "source", "query_id": "q1", "target_node_id": "target"})
        assert r.status_code == 200
        event = ws.receive_json()

    assert event["event"] == "conflict.detected"
    assert event["source_node_id"] == "source"
    assert event["target_node_id"] == "target"
    assert event["diff_preview"]


def test_patch_conflict_creates_nothing(client):
    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: '{"code": "line1\\nCHANGED\\n", "reasoning": "ok"}'
    ))
    _add_node("source", "root", code_snapshot="line1\nline2\n")
    _add_node("target", "root", code_snapshot="totally different\nnot line1 or line2\n")
    _add_query("source", "what should line2 be?", "CHANGED")
    nodes_before = len(client.get("/session").json()["nodes"])

    r = client.post("/patch", json={"source_node_id": "source", "query_id": "q1", "target_node_id": "target"})
    assert r.status_code == 200
    outcome = r.json()["outcome"]
    assert outcome["conflict"] is True
    assert outcome["node"] is None
    assert outcome["diff_preview"]

    nodes_after = len(client.get("/session").json()["nodes"])
    assert nodes_after == nodes_before


def test_patch_unknown_node_is_404(client):
    r = client.post("/patch", json={"source_node_id": "nope", "query_id": "q1", "target_node_id": "root"})
    assert r.status_code == 404


def test_patch_file_mismatch_is_400(client, tmp_path):
    # Real bug, reported by the user (2026-10-19): a clarification about a
    # different file than `source` used to produce a nonsense diff and a
    # bogus conflict banner. Now a clear 400 instead.
    (tmp_path / "main.py").write_text("x = 1\n")
    (tmp_path / "unrelated.py").write_text("y = 2\n")
    main._orchestrator = Orchestrator(FakeLLM(response_fn=lambda i: json.dumps({
        "code": "x", "reasoning": "r", "is_code_change": True, "target_file": "unrelated.py",
    })))
    client.post("/workspace", json={"root": str(tmp_path)})
    _add_node("source", "root", code_snapshot="line1\nline2\n", target_file="main.py",
              codebase_context=main._orchestrator.tree.get_node("root").codebase_context)
    _add_node("target", "root", code_snapshot="line1\nline2\n")
    _add_query("source", "change something in unrelated.py", "sure")

    r = client.post("/patch", json={"source_node_id": "source", "query_id": "q1", "target_node_id": "target"})
    assert r.status_code == 400
    assert "unrelated.py" in r.json()["detail"]


def test_merge_creates_new_active_child_of_source(client):
    call_log = []

    def response_fn(i):
        call_log.append(i)
        if len(call_log) == 1:
            return '["use type hints"]'
        return '{"code": "def f(x: int) -> int:\\n    return x\\n", "reasoning": "ok"}'

    main._orchestrator = Orchestrator(FakeLLM(response_fn=response_fn))
    _add_node("source", "root", code_snapshot="def f(x):\n    return x\n")
    _add_node("current", "source", context_summary="Task: t\nApproach: a")

    r = client.post("/merge", json={"source_node_id": "source", "current_node_id": "current"})
    assert r.status_code == 200
    outcome = r.json()["outcome"]
    assert outcome["conflict"] is False
    assert outcome["node"]["parent_id"] == "source"
    assert "use type hints" in outcome["node"]["user_preferences"]

    active = [n for n in client.get("/session").json()["nodes"] if n["status"] == "active"][0]
    assert active["id"] == outcome["node"]["id"]


def test_merge_source_not_ancestor_is_400(client):
    _add_node("a", "root")
    _add_node("b", "root")
    r = client.post("/merge", json={"source_node_id": "a", "current_node_id": "b"})
    assert r.status_code == 400


def test_merge_unknown_node_is_404(client):
    r = client.post("/merge", json={"source_node_id": "nope", "current_node_id": "root"})
    assert r.status_code == 404


def test_merge_siblings_creates_new_active_child_of_shared_parent(client):
    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: '{"code": "combined", "reasoning": "best of both"}'
    ))
    _add_node("a", "root", code_snapshot="approach a")
    _add_node("b", "root", code_snapshot="approach b")

    r = client.post("/merge-siblings", json={"node_a_id": "a", "node_b_id": "b"})
    assert r.status_code == 200
    node = r.json()["node"]
    assert node["parent_id"] == "root"
    assert node["code_snapshot"] == "combined"

    active = [n for n in client.get("/session").json()["nodes"] if n["status"] == "active"][0]
    assert active["id"] == node["id"]


def test_merge_siblings_different_parents_is_400(client):
    _add_node("a", "root")
    _add_node("b", "a")
    r = client.post("/merge-siblings", json={"node_a_id": "a", "node_b_id": "b"})
    assert r.status_code == 400


def test_merge_siblings_unknown_node_is_404(client):
    r = client.post("/merge-siblings", json={"node_a_id": "nope", "node_b_id": "root"})
    assert r.status_code == 404


def test_patch_force_applies_despite_conflict(client):
    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: '{"code": "line1\\nCHANGED\\n", "reasoning": "ok"}'
    ))
    _add_node("source", "root", code_snapshot="line1\nline2\n")
    _add_node("target", "root", code_snapshot="totally different\nnot line1 or line2\n")
    _add_query("source", "what should line2 be?", "CHANGED")

    r = client.post("/patch", json={
        "source_node_id": "source", "query_id": "q1", "target_node_id": "target", "force": True,
    })
    assert r.status_code == 200
    outcome = r.json()["outcome"]
    assert outcome["conflict"] is False
    assert outcome["node"]["code_snapshot"] == "line1\nCHANGED\n"


# ---- Phase 5.6: live active-editor-context --------------------------------

def test_active_editor_context_starts_as_none(client):
    r = client.get("/active-editor-context")
    assert r.status_code == 200
    assert r.json()["context"] is None


def test_setting_active_editor_context_round_trips(client):
    r = client.post("/active-editor-context", json={"file": "util.py", "start_line": 2, "end_line": 4})
    assert r.status_code == 200
    assert r.json()["context"] == {"file": "util.py", "start_line": 2, "end_line": 4}

    r2 = client.get("/active-editor-context")
    assert r2.json()["context"] == {"file": "util.py", "start_line": 2, "end_line": 4}


def test_setting_file_none_clears_active_editor_context(client):
    client.post("/active-editor-context", json={"file": "util.py", "start_line": 1, "end_line": 1})
    r = client.post("/active-editor-context", json={"file": None})
    assert r.json()["context"] is None
    assert client.get("/active-editor-context").json()["context"] is None


def test_active_editor_context_change_fires_over_the_websocket(client):
    with client.websocket_connect("/ws") as ws:
        client.post("/active-editor-context", json={"file": "util.py", "start_line": 2, "end_line": 4})
        event = ws.receive_json()
    assert event["event"] == "active_editor.changed"
    assert event["context"] == {"file": "util.py", "start_line": 2, "end_line": 4}


# ---- Phase 5.6: activation triggers a real file write ---------------------

def test_continue_with_target_file_writes_it_immediately_no_separate_apply(client, tmp_path):
    client.post("/workspace", json={"root": str(tmp_path)})
    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: json.dumps({"code": "written by continue\n", "reasoning": "r", "is_code_change": True})
    ))
    client.post("/continue", json={"node_id": "root", "prompt": "hi", "target_file": "out.py"})
    # No /apply-to-file call at all -- the file should already be there.
    assert (tmp_path / "out.py").read_text() == "written by continue\n"


def test_continue_with_additional_files_writes_both_to_disk(client, tmp_path):
    # Phase 8 (2026-10-19): the exact real-world scenario that motivated
    # this phase -- a compound request needing two files changed in one
    # turn, both actually written to disk, not just one.
    (tmp_path / "constants.py").write_text("MAX_RETRIES = 5\n")
    (tmp_path / "util.py").write_text("from constants import MAX_RETRIES\n")
    # Order matters: /workspace gathers the codebase snapshot onto
    # WHICHEVER orchestrator is live at that moment, so the FakeLLM swap
    # must happen first, or the gather lands on an instance that then gets
    # discarded -- same ordering the existing target_file-only tests
    # above happened not to depend on, but this one needs the real
    # codebase_context for additional_files resolution to work at all.
    main._orchestrator = Orchestrator(FakeLLM(response_fn=lambda i: json.dumps({
        "code": "MAX_RETRIES = 7\n", "reasoning": "r", "is_code_change": True,
        "additional_files": {"util.py": "from constants import MAX_RETRIES\nprint(MAX_RETRIES)\n"},
    })))
    client.post("/workspace", json={"root": str(tmp_path)})
    client.post("/continue", json={
        "node_id": "root", "prompt": "change retries and add a print in util.py",
        "target_file": "constants.py",
    })
    assert (tmp_path / "constants.py").read_text() == "MAX_RETRIES = 7\n"
    assert (tmp_path / "util.py").read_text() == "from constants import MAX_RETRIES\nprint(MAX_RETRIES)\n"


def test_additional_file_conflict_does_not_block_the_primary_files_write(client, tmp_path):
    # Fail-open rule extended to N files (Phase 8): a conflict syncing ONE
    # additional file must never block the primary file (or any other
    # additional file) from writing.
    (tmp_path / "constants.py").write_text("MAX_RETRIES = 5\n")
    (tmp_path / "util.py").write_text("from constants import MAX_RETRIES\n")
    main._orchestrator = Orchestrator(FakeLLM(response_fn=lambda i: json.dumps({
        "code": "MAX_RETRIES = 7\n", "reasoning": "r", "is_code_change": True,
        "additional_files": {"util.py": "from constants import MAX_RETRIES\nprint(MAX_RETRIES)\n"},
    })))
    client.post("/workspace", json={"root": str(tmp_path)})

    node = client.post("/continue", json={
        "node_id": "root", "prompt": "change retries and add a print in util.py",
        "target_file": "constants.py",
    }).json()["node"]
    assert (tmp_path / "util.py").read_text() == "from constants import MAX_RETRIES\nprint(MAX_RETRIES)\n"

    # An external edit to util.py that conflicts with what this node would
    # write there next time it's re-activated (e.g. via backtrack).
    (tmp_path / "util.py").write_text("SOMETHING COMPLETELY DIFFERENT, CONFLICTS\n")
    r = client.post("/backtrack", json={"node_id": node["id"]})
    assert r.status_code == 200  # activation itself always succeeds
    # The conflicting additional file is left alone (fail-open: don't
    # overwrite, don't block) -- but the PRIMARY file still gets its sync.
    assert (tmp_path / "constants.py").read_text() == "MAX_RETRIES = 7\n"


def test_backtrack_to_a_past_node_rewrites_the_file_to_match_it(client, tmp_path):
    client.post("/workspace", json={"root": str(tmp_path)})
    main._orchestrator = Orchestrator(FakeLLM(response_fn=lambda i: json.dumps({"code": "V1\n", "reasoning": "r", "is_code_change": True})))
    node1 = client.post("/continue", json={"node_id": "root", "prompt": "hi", "target_file": "out.py"}).json()["node"]
    assert (tmp_path / "out.py").read_text() == "V1\n"

    main._orchestrator.llm = FakeLLM(response_fn=lambda i: json.dumps({"code": "V2\n", "reasoning": "r", "is_code_change": True}))
    client.post("/continue", json={"node_id": node1["id"], "prompt": "more", "target_file": "out.py"})
    assert (tmp_path / "out.py").read_text() == "V2\n"  # the newer node auto-wrote over V1

    client.post("/backtrack", json={"node_id": node1["id"]})
    assert (tmp_path / "out.py").read_text() == "V1\n"  # back to matching the now-active node


def test_activation_conflict_still_lets_the_switch_proceed(client, tmp_path):
    # Design decision (2026-10-06): activation always succeeds at the tree
    # level even when the file-sync side effect conflicts -- never blocks
    # tree navigation.
    client.post("/workspace", json={"root": str(tmp_path)})
    _add_node("n1", "root", code_snapshot="print(1)\n", target_file="out.py")
    main._source_snapshots["n1"] = None  # file didn't exist at "generation" time
    (tmp_path / "out.py").write_text("# unrelated content already here\n")

    with client.websocket_connect("/ws") as ws:
        r = client.post("/backtrack", json={"node_id": "n1"})
        event = ws.receive_json()

    assert r.status_code == 200
    assert r.json()["node"]["id"] == "n1"
    # the tree switch happened regardless of the file conflict
    active = [n for n in client.get("/session").json()["nodes"] if n["status"] == "active"][0]
    assert active["id"] == "n1"
    # the conflict was still surfaced
    assert event["event"] == "conflict.detected"
    # and the file was correctly left untouched, not clobbered
    assert (tmp_path / "out.py").read_text() == "# unrelated content already here\n"


def test_patch_success_writes_the_target_file_automatically(client, tmp_path):
    client.post("/workspace", json={"root": str(tmp_path)})
    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: '{"code": "line1\\nCHANGED\\n", "reasoning": "ok"}'
    ))
    _add_node("source", "root", code_snapshot="line1\nline2\n")
    _add_node("target", "root", code_snapshot="line1\nline2\n", target_file="out.py")
    (tmp_path / "out.py").write_text("line1\nline2\n")
    main._source_snapshots["target"] = "line1\nline2\n"
    _add_query("source", "what should line2 be?", "CHANGED")

    r = client.post("/patch", json={"source_node_id": "source", "query_id": "q1", "target_node_id": "target"})
    assert r.status_code == 200
    assert r.json()["outcome"]["conflict"] is False
    assert (tmp_path / "out.py").read_text() == "line1\nCHANGED\n"


def test_node_without_target_file_activation_is_a_no_op_for_disk(client, tmp_path):
    client.post("/workspace", json={"root": str(tmp_path)})
    r = client.post("/continue", json={"node_id": "root", "prompt": "hi"})  # no target_file
    assert r.status_code == 200  # unaffected by the sync hook at all


# ---- 2026-10-17 amendment: auto/question/code_change intent via HTTP -----

def test_continue_intent_auto_strips_target_file_when_model_says_not_a_code_change(client):
    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: json.dumps({"code": "x", "reasoning": "r", "is_code_change": False})
    ))
    r = client.post("/continue", json={"node_id": "root", "prompt": "hi", "target_file": "out.py"})
    assert r.json()["node"]["target_file"] is None


def test_continue_intent_question_overrides_model_classification(client):
    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: json.dumps({"code": "x", "reasoning": "r", "is_code_change": True})
    ))
    r = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "target_file": "out.py", "intent": "question",
    })
    assert r.json()["node"]["target_file"] is None


def test_continue_intent_code_change_overrides_model_classification(client):
    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: json.dumps({"code": "x", "reasoning": "r", "is_code_change": False})
    ))
    r = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "target_file": "out.py", "intent": "code_change",
    })
    assert r.json()["node"]["target_file"] == "out.py"


def test_continue_intent_defaults_to_auto(client):
    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: json.dumps({"code": "x", "reasoning": "r", "is_code_change": False})
    ))
    r = client.post("/continue", json={"node_id": "root", "prompt": "hi", "target_file": "out.py"})
    assert r.json()["node"]["target_file"] is None  # behaves as auto, not forced through
