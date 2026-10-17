/**
 * Thin HTTP client for the Branch-Out FastAPI backend. Talks to the same
 * local server the standalone web app uses (localhost:8000) — no second
 * backend, this is just a second client. Shapes here mirror backend/main.py
 * exactly; keep them in sync if the backend contract changes.
 */

import * as vscode from "vscode";

const BASE_URL = "http://localhost:8000";

export interface TargetRange {
  start_line: number;
  end_line: number;
}

export interface Branch {
  node_id: string;
  code: string;
  reasoning: string;
  score: number | null;
  rank: number | null;
  score_source: "tests" | "llm_judge" | null;
}

export interface TreeNodeData {
  id: string;
  parent_id: string | null;
  children_ids: string[];
  code_snapshot: string;
  context_summary: string;
  branch_label: string;
  status: "active" | "stale" | "orphaned" | "normal";
  score: number | null;
  score_source: "tests" | "llm_judge" | null;
  target_file: string | null;
  target_range: TargetRange | null;
  // Phase 1 amendment (2026-09-27): static codebase snapshot, inherited
  // parent->child. Only ever populated once `setWorkspace` has been called.
  codebase_context: string | null;
}

class ApiError extends Error {}

async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    const message = (detail && typeof detail === "object" && "detail" in detail)
      ? String((detail as { detail: unknown }).detail)
      : res.statusText;
    throw new ApiError(`${method} ${path} failed (${res.status}): ${message}`);
  }
  return res.json() as Promise<T>;
}

export async function setWorkspace(root: string): Promise<void> {
  await request("POST", "/workspace", { root });
}

/**
 * Bug fix (2026-09-27): the backend is a single shared process across every
 * VS Code window (deliberate single-session POC scope — see CLAUDE.md), but
 * the extension only ever declared its workspace root once, at cold
 * `activate()`. Whichever window activated most recently silently "won" the
 * declared root forever after — so a second window (or one left open from
 * an earlier debug session) would have every apply-to-file call checked
 * against the WRONG folder, surfacing as a confusing "outside the
 * workspace root" 400 with no indication another window was the cause.
 *
 * Fix: re-declare the *current* window's folder right before any command
 * that needs it, so the window you're actually working in always wins for
 * its own commands, rather than relying on a one-time declaration made at
 * an arbitrary point in the past by whichever window happened to activate
 * last. Failures are swallowed here too (consistent with activate()'s own
 * best-effort behavior) — a command that actually needs the workspace
 * (e.g. apply-to-file) will still surface its own clear error if this silently
 * didn't get through.
 */
export async function ensureWorkspaceDeclared(): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    return;
  }
  try {
    await setWorkspace(folder.uri.fsPath);
  } catch {
    // backend not up yet, or unreachable — swallow, same as activate()'s
    // best-effort semantics; the calling command will surface its own error.
  }
}

export async function getSession(): Promise<{ nodes: TreeNodeData[] }> {
  return request("GET", "/session");
}

export type Intent = "auto" | "question" | "code_change";

export async function branch(
  nodeId: string,
  prompt: string,
  beamWidth: number,
  targetFile?: string,
  targetRange?: TargetRange,
  includeCodebase = true,
  intent: Intent = "auto"
): Promise<{ branches: Branch[] }> {
  return request("POST", "/branch", {
    node_id: nodeId,
    prompt,
    beam_width: beamWidth,
    target_file: targetFile,
    target_range: targetRange,
    include_codebase: includeCodebase,
    intent,
  });
}

export async function continueFrom(
  nodeId: string,
  prompt: string,
  targetFile?: string,
  targetRange?: TargetRange,
  includeCodebase = true,
  intent: Intent = "auto"
): Promise<{ node: TreeNodeData }> {
  return request("POST", "/continue", {
    node_id: nodeId,
    prompt,
    target_file: targetFile,
    target_range: targetRange,
    include_codebase: includeCodebase,
    intent,
  });
}

export async function applyToFile(nodeId: string): Promise<{ path: string }> {
  return request("POST", "/apply-to-file", { node_id: nodeId });
}

/**
 * Phase 5.6: reports the active editor/selection into the backend's shared
 * session state, so both the embedded webview and a standalone browser tab
 * can show the same "currently targeting" state without either needing its
 * own file picker. `file: null` means no active editor — closing the last
 * file, or switching to a non-text view. Swallows failures (consistent
 * with `ensureWorkspaceDeclared`'s own best-effort semantics) — this fires
 * on every editor/selection change, far too often to surface errors for.
 */
export async function reportActiveEditorContext(
  file: string | null, targetRange?: TargetRange
): Promise<void> {
  try {
    await request("POST", "/active-editor-context", {
      file,
      start_line: targetRange?.start_line ?? null,
      end_line: targetRange?.end_line ?? null,
    });
  } catch {
    // backend not up, or a transient blip -- not worth surfacing for a
    // background report that fires this often.
  }
}

export function activeNode(nodes: TreeNodeData[]): TreeNodeData | undefined {
  return nodes.find((n) => n.status === "active");
}

export { ApiError };
