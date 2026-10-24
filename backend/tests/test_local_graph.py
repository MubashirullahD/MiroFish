"""Local graph contracts: real SQLite and services, deterministic model replies."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
import time
from types import SimpleNamespace

import pytest
from zep_cloud import BatchAddItem, NotFoundError

from app.config import Config
from app.services.graph_builder import GraphBuilderService
from app.services.zep_entity_reader import ZepEntityReader
from app.services.zep_graph_memory_updater import ZepGraphMemoryUpdater
from app.services.zep_tools import ZepToolsService
from app.utils import zep
from app.utils.local_graph import LocalExtractionError, LocalGraphClient
from app.utils.zep_paging import fetch_all_edges, fetch_all_nodes


class ModelReplies:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return deepcopy(reply)


def extraction(*, fact="Alice uses Atlas for onboarding", summary="Alice is a trial user."):
    return {
        "entities": [
            {"name": "Alice", "entity_type": "Person", "summary": summary, "attributes": {"role": "trial user"}},
            {"name": "Atlas", "entity_type": "Product", "summary": "An onboarding product.", "attributes": {}},
        ],
        "relationships": [{"source": "Alice", "target": "Atlas", "name": "USES", "fact": fact, "attributes": {}}],
    }


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("Local extraction did not finish before the test deadline")


def wait_batch(client, batch_id):
    return wait_for(lambda: (summary if (summary := client.batch.get(batch_id=batch_id)).status in {"succeeded", "partial", "failed"} else None))


@pytest.fixture
def local_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_BACKEND", "local")
    monkeypatch.setattr(Config, "LOCAL_GRAPH_PATH", str(tmp_path / "memory.sqlite3"))
    monkeypatch.setattr(Config, "ZEP_API_KEY", None)
    client = zep.get_zep_client()
    replies = ModelReplies([extraction()])
    client._llm_factory = lambda: replies
    yield client, replies
    if client._worker:
        client._worker.join(timeout=5)
    zep.clear_zep_client_cache()


def test_local_routing_needs_no_zep_key_or_cloud_connection(local_memory, monkeypatch):
    client, _ = local_memory
    monkeypatch.setenv("ZEP_API_URL", "https://example.invalid")
    monkeypatch.setattr(zep, "Zep", lambda **_: pytest.fail("Local memory must never connect to Zep"))
    assert zep.get_zep_client() is client
    assert GraphBuilderService().client is client
    assert ZepEntityReader().client is client
    assert ZepToolsService().client is client
    assert ZepGraphMemoryUpdater("graph-id").client is client


def test_graph_builder_entities_and_report_tools_share_real_local_data(local_memory):
    client, model = local_memory
    builder = GraphBuilderService()
    graph_id = builder.create_graph("Local test")
    builder.set_ontology(graph_id, {
        "entity_types": [{"name": "Person", "description": "A user"}, {"name": "Product", "description": "An application"}],
        "edge_types": [{"name": "USES", "source_targets": [{"source": "Person", "target": "Product"}]}],
    })
    submission = builder.add_text_batches(graph_id, ["Alice uses Atlas for onboarding."])
    assert wait_batch(client, submission.batch_id).status == "succeeded"
    episodes = builder._wait_for_batch(submission, timeout=2)
    builder._wait_for_episodes(episodes, timeout=2)
    graph_data = builder.get_graph_data(graph_id)
    assert len(graph_data["nodes"]) == 2
    assert len(graph_data["edges"]) == 1
    reader = ZepEntityReader()
    personas = reader.filter_defined_entities(graph_id, defined_entity_types=["Person"])
    assert personas.filtered_count == 1
    assert personas.entities[0].name == "Alice"
    assert personas.entities[0].related_nodes[0]["name"] == "Atlas"
    report_context = ZepToolsService().search_graph(graph_id, "Alice onboarding")
    assert report_context.facts == ["Alice uses Atlas for onboarding"]
    prompt = json.loads(model.calls[0]["messages"][1]["content"])
    assert set(prompt["ontology"]["entities"]) == {"Person", "Product"}
    assert prompt["source_text"] == "Alice uses Atlas for onboarding."


def test_sources_persist_and_repeated_facts_merge_with_provenance(local_memory):
    client, model = local_memory
    client.graph.create(graph_id="g")
    first = client.graph.add(graph_id="g", data="Alice uses Atlas.", metadata={"origin": "document"})
    wait_for(lambda: client.graph.episode.get(uuid_=first.uuid_).processed)
    model.replies.append(extraction())
    second = client.graph.add(graph_id="g", data="Alice uses Atlas.", source_description="simulation round 1", metadata={"round": 1})
    wait_for(lambda: client.graph.episode.get(uuid_=second.uuid_).processed)
    reopened = LocalGraphClient(client.path, llm_factory=lambda: pytest.fail("Reads must not invoke a model"))
    nodes, edges = fetch_all_nodes(reopened, "g"), fetch_all_edges(reopened, "g")
    assert len(nodes) == 2 and len(edges) == 1
    assert set(edges[0].attributes["local_source_episode_uuids"]) == {first.uuid_, second.uuid_}
    assert reopened.graph.episode.get(uuid_=second.uuid_).data == "Alice uses Atlas."
    assert reopened.graph.episode.get(uuid_=second.uuid_).metadata == {"round": 1}
    assert reopened.graph.episode.get(uuid_=second.uuid_).source_description == "simulation round 1"


@pytest.mark.parametrize("product_type", ["Entity", "Product"])
def test_unmodeled_products_do_not_become_people_or_bypass_ontology(local_memory, product_type):
    client, model = local_memory
    reply = extraction()
    reply["entities"][1]["entity_type"] = product_type
    model.replies[:] = [reply]
    builder = GraphBuilderService()
    graph_id = builder.create_graph("People-only persona ontology")
    builder.set_ontology(graph_id, {
        "entity_types": [{"name": "Person", "description": "A participant"}],
        "edge_types": [],
    })
    submission = builder.add_text_batches(graph_id, ["Alice uses the Atlas application."])
    result = wait_batch(client, submission.batch_id)
    if product_type == "Product":
        assert result.status == "failed"
        assert fetch_all_nodes(client, graph_id) == []
        assert fetch_all_edges(client, graph_id) == []
    else:
        assert result.status == "succeeded"
        nodes = fetch_all_nodes(client, graph_id)
        atlas = next(node for node in nodes if node.name == "Atlas")
        assert "Entity" in atlas.labels and "Person" not in atlas.labels
        personas = ZepEntityReader().filter_defined_entities(
            graph_id, defined_entity_types=["Person"]
        )
        assert [entity.name for entity in personas.entities] == ["Alice"]


def test_bad_extraction_fails_batch_without_partially_writing_nodes(local_memory):
    client, model = local_memory
    bad = extraction()
    bad["relationships"][0]["target"] = "Invented missing entity"
    model.replies[:] = [bad]
    builder = GraphBuilderService()
    graph_id = builder.create_graph("Broken extraction")
    submission = builder.add_text_batches(graph_id, ["Alice uses Atlas."])
    assert wait_batch(client, submission.batch_id).status == "failed"
    assert fetch_all_nodes(client, graph_id) == []
    assert fetch_all_edges(client, graph_id) == []
    with pytest.raises(RuntimeError, match="failed_items=1"):
        builder._wait_for_batch(submission, timeout=2)
    episode_id = client.batch.list_items(batch_id=submission.batch_id).items[0].episode_uuid
    with pytest.raises(LocalExtractionError, match="endpoint"):
        client.graph.episode.get(uuid_=episode_id)
    with client._db() as db:
        row = db.execute("SELECT payload,status FROM episodes WHERE uuid=?", (episode_id,)).fetchone()
        assert row["status"] == "failed"
        assert json.loads(row["payload"])["data"] == "Alice uses Atlas."


def test_model_failure_remains_visible_without_persisting_provider_secrets(local_memory):
    client, model = local_memory
    model.replies[:] = [ConnectionError("Authorization: secret-example-token")]
    client.graph.create(graph_id="g")
    episode = client.graph.add(graph_id="g", data="Alice uses Atlas.")
    def failed():
        with client._db() as db:
            return db.execute("SELECT status FROM episodes WHERE uuid=?", (episode.uuid_,)).fetchone()[0] == "failed"
    wait_for(failed)
    with pytest.raises(LocalExtractionError, match="ConnectionError") as captured:
        client.graph.episode.get(uuid_=episode.uuid_)
    assert "secret-example-token" not in str(captured.value)
    assert fetch_all_nodes(client, "g") == []


def test_partial_batch_is_not_misreported_as_success(local_memory):
    client, model = local_memory
    model.replies.append({"invalid": "response"})
    builder = GraphBuilderService()
    graph_id = builder.create_graph("Partial")
    submission = builder.add_text_batches(graph_id, ["Alice uses Atlas.", "Unusable reply."])
    summary = wait_batch(client, submission.batch_id)
    assert summary.status == "partial"
    assert summary.progress.succeeded_items == 1
    assert summary.progress.failed_items == 1
    assert summary.progress.percent_complete == 100
    with pytest.raises(RuntimeError, match="ended as partial"):
        builder._wait_for_batch(submission, timeout=2)


def test_paging_covers_all_nodes_edges_and_batch_items(local_memory):
    client, model = local_memory
    many = {
        "entities": [{"name": f"User {i}", "entity_type": "Person", "summary": f"User {i} joined.", "attributes": {}} for i in range(106)],
        "relationships": [{"source": f"User {i}", "target": f"User {i+1}", "name": "FOLLOWS", "fact": f"User {i} follows User {i+1}", "attributes": {}} for i in range(105)],
    }
    model.replies[:] = [many, {"entities": [], "relationships": []}]
    builder = GraphBuilderService()
    graph_id = builder.create_graph("Pagination")
    submission = builder.add_text_batches(graph_id, ["Explicit test users.", "No named entities."])
    assert wait_batch(client, submission.batch_id).status == "succeeded"
    assert len(fetch_all_nodes(client, graph_id, page_size=7)) == 106
    assert len(fetch_all_edges(client, graph_id, page_size=9)) == 105
    first_page = client.batch.list_items(batch_id=submission.batch_id, limit=1)
    second_page = client.batch.list_items(batch_id=submission.batch_id, limit=1, cursor=first_page.next_cursor)
    assert [first_page.items[0].sequence_index, second_page.items[0].sequence_index] == [0, 1]
    assert second_page.next_cursor is None


def test_draft_batch_waits_for_process_and_submissions_are_atomic(local_memory):
    client, model = local_memory
    client.graph.create(graph_id="g")
    batch = client.batch.create(metadata={"graph_id": "g"})
    item = BatchAddItem(type="graph_episode", graph_id="g", data="Alice uses Atlas.", data_type="text")
    client.batch.add(batch_id=batch.batch_id, items=[item])
    assert client.batch.list_items(batch_id=batch.batch_id).items[0].status == "draft"
    assert model.calls == []
    with pytest.raises(NotFoundError):
        client.batch.add(batch_id=batch.batch_id, items=[item, {"type": "graph_episode", "graph_id": "missing", "data": "Fail"}])
    assert len(client.batch.list_items(batch_id=batch.batch_id).items) == 1
    client.batch.process(batch_id=batch.batch_id)
    assert wait_batch(client, batch.batch_id).status == "succeeded"
    with pytest.raises(ValueError, match="Cannot add"):
        client.batch.add(batch_id=batch.batch_id, items=[item])
    assert len(model.calls) == 1


def test_local_search_is_graph_scoped_and_missing_data_does_not_become_empty_success(local_memory):
    client, _ = local_memory
    client.graph.create(graph_id="g")
    client.graph.create(graph_id="other")
    episode = client.graph.add(graph_id="g", data="Alice uses Atlas.")
    wait_for(lambda: client.graph.episode.get(uuid_=episode.uuid_).processed)
    assert client.graph.search(graph_id="g", query="Alice", scope="nodes").nodes[0].name == "Alice"
    assert client.graph.search(graph_id="other", query="Alice").edges == []
    assert client.graph.search(graph_id="g", query="unrelatedword").edges == []
    with pytest.raises(NotFoundError):
        client.graph.search(graph_id="missing", query="Alice")
    with pytest.raises(NotFoundError):
        fetch_all_nodes(client, "missing")
    alice = next(node for node in fetch_all_nodes(client, "g") if node.name == "Alice")
    assert client.graph.node.get_edges(node_uuid=alice.uuid_)[0].fact == "Alice uses Atlas for onboarding"


def test_delete_removes_graph_data_episodes_and_batch_journals(local_memory):
    client, _ = local_memory
    builder = GraphBuilderService()
    graph_id = builder.create_graph("Delete me")
    submission = builder.add_text_batches(graph_id, ["Alice uses Atlas."])
    assert wait_batch(client, submission.batch_id).status == "succeeded"
    episode_id = client.batch.list_items(batch_id=submission.batch_id).items[0].episode_uuid
    client.graph.delete(graph_id=graph_id)
    with pytest.raises(NotFoundError):
        client.graph.get(graph_id)
    with pytest.raises(NotFoundError):
        client.graph.episode.get(uuid_=episode_id)
    with pytest.raises(NotFoundError):
        client.batch.get(batch_id=submission.batch_id)
    assert client.batch.list().batches == []


def test_deletion_cleans_batches_without_graph_metadata_and_invalidates_mixed_batches(local_memory):
    client, _ = local_memory
    client.graph.create(graph_id="g")
    client.graph.create(graph_id="other")
    single = client.batch.create()
    mixed = client.batch.create()
    item = {"type": "graph_episode", "graph_id": "g", "data": "Alice uses Atlas."}
    client.batch.add(batch_id=single.batch_id, items=[item])
    client.batch.add(batch_id=mixed.batch_id, items=[item, {**item, "graph_id": "other"}])
    client.graph.delete(graph_id="g")
    with pytest.raises(NotFoundError):
        client.batch.get(batch_id=single.batch_id)
    assert client.batch.get(batch_id=mixed.batch_id).status == "invalid"
    assert len(client.batch.list_items(batch_id=mixed.batch_id).items) == 1
    client.graph.delete(graph_id="other")
    with pytest.raises(NotFoundError):
        client.batch.get(batch_id=mixed.batch_id)


def test_interrupted_extraction_is_failed_instead_of_waiting_forever(local_memory, monkeypatch):
    client, _ = local_memory
    client.graph.create(graph_id="g")
    with client._db() as db:
        payload = client._new_episode(db, graph_id="g", data="Alice uses Atlas.", status="processing")
        db.execute("UPDATE episodes SET owner_pid=? WHERE uuid=?", (987654321, payload["uuid_"]))
    monkeypatch.setattr(os, "kill", lambda *_args: (_ for _ in ()).throw(ProcessLookupError()))
    with pytest.raises(LocalExtractionError, match="interrupted"):
        client.graph.episode.get(uuid_=payload["uuid_"])


def test_parallel_clients_claim_each_episode_once_and_merge_sources_atomically(local_memory):
    client, model = local_memory
    model.replies[:] = [extraction() for _ in range(24)]
    other = LocalGraphClient(client.path, llm_factory=lambda: model)
    client.graph.create(graph_id="g")
    def submit(index):
        destination = client if index % 2 else other
        return destination.graph.add(graph_id="g", data=f"Source {index}: Alice uses Atlas.")
    with ThreadPoolExecutor(max_workers=4) as pool:
        episodes = list(pool.map(submit, range(24)))
    wait_for(lambda: all(client.graph.episode.get(uuid_=episode.uuid_).processed for episode in episodes))
    assert len(model.calls) == 24
    assert len(fetch_all_nodes(client, "g")) == 2
    edges = fetch_all_edges(client, "g")
    assert len(edges) == 1
    assert set(edges[0].attributes["local_source_episode_uuids"]) == {episode.uuid_ for episode in episodes}
    if other._worker:
        other._worker.join(timeout=5)


def test_cloud_backend_is_still_available_when_selected(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY_BACKEND", "zep")
    monkeypatch.delenv("ZEP_API_URL", raising=False)
    cloud = SimpleNamespace(graph="cloud")
    calls = []
    monkeypatch.setattr(zep, "Zep", lambda **kwargs: (calls.append(kwargs) or cloud))
    zep.clear_zep_client_cache()
    assert zep.get_zep_client("cloud-test-key") is cloud
    assert calls[0]["api_key"] == "cloud-test-key"
    zep.clear_zep_client_cache()
