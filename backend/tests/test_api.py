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
    main._source_hashes.clear()
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


def test_apply_to_file_with_target_range_splices_instead_of_overwriting(client, tmp_path):
    # Real bug (2026-09-27): a node created from a *selection* only ever
    # holds that selection's code — applying it as a full-file overwrite
    # silently destroyed everything else in the file. Fix: splice into just
    # target_range's lines when one is set.
    original = "line1\nline2\nline3\nline4\nline5\n"
    (tmp_path / "out.py").write_text(original)
    client.post("/workspace", json={"root": str(tmp_path)})

    main._orchestrator = Orchestrator(FakeLLM(
        response_fn=lambda i: json.dumps({"code": "REPLACED-2\nREPLACED-3\n", "reasoning": "r"})
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


def test_apply_to_file_refuses_when_file_changed_since_generation(client, tmp_path):
    client.post("/workspace", json={"root": str(tmp_path)})
    node = client.post("/continue", json={
        "node_id": "root", "prompt": "hi", "target_file": "out.py",
    }).json()["node"]

    # the file didn't exist when the node was created (hash captured as
    # None); someone/something creates it in the meantime
    (tmp_path / "out.py").write_text("# surprise content\n")

    r = client.post("/apply-to-file", json={"node_id": node["id"]})
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
    main._source_hashes.pop(node["id"], None)

    r = client.post("/apply-to-file", json={"node_id": node["id"]})
    assert r.status_code == 200
