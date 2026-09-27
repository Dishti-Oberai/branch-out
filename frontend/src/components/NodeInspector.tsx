import { useState } from "react";
import type { TreeNode } from "../types";
import { deriveNodeTitle } from "../nodeTitle";
import { isVsCodeWebview } from "../vscodeBridge";

// Phase 1: State tab only. Queries tab arrives in Phase 4.
export default function NodeInspector({
  node,
  nodes,
  isActive,
  pending,
  onMakeActive,
  onClose,
  onEditPrompt,
  onApplyToFile,
}: {
  node: TreeNode;
  nodes: TreeNode[];
  isActive: boolean;
  pending: boolean;
  onMakeActive: () => void;
  onClose: () => void;
  onEditPrompt: (newPrompt: string) => void;
  onApplyToFile: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
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
    </aside>
  );
}
