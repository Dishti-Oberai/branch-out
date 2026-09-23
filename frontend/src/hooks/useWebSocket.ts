import { useEffect, useRef } from "react";

const WS_URL = "ws://localhost:8000/ws";

export interface BranchReadyEvent {
  event: "branch.ready";
  branch: { node_id: string; code: string; reasoning: string; score: number | null; rank: number | null };
}

export interface ScoreReadyEvent {
  event: "score.ready";
  node_id: string;
  score: number | null;
  rank: number | null;
  score_source: "tests" | "llm_judge" | null;
}

export type BranchOutEvent = BranchReadyEvent | ScoreReadyEvent;

/**
 * Connects to the backend's /ws endpoint and calls `onEvent` for every
 * branch.ready / score.ready message. Reconnects with a fixed short delay
 * if the connection drops — best-effort, matching the backend's own
 * best-effort broadcast (main.py's `_broadcast` drops dead connections
 * silently). A missed event just means a score badge updates a little
 * late; `/session` stays the source of truth either way.
 */
export function useTreeWebSocket(onEvent: (event: BranchOutEvent) => void) {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    const connect = () => {
      socket = new WebSocket(WS_URL);
      socket.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data?.event === "branch.ready" || data?.event === "score.ready") {
            onEventRef.current(data as BranchOutEvent);
          }
        } catch {
          // Malformed message — ignore rather than crash the connection.
        }
      };
      socket.onclose = () => {
        if (!stopped) reconnectTimer = setTimeout(connect, 2000);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);
}
