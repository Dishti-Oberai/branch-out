import type { TreeNode } from "../types";

function pathTo(nodes: TreeNode[], id: string): TreeNode[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const path: TreeNode[] = [];
  for (let n = byId.get(id); n; n = n.parent_id ? byId.get(n.parent_id) : undefined) path.unshift(n);
  return path;
}

export default function StatusBar({ nodes, active }: { nodes: TreeNode[]; active: TreeNode | null }) {
  const path = active ? pathTo(nodes, active.id) : [];
  return (
    <div className="status-bar">
      <span className="brand">Branch-Out</span>
      <span className="chip">
        <span className="dot" />
        Active <b>{active?.branch_label ?? "—"}</b>
      </span>
      <span className="chip chip-path">
        Path&nbsp;<b>{path.map((n) => n.branch_label).join(" › ") || "—"}</b>
      </span>
      <span className="chip">
        Depth <b>{Math.max(path.length - 1, 0)}</b>
      </span>
      <span className="chip">
        Nodes <b>{nodes.length}</b>
      </span>
    </div>
  );
}
