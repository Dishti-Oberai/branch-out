import { useCallback, useState } from "react";
import NodeInspector from "./components/NodeInspector";
import StatusBar from "./components/StatusBar";
import TreeCanvas from "./components/TreeCanvas";
import { useTree } from "./hooks/useTree";

const DEFAULT_SESSION_PATH = "sessions/session.json";

export default function App() {
  const tree = useTree();
  const [prompt, setPrompt] = useState("");
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

  const submit = () => {
    if (!from || !prompt.trim()) return;
    tree.continueFrom(from.id, prompt.trim());
    setPrompt("");
  };

  const submitBranch = () => {
    if (!from || !prompt.trim()) return;
    tree.branch(from.id, prompt.trim());
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
