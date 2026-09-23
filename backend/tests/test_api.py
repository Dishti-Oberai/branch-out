import pytest
from fastapi.testclient import TestClient

import main
from orchestrator import Orchestrator
from tests.conftest import FakeLLM


@pytest.fixture
def client():
    main._orchestrator = Orchestrator(FakeLLM())
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
