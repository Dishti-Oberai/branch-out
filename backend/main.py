import asyncio
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from claude_client import ClaudeClient  # noqa: E402
from models import DEFAULT_BEAM_WIDTH, branch_to_dict, node_to_dict  # noqa: E402
from orchestrator import LLMGenerationFailed, Orchestrator  # noqa: E402
from session import load_session, save_session  # noqa: E402

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


def orch() -> Orchestrator:
    # Lazy so the server (and tests) can start without an API key
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(ClaudeClient())
    return _orchestrator


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
    try:
        branches = await _run_cancelable(
            orch().branch(req.node_id, req.prompt, req.beam_width), request
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except asyncio.CancelledError:
        raise HTTPException(499, "Request cancelled by client")
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
def session_load(req: LoadRequest):
    global _orchestrator
    try:
        tree = load_session(req.path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _orchestrator = Orchestrator(orch().llm, tree)
    return {"nodes": [node_to_dict(n) for n in tree.all_nodes()]}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # Phase 1: accept the connection only. Phase 2 adds branch.ready/score.ready
    # and later events; nothing is emitted yet.
    await ws.accept()
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
