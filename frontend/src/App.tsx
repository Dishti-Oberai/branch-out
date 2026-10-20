import { useCallback, useEffect, useState } from "react";
import NodeInspector from "./components/NodeInspector";
import StatusBar from "./components/StatusBar";
import TreeCanvas from "./components/TreeCanvas";
import { useTree } from "./hooks/useTree";
import type { Intent } from "./types";

const DEFAULT_SESSION_PATH = "sessions/session.json";

export default function App() {
  const tree = useTree();
  const [prompt, setPrompt] = useState("");
  // 2026-10-17 amendment: the toggle no longer composes steering text or
  // decides target_file client-side at all — it just tells the backend
  // which of the three intents to use. "Auto" (default) lets the model
  // classify its own response (see orchestrator._resolve_target);
  // "Question"/"Code change" are explicit overrides that always win over
  // the model's guess, since the user's own stated intent is cheaper and
  // more reliable than anything inferred from the prompt text. A real bug
  // this fixed: a forced "this is a question" framing overrode the
  // model's own correct judgment on a real code-change request, and
  // nothing downstream (not even /patch clarifications applied later)
  // could ever recover a target_file once that chain had none.
  const [intent, setIntent] = useState<Intent>("auto");
  // Step 3.5: applying to a file happens inside the VS Code webview, where
  // window.alert()/confirm() are exactly the kind of native dialog that's
  // already been unreliable elsewhere in this app (see the Explore-
  // alternatives fix) — a transient inline message instead.
  const [applyStatus, setApplyStatus] = useState<string | null>(null);
  // Phase 6.2: a soft, dismissible warning once the tree crosses 50 nodes
  // (ROADMAP 6.2's risk-register item — large sessions were never load-
  // tested). Not a hard limit: nothing is disabled, this is purely
  // informational. `nodeCountWarningDismissed` persists the dismissal for
  // the rest of this session so it doesn't reappear on every re-render
  // once acknowledged, but resets on reload (deliberately not stored
  // anywhere durable — it's a one-time nudge, not a setting).
  const [nodeCountWarningDismissed, setNodeCountWarningDismissed] = useState(false);
  const { branch } = tree;

  // Every click activates the node: opens the inspector AND backtracks there,
  // so the real file on disk reflects whichever node is currently clicked
  // (matches CLAUDE.md's "activating a node writes straight to the real
  // file" contract) and the backend's "active" node — which /continue and
  // the VS Code "Ask about selection" command both key off — always matches
  // what's actually selected in the tree panel. Previously only orphaned
  // nodes backtracked on click; a plain click on a fresh sibling branch left
  // the backend's active node unchanged, so neither the file nor a
  // follow-up continue reflected the node the user had just clicked.
  const select = useCallback((id: string) => {
    tree.select(id);
    tree.backtrack(id);
  }, [tree]);

  // Explore alternatives: select that node so its inspector opens; if a
  // prompt is already typed in the bottom bar, branch it immediately.
  // (Previously used window.prompt() for this, which browsers silently
  // block with no error in a lot of contexts — cross-origin iframes,
  // some embedded webviews, Chrome's repeated-dialog throttling — so the
  // button just did nothing. No native dialogs anywhere now.)
  const explore = useCallback((id: string) => {
    select(id);
    if (prompt.trim()) branch(id, prompt.trim());
  }, [branch, prompt, select]);

  const from = tree.selected ?? tree.active;
  const activeFile = tree.activeEditorContext;
  const targetRange = activeFile?.start_line != null && activeFile?.end_line != null
    ? { start_line: activeFile.start_line, end_line: activeFile.end_line }
    : undefined;

  const submit = () => {
    if (!from || !prompt.trim()) return;
    // target_file/target_range are always passed as *candidates* when a
    // file is known — the backend decides whether they're actually kept,
    // based on `intent`.
    tree.continueFrom(from.id, prompt.trim(), activeFile?.file, targetRange, intent);
    setPrompt("");
  };

  // "Explore 3 approaches" isn't part of the intent toggle — it's always
  // a code-exploration action (it inherently produces {code, reasoning}
  // branches), but doesn't require a known file to be meaningful (e.g.
  // exploring approaches to a fresh problem that isn't a real file yet).
  // When a file IS known, it opportunistically gets grounded in it;
  // when not, this is unchanged from before Phase 5.6. Always "auto" --
  // the explicit toggle only applies to the single-answer "Ask" action.
  const submitBranch = () => {
    if (!from || !prompt.trim()) return;
    tree.branch(from.id, prompt.trim(), undefined, activeFile?.file, targetRange, "auto");
    setPrompt("");
  };

  // Phase 6.2: keyboard shortcuts. Cmd/Ctrl+B and Cmd/Ctrl+Z are only
  // handled OUTSIDE text inputs/textareas/selects — those are exactly the
  // elements where a user legitimately expects normal browser undo/typing
  // behavior (Cmd+Z undoing a keystroke, Cmd+B doing nothing special but
  // still shouldn't be hijacked mid-edit). Esc is always safe to handle
  // globally — closing the inspector is the same "escape a context" idiom
  // Esc already has everywhere else.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isFormField = !!target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
      if (e.key === "Escape") {
        tree.select(null);
        return;
      }
      if (isFormField || !(e.metaKey || e.ctrlKey)) return;
      if (e.key === "b" || e.key === "B") {
        e.preventDefault();
        submitBranch();
      } else if (e.key === "z" || e.key === "Z") {
        e.preventDefault();
        const current = from;
        if (current?.parent_id) tree.backtrack(current.parent_id);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, prompt]);

  return (
    <div className="app">
      {tree.nodes.length > 50 && !nodeCountWarningDismissed && (
        <div className="node-count-toast">
          <span>⚠ This tree has {tree.nodes.length} nodes — large sessions haven't been load-tested.</span>
          <button type="button" onClick={() => setNodeCountWarningDismissed(true)}>Dismiss</button>
        </div>
      )}
      <StatusBar nodes={tree.nodes} active={tree.active} />
      <div className="main">
        <div className="canvas">
          {tree.loading ? (
            <div className="canvas-empty-state">
              <p>Loading your tree…</p>
            </div>
          ) : tree.nodes.length === 0 ? (
            <div className="canvas-empty-state">
              <p className="canvas-empty-title">
                {tree.error ? "Can't reach the backend" : "Nothing here yet"}
              </p>
              <p className="muted">
                {tree.error ?? "Type a prompt below to start the tree."}
              </p>
            </div>
          ) : (
            <TreeCanvas
              nodes={tree.nodes}
              pendingScoreIds={tree.pendingScoreIds}
              onSelect={select}
              onExplore={explore}
            />
          )}
        </div>
        {tree.selected && (
          <NodeInspector
            key={tree.selected.id}
            node={tree.selected}
            nodes={tree.nodes}
            isActive={tree.selected.id === tree.active?.id}
            pending={tree.pendingScoreIds.has(tree.selected.id)}
            onMakeActive={() => tree.backtrack(tree.selected!.id)}
            onClose={() => tree.select(null)}
            onEditPrompt={(newPrompt) => tree.editPrompt(tree.selected!.id, newPrompt)}
            onQuery={(nodeId, question, chainFrom) => tree.query(nodeId, question, chainFrom)}
            activeNodeId={tree.active?.id ?? null}
            onPatch={(sourceNodeId, queryId, targetNodeId, force) =>
              tree.patch(sourceNodeId, queryId, targetNodeId, force)}
            onMerge={(sourceNodeId, currentNodeId) => tree.merge(sourceNodeId, currentNodeId)}
            onMergeSiblings={(nodeAId, nodeBId) => tree.mergeSiblings(nodeAId, nodeBId)}
            onApplyToFile={async () => {
              setApplyStatus(null);
              try {
                const { path } = await tree.applyToFile(tree.selected!.id);
                setApplyStatus(`Applied to ${path}`);
              } catch (e) {
                setApplyStatus(`Apply failed: ${e instanceof Error ? e.message : String(e)}`);
              }
            }}
          />
        )}
      </div>
      <form
        className="prompt-bar"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
      >
        <div className="prompt-bar-row prompt-bar-row-input">
          <div className="prompt-bar-field">
            <span className="prompt-bar-hint">from {from?.branch_label ?? "Root"}</span>
            <input
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Ask for one answer, or explore 3 approaches at once →"
              disabled={tree.busy}
            />
          </div>
        </div>
        <div className="prompt-bar-row prompt-bar-row-controls">
          <div className="prompt-bar-controls-left">
            <div className="intent-toggle">
              <button
                type="button"
                className={`intent-option${intent === "auto" ? " on" : ""}`}
                title="Branch-Out decides based on the model's own classification"
                onClick={() => setIntent("auto")}
              >
                Auto
              </button>
              <button
                type="button"
                className={`intent-option${intent === "question" ? " on" : ""}`}
                title="Always answer in the tree, never touch a file, regardless of what the model thinks"
                onClick={() => setIntent("question")}
              >
                Question
              </button>
              <button
                type="button"
                className={`intent-option${intent === "code_change" ? " on" : ""}`}
                disabled={!activeFile}
                title={activeFile ? "Always apply to the active file, regardless of what the model thinks" : "No active file reported by VS Code yet"}
                onClick={() => setIntent("code_change")}
              >
                Code change
              </button>
            </div>
            <span className="active-file-indicator">
              {activeFile
                ? `📄 ${activeFile.file.split("/").pop()}${targetRange ? ` lines ${targetRange.start_line}–${targetRange.end_line}` : ""}`
                : "No active file"}
            </span>
          </div>
          <div className="prompt-bar-controls-right">
            <button className="btn btn-primary" disabled={tree.busy || !prompt.trim()}>
              {tree.busy ? "Working…" : "Ask"}
            </button>
            <button
              type="button"
              className="btn btn-accent"
              disabled={tree.busy || !prompt.trim()}
              onClick={submitBranch}
            >
              Explore 3 approaches
            </button>
            {tree.busy && <button type="button" className="btn btn-ghost" onClick={tree.cancel}>Cancel</button>}
            <div className="prompt-bar-spacer" />
            <button type="button" className="btn btn-ghost" onClick={tree.save} disabled={tree.busy}>
              Save
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={tree.busy}
              onClick={() => tree.load(DEFAULT_SESSION_PATH)}
            >
              Load
            </button>
          </div>
        </div>
        {(tree.error || applyStatus) && (
          <div className="prompt-bar-row prompt-bar-row-status">
            {tree.error && <span className="error">{tree.error}</span>}
            {applyStatus && <span className="apply-status">{applyStatus}</span>}
          </div>
        )}
      </form>
    </div>
  );
}
