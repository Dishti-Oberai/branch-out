import { useState } from "react";
import type { QueryEntry, TreeNode } from "../types";
import { deriveNodeTitle } from "../nodeTitle";
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
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [tab, setTab] = useState<"state" | "queries">("queries");
  const [question, setQuestion] = useState("");
  const [queryBusy, setQueryBusy] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);
  const isRoot = node.parent_id === null;
  const title = deriveNodeTitle(node);
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
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">
              Ask this node something — it answers from its own context, without touching whatever's active.
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
