import json
import logging
from pathlib import Path
from typing import List

from models import TreeNode, node_from_dict, node_to_dict
from tree_state import TreeStateManager

logger = logging.getLogger(__name__)


def save_session(tree: TreeStateManager, path: str) -> str:
    """Serialise the whole tree to JSON. Called once, on session end.

    Fail-open: a write failure is logged and re-raised for the caller to
    surface to the user — it must never crash or silently lose the session.
    """
    try:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"nodes": [node_to_dict(n) for n in tree.all_nodes()]}
        target.write_text(json.dumps(payload, indent=2))
        return str(target)
    except OSError as e:
        logger.error("Failed to save session to %s: %s", path, e)
        raise


def load_session(path: str) -> TreeStateManager:
    try:
        data = json.loads(Path(path).expanduser().read_text())
        nodes: List[TreeNode] = [node_from_dict(d) for d in data["nodes"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
        raise ValueError(f"Malformed session file at {path}: {e}") from e
    return TreeStateManager.from_nodes(nodes)
