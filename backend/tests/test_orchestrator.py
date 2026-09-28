import asyncio

import pytest

from orchestrator import LLMGenerationFailed, Orchestrator
from tests.conftest import FakeLLM


def test_branch_adds_n_nodes_with_correct_parent():
    o = Orchestrator(FakeLLM())
    branches = asyncio.run(o.branch("root", "task", beam_width=3))
    assert len(branches) == 3
    for b in branches:
        node = o.tree.get_node(b.node_id)
        assert node.parent_id == "root"


def test_branch_sets_target_file_and_range_on_every_created_node():
    o = Orchestrator(FakeLLM())
    branches = asyncio.run(o.branch(
        "root", "task", beam_width=3,
        target_file="/workspace/src/foo.py",
        target_range={"start_line": 1, "end_line": 5},
    ))
    for b in branches:
        node = o.tree.get_node(b.node_id)
        assert node.target_file == "/workspace/src/foo.py"
        assert node.target_range == {"start_line": 1, "end_line": 5}


def test_branch_without_target_file_leaves_it_none():
    o = Orchestrator(FakeLLM())
    branches = asyncio.run(o.branch("root", "task", beam_width=1))
    node = o.tree.get_node(branches[0].node_id)
    assert node.target_file is None
    assert node.target_range is None


def test_continue_from_sets_target_file_and_range():
    o = Orchestrator(FakeLLM())
    node = asyncio.run(o.continue_from(
        "root", "task", target_file="/workspace/src/bar.py", target_range={"start_line": 10, "end_line": 20}
    ))
    assert node.target_file == "/workspace/src/bar.py"
    assert node.target_range == {"start_line": 10, "end_line": 20}
    assert o.tree.get_node(node.id).target_file == "/workspace/src/bar.py"


def test_branch_inherits_parent_codebase_context():
    o = Orchestrator(FakeLLM())
    o.tree.set_codebase_context("root", "--- main.py ---\nprint('hi')")
    branches = asyncio.run(o.branch("root", "task", beam_width=2))
    for b in branches:
        assert o.tree.get_node(b.node_id).codebase_context == "--- main.py ---\nprint('hi')"


def test_continue_from_inherits_parent_codebase_context():
    o = Orchestrator(FakeLLM())
    o.tree.set_codebase_context("root", "--- main.py ---\nprint('hi')")
    node = asyncio.run(o.continue_from("root", "task"))
    assert node.codebase_context == "--- main.py ---\nprint('hi')"
    # and it keeps propagating to grandchildren
    grandchild = asyncio.run(o.continue_from(node.id, "task 2"))
    assert grandchild.codebase_context == "--- main.py ---\nprint('hi')"


def test_no_codebase_context_set_leaves_it_none_on_children():
    o = Orchestrator(FakeLLM())
    node = asyncio.run(o.continue_from("root", "task"))
    assert node.codebase_context is None


def test_include_codebase_true_sends_it_to_the_llm():
    # Caching fix (2026-09-27): codebase_context now goes into `system`,
    # not the per-call message -- that's the only field Tokenetics' cache
    # breakpoint optimizer can ever anchor a cache_control marker on.
    llm = FakeLLM()
    o = Orchestrator(llm)
    o.tree.set_codebase_context("root", "--- main.py ---\nSECRET_MARKER")
    asyncio.run(o.continue_from("root", "task", include_codebase=True))
    assert llm.received[0]["system"] == "--- main.py ---\nSECRET_MARKER"
    assert "SECRET_MARKER" not in llm.received[0]["messages"][0]["content"]


def test_include_codebase_false_excludes_it_from_the_prompt_but_node_still_inherits():
    llm = FakeLLM()
    o = Orchestrator(llm)
    o.tree.set_codebase_context("root", "--- main.py ---\nSECRET_MARKER")
    node = asyncio.run(o.continue_from("root", "task", include_codebase=False))
    assert llm.received[0]["system"] is None
    assert "SECRET_MARKER" not in llm.received[0]["messages"][0]["content"]
    # the node itself still has it, for a later turn that opts back in
    assert node.codebase_context == "--- main.py ---\nSECRET_MARKER"


def _multi_file_snapshot() -> str:
    return (
        "--- main.py ---\nfrom utils import helper\n\n\n"
        "--- utils.py ---\ndef helper():\n    pass\n\n\n"
        "--- unrelated.py ---\nSECRET_UNRELATED_MARKER\n\n\n"
    )


def test_continue_from_with_target_file_sends_only_the_relevant_graph_subset():
    # Graph-based context (2026-09-27): with a target_file set, the prompt
    # gets target_file + its import neighbors, not the whole codebase --
    # unrelated.py (no import relationship to main.py) must not appear.
    llm = FakeLLM()
    o = Orchestrator(llm)
    o.tree.set_codebase_context("root", _multi_file_snapshot())
    asyncio.run(o.continue_from("root", "task", target_file="main.py", include_codebase=True))
    sent_system = llm.received[0]["system"]
    assert "utils.py" in sent_system
    assert "def helper" in sent_system
    assert "SECRET_UNRELATED_MARKER" not in sent_system


def test_continue_from_without_target_file_still_sends_the_whole_snapshot():
    # No target_file to anchor a subgraph on -> falls back to the full
    # snapshot, same as before graph-scoping existed.
    llm = FakeLLM()
    o = Orchestrator(llm)
    o.tree.set_codebase_context("root", _multi_file_snapshot())
    asyncio.run(o.continue_from("root", "task", include_codebase=True))
    assert "SECRET_UNRELATED_MARKER" in llm.received[0]["system"]


def test_continue_from_with_unknown_target_file_falls_back_to_full_snapshot():
    # target_file not present in the snapshot at all (e.g. outside the
    # workspace) -- fail open to the full snapshot rather than sending
    # nothing.
    llm = FakeLLM()
    o = Orchestrator(llm)
    o.tree.set_codebase_context("root", _multi_file_snapshot())
    asyncio.run(o.continue_from("root", "task", target_file="nope.py", include_codebase=True))
    assert "SECRET_UNRELATED_MARKER" in llm.received[0]["system"]


def test_branch_with_target_file_scopes_every_parallel_call():
    llm = FakeLLM()
    o = Orchestrator(llm)
    o.tree.set_codebase_context("root", _multi_file_snapshot())
    asyncio.run(o.branch("root", "task", beam_width=3, target_file="main.py", include_codebase=True))
    for call in llm.received:
        assert "SECRET_UNRELATED_MARKER" not in call["system"]
        assert "utils.py" in call["system"]


def test_node_still_inherits_the_full_unscoped_snapshot_even_when_the_prompt_was_scoped():
    # The graph-scoped subset only affects what one call's prompt sees --
    # the node's own codebase_context (what children inherit) stays the
    # full snapshot, same append-only-metadata pattern as include_codebase.
    llm = FakeLLM()
    o = Orchestrator(llm)
    o.tree.set_codebase_context("root", _multi_file_snapshot())
    node = asyncio.run(o.continue_from("root", "task", target_file="main.py", include_codebase=True))
    assert "SECRET_UNRELATED_MARKER" in node.codebase_context


def test_branch_include_codebase_false_excludes_it_from_all_parallel_calls():
    llm = FakeLLM()
    o = Orchestrator(llm)
    o.tree.set_codebase_context("root", "--- main.py ---\nSECRET_MARKER")
    asyncio.run(o.branch("root", "task", beam_width=3, include_codebase=False))
    for call in llm.received:
        assert call["system"] is None
        assert "SECRET_MARKER" not in call["messages"][0]["content"]


def test_on_branch_ready_fires_per_branch_with_node_id_already_set():
    o = Orchestrator(FakeLLM())
    ready = []

    async def on_ready(b):
        ready.append(b.node_id)
        # the node must already exist in the tree by the time this fires
        assert o.tree.get_node(b.node_id).parent_id == "root"

    branches = asyncio.run(o.branch("root", "task", beam_width=3, on_branch_ready=on_ready))
    assert sorted(ready) == sorted(b.node_id for b in branches)


def test_on_branch_ready_not_called_for_a_failed_branch():
    o = Orchestrator(FakeLLM(fail_on={1}))
    ready = []

    async def on_ready(b):
        ready.append(b.node_id)

    branches = asyncio.run(o.branch("root", "task", beam_width=3, on_branch_ready=on_ready))
    assert len(branches) == 2
    assert len(ready) == 2


def test_backtrack_activates_target_and_deletes_nothing():
    o = Orchestrator(FakeLLM())
    branches = asyncio.run(o.branch("root", "task", beam_width=3))
    before = o.tree.node_count()
    target = branches[1].node_id
    node = asyncio.run(o.backtrack(target))
    assert node.status == "active"
    assert o.tree.node_count() == before


def test_continue_from_adds_one_child():
    o = Orchestrator(FakeLLM())
    node = asyncio.run(o.continue_from("root", "task"))
    assert node.parent_id == "root"
    assert len(o.tree.get_children("root")) == 1


def test_continue_from_activates_the_new_node():
    # Regression: continue_from used to leave the parent active, so asking
    # a second question from the same (still-active) node just fanned out
    # another sibling instead of continuing the thread — reported as
    # "prompting again opens a new branch instead of replying to it".
    o = Orchestrator(FakeLLM())
    node = asyncio.run(o.continue_from("root", "hi"))
    assert node.status == "active"
    assert o.tree.get_node("root").status == "normal"
    assert o.tree.active_id == node.id


def test_asking_again_continues_the_thread_instead_of_fanning_out_from_root():
    o = Orchestrator(FakeLLM())
    first = asyncio.run(o.continue_from("root", "hi"))
    # The frontend always asks from `tree.active`, which is now `first` —
    # simulate that rather than hardcoding "root" again.
    second = asyncio.run(o.continue_from(first.id, "hi again"))

    assert second.parent_id == first.id
    assert second.status == "active"
    assert o.tree.get_node(first.id).status == "normal"
    assert len(o.tree.get_children("root")) == 1  # not fanned out under root
    assert len(o.tree.get_children(first.id)) == 1


def test_edit_prompt_new_sibling_is_not_activated():
    # Unlike continue_from, edit_prompt's new sibling must stay inactive —
    # the user decides whether to adopt it.
    o = Orchestrator(FakeLLM())
    node = asyncio.run(o.continue_from("root", "original task"))
    new_node, _ = asyncio.run(o.edit_prompt(node.id, "revised task"))
    assert new_node.status != "active"
    assert o.tree.active_id == node.id


def test_orphaned_branch_siblings_are_dimmed_not_deleted_on_backtrack():
    o = Orchestrator(FakeLLM())
    branches = asyncio.run(o.branch("root", "task", beam_width=3))
    picked, other_a, other_b = branches[0].node_id, branches[1].node_id, branches[2].node_id
    asyncio.run(o.backtrack(picked))
    asyncio.run(o.branch(picked, "next step", beam_width=1))

    assert o.tree.get_node(other_a).status == "orphaned"
    assert o.tree.get_node(other_b).status == "orphaned"
    assert o.tree.node_count() == 5  # root + 3 branches + 1 continuation, none deleted


def test_revisiting_an_orphaned_node_revives_it():
    o = Orchestrator(FakeLLM())
    branches = asyncio.run(o.branch("root", "task", beam_width=2))
    a, b = branches[0].node_id, branches[1].node_id
    asyncio.run(o.backtrack(a))
    assert o.tree.get_node(b).status == "orphaned"
    asyncio.run(o.backtrack(b))
    assert o.tree.get_node(b).status == "active"
    assert o.tree.get_node(a).status == "orphaned"


def test_edit_prompt_creates_sibling_and_leaves_original_untouched():
    o = Orchestrator(FakeLLM())
    node = asyncio.run(o.continue_from("root", "original task"))
    original_snapshot = o.tree.get_node(node.id).code_snapshot

    new_node, stale_ids = asyncio.run(o.edit_prompt(node.id, "revised task"))

    assert new_node.parent_id == "root"
    assert new_node.id != node.id
    unchanged = o.tree.get_node(node.id)
    assert unchanged.code_snapshot == original_snapshot  # never rewritten
    assert unchanged.status == "stale"
    assert stale_ids == [node.id]


def test_edit_prompt_marks_whole_descendant_subtree_stale():
    o = Orchestrator(FakeLLM())
    a = asyncio.run(o.continue_from("root", "step 1"))
    b = asyncio.run(o.continue_from(a.id, "step 2"))
    c = asyncio.run(o.continue_from(b.id, "step 3"))

    _, stale_ids = asyncio.run(o.edit_prompt(a.id, "revised step 1"))

    assert set(stale_ids) == {a.id, b.id, c.id}
    for nid in (a.id, b.id, c.id):
        assert o.tree.get_node(nid).status == "stale"


def test_edit_prompt_on_root_raises():
    o = Orchestrator(FakeLLM())
    with pytest.raises(ValueError):
        asyncio.run(o.edit_prompt("root", "new task"))


def test_continue_from_raises_distinct_error_on_total_llm_failure():
    # continue_from needs exactly one branch; if that single call fails,
    # generate_branches fails open to [] and continue_from must not let
    # that surface as a bare ValueError (which /edit-prompt's 400 handler
    # would misreport as a bad request rather than an upstream failure).
    o = Orchestrator(FakeLLM(fail_on={0}))
    with pytest.raises(LLMGenerationFailed):
        asyncio.run(o.continue_from("root", "task"))


def test_edit_prompt_raises_llm_generation_failed_not_value_error():
    o = Orchestrator(FakeLLM())
    node = asyncio.run(o.continue_from("root", "original task"))

    o2 = Orchestrator(FakeLLM(fail_on={1}))  # call 0 (creating node2) succeeds, call 1 (the edit) fails
    node2 = asyncio.run(o2.continue_from("root", "original task"))
    with pytest.raises(LLMGenerationFailed):
        asyncio.run(o2.edit_prompt(node2.id, "revised task"))
    # the failed edit must not have marked anything stale
    assert o2.tree.get_node(node2.id).status != "stale"


def test_concurrent_branch_calls_do_not_collide_on_labels_or_children():
    # Step 1.11: the lock must serialize two branch() calls against the same
    # parent so their children_ids offsets can't both be read stale.
    o = Orchestrator(FakeLLM(delay=0.01))

    async def run():
        results = await asyncio.gather(
            o.branch("root", "task A", beam_width=2),
            o.branch("root", "task B", beam_width=2),
        )
        return results

    first, second = asyncio.run(run())
    labels = [b.reasoning for b in first + second]  # not used, just touching results
    assert len(labels) == 4
    children = o.tree.get_children("root")
    assert len(children) == 4
    assert len({c.branch_label for c in children}) == 4  # no label collisions
    assert len({c.id for c in children}) == 4  # no id collisions


def test_backtrack_cannot_interleave_mid_branch_generation():
    # A slow branch() call and a backtrack() must not interleave: without
    # the lock, backtrack() could run (and orphan things) while branch()
    # is still awaiting its LLM call and hasn't added its new nodes yet.
    o = Orchestrator(FakeLLM(delay=0.05))

    async def run():
        branch_task = asyncio.ensure_future(o.branch("root", "task", beam_width=1))
        await asyncio.sleep(0.01)  # branch() is now awaiting the LLM call, lock held
        backtrack_task = asyncio.ensure_future(o.backtrack("root"))
        await asyncio.gather(branch_task, backtrack_task)

    asyncio.run(run())
    # Exactly one child was added; backtrack() didn't run concurrently and
    # orphan a half-built tree.
    assert len(o.tree.get_children("root")) == 1


def test_branch_generation_can_be_cancelled_without_partial_nodes():
    o = Orchestrator(FakeLLM(delay=0.2))

    async def run():
        task = asyncio.ensure_future(o.branch("root", "task", beam_width=3))
        await asyncio.sleep(0.01)  # let it start, still awaiting the LLM calls
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert o.tree.node_count() == 1  # only root — nothing partially added


def test_lock_is_released_after_a_cancelled_call():
    o = Orchestrator(FakeLLM(delay=0.2))

    async def run():
        task = asyncio.ensure_future(o.branch("root", "task", beam_width=1))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # the lock must be free now — a subsequent call must not hang
        branches = await asyncio.wait_for(o.branch("root", "task 2", beam_width=1), timeout=1)
        return branches

    branches = asyncio.run(run())
    assert len(branches) == 1


# ---- Phase 4: cross-node querying (orchestrator.query) --------------------

def test_query_returns_a_query_entry_with_the_answer():
    llm = FakeLLM(response_fn=lambda i: "the answer")
    o = Orchestrator(llm)
    entry = asyncio.run(o.query("root", "what is this?"))
    assert entry.question == "what is this?"
    assert entry.answer == "the answer"
    assert entry.chained_from is None


def test_query_appends_to_the_queried_nodes_query_history():
    llm = FakeLLM(response_fn=lambda i: "the answer")
    o = Orchestrator(llm)
    entry = asyncio.run(o.query("root", "what is this?"))
    assert o.tree.get_node("root").query_history == [entry]


def test_query_never_touches_the_active_node():
    llm = FakeLLM()
    o = Orchestrator(llm)
    branches = asyncio.run(o.branch("root", "task", beam_width=1))
    active_before = o.tree.active_id
    asyncio.run(o.query("root", "what happened here?"))  # queries root, not the active node
    assert o.tree.active_id == active_before
    assert o.tree.get_node(active_before).status == "active"


def test_query_unknown_node_raises_key_error():
    o = Orchestrator(FakeLLM())
    with pytest.raises(KeyError):
        asyncio.run(o.query("nope", "what is this?"))


def test_query_llm_failure_leaves_history_unchanged():
    o = Orchestrator(FakeLLM(fail_on={0}))
    with pytest.raises(LLMGenerationFailed):
        asyncio.run(o.query("root", "what is this?"))
    assert o.tree.get_node("root").query_history == []


def test_chained_query_includes_the_prior_qa_in_the_prompt():
    llm = FakeLLM(response_fn=lambda i: f"answer {i}")
    o = Orchestrator(llm)
    first = asyncio.run(o.query("root", "first question"))
    asyncio.run(o.query("root", "follow-up question", chain_from=first.id))
    second_call_content = llm.received[1]["messages"][0]["content"]
    assert "first question" in second_call_content
    assert "answer 0" in second_call_content  # the prior answer, verbatim


def test_chained_query_unknown_chain_from_raises_key_error():
    o = Orchestrator(FakeLLM())
    with pytest.raises(KeyError):
        asyncio.run(o.query("root", "follow-up", chain_from="not-a-real-entry-id"))


def test_two_queries_on_the_same_node_both_recorded_in_order():
    llm = FakeLLM(response_fn=lambda i: f"answer {i}")
    o = Orchestrator(llm)
    e1 = asyncio.run(o.query("root", "q1"))
    e2 = asyncio.run(o.query("root", "q2"))
    ids = [q.id for q in o.tree.get_node("root").query_history]
    assert ids == [e1.id, e2.id]
