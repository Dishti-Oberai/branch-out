import { useCallback, useEffect, useRef, useState } from "react";
import type { PatchOutcome, QueryEntry, TreeNode } from "../types";
import { useTreeWebSocket, type BranchOutEvent } from "./useWebSocket";

const API = "http://localhost:8000";

async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? res.statusText);
  return res.json();
}

export function useTree() {
  const [nodes, setNodes] = useState<TreeNode[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Step 2.4: node ids whose score is still being evaluated in the
  // background (from branch.ready until score.ready arrives), so the UI
  // can show "evaluating…" rather than a bare "not scored" that looks
  // identical to a node that will never be scored (continue/edit-prompt
  // nodes never go through the evaluator at all).
  const [pendingScoreIds, setPendingScoreIds] = useState<Set<string>>(new Set());
  // Step 1.10: lets the caller abort the in-flight request (the backend
  // detects the dropped connection via request.is_disconnected() and
  // cancels the outstanding branch generation).
  const controllerRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    const res = await fetch(`${API}/session`);
    setNodes((await res.json()).nodes);
  }, []);

  // Returns `fn`'s result (undefined on failure/cancel, already surfaced
  // via `error`) so callers that need the response — Phase 5's patch()/
  // merge(), which must know whether a conflict happened — can act on it,
  // while existing callers that don't care just ignore the return value.
  const run = useCallback(async <T,>(fn: (signal: AbortSignal) => Promise<T>): Promise<T | undefined> => {
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setError(null);
    try {
      const result = await fn(controller.signal);
      await refresh();
      return result;
    } catch (e) {
      if (controller.signal.aborted) {
        setError("Cancelled");
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
      return undefined;
    } finally {
      setBusy(false);
      controllerRef.current = null;
    }
  }, [refresh]);

  useEffect(() => {
    refresh().catch(() => setError("Backend not reachable on :8000"));
  }, [refresh]);

  const handleWsEvent = useCallback((event: BranchOutEvent) => {
    if (event.event === "branch.ready") {
      setPendingScoreIds((prev) => {
        const next = new Set(prev);
        next.add(event.branch.node_id);
        return next;
      });
    } else {
      setPendingScoreIds((prev) => {
        if (!prev.has(event.node_id)) return prev;
        const next = new Set(prev);
        next.delete(event.node_id);
        return next;
      });
      setNodes((prev) => prev.map((n) =>
        n.id === event.node_id
          ? { ...n, score: event.score, score_source: event.score_source }
          : n
      ));
    }
  }, []);
  useTreeWebSocket(handleWsEvent);

  const active = nodes.find((n) => n.status === "active") ?? null;

  return {
    nodes,
    pendingScoreIds,
    active,
    selectedId,
    selected: nodes.find((n) => n.id === selectedId) ?? null,
    select: setSelectedId,
    busy,
    error,
    cancel: () => controllerRef.current?.abort(),
    branch: (nodeId: string, prompt: string, beamWidth?: number) =>
      run((signal) => post("/branch", { node_id: nodeId, prompt, ...(beamWidth ? { beam_width: beamWidth } : {}) }, signal)),
    // A single answer, no branching — the default for an ordinary prompt.
    continueFrom: (nodeId: string, prompt: string) =>
      run((signal) => post("/continue", { node_id: nodeId, prompt }, signal)),
    backtrack: (nodeId: string) => run((signal) => post("/backtrack", { node_id: nodeId }, signal)),
    editPrompt: (nodeId: string, newPrompt: string) =>
      run((signal) => post("/edit-prompt", { node_id: nodeId, new_prompt: newPrompt }, signal)),
    // Step 3.5: only meaningful for nodes with target_file set (extension-
    // originated). Doesn't change the tree, so it deliberately doesn't
    // route through the same `run()`/busy-state wrapper as the mutating
    // actions above — this writes to a file, not to the session.
    applyToFile: (nodeId: string) => post<{ path: string }>("/apply-to-file", { node_id: nodeId }),
    // Phase 4: querying a past node deliberately does NOT touch the active
    // node or create any new node — reusing `run()`'s global `busy` flag
    // would incorrectly grey out the main prompt bar while a background
    // query on some other node is in flight, which defeats the whole
    // point (query a past decision without disturbing what you're doing
    // now). Patches just the queried node's `query_history` into local
    // state directly from the response, instead of a full `/session`
    // refetch — cheaper, and avoids any risk of clobbering other
    // in-flight local state.
    query: async (nodeId: string, question: string, chainFrom?: string) => {
      const { query: entry } = await post<{ query: QueryEntry }>("/query", {
        node_id: nodeId, question, chain_from: chainFrom,
      });
      setNodes((prev) => prev.map((n) =>
        n.id === nodeId ? { ...n, query_history: [...n.query_history, entry] } : n
      ));
      return entry;
    },
    // Phase 5.2, Option A: apply a clarification (a QueryEntry) from
    // `sourceNodeId` onto `targetNodeId`. Routed through `run()` (unlike
    // `query()` above) since this DOES mutate the tree — a new node,
    // possibly a new active node — the same as branch/continueFrom.
    // `force=true` is the conflict banner's "Apply anyway" action.
    patch: (sourceNodeId: string, queryId: string, targetNodeId: string, force = false) =>
      run((signal) => post<{ outcome: PatchOutcome }>("/patch", {
        source_node_id: sourceNodeId, query_id: queryId, target_node_id: targetNodeId, force,
      }, signal)),
    // Phase 5.3, Option C: branch fresh from `sourceNodeId` with
    // preferences folded in from the path down to `currentNodeId`.
    merge: (sourceNodeId: string, currentNodeId: string) =>
      run((signal) => post<{ outcome: PatchOutcome }>("/merge", {
        source_node_id: sourceNodeId, current_node_id: currentNodeId,
      }, signal)),
    save: () => run(() => post("/session/save", {})),
    load: (path: string) => run(async () => { await post("/session/load", { path }); }),
  };
}
