export interface QueryEntry {
  id: string;
  question: string;
  answer: string;
  timestamp: string;
  chained_from: string | null;
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
}

export interface Branch {
  node_id: string;
  code: string;
  reasoning: string;
  score: number | null;
  rank: number | null;
}
