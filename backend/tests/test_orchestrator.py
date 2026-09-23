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
