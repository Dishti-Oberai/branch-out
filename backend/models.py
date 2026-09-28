from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

DEFAULT_BEAM_WIDTH = 3


@dataclass
class QueryEntry:
    id: str
    question: str
    answer: str
    timestamp: datetime
    chained_from: Optional[str] = None  # ID of prior QueryEntry this builds on


@dataclass
class TreeNode:
    id: str
    parent_id: Optional[str]
    children_ids: List[str]
    code_snapshot: str
    context_summary: str
    user_preferences: List[str]  # Explicit decisions ("use Python 3.11")
    score: Optional[float]
    score_source: Optional[Literal["tests", "llm_judge"]]
    status: Literal["active", "stale", "orphaned", "normal"]
    query_history: List[QueryEntry]
    branch_label: str  # "Branch A", "Branch B" etc.
    created_at: datetime = field(default_factory=datetime.utcnow)
    # Phase 3 addition (VS Code extension): which real file/lines this
    # node's code applies to. Flagged schema addition, same pattern as
    # Branch.score_source in Phase 2. Nodes created via the web UI leave
    # these None; nodes created via the extension always set them. Both
    # have defaults so old session files (saved before this field existed)
    # still load via node_from_dict without any special-case code.
    target_file: Optional[str] = None
    target_range: Optional[Dict[str, int]] = None  # {"start_line": int, "end_line": int}
    # Phase 1 amendment (2026-09-27): a static codebase snapshot, distinct
    # from context_summary on purpose — that field is an accumulating
    # per-turn decision log; this is reference material that doesn't grow
    # per-turn. Inherited parent->child by Orchestrator (same pattern as
    # user_preferences), populated by Phase 3's POST /workspace. None for
    # every node until a workspace is declared. See ROADMAP.md Phase 1's
    # "Amendment planned 2026-09-27" note for the full rationale.
    codebase_context: Optional[str] = None


@dataclass
class Branch:
    node_id: str
    code: str
    reasoning: str
    score: Optional[float] = None
    rank: Optional[int] = None  # Set by score_normaliser
    # Phase 2 addition (roadmap Step 2.3's own evaluator.py snippet assigns
    # branch.score_source directly): mirrors TreeNode.score_source so the
    # evaluator can tell main.py which value to persist onto the TreeNode.
    score_source: Optional[Literal["tests", "llm_judge"]] = None


def _iso(d: Dict[str, Any], key: str) -> None:
    if isinstance(d.get(key), datetime):
        d[key] = d[key].isoformat()


def node_to_dict(node: TreeNode) -> Dict[str, Any]:
    d = asdict(node)
    _iso(d, "created_at")
    for q in d["query_history"]:
        _iso(q, "timestamp")
    return d


def node_from_dict(d: Dict[str, Any]) -> TreeNode:
    queries = [
        QueryEntry(**{**q, "timestamp": datetime.fromisoformat(q["timestamp"])})
        for q in d.get("query_history", [])
    ]
    return TreeNode(
        **{
            **d,
            "query_history": queries,
            "created_at": datetime.fromisoformat(d["created_at"]),
        }
    )


def branch_to_dict(branch: Branch) -> Dict[str, Any]:
    return asdict(branch)


def query_entry_to_dict(entry: QueryEntry) -> Dict[str, Any]:
    """Phase 4: serializes one QueryEntry for the /query response — the
    same `_iso()` timestamp handling `node_to_dict` already applies inline
    to each entry inside a node's own `query_history`, reused here for a
    standalone entry (the /query endpoint returns just the new entry, not
    the whole node — the active node is deliberately untouched by a query)."""
    d = asdict(entry)
    _iso(d, "timestamp")
    return d
