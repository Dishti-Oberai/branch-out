from codebase_context import gather_codebase_context
from codebase_graph import (
    build_import_graph,
    parse_snapshot_chunks,
    select_relevant_subset,
)


def make_snapshot(files: dict) -> str:
    """Builds a snapshot string in exactly gather_codebase_context's chunk
    format, without touching a real filesystem -- keeps these tests fast
    and independent of it, while a round-trip test below confirms the
    parser actually matches the real gatherer's output.
    """
    return "".join(f"--- {path} ---\n{content}\n\n" for path, content in files.items())


def test_parse_snapshot_chunks_round_trips_a_real_gathered_snapshot(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    snapshot = gather_codebase_context(tmp_path)
    chunks = parse_snapshot_chunks(snapshot)
    # the file's own trailing newline is preserved -- only the "\n\n"
    # separator gather_codebase_context adds between chunks is stripped.
    assert chunks == {"a.py": "x = 1\n", "b.py": "y = 2\n"}


def test_parse_snapshot_chunks_skips_the_truncation_marker():
    snapshot = "--- a.py ---\nx = 1\n\n--- (truncated: workspace exceeds 10 char snapshot limit) ---\n"
    chunks = parse_snapshot_chunks(snapshot)
    assert chunks == {"a.py": "x = 1"}


def test_python_absolute_import_resolves_to_the_right_file():
    files = {
        "main.py": "from utils import helper\n\nhelper()\n",
        "utils.py": "def helper():\n    pass\n",
        "unrelated.py": "z = 3\n",
    }
    graph = build_import_graph(parse_snapshot_chunks(make_snapshot(files)))
    assert "utils.py" in graph["main.py"]
    assert "main.py" in graph["utils.py"]  # undirected
    assert "unrelated.py" not in graph["main.py"]


def test_python_dotted_package_import_resolves():
    files = {
        "app/main.py": "from app.stages.foo import bar\n",
        "app/stages/foo.py": "def bar():\n    pass\n",
        "app/other.py": "z = 1\n",
    }
    graph = build_import_graph(parse_snapshot_chunks(make_snapshot(files)))
    assert "app/stages/foo.py" in graph["app/main.py"]
    assert "app/other.py" not in graph["app/main.py"]


def test_python_relative_import_resolves():
    files = {
        "pkg/a.py": "from . import b\n",
        "pkg/b.py": "y = 1\n",
    }
    graph = build_import_graph(parse_snapshot_chunks(make_snapshot(files)))
    assert "pkg/b.py" in graph["pkg/a.py"]


def test_js_relative_import_resolves_with_extension_guessing():
    files = {
        "src/index.ts": "import { helper } from './utils';\n",
        "src/utils.ts": "export const helper = () => 1;\n",
        "src/unrelated.ts": "export const x = 2;\n",
    }
    graph = build_import_graph(parse_snapshot_chunks(make_snapshot(files)))
    assert "src/utils.ts" in graph["src/index.ts"]
    assert "src/unrelated.ts" not in graph["src/index.ts"]


def test_bare_package_import_is_never_matched_as_a_local_file():
    # "requests" (a real pip package) must never resolve to some unrelated
    # local file just because a substring happens to match.
    files = {
        "main.py": "import requests\n\nrequests.get('x')\n",
        "requests_helper.py": "z = 1\n",
    }
    graph = build_import_graph(parse_snapshot_chunks(make_snapshot(files)))
    assert graph["main.py"] == set()


def test_unparseable_python_file_does_not_raise_and_others_still_link_to_it():
    files = {
        "broken.py": "def f(:\n",  # syntax error -- broken.py's OWN imports can't be extracted
        "fine.py": "from broken import x\n",
    }
    graph = build_import_graph(parse_snapshot_chunks(make_snapshot(files)))  # must not raise
    # fine.py's own import is still resolved correctly despite broken.py
    # being unparseable -- the failure is isolated to broken.py's own
    # (nonexistent) outgoing edges, not to edges pointing AT it.
    assert "broken.py" in graph["fine.py"]
    assert "fine.py" in graph["broken.py"]  # undirected


def test_select_relevant_subset_includes_target_and_direct_neighbor_only_at_depth_1():
    files = {
        "a.py": "from b import x\n",
        "b.py": "from c import y\n",
        "c.py": "z = 1\n",
        "unrelated.py": "w = 1\n",
    }
    subset = select_relevant_subset(make_snapshot(files), "a.py", depth=1)
    chunks = parse_snapshot_chunks(subset)
    assert set(chunks) == {"a.py", "b.py"}  # not c.py (2 hops away), not unrelated.py


def test_select_relevant_subset_depth_2_reaches_further():
    files = {
        "a.py": "from b import x\n",
        "b.py": "from c import y\n",
        "c.py": "z = 1\n",
    }
    subset = select_relevant_subset(make_snapshot(files), "a.py", depth=2)
    chunks = parse_snapshot_chunks(subset)
    assert set(chunks) == {"a.py", "b.py", "c.py"}


def test_select_relevant_subset_matches_an_absolute_target_file_path():
    files = {"src/util.py": "x = 1\n", "src/other.py": "y = 2\n"}
    subset = select_relevant_subset(make_snapshot(files), "/Users/me/project/src/util.py", depth=1)
    chunks = parse_snapshot_chunks(subset)
    assert "src/util.py" in chunks
    assert "src/other.py" not in chunks


def test_select_relevant_subset_returns_none_when_target_file_not_in_snapshot():
    files = {"a.py": "x = 1\n"}
    assert select_relevant_subset(make_snapshot(files), "not_in_here.py", depth=1) is None


def test_select_relevant_subset_output_is_valid_reparseable_snapshot_format():
    files = {"a.py": "from b import x\n", "b.py": "y = 1\n"}
    subset = select_relevant_subset(make_snapshot(files), "a.py", depth=1)
    # round-trips through the parser cleanly, same shape as a real gather
    reparsed = parse_snapshot_chunks(subset)
    assert reparsed["a.py"] == "from b import x\n"
    assert reparsed["b.py"] == "y = 1\n"
