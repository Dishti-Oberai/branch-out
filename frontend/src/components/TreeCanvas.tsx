import { useMemo, type MouseEvent as ReactMouseEvent } from "react";
import ReactFlow, { Background, Controls, Handle, Position, type Edge, type Node, type NodeProps } from "reactflow";
import "reactflow/dist/style.css";
import type { TreeNode } from "../types";
import { deriveNodeTitle } from "../nodeTitle";

interface Data {
  node: TreeNode;
}

function BranchNode({ data }: NodeProps<Data>) {
  const { node } = data;
  const title = deriveNodeTitle(node);
  return (
    <div className={`bo-node ${node.status}`}>
      <span className="status-dot" />
      <Handle type="target" position={Position.Top} />
      <div className="label">{title ?? node.branch_label}</div>
      <div className="sub">
        <span className="branch-tag">{node.branch_label}</span> · {node.id}
        {node.score !== null ? <span className="score"> · {node.score}</span> : null}
      </div>
      {/* Visible only on hover (pure CSS — see .bo-node .explore rules),
          fully inside the node's own box. Its click is handled by a
          capture-phase listener on the canvas wrapper below, not by an
          onClick here — see that listener for why. */}
      <button
        type="button"
        className="explore nodrag nopan"
        data-explore-node-id={node.id}
      >
        Explore alternatives
      </button>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const nodeTypes = { branch: BranchNode };
const X_GAP = 230; // wide enough for the derived-title cards (up to 210px)
const Y_GAP = 150; // tall enough for a 2-line clamped title + tag/id row

// A branch's connector line is colored by what it leads to — active
// (indigo, solid), stale (amber, dashed) or orphaned (rose, dashed, faint) —
// so the tree's state is visible in the edges, not just the node cards.
function edgeStyle(targetStatus: TreeNode["status"]) {
  switch (targetStatus) {
    case "active":
      return { stroke: "#4f46e5", strokeWidth: 2.5 };
    case "stale":
      return { stroke: "#f59e0b", strokeWidth: 2, strokeDasharray: "4 4" };
    case "orphaned":
      return { stroke: "#e11d48", strokeWidth: 1.5, strokeDasharray: "3 5", opacity: 0.5 };
    default:
      return { stroke: "#1a1a1a", strokeWidth: 1.5 };
  }
}

// Tidy layout: leaves get consecutive columns, parents centre over children.
function layout(nodes: TreeNode[]): Map<string, { x: number; y: number }> {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const pos = new Map<string, { x: number; y: number }>();
  let col = 0;
  const walk = (id: string, depth: number): number => {
    const n = byId.get(id)!;
    const xs = n.children_ids.filter((c) => byId.has(c)).map((c) => walk(c, depth + 1));
    const x = xs.length ? (xs[0] + xs[xs.length - 1]) / 2 : col++ * X_GAP;
    pos.set(id, { x, y: depth * Y_GAP });
    return x;
  };
  const root = nodes.find((n) => n.parent_id === null);
  if (root) walk(root.id, 0);
  return pos;
}

export default function TreeCanvas({
  nodes,
  onSelect,
  onExplore,
}: {
  nodes: TreeNode[];
  onSelect: (id: string) => void;
  onExplore: (id: string) => void;
}) {
  const { rfNodes, rfEdges } = useMemo(() => {
    const pos = layout(nodes);
    const rfNodes: Node<Data>[] = nodes.map((n) => ({
      id: n.id,
      type: "branch",
      position: pos.get(n.id) ?? { x: 0, y: 0 },
      data: { node: n },
    }));
    const rfEdges: Edge[] = nodes
      .filter((n) => n.parent_id)
      .map((n) => ({
        id: `${n.parent_id}-${n.id}`,
        source: n.parent_id!,
        target: n.id,
        type: "step",
        style: edgeStyle(n.status),
      }));
    return { rfNodes, rfEdges };
  }, [nodes]);

  // Handles the Explore-alternatives click at the CAPTURE phase, on an
  // element ABOVE React Flow's entire rendered tree. Why: React Flow (via
  // d3-drag/d3-zoom internally) may attach its own node/pane listeners in
  // the capture phase. A capture listener on an ancestor always runs before
  // any listener further down the tree — including ours, if ours were on
  // the button itself — so stopPropagation() called from inside the button
  // can't reliably win that race. Intercepting here, above all of it, does:
  // this fires first no matter what React Flow does deeper in the tree, we
  // handle the action directly, then stop the event before it ever reaches
  // React Flow's own node/pane handlers.
  const handleCaptureClick = (e: ReactMouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    const btn = target.closest<HTMLElement>("[data-explore-node-id]");
    if (!btn) return;
    e.stopPropagation();
    e.preventDefault();
    const nodeId = btn.dataset.exploreNodeId;
    if (nodeId) onExplore(nodeId);
  };

  return (
    <div className="tree-canvas-wrapper" onClickCapture={handleCaptureClick}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, n) => onSelect(n.id)}
        nodesDraggable={false}
        fitView
      >
        <Background color="#e3dccb" gap={26} size={1.4} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
