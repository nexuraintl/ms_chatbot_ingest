import json
from unittest.mock import patch

import pytest

from api.routers import events

BUCKET = "nexura-chatbot-tenants-qa"
FINALIZED = "google.cloud.storage.object.v1.finalized"
DELETED = "google.cloud.storage.object.v1.deleted"


def _post(client, event_type, name, bucket=BUCKET, generation=None):
    """Entrega un CloudEvent en modo binario, como lo hace Eventarc."""
    body = {"bucket": bucket, "name": name} if name is not None else {"bucket": bucket}
    if generation is not None:
        body["generation"] = str(generation)
    return client.post(
        "/events/gcs",
        content=json.dumps(body),
        headers={
            "ce-specversion": "1.0",
            "ce-id": "evt-1",
            "ce-source": "//storage.googleapis.com/projects/_/buckets/" + bucket,
            "ce-type": event_type,
            "content-type": "application/json",
        },
    )


@pytest.fixture
def svc():
    with patch.object(events, "ingestion_service") as mocked:
        mocked.get_tenant.return_value = {"active": True}
        mocked.ensure_store.return_value = "fileSearchStores/tenant-x"
        yield mocked


def test_parse_object_name():
    assert events._parse_object_name("t/knowledge/a/b.md") == ("t", "knowledge/a/b.md")


@pytest.mark.parametrize("bad", ["solo-un-segmento", "/knowledge/a.md", "tenant/"])
def test_parse_object_name_invalid(bad):
    with pytest.raises(ValueError):
        events._parse_object_name(bad)


def test_upsert_knowledge_imports(client, svc):
    r = _post(client, FINALIZED, "floridablanca/knowledge/general/a.md")
    assert r.status_code == 204
    svc.ensure_store.assert_called_once()
    svc.import_gcs_object.assert_called_once_with(
        "fileSearchStores/tenant-x", BUCKET, "floridablanca/knowledge/general/a.md",
        "floridablanca", "knowledge/general/a.md", None,
    )


def test_generation_is_forwarded(client, svc):
    _post(client, FINALIZED, "floridablanca/knowledge/a.md", generation=1700000000000002)
    assert svc.import_gcs_object.call_args.args[-1] == 1700000000000002
    _post(client, DELETED, "floridablanca/knowledge/a.md", generation=1700000000000001)
    svc.remove_gcs_object.assert_called_once_with("floridablanca", "knowledge/a.md", 1700000000000001)


def test_upsert_config_file_is_ignored(client, svc):
    r = _post(client, FINALIZED, "floridablanca/predetermined_answers.json")
    assert r.status_code == 204
    svc.get_tenant.assert_not_called()
    svc.import_gcs_object.assert_not_called()


def test_unknown_tenant_is_dropped(client, svc):
    svc.get_tenant.return_value = None
    r = _post(client, FINALIZED, "fantasma/knowledge/a.md")
    assert r.status_code == 204
    svc.ensure_store.assert_not_called()
    svc.import_gcs_object.assert_not_called()


def test_delete_knowledge_removes(client, svc):
    r = _post(client, DELETED, "floridablanca/knowledge/general/a.md")
    assert r.status_code == 204
    svc.remove_gcs_object.assert_called_once_with("floridablanca", "knowledge/general/a.md", None)


def test_delete_outside_knowledge_is_ignored(client, svc):
    r = _post(client, DELETED, "floridablanca/identity.json")
    assert r.status_code == 204
    svc.remove_gcs_object.assert_not_called()


def test_missing_name_is_204(client, svc):
    r = _post(client, FINALIZED, None)
    assert r.status_code == 204
    svc.import_gcs_object.assert_not_called()


def test_unparseable_path_is_204(client, svc):
    r = _post(client, FINALIZED, "archivo-en-la-raiz.md")
    assert r.status_code == 204
    svc.import_gcs_object.assert_not_called()


def test_processing_error_returns_500_so_eventarc_retries(client, svc):
    svc.import_gcs_object.side_effect = RuntimeError("gemini caido")
    r = _post(client, FINALIZED, "floridablanca/knowledge/a.md")
    assert r.status_code == 500
