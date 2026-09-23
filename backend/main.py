import asyncio
import logging
from typing import Optional, Set

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from claude_client import ClaudeClient  # noqa: E402
from evaluator import evaluate_branches, rescore_with_tests  # noqa: E402
from models import DEFAULT_BEAM_WIDTH, Branch, branch_to_dict, node_to_dict  # noqa: E402
from orchestrator import LLMGenerationFailed, Orchestrator  # noqa: E402
from session import load_session, save_session  # noqa: E402
from test_runner import has_test_imports  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(title="Branch-Out")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_orchestrator: Optional[Orchestrator] = None
DEFAULT_SESSION_PATH = "sessions/session.json"

# How often to poll for a client disconnect while an LLM call is in flight.
# Small enough to cancel promptly, large enough not to busy-loop.
_DISCONNECT_POLL_SECONDS = 0.05

# Step 2.4 / single-session POC scope: a plain set of connected clients is
# enough — no per-session routing, since there's only one session (see
# ROADMAP.md's "single session for the POC" decision). Every connected
# client sees every event.
_ws_clients: Set[WebSocket] = set()


def orch() -> Orchestrator:
    # Lazy so the server (and tests) can start without an API key
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(ClaudeClient())
    return _orchestrator


async def _broadcast(event: str, payload: dict) -> None:
    """Fire-and-forget to every connected WebSocket client.

    A dead/broken connection is dropped rather than allowed to raise and
    take the broadcast (and whatever background task called it) down —
    WebSocket delivery is best-effort, never load-bearing.
    """
    dead = []
    for ws in list(_ws_clients):
        try:
            await ws.send_json({"event": event, **payload})
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


async def _run_cancelable(coro, request: Request):
    """Run `coro` as a task, cancelling it if the client disconnects.

    Step 1.10: cancel in-flight branch generation. A plain `await coro`
    can't be interrupted once the client goes away; running it as a task
    lets us poll `request.is_disconnected()` alongside it and cancel the
    outstanding `asyncio.gather` calls inside branch_generator rather than
    letting them run to completion for nobody. Raises `asyncio.CancelledError`
    if cancelled — callers map that to a response the client will never see.
    """
    task = asyncio.ensure_future(coro)
    while not task.done():
        if await request.is_disconnected():
            task.cancel()
            break
        await asyncio.sleep(_DISCONNECT_POLL_SECONDS)
    return await task


async def _evaluate_and_notify(branches: list[Branch]) -> None:
    """Step 2.4: fires strictly after `/branch`'s HTTP response is already
    under construction (scheduled via asyncio.create_task, never awaited by
    the endpoint) — branches must appear in the UI before scores do.
    """
    if not branches:
        return
    try:
        scored = await evaluate_branches(branches, orch().llm)
    except Exception as e:  # noqa: BLE001 — evaluation is informational, never load-bearing
        logger.error("Evaluation failed: %s", e)
        return
    for b in scored:
        if not b.node_id:
            continue
        if b.score is not None:
            orch().tree.set_score(b.node_id, b.score, b.score_source)
        await _broadcast("score.ready", {
            "node_id": b.node_id,
            "score": b.score,
            "rank": b.rank,
            "score_source": b.score_source,
        })


async def _rescore_and_notify(node_ids: list[str]) -> None:
    # run_tests is blocking (subprocess.run) — keep it off the event loop.
    updated = await asyncio.to_thread(rescore_with_tests, node_ids, orch().tree)
    for node in updated:
        await _broadcast("score.ready", {
            "node_id": node.id,
            "score": node.score,
            "rank": None,
            "score_source": node.score_source,
        })


class BranchRequest(BaseModel):
    node_id: str
    prompt: str
    beam_width: int = DEFAULT_BEAM_WIDTH


class BacktrackRequest(BaseModel):
    node_id: str


class ContinueRequest(BaseModel):
    node_id: str
    prompt: str


class EditPromptRequest(BaseModel):
    node_id: str
    new_prompt: str


class LoadRequest(BaseModel):
    path: str


class SaveRequest(BaseModel):
    path: str = DEFAULT_SESSION_PATH


@app.post("/branch")
async def branch(req: BranchRequest, request: Request):
    if req.beam_width < 1:
        raise HTTPException(400, "beam_width must be >= 1")

    async def on_branch_ready(b: Branch) -> None:
        await _broadcast("branch.ready", {"branch": branch_to_dict(b)})

    try:
        branches = await _run_cancelable(
            orch().branch(req.node_id, req.prompt, req.beam_width, on_branch_ready=on_branch_ready),
            request,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except asyncio.CancelledError:
        raise HTTPException(499, "Request cancelled by client")

    # Evaluation happens in the background — /branch never waits on it.
    asyncio.create_task(_evaluate_and_notify(list(branches)))
    return {"branches": [branch_to_dict(b) for b in branches]}


@app.post("/continue")
async def continue_from(req: ContinueRequest, request: Request):
    try:
        node = await _run_cancelable(
            orch().continue_from(req.node_id, req.prompt), request
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except LLMGenerationFailed as e:
        raise HTTPException(502, str(e))
    except asyncio.CancelledError:
        raise HTTPException(499, "Request cancelled by client")
    return {"node": node_to_dict(node)}


@app.post("/edit-prompt")
async def edit_prompt(req: EditPromptRequest, request: Request):
    try:
        new_node, stale_ids = await _run_cancelable(
            orch().edit_prompt(req.node_id, req.new_prompt), request
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LLMGenerationFailed as e:
        raise HTTPException(502, str(e))
    except asyncio.CancelledError:
        raise HTTPException(499, "Request cancelled by client")
    return {"new_node": node_to_dict(new_node), "stale_node_ids": stale_ids}


@app.post("/backtrack")
async def backtrack(req: BacktrackRequest):
    try:
        node = await orch().backtrack(req.node_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"node": node_to_dict(node)}


@app.get("/session")
def get_session():
    return {"nodes": [node_to_dict(n) for n in orch().all_nodes()]}


@app.post("/session/save")
def session_save(req: Optional[SaveRequest] = None):
    path = (req or SaveRequest()).path
    try:
        saved_path = save_session(orch().tree, path)
    except OSError as e:
        raise HTTPException(500, f"Could not save session: {e}")
    return {"path": saved_path}


@app.post("/session/load")
async def session_load(req: LoadRequest):
    global _orchestrator
    try:
        tree = load_session(req.path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _orchestrator = Orchestrator(orch().llm, tree)

    # Step 2.4: retroactive rescoring — a node scored by the LLM judge
    # before it had tests, that now has tests in its code, gets re-run
    # through the test runner instead of staying LLM-judge-scored forever.
    to_rescore = [
        n.id for n in tree.all_nodes()
        if n.score_source == "llm_judge" and has_test_imports(n.code_snapshot)
    ]
    if to_rescore:
        asyncio.create_task(_rescore_and_notify(to_rescore))

    return {"nodes": [node_to_dict(n) for n in tree.all_nodes()]}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)
