import asyncio
import logging
from pathlib import Path
from typing import Callable, Dict, Literal, Optional, Set

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from claude_client import ClaudeClient  # noqa: E402
from codebase_context import gather_codebase_context  # noqa: E402
from evaluator import evaluate_branches, rescore_with_tests  # noqa: E402
from git_engine import make_patch, merge_code  # noqa: E402
from models import (  # noqa: E402
    DEFAULT_BEAM_WIDTH,
    Branch,
    TreeNode,
    branch_to_dict,
    node_to_dict,
    patch_outcome_to_dict,
    query_entry_to_dict,
)
from orchestrator import LLMGenerationFailed, Orchestrator  # noqa: E402
from session import load_session, save_session  # noqa: E402
from test_runner import has_test_imports  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(title="Branch-Out")
app.add_middleware(
    CORSMiddleware,
    # Phase 3 real bug (2026-09-26): the VS Code webview's origin is
    # something like vscode-webview://<random-id>, not http://localhost:5173
    # — allowlisting only the web app's dev-server origin meant every fetch
    # from the embedded tree panel was silently CORS-rejected, surfacing in
    # the browser as an opaque "Failed to fetch" with no useful detail.
    # This is a local-only single-user dev tool with no cookies/credentials
    # (allow_credentials defaults to False), so a wildcard origin is the
    # simple, correct fix rather than trying to track VS Code's webview
    # origin format (which isn't guaranteed stable across versions).
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_orchestrator: Optional[Orchestrator] = None
DEFAULT_SESSION_PATH = "sessions/session.json"

# Step 3.2: the VS Code extension declares its workspace root via POST
# /workspace on activation. /apply-to-file refuses to write anywhere
# outside it. Single-session POC scope, same as _ws_clients below — one
# workspace root, not per-connection.
_workspace_root: Optional[Path] = None

# Step 3.9: the on-disk hash of a node's target_file at the moment that
# node was created (None if the file didn't exist yet). Deliberately NOT
# part of the persisted TreeNode schema. Step 3.9 (2026-09-27) originally
# stored just a hash here (change-detection only, refuse-on-any-change).
# Phase 5.5 (2026-09-28) upgraded this to store the FULL original file
# content instead — a hash alone is enough to detect *that* something
# changed, but real conflict detection (git_engine.merge_code) needs the
# actual text to diff against. Still deliberately NOT part of the
# persisted schema (it's meant to be superseded again once nodes carry
# their own real diff history, if that's ever built) — lost on restart or
# session load, same as the WebSocket client set below; apply-to-file
# fails open (allows the write) for any node it has no record for, rather
# than blocking things it can't reason about.
_source_snapshots: Dict[str, Optional[str]] = {}

# Phase 8 (2026-10-19): the same idea as `_source_snapshots` above, but for
# a node's `additional_edits` -- keyed by node id, then by that file's own
# resolved path. There's no `target_range` concept for additional files
# (always full-file content), so this only ever needs the real on-disk
# content at generation time, same conflict-detection role the primary
# file's snapshot plays for `_sync_active_file_to_disk`'s first sync of a
# given path.
_additional_source_snapshots: Dict[str, Dict[str, str]] = {}

# Phase 5.6 (2026-10-06): the VS Code extension's live active editor/
# selection, reported continuously via POST /active-editor-context.
# Deliberately NOT part of the persisted TreeNode schema -- this is
# ephemeral UI state, not tree content, same category as _workspace_root.
# None means "no active editor right now" (nothing open, or VS Code
# hasn't reported yet / isn't running). Shared by both clients (the
# embedded webview and the standalone browser tab) via GET for the
# initial fetch and the active_editor.changed WebSocket event for live
# updates -- one mechanism, not two independently-built file pickers.
_active_editor_context: Optional[Dict[str, object]] = None

# Phase 5.6: the last content _sync_active_file_to_disk (or /apply-to-file)
# itself wrote to a given resolved target path -- keyed by file, not by
# node, since it answers "what did WE last put here" regardless of which
# node wrote it. This is what makes activation-triggered writes a real
# three-way merge (ancestor=this, ours=current disk, theirs=the node being
# switched to) instead of diffing against the activating node's own
# creation-time baseline, which is always a no-op for backtrack (see
# _sync_active_file_to_disk's docstring for the real bug this fixed).
_file_last_synced: Dict[str, Optional[str]] = {}

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


def _resolve_target_path(target_file: str) -> Path:
    """Step 3.2's path-safety check, shared by /apply-to-file and Step
    3.9's hash capture. Raises HTTPException(400) if no workspace root has
    been declared, or if the resolved path escapes it."""
    if _workspace_root is None:
        raise HTTPException(400, "No workspace root declared — call POST /workspace first")
    candidate = Path(target_file)
    target = candidate if candidate.is_absolute() else (_workspace_root / candidate)
    target = target.resolve()
    try:
        target.relative_to(_workspace_root)
    except ValueError:
        raise HTTPException(400, f"target_file {target_file!r} is outside the workspace root")
    return target


def _splice_range(original: str, target_range: Dict[str, int], replacement: str) -> str:
    """Step 3.2 fix (2026-09-27): a node created from a *selection* (not the
    whole file) only ever holds that selection's code as `code_snapshot` —
    the model was never shown the rest of the file, so it can't be trusted
    to reproduce it. Applying `code_snapshot` as a full-file overwrite in
    that case silently destroys everything outside the selection (real bug,
    caught by the user: "the answer changes the file even if it is just a
    question about what the file is" surfaced this exact class of damage).
    Fix: when a node carries a `target_range`, splice its code into just
    those lines instead of replacing the whole file. `target_range` is
    1-indexed and inclusive on both ends, matching how quickPick.ts derives
    it from a VS Code Selection.
    """
    lines = original.splitlines(keepends=True)
    start = max(target_range["start_line"] - 1, 0)
    end = min(target_range["end_line"], len(lines))
    if replacement and not replacement.endswith("\n") and end < len(lines):
        replacement += "\n"
    return "".join(lines[:start] + [replacement] + lines[end:])


def _read_file(path: Path) -> Optional[str]:
    try:
        return path.read_text()
    except OSError:
        return None  # file doesn't exist (yet) — a legitimate, common case


def _capture_source_snapshot(node: TreeNode) -> None:
    """Records target_file's full content when this node was created, so
    /apply-to-file can later diff against it for real conflict detection
    (Phase 5.5). A no-op for nodes without target_file, or before any
    workspace is declared (nothing to snapshot against yet).
    """
    if node.target_file and _workspace_root is not None:
        try:
            target = _resolve_target_path(node.target_file)
            _source_snapshots[node.id] = _read_file(target)
        except HTTPException:
            pass  # can't resolve it safely; just don't track it

    # Phase 8 (2026-10-19): same capture, per additional file -- needed
    # for the exact same reason, just scoped to each extra path instead
    # of the one primary target_file.
    if node.additional_edits and _workspace_root is not None:
        snapshots: Dict[str, str] = {}
        for path in node.additional_edits:
            try:
                target = _resolve_target_path(path)
                content = _read_file(target)
                if content is not None:
                    snapshots[path] = content
            except HTTPException:
                continue  # can't resolve it safely; just don't track it
        if snapshots:
            _additional_source_snapshots[node.id] = snapshots


async def _sync_one_file(
    node_id: str, raw_target_file: str, intended: str, fallback_ancestor: str,
    on_synced: Optional[Callable[[str], None]] = None,
) -> None:
    """The actual per-file sync: real three-way merge (ancestor=last
    synced, ours=current disk, theirs=`intended`), real conflict detection,
    fail-open throughout. Factored out (Phase 8, 2026-10-19) so the
    primary `target_file` and each of `node.additional_edits` go through
    the exact same logic — previously this lived inline in
    `_sync_active_file_to_disk`, written before there was more than one
    file to sync per node. See that function's own docstring (still above
    it) for the full "why `_file_last_synced`, not just the node's own
    snapshot" rationale, which applies identically here.

    `on_synced`, if given, is called with the final written content on
    success — lets the primary-file caller also update `_source_snapshots`
    for apply-to-file's own diffing, without this helper needing to know
    about that at all.
    """
    try:
        target = _resolve_target_path(raw_target_file)
    except HTTPException:
        return  # can't resolve it safely; just don't sync
    key = str(target)

    ancestor = _file_last_synced.get(key)
    if ancestor is None:
        ancestor = fallback_ancestor
    current_on_disk = _read_file(target) or ""

    if current_on_disk != ancestor:
        patch_text = make_patch(ancestor, intended)
        result = merge_code(current_on_disk, patch_text)
        if not result.success:
            await _broadcast("conflict.detected", {
                "node_id": node_id,
                "target_file": str(target),
                "diff_preview": result.diff_preview,
            })
            return  # best-effort: don't write, don't block activation
        intended = result.merged_code

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(intended)
        _file_last_synced[key] = intended
        if on_synced:
            on_synced(intended)
    except OSError as e:
        logger.error("Phase 5.6 file sync failed for %s: %s", target, e)  # fail open


async def _sync_active_file_to_disk(node: TreeNode) -> None:
    """Phase 5.6: the real "file always matches the active node" behavior —
    triggered by activation (continue's auto-activate, backtrack, patch/
    merge's own activation) instead of a separate manual Apply button.

    Real design correction, caught by a failing test before this ever
    shipped: the first draft diffed against *this node's own* creation-time
    baseline (`_source_snapshots[node.id]`) — correct for continue/patch/
    merge (where there's a genuine new change to merge in), but wrong for
    backtrack. Backtracking isn't "apply a new change" at all — it's
    "check out old content" — and a node's own baseline-to-itself diff is
    always empty by definition, which silently no-op'd every backtrack
    (confirmed: backtracking from a newer node back to an older one left
    the newer content on disk, untouched). Fix: the merge's ancestor is
    `_file_last_synced[path]` — the last content *this mechanism itself*
    wrote to that file, tracked per file, not per node — falling back to
    the activating node's own snapshot only when this file has never been
    synced this way before. This makes the merge a real, meaningful
    three-way one: ancestor=last synced, ours=whatever's actually on disk
    now (a possible external edit), theirs=the node being switched to.

    Design decision (2026-10-06, see ROADMAP.md): activation at the tree
    level always succeeds regardless of what happens here — this is a
    best-effort side effect, not a gate. A real conflict fires
    `conflict.detected` (same event, same payload shape every other
    conflict path uses) so the user finds out immediately, but never
    blocks the switch itself.

    Phase 8 (2026-10-19): now ALSO syncs every file in `node.additional_
    edits`, independently of the primary `target_file` and of each other
    -- one file's conflict (or missing workspace resolution) must never
    block the others from writing, same fail-open rule the primary file
    already followed, just extended to N files instead of 1.

    No-op for a node with no `target_file` and no `additional_edits`, or
    before any workspace is declared — nothing to sync.
    """
    if _workspace_root is None:
        return

    if node.target_file:
        # Reconstruct what the whole file looks like with this node's code
        # applied — needed for target_range nodes, whose code_snapshot is
        # only ever a snippet, never the whole file.
        original_at_generation = _source_snapshots.get(node.id) or ""
        intended = (
            _splice_range(original_at_generation, node.target_range, node.code_snapshot)
            if node.target_range else node.code_snapshot
        )

        def _remember_primary(written: str) -> None:
            _source_snapshots[node.id] = written

        await _sync_one_file(node.id, node.target_file, intended, original_at_generation, _remember_primary)

    for path, content in node.additional_edits.items():
        fallback_ancestor = _additional_source_snapshots.get(node.id, {}).get(path, "")
        await _sync_one_file(node.id, path, content, fallback_ancestor)


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


class TargetRange(BaseModel):
    start_line: int
    end_line: int


class BranchRequest(BaseModel):
    node_id: str
    prompt: str
    beam_width: int = DEFAULT_BEAM_WIDTH
    target_file: Optional[str] = None
    target_range: Optional[TargetRange] = None
    include_codebase: bool = True
    intent: Literal["auto", "question", "code_change"] = "auto"


class BacktrackRequest(BaseModel):
    node_id: str


class QueryRequest(BaseModel):
    node_id: str
    question: str
    chain_from: Optional[str] = None


class PatchRequest(BaseModel):
    source_node_id: str
    query_id: str
    target_node_id: str
    force: bool = False


class MergeRequest(BaseModel):
    source_node_id: str
    current_node_id: str


class MergeSiblingsRequest(BaseModel):
    node_a_id: str
    node_b_id: str


class ContinueRequest(BaseModel):
    node_id: str
    prompt: str
    target_file: Optional[str] = None
    target_range: Optional[TargetRange] = None
    include_codebase: bool = True
    intent: Literal["auto", "question", "code_change"] = "auto"


class EditPromptRequest(BaseModel):
    node_id: str
    new_prompt: str


class LoadRequest(BaseModel):
    path: str


class SaveRequest(BaseModel):
    path: str = DEFAULT_SESSION_PATH


class WorkspaceRequest(BaseModel):
    root: str


class ActiveEditorContextRequest(BaseModel):
    file: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None


class ApplyToFileRequest(BaseModel):
    node_id: str


@app.post("/branch")
async def branch(req: BranchRequest, request: Request):
    if req.beam_width < 1:
        raise HTTPException(400, "beam_width must be >= 1")

    async def on_branch_ready(b: Branch) -> None:
        _capture_source_snapshot(orch().tree.get_node(b.node_id))
        await _broadcast("branch.ready", {"branch": branch_to_dict(b)})

    try:
        branches = await _run_cancelable(
            orch().branch(
                req.node_id, req.prompt, req.beam_width,
                on_branch_ready=on_branch_ready,
                target_file=req.target_file,
                target_range=req.target_range.model_dump() if req.target_range else None,
                include_codebase=req.include_codebase,
                intent=req.intent,
            ),
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
            orch().continue_from(
                req.node_id, req.prompt,
                target_file=req.target_file,
                target_range=req.target_range.model_dump() if req.target_range else None,
                include_codebase=req.include_codebase,
                intent=req.intent,
            ),
            request,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except LLMGenerationFailed as e:
        raise HTTPException(502, str(e))
    except asyncio.CancelledError:
        raise HTTPException(499, "Request cancelled by client")
    _capture_source_snapshot(node)
    await _sync_active_file_to_disk(node)
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
    await _sync_active_file_to_disk(node)
    return {"node": node_to_dict(node)}


@app.post("/query")
async def query(req: QueryRequest, request: Request):
    """Phase 4: ask any past node a question without moving off the
    active node. 404 for an unknown node_id or an unknown chain_from (a
    QueryEntry id that isn't actually on that node); LLM failure returns
    502 with the node's query_history left exactly as it was — no partial
    write, same fail-open contract as /continue.
    """
    try:
        entry = await _run_cancelable(
            orch().query(req.node_id, req.question, chain_from=req.chain_from), request,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except LLMGenerationFailed as e:
        raise HTTPException(502, str(e))
    except asyncio.CancelledError:
        raise HTTPException(499, "Request cancelled by client")
    return {"query": query_entry_to_dict(entry)}


@app.post("/patch")
async def patch(req: PatchRequest, request: Request):
    """Phase 5.2, Option A: apply a clarification from `source_node_id`
    (a past node's query answer) onto `target_node_id`. Always runs real
    conflict detection first (CLAUDE.md: non-negotiable, never skipped
    even when the caller is confident there's no conflict) — a conflict
    fires `conflict.detected` and creates nothing; a clean patch creates
    one new active child of `target_node_id` and re-scores it in the
    background, same as every other node-creating endpoint.
    """
    try:
        outcome = await _run_cancelable(
            orch().patch(req.source_node_id, req.query_id, req.target_node_id, force=req.force), request,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except LLMGenerationFailed as e:
        raise HTTPException(502, str(e))
    except asyncio.CancelledError:
        raise HTTPException(499, "Request cancelled by client")

    if outcome.conflict:
        await _broadcast("conflict.detected", {
            "source_node_id": req.source_node_id,
            "target_node_id": req.target_node_id,
            "diff_preview": outcome.diff_preview,
        })
    else:
        _capture_source_snapshot(outcome.node)
        await _sync_active_file_to_disk(outcome.node)
        asyncio.create_task(_evaluate_and_notify(
            [Branch(node_id=outcome.node.id, code=outcome.node.code_snapshot, reasoning="")]
        ))
    return {"outcome": patch_outcome_to_dict(outcome)}


@app.post("/merge")
async def merge(req: MergeRequest, request: Request):
    """Phase 5.3, Option C: branch fresh from `source_node_id` with the
    preferences accumulated along the path to `current_node_id` folded
    in. No conflict detection (there's no divergent target being
    reconciled against — see Orchestrator.merge's own docstring); always
    creates a new child of `source_node_id` and makes it active.
    """
    try:
        outcome = await _run_cancelable(
            orch().merge(req.source_node_id, req.current_node_id), request,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LLMGenerationFailed as e:
        raise HTTPException(502, str(e))
    except asyncio.CancelledError:
        raise HTTPException(499, "Request cancelled by client")

    _capture_source_snapshot(outcome.node)
    await _sync_active_file_to_disk(outcome.node)
    asyncio.create_task(_evaluate_and_notify(
        [Branch(node_id=outcome.node.id, code=outcome.node.code_snapshot, reasoning="")]
    ))
    return {"outcome": patch_outcome_to_dict(outcome)}


@app.post("/merge-siblings")
async def merge_siblings(req: MergeSiblingsRequest, request: Request):
    """Phase 6.2: combine two sibling branches (same parent) into one new
    node via LLM synthesis -- see Orchestrator.merge_siblings's own
    docstring for why this is distinct from /merge and /patch above.
    """
    try:
        node = await _run_cancelable(
            orch().merge_siblings(req.node_a_id, req.node_b_id), request,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LLMGenerationFailed as e:
        raise HTTPException(502, str(e))
    except asyncio.CancelledError:
        raise HTTPException(499, "Request cancelled by client")

    _capture_source_snapshot(node)
    await _sync_active_file_to_disk(node)
    asyncio.create_task(_evaluate_and_notify(
        [Branch(node_id=node.id, code=node.code_snapshot, reasoning="")]
    ))
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


@app.post("/workspace")
async def set_workspace(req: WorkspaceRequest):
    """Step 3.2: the VS Code extension calls this once on activation to
    declare its workspace root. /apply-to-file refuses any write outside it.

    Step 3.6/3.7 (2026-09-27): also (re-)gathers a codebase snapshot and
    stores it on the root node — this doubles as the "refresh codebase
    context" action from Step 3.7: calling /workspace again (even with the
    same root) re-walks the directory and updates what future nodes
    inherit. It never touches already-created nodes — append-only holds.
    """
    global _workspace_root
    root = Path(req.root).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(400, f"Not a directory: {root}")
    _workspace_root = root

    # Filesystem walk is blocking; keep it off the event loop.
    snapshot = await asyncio.to_thread(gather_codebase_context, root)
    orch().tree.set_codebase_context("root", snapshot or None)

    return {"root": str(_workspace_root), "codebase_context_chars": len(snapshot)}


@app.post("/active-editor-context")
async def set_active_editor_context(req: ActiveEditorContextRequest):
    """Phase 5.6: the extension calls this on every active-editor/selection
    change (debounced on its side). `file=None` means "no active editor" —
    e.g. the user closed the last file, or switched to a non-text view.
    Broadcasts the same payload over the WebSocket so both connected
    clients (the embedded webview and a standalone browser tab) update
    live, without polling.
    """
    global _active_editor_context
    if req.file is None:
        _active_editor_context = None
    else:
        _active_editor_context = {
            "file": req.file, "start_line": req.start_line, "end_line": req.end_line,
        }
    await _broadcast("active_editor.changed", {"context": _active_editor_context})
    return {"context": _active_editor_context}


@app.get("/active-editor-context")
def get_active_editor_context():
    """Phase 5.6: for a client that just opened (webview or a fresh browser
    tab) and needs the current state immediately, before any further
    change event fires.
    """
    return {"context": _active_editor_context}


@app.post("/apply-to-file")
async def apply_to_file(req: ApplyToFileRequest):
    """Step 3.2, upgraded by Phase 5.5 (2026-09-28): writes a node's
    intended content to its target_file.

    Step 3.9's original interim safeguard (2026-09-27) only detected
    *that* the file had changed since generation and refused outright —
    a blunt but real fix for the README incident. This is the real fix it
    was always meant to be superseded by: when the file HAS changed,
    attempt a genuine merge via `git_engine.merge_code` (the same engine
    `/patch` uses) instead of just refusing. A clean merge reconciles the
    node's intended change with whatever else changed in the file; a real
    conflict fires `conflict.detected` (same event, same payload shape
    `/patch` uses) and refuses with a 409 — never silently applies a
    broken merge (CLAUDE.md's git-merge fail-open rule).

    A node with no snapshot on record (no workspace was declared when it
    was created, a session loaded from before this existed, etc.) still
    fails open exactly as before — nothing to diff against, so the write
    proceeds directly.
    """
    try:
        node = orch().tree.get_node(req.node_id)
    except KeyError as e:
        raise HTTPException(404, str(e))

    if not node.target_file:
        raise HTTPException(400, f"Node {req.node_id} has no target_file set")

    target = _resolve_target_path(node.target_file)

    # What this node intends the full file to become — splicing into just
    # target_range when the node came from a selection (Step 3.2's own
    # fix), or the whole code_snapshot otherwise.
    original_at_generation = _source_snapshots.get(req.node_id) or ""
    intended = (
        _splice_range(original_at_generation, node.target_range, node.code_snapshot)
        if node.target_range else node.code_snapshot
    )

    if req.node_id in _source_snapshots:
        current_on_disk = _read_file(target) or ""
        if current_on_disk != original_at_generation:
            # The file genuinely changed since this node was generated —
            # attempt a real merge instead of just refusing.
            patch_text = make_patch(original_at_generation, intended)
            result = merge_code(current_on_disk, patch_text)
            if not result.success:
                await _broadcast("conflict.detected", {
                    "node_id": req.node_id,
                    "target_file": str(target),
                    "diff_preview": result.diff_preview,
                })
                raise HTTPException(
                    409,
                    f"{target} has changed since this branch was generated, and the "
                    "change couldn't be merged cleanly — refusing to overwrite it "
                    "blindly. Re-generate from the current file, or apply manually.",
                )
            intended = result.merged_code

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(intended)
    except OSError as e:
        raise HTTPException(500, f"Could not write {target}: {e}")

    _source_snapshots[req.node_id] = intended
    _file_last_synced[str(target)] = intended

    # Phase 8 (2026-10-19): a node reached via this endpoint (a branch the
    # user picked manually, not yet auto-activated) can also have
    # additional_edits. These use the same fail-open sync as activation
    # does, deliberately NOT the strict refuse-with-409 contract above --
    # that contract is specifically about the PRIMARY file the user just
    # explicitly asked to apply; an additional file conflicting shouldn't
    # turn an otherwise-successful "Apply" action into a hard failure.
    for path, content in node.additional_edits.items():
        fallback_ancestor = _additional_source_snapshots.get(req.node_id, {}).get(path, "")
        await _sync_one_file(req.node_id, path, content, fallback_ancestor)

    return {"path": str(target)}


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
