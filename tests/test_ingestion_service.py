from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from api.services import ingestion_service as svc


@pytest.fixture
def env():
    """Mockea Firestore (tracking), Gemini y borrado; devuelve los mocks para asertar."""
    with patch.object(svc, "get_tracked") as get_tracked, \
         patch.object(svc, "save_tracked_document") as save, \
         patch.object(svc, "delete_tracked_document") as delete_tracked, \
         patch.object(svc, "_delete_document") as delete_doc, \
         patch.object(svc, "_client") as client, \
         patch.object(svc, "_gcp_credentials"):
        op = SimpleNamespace(done=True, error=None, response=SimpleNamespace(document_name="newdoc"))
        client.return_value.file_search_stores.import_file.return_value = op
        client.return_value.files.register_files.return_value = SimpleNamespace(files=[SimpleNamespace(name="files/x")])
        yield SimpleNamespace(get_tracked=get_tracked, save=save, delete_tracked=delete_tracked, delete_doc=delete_doc, client=client)


STORE = "fileSearchStores/s"


def _import(generation):
    return svc.import_gcs_object(STORE, "b", "t/knowledge/a.md", "t", "knowledge/a.md", generation)


def test_import_first_time_tracks_generation(env):
    env.get_tracked.return_value = None
    assert _import(5) == f"{STORE}/documents/newdoc"
    env.delete_doc.assert_not_called()
    env.save.assert_called_once_with("t", "knowledge/a.md", f"{STORE}/documents/newdoc", 5)


def test_import_replaces_older_version(env):
    env.get_tracked.return_value = {"document_name": "old", "generation": 3}
    _import(5)
    env.delete_doc.assert_called_once_with("old")
    env.save.assert_called_once()


@pytest.mark.parametrize("tracked_gen", [5, 9])
def test_import_skips_stale_or_duplicate_event(env, tracked_gen):
    env.get_tracked.return_value = {"document_name": "cur", "generation": tracked_gen}
    assert _import(5) is None
    env.client.return_value.files.register_files.assert_not_called()
    env.save.assert_not_called()


def test_import_legacy_record_without_generation_still_imports(env):
    env.get_tracked.return_value = {"document_name": "old"}
    assert _import(5) is not None
    env.delete_doc.assert_called_once_with("old")


def test_remove_deletes_when_generation_matches(env):
    env.get_tracked.return_value = {"document_name": "cur", "generation": 5}
    svc.remove_gcs_object("t", "knowledge/a.md", 5)
    env.delete_doc.assert_called_once_with("cur")
    env.delete_tracked.assert_called_once()


def test_remove_ignores_delete_of_overwritten_version(env):
    """Regresión: el `deleted` de la versión vieja no debe borrar la versión nueva ya importada."""
    env.get_tracked.return_value = {"document_name": "new", "generation": 6}
    svc.remove_gcs_object("t", "knowledge/a.md", 5)
    env.delete_doc.assert_not_called()
    env.delete_tracked.assert_not_called()


def test_remove_without_tracking_is_noop(env):
    env.get_tracked.return_value = None
    svc.remove_gcs_object("t", "knowledge/a.md", 5)
    env.delete_doc.assert_not_called()


def test_delete_document_tolerates_404():
    err = svc.genai_errors.ClientError(404, {"error": {"message": "nf", "status": "NOT_FOUND"}})
    with patch.object(svc, "_client") as client:
        client.return_value.file_search_stores.documents.delete.side_effect = err
        svc._delete_document("fileSearchStores/s/documents/x")  # no lanza


def test_delete_document_raises_other_errors():
    err = svc.genai_errors.ClientError(500, {"error": {"message": "boom", "status": "INTERNAL"}})
    with patch.object(svc, "_client") as client:
        client.return_value.file_search_stores.documents.delete.side_effect = err
        with pytest.raises(svc.genai_errors.ClientError):
            svc._delete_document("fileSearchStores/s/documents/x")


def test_import_timeout_discards_orphan_and_raises(env):
    pending = SimpleNamespace(done=False, error=None, name=f"{STORE}/operations/abc-123", response=None)
    env.client.return_value.file_search_stores.import_file.return_value = pending
    env.client.return_value.operations.get.return_value = pending
    env.get_tracked.return_value = None
    with patch.object(svc, "_IMPORT_POLL_ATTEMPTS", 2), patch.object(svc, "_IMPORT_POLL_INTERVAL_SECONDS", 0):
        with pytest.raises(TimeoutError):
            _import(5)
    env.delete_doc.assert_called_once_with(f"{STORE}/documents/abc-123")
    env.save.assert_not_called()


def test_import_error_discards_orphan_and_raises(env):
    failed = SimpleNamespace(done=True, error="boom", name=f"{STORE}/operations/zzz-9", response=None)
    env.client.return_value.file_search_stores.import_file.return_value = failed
    env.get_tracked.return_value = None
    with pytest.raises(RuntimeError):
        _import(5)
    env.delete_doc.assert_called_once_with(f"{STORE}/documents/zzz-9")


def test_orphan_cleanup_failure_does_not_mask_original_error(env):
    failed = SimpleNamespace(done=True, error="boom", name=f"{STORE}/operations/zzz-9", response=None)
    env.client.return_value.file_search_stores.import_file.return_value = failed
    env.get_tracked.return_value = None
    env.delete_doc.side_effect = RuntimeError("cleanup roto")
    with pytest.raises(RuntimeError, match="import_file falló"):
        _import(5)
