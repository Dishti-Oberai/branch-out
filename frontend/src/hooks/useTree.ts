import { useCallback, useEffect, useRef, useState } from "react";
import type { TreeNode } from "../types";
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

  const run = useCallback(async (fn: (signal: AbortSignal) => Promise<unknown>) => {
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setError(null);
    try {
      await fn(controller.signal);
      await refresh();
    } catch (e) {
      if (controller.signal.aborted) {
        setError("Cancelled");
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
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
    save: () => run(() => post("/session/save", {})),
    load: (path: string) => run(async () => { await post("/session/load", { path }); }),
  };
}
