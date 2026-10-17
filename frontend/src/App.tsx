import { useCallback, useState } from "react";
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
  const { branch } = tree;

  // Orphaned nodes revive on click (backtrack there); everything else just opens the inspector.
  const select = useCallback((id: string) => {
    tree.select(id);
    const node = tree.nodes.find((n) => n.id === id);
    if (node?.status === "orphaned") tree.backtrack(id);
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

  return (
    <div className="app">
      <StatusBar nodes={tree.nodes} active={tree.active} />
      <div className="main">
        <div className="canvas">
          <TreeCanvas
            nodes={tree.nodes}
            pendingScoreIds={tree.pendingScoreIds}
            onSelect={select}
            onExplore={explore}
          />
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
        <div className="prompt-bar-field">
          <span className="prompt-bar-hint">from {from?.branch_label ?? "Root"}</span>
          <input
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Ask for one answer, or explore 3 approaches at once →"
            disabled={tree.busy}
          />
        </div>
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
        {tree.error && <span className="error">{tree.error}</span>}
        {applyStatus && <span className="apply-status">{applyStatus}</span>}
      </form>
    </div>
  );
}
