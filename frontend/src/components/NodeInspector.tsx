import { useState } from "react";
import type { PatchOutcome, QueryEntry, TreeNode } from "../types";
import { deriveLatestAnswer, deriveNodeTitle } from "../nodeTitle";
import { isVsCodeWebview } from "../vscodeBridge";

// Phase 4 (2026-09-27): Queries tab. Node click opens it by default (per
// ROADMAP 4.3) -- the parent (App.tsx) remounts this component per node
// via `key={node.id}`, so a fresh `useState("queries")` on every selection
// already gives "opens by default" for free, while still letting someone
// switch to State within one selection and have it stick until the next.
export default function NodeInspector({
  node,
  nodes,
  isActive,
  pending,
  onMakeActive,
  onClose,
  onEditPrompt,
  onApplyToFile,
  onQuery,
  activeNodeId,
  onPatch,
  onMerge,
  onMergeSiblings,
}: {
  node: TreeNode;
  nodes: TreeNode[];
  isActive: boolean;
  pending: boolean;
  onMakeActive: () => void;
  onClose: () => void;
  onEditPrompt: (newPrompt: string) => void;
  onApplyToFile: () => void;
  onQuery: (nodeId: string, question: string, chainFrom?: string) => Promise<QueryEntry>;
  // Phase 5.2/5.3: Option A (apply a clarification to the active node) and
  // Option C (spawn a fresh branch from this node with accumulated
  // preferences folded in). Both return undefined on failure (the error
  // already surfaces via the app's global error state) — only a defined
  // result with `conflict: true` needs handling here, for the banner.
  activeNodeId: string | null;
  onPatch: (
    sourceNodeId: string, queryId: string, targetNodeId: string, force?: boolean
  ) => Promise<{ outcome: PatchOutcome } | undefined>;
  onMerge: (sourceNodeId: string, currentNodeId: string) => Promise<{ outcome: PatchOutcome } | undefined>;
  // Phase 6.2: combine this node with a sibling (same parent) into one new
  // synthesized node — see orchestrator.merge_siblings's own docstring.
  onMergeSiblings: (nodeAId: string, nodeBId: string) => Promise<{ node: TreeNode } | undefined>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [tab, setTab] = useState<"state" | "queries">("queries");
  const [question, setQuestion] = useState("");
  const [queryBusy, setQueryBusy] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);
  const isRoot = node.parent_id === null;
  const title = deriveNodeTitle(node);
  const latestAnswer = deriveLatestAnswer(node);
  // Step 3.5: only shown inside the VS Code webview, and only for nodes
  // that actually have somewhere to apply to.
  const canApplyToFile = isVsCodeWebview() && node.target_file !== null;
  // Finding (2026-09-27): a question-mode node (see quickPick.ts's "Ask a
  // question") deliberately leaves code_snapshot byte-identical to its
  // parent's -- the real answer lives in context_summary. Unconditionally
  // showing a full "Code snapshot" block for that node made it look like a
  // code change happened when nothing did. Collapse the section instead of
  // hiding it outright, so it's still reachable if someone wants to confirm
  // nothing changed, without it being the visually dominant thing on a
  // node that was really just a question.
  const parent = node.parent_id ? nodes.find((n) => n.id === node.parent_id) : undefined;
  const codeUnchangedFromParent = parent !== undefined && node.code_snapshot === parent.code_snapshot;
  const [codeExpanded, setCodeExpanded] = useState(!codeUnchangedFromParent);

  // Follow-ups auto-chain from the most recent query on this node (ROADMAP
  // 4.3: "Follow-ups auto-send chain_from") -- no separate UI toggle, a
  // second question in the same session just naturally builds on the last
  // answer, the way asking a follow-up in conversation normally would.
  const lastEntry = node.query_history[node.query_history.length - 1];

  async function submitQuery() {
    const q = question.trim();
    if (!q) return;
    setQueryBusy(true);
    setQueryError(null);
    try {
      await onQuery(node.id, q, lastEntry?.id);
      setQuestion("");
    } catch (e) {
      setQueryError(e instanceof Error ? e.message : String(e));
    } finally {
      setQueryBusy(false);
    }
  }

  // Phase 5.4: the conflict banner. Inline, never a modal, scoped to
  // whichever query entry triggered it (ROADMAP 5.4). `patchBusy` tracks
  // just this node's patch/merge actions, separate from `queryBusy`.
  const [conflict, setConflict] = useState<{ queryId: string; diffPreview: string } | null>(null);
  const [patchBusy, setPatchBusy] = useState(false);
  // Real bug, found via manual testing (2026-10-18): both buttons used to
  // share one `canActOnClarifications` flag, hidden entirely whenever you
  // queried the ACTIVE node about itself. But "Apply to current" (/patch)
  // revises THIS node's code with the clarification and merges it onto
  // the target -- when source and target are the same node, that's just
  // "revise the active node with this answer," which orchestrator.py's
  // _incorporate_clarification/merge_code handle correctly with no special
  // case needed. "Spawn branch from here" (/merge) is different: it never
  // even looks at the query's answer, only at preferences accumulated
  // along the path FROM this node TO the active one -- when they're the
  // same node that path is empty, so it would silently produce a branch
  // that ignores the clarification entirely. Split in two: apply-to-
  // current only needs a known active node; spawn-branch still needs a
  // genuinely different one to have anything to merge.
  const canApplyToCurrent = activeNodeId !== null;
  const canSpawnBranch = activeNodeId !== null && activeNodeId !== node.id;

  // Phase 6.2: other children of the same parent — root has no parent, so
  // it never has siblings by definition (every other node shares SOME
  // parent, even if root itself).
  const siblings = node.parent_id
    ? nodes.filter((n) => n.parent_id === node.parent_id && n.id !== node.id)
    : [];
  const [mergeSiblingId, setMergeSiblingId] = useState("");
  const [mergeBusy, setMergeBusy] = useState(false);

  async function mergeWithSibling() {
    if (!mergeSiblingId) return;
    setMergeBusy(true);
    try {
      await onMergeSiblings(node.id, mergeSiblingId);
    } finally {
      setMergeBusy(false);
    }
  }

  async function applyToCurrent(queryId: string, force = false) {
    if (!activeNodeId) return;
    setPatchBusy(true);
    try {
      const result = await onPatch(node.id, queryId, activeNodeId, force);
      if (result?.outcome.conflict) {
        setConflict({ queryId, diffPreview: result.outcome.diff_preview ?? "" });
      } else {
        setConflict(null);
      }
    } finally {
      setPatchBusy(false);
    }
  }

  async function spawnBranch() {
    if (!activeNodeId) return;
    setPatchBusy(true);
    try {
      await onMerge(node.id, activeNodeId);
      setConflict(null);
    } finally {
      setPatchBusy(false);
    }
  }

  return (
    <aside className="inspector">
      <header>
        <div className="inspector-heading">
          <span className="branch-tag">{node.branch_label}</span>
          <span className="label-pill">{title ?? node.branch_label}</span>
        </div>
        <code>{node.id}</code>
        <button className="icon-btn" onClick={onClose}>×</button>
      </header>
      <div className="tabs">
        <span className={`tab${tab === "queries" ? " on" : ""}`} onClick={() => setTab("queries")}>
          Queries{node.query_history.length ? ` (${node.query_history.length})` : ""}
        </span>
        <span className={`tab${tab === "state" ? " on" : ""}`} onClick={() => setTab("state")}>State</span>
      </div>
      {node.status === "stale" && (
        <p className="stale-banner">
          ⚠ Stale — the prompt that led here was revised. Descendants may no longer reflect it.
        </p>
      )}
      {!isActive && <button onClick={onMakeActive}>↩ Make active (backtrack here)</button>}

      {tab === "queries" ? (
        <div className="queries-tab">
          {node.query_history.length ? (
            <ul className="query-list">
              {node.query_history.map((q) => (
                <li key={q.id} className="query-entry">
                  <p className="query-question">{q.question}</p>
                  <p className="query-answer">{q.answer}</p>
                  {(canApplyToCurrent || canSpawnBranch) && (
                    <div className="query-actions">
                      {canApplyToCurrent && (
                        <button type="button" disabled={patchBusy} onClick={() => applyToCurrent(q.id)}>
                          Apply to current
                        </button>
                      )}
                      {canSpawnBranch && (
                        <button type="button" disabled={patchBusy} onClick={spawnBranch}>
                          Spawn branch from here
                        </button>
                      )}
                    </div>
                  )}
                  {conflict?.queryId === q.id && (
                    <div className="conflict-banner">
                      <p>⚠ This clarification conflicts with the active node's current code.</p>
                      <pre className="conflict-diff">{conflict.diffPreview}</pre>
                      <div className="conflict-actions">
                        <button type="button" disabled={patchBusy} onClick={() => applyToCurrent(q.id, true)}>
                          Apply anyway
                        </button>
                        <button type="button" onClick={() => setConflict(null)}>Cancel</button>
                        {canSpawnBranch && (
                          <button type="button" disabled={patchBusy} onClick={spawnBranch}>Spawn branch</button>
                        )}
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">
              Ask this node something — it answers from its own context, without touching whatever's active.
            </p>
          )}
          {!canSpawnBranch && node.query_history.length > 0 && activeNodeId === node.id && (
            <p className="muted">
              This is the active node — "Spawn branch from here" isn't offered since there's nothing
              downstream to merge; use "Apply to current" to revise it with this answer.
            </p>
          )}
          <form
            className="query-form"
            onSubmit={(e) => { e.preventDefault(); submitQuery(); }}
          >
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={lastEntry ? "Ask a follow-up…" : "Ask this node a question…"}
              disabled={queryBusy}
            />
            <button type="submit" disabled={queryBusy || !question.trim()}>
              {queryBusy ? "Asking…" : "Ask"}
            </button>
          </form>
          {queryError && <p className="query-error">{queryError}</p>}
        </div>
      ) : (
        <>
          {canApplyToFile && (
            <button onClick={onApplyToFile} title={node.target_file ?? undefined}>
              ⇥ Apply to file{node.target_file ? `: ${node.target_file.split("/").pop()}` : ""}
            </button>
          )}
          {!isRoot && (
            editing ? (
              <form
                className="edit-prompt"
                onSubmit={(e) => { e.preventDefault(); if (draft.trim()) { onEditPrompt(draft.trim()); setEditing(false); } }}
              >
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="Revised prompt for this step"
                  autoFocus
                />
                <div>
                  <button type="submit">Save (creates a sibling, marks this stale)</button>
                  <button type="button" onClick={() => setEditing(false)}>Cancel</button>
                </div>
              </form>
            ) : (
              <button onClick={() => { setDraft(""); setEditing(true); }}>✎ Edit prompt</button>
            )
          )}
          <h4>Score</h4>
          <span className={`score-badge${pending ? " score-pending" : ""}`}>
            {pending
              ? "evaluating…"
              : node.score === null
                ? "not scored yet"
                : `${node.score.toFixed(2)} · ${node.score_source}`}
          </span>
          <h4>Key decisions</h4>
          {node.user_preferences.length ? (
            <ul>{node.user_preferences.map((p) => <li key={p}>{p}</li>)}</ul>
          ) : <p className="muted">none</p>}
          {siblings.length > 0 && (
            <>
              <h4>Merge with sibling branch</h4>
              <div className="merge-siblings-row">
                <select
                  value={mergeSiblingId}
                  onChange={(e) => setMergeSiblingId(e.target.value)}
                  disabled={mergeBusy}
                >
                  <option value="">Choose a sibling…</option>
                  {siblings.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.branch_label} — {deriveNodeTitle(s) ?? s.id}
                    </option>
                  ))}
                </select>
                <button type="button" disabled={!mergeSiblingId || mergeBusy} onClick={mergeWithSibling}>
                  {mergeBusy ? "Merging…" : "Synthesize combined approach"}
                </button>
              </div>
              <p className="muted">
                Combines both branches' code into one new node via the model — not a pick, a real synthesis.
              </p>
            </>
          )}
          <h4>Response</h4>
          <p className="node-answer">{latestAnswer ?? "—"}</p>
          <h4>Context</h4>
          <pre>{node.context_summary || "—"}</pre>
          <h4 className="code-snapshot-heading">
            Code snapshot
            {codeUnchangedFromParent && (
              <>
                <span className="muted code-unchanged-note"> · unchanged from parent</span>
                <button
                  type="button"
                  className="icon-btn code-toggle"
                  onClick={() => setCodeExpanded((v) => !v)}
                >
                  {codeExpanded ? "hide" : "show"}
                </button>
              </>
            )}
          </h4>
          {codeExpanded && (
            <div className="code-block">
              <div className="code-block-bar">
                <span className="dot dot-red" /><span className="dot dot-amber" /><span className="dot dot-green" />
              </div>
              <pre>{node.code_snapshot || "—"}</pre>
            </div>
          )}
        </>
      )}
    </aside>
  );
}
