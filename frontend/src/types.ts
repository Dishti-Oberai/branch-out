export interface QueryEntry {
  id: string;
  question: string;
  answer: string;
  timestamp: string;
  chained_from: string | null;
}

export interface TargetRange {
  start_line: number;
  end_line: number;
}

export interface TreeNode {
  id: string;
  parent_id: string | null;
  children_ids: string[];
  code_snapshot: string;
  context_summary: string;
  user_preferences: string[];
  score: number | null;
  score_source: "tests" | "llm_judge" | null;
  status: "active" | "stale" | "orphaned" | "normal";
  query_history: QueryEntry[];
  branch_label: string;
  created_at: string;
  // Phase 3 (VS Code extension): which real file/lines this node's code
  // applies to. Always null for nodes created via this web app; only the
  // extension ever sets them.
  target_file: string | null;
  target_range: TargetRange | null;
  // Phase 1 amendment (2026-09-27): a static codebase snapshot, inherited
  // parent->child, populated once a workspace is declared (extension-only
  // — the standalone web app has no filesystem access to declare one).
  codebase_context: string | null;
}

// 2026-10-17 amendment: "auto" (default) lets the model classify its own
// response; "question"/"code_change" are explicit user overrides that
// always win over the model's guess.
export type Intent = "auto" | "question" | "code_change";

export interface PatchOutcome {
  conflict: boolean;
  node: TreeNode | null;
  diff_preview: string | null;
}

export interface Branch {
  node_id: string;
  code: string;
  reasoning: string;
  score: number | null;
  rank: number | null;
  score_source: "tests" | "llm_judge" | null;
}
