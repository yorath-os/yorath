"""Pure rules of model servers and slot choice (decision 44, change model-placements)."""

import uuid

from yorath.control.cluster.placements import (
    Candidate,
    catalog_name,
    fits_server,
    server_specs,
)
from yorath.control.setup.hardware import HardwareInfo
from yorath.distribution.manifest import load_manifest
from yorath.enforcement.gateway.slots import ModelQueue, Waiter, choose
from yorath.settings import get_settings

A, B = uuid.UUID(int=1), uuid.UUID(int=2)


def candidate(server_id: uuid.UUID, slots: int = 1) -> Candidate:
    return Candidate(server_id, f"node/{server_id.int}", f"http://s{server_id.int}", slots)


def test_worker_runs_a_server_per_card_and_the_main_node_one_for_all_cards():
    assert server_specs("worker", 2, False) == [("gpu0", [0]), ("gpu1", [1])]
    assert server_specs("worker", 0, True) == [("cpu", [])]
    assert server_specs("worker", 0, False) == []
    assert server_specs("main", 2, False) == [("ollama", [0, 1])]
    assert server_specs("main", 0, False) == [("ollama", [])]


def test_catalog_name_is_local_plus_the_ollama_id():
    assert catalog_name("qwen3.5:9b") == "local/qwen3.5:9b"
    assert catalog_name("local/qwen3.5:9b") == "local/qwen3.5:9b"
    assert catalog_name("hf.co/a/b:Q4") == "local/hf.co/a/b:Q4"


def test_choose_takes_the_least_busy_server_and_the_first_on_a_tie():
    free = [candidate(A), candidate(B)]
    assert choose(free, "u1", {}, {}).server_id == A
    assert choose(free, "u1", {A: 1}, {}).server_id == B
    assert choose([candidate(A, slots=2), candidate(B)], "u1", {A: 1, B: 1}, {}).server_id == A


def test_choose_prefers_the_server_that_served_this_participant_last():
    """Its prompt cache is warm there (0.3 s against 9.8 s, measured on two GPUs)."""
    free = [candidate(A), candidate(B)]
    assert choose(free, "u1", {}, {B: "u1"}).server_id == B
    assert choose(free, "u2", {}, {B: "u1"}).server_id == A  # somebody else's server: no pull
    # the preferred server is busy and so not among the free ones: any free server is taken
    assert choose([candidate(A)], "u1", {B: 1}, {B: "u1"}).server_id == A


def waiter(participant: str, tag: str) -> Waiter:
    w = Waiter(participant, None)  # type: ignore[arg-type]  # the ring does not touch futures
    w.tag = tag  # type: ignore[attr-defined]
    return w


def order(queue: ModelQueue) -> list[str]:
    out = []
    while (w := queue.pop_next()) is not None:
        out.append(w.tag)  # type: ignore[attr-defined]
    return out


def test_queue_is_served_round_robin_between_participants():
    queue = ModelQueue()
    for participant, tag in (("a", "a1"), ("a", "a2"), ("a", "a3"), ("b", "b1"), ("c", "c1")):
        queue.push(waiter(participant, tag))
    assert len(queue) == 5
    # a burst from one participant does not hold the others: a, b, c, then the rest of a
    assert order(queue) == ["a1", "b1", "c1", "a2", "a3"]
    assert len(queue) == 0 and not queue.ring and not queue.lines


def test_retried_call_goes_first_among_its_participant_and_a_left_call_frees_its_place():
    queue = ModelQueue()
    first, second = waiter("a", "a1"), waiter("a", "a2")
    queue.push(first)
    queue.push(waiter("b", "b1"))
    queue.push(second)
    queue.push(waiter("a", "retry"), front=True)
    assert queue.remove(second) is True and queue.remove(second) is False
    assert order(queue) == ["retry", "b1", "a1"]
    # the only call of a participant leaves: the participant leaves the ring
    queue.push(first)
    lone = waiter("z", "z1")
    queue.push(lone)
    assert queue.remove(lone) is True
    assert list(queue.ring) == ["a"]


def test_fit_on_a_worker_card_counts_one_card_and_no_embeddings():
    manifest = load_manifest(get_settings().manifest_path)
    card6 = HardwareInfo(vcpu=18, ram_gb=31, disk_free_gb=0, gpu_name="A10", vram_gb=6)
    # qwen3.5:9b takes 5.7 GB of video memory, embeddings 0.6 GB
    alone = fits_server("qwen3.5:9b", manifest, card6, with_embeddings=False)
    shared = fits_server("qwen3.5:9b", manifest, card6, with_embeddings=True)
    assert (alone[0], alone[2]) == (5.7, True)
    assert (shared[0], shared[2]) == (5.7, False)
    unknown = HardwareInfo(vcpu=18, ram_gb=31, disk_free_gb=0, gpu_name="A10", vram_gb=None)
    assert fits_server("qwen3.5:9b", manifest, unknown, with_embeddings=False)[2] is None
