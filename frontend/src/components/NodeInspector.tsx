import { useState } from "react";
import type { TreeNode } from "../types";
import { deriveNodeTitle } from "../nodeTitle";

// Phase 1: State tab only. Queries tab arrives in Phase 3.
export default function NodeInspector({
  node,
  isActive,
  onMakeActive,
  onClose,
  onEditPrompt,
}: {
  node: TreeNode;
  isActive: boolean;
  onMakeActive: () => void;
  onClose: () => void;
  onEditPrompt: (newPrompt: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const isRoot = node.parent_id === null;
  const title = deriveNodeTitle(node);

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
      <div className="tabs"><span className="tab on">State</span></div>
      {node.status === "stale" && (
        <p className="stale-banner">
          ⚠ Stale — the prompt that led here was revised. Descendants may no longer reflect it.
        </p>
      )}
      {!isActive && <button onClick={onMakeActive}>↩ Make active (backtrack here)</button>}
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
      <span className="score-badge">
        {node.score === null ? "not scored yet" : `${node.score} · ${node.score_source}`}
      </span>
      <h4>Key decisions</h4>
      {node.user_preferences.length ? (
        <ul>{node.user_preferences.map((p) => <li key={p}>{p}</li>)}</ul>
      ) : <p className="muted">none</p>}
      <h4>Context</h4>
      <pre>{node.context_summary || "—"}</pre>
      <h4>Code snapshot</h4>
      <div className="code-block">
        <div className="code-block-bar">
          <span className="dot dot-red" /><span className="dot dot-amber" /><span className="dot dot-green" />
        </div>
        <pre>{node.code_snapshot || "—"}</pre>
      </div>
    </aside>
  );
}
