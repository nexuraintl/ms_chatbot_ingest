import logging
import time
from functools import lru_cache
from typing import Optional

import google.auth
from google import genai
from google.cloud import firestore
from google.genai import errors as genai_errors
from google.genai import types

from api.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Cuántas veces hacer polling sobre una long-running operation de import_file
# (2s entre intentos) antes de darla por atascada.
_IMPORT_POLL_ATTEMPTS = 30
_IMPORT_POLL_INTERVAL_SECONDS = 2


# Los clientes de Firestore/Gemini resuelven credenciales al construirse, no de forma
# perezosa — instanciarlos a nivel de módulo rompe cualquier import sin credenciales
# reales disponibles (incluidos los tests). Se crean solo al primer uso real.
@lru_cache
def _client() -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


@lru_cache
def _firestore_client() -> firestore.Client:
    return firestore.Client(project=settings.gcp_project)


@lru_cache
def _gcp_credentials():
    # files.register_files() requiere credenciales explícitas de GCP (para leer el
    # objeto del bucket), no las toma del entorno automáticamente como Firestore.
    # El scope por defecto de google.auth.default() no alcanza: la API rechaza la
    # llamada con ACCESS_TOKEN_SCOPE_INSUFFICIENT si el token no incluye también
    # devstorage.read_only explícitamente (validado contra la API real).
    credentials, _ = google.auth.default(
        scopes=[
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/devstorage.read_only",
        ]
    )
    return credentials

# Lógica de ingesta duplicada de ms_ia_chatbot/api/services/ingestion_service.py,
# intencionalmente: este es un Cloud Run service separado (repo/deploy propio), no
# comparte proceso ni paquete con el chatbot público. Ver plan de multitenencia,
# sección 4, para el porqué de la separación (permisos e IAM distintos por servicio).


def _doc_id_for_path(rel_path: str) -> str:
    return rel_path.replace("/", "__")


def _tenant_ref(tenant_id: str):
    return _firestore_client().collection("tenants").document(tenant_id)


def get_tenant(tenant_id: str) -> Optional[dict]:
    """Devuelve el doc del tenant si existe y está activo; None si no (evita auto-provisionar)."""
    snapshot = _tenant_ref(tenant_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict()
    if not data.get("active", False):
        return None
    return data


def ensure_store(tenant_id: str, tenant: dict) -> str:
    """Devuelve el file_search_store_name del tenant, creándolo y persistiéndolo la primera vez."""
    store_name = tenant.get("file_search_store_name")
    if store_name:
        return store_name

    display_name = f"tenant-{tenant_id}"
    for store in _client().file_search_stores.list():
        if store.display_name == display_name:
            store_name = store.name
            break
    if not store_name:
        store = _client().file_search_stores.create(config={"display_name": display_name})
        store_name = store.name

    _tenant_ref(tenant_id).update({"file_search_store_name": store_name})
    logger.info("ingest_store_ready", extra={"tenant_id": tenant_id, "store_name": store_name})
    return store_name


def get_tracked(tenant_id: str, rel_path: str) -> Optional[dict]:
    """Registro de tracking de un documento ({document_name, rel_path, generation?}) o None."""
    doc_ref = _tenant_ref(tenant_id).collection("kb_documents").document(_doc_id_for_path(rel_path))
    snapshot = doc_ref.get()
    return snapshot.to_dict() if snapshot.exists else None


def get_tracked_document(tenant_id: str, rel_path: str) -> Optional[str]:
    tracked = get_tracked(tenant_id, rel_path)
    return tracked.get("document_name") if tracked else None


def save_tracked_document(tenant_id: str, rel_path: str, document_name: str, generation: Optional[int] = None) -> None:
    doc_ref = _tenant_ref(tenant_id).collection("kb_documents").document(_doc_id_for_path(rel_path))
    record = {"document_name": document_name, "rel_path": rel_path}
    if generation is not None:
        record["generation"] = generation
    doc_ref.set(record)


def delete_tracked_document(tenant_id: str, rel_path: str) -> None:
    _tenant_ref(tenant_id).collection("kb_documents").document(_doc_id_for_path(rel_path)).delete()


def _delete_document(document_name: str) -> None:
    """force=True va envuelto en DeleteDocumentConfig, no como kwarg directo (validado contra la API real).

    Un 404 se tolera: el documento ya no existe, que es el estado que se busca (idempotente ante reintentos).
    """
    try:
        _client().file_search_stores.documents.delete(
            name=document_name,
            config=types.DeleteDocumentConfig(force=True),
        )
    except genai_errors.ClientError as e:
        if getattr(e, "code", None) != 404:
            raise
        logger.info("ingest_delete_already_gone", extra={"document_name": document_name})


def _discard_orphan_document(store_name: str, operation) -> None:
    """
    Si el import falla o se atasca, Gemini puede dejar el documento a medias (PENDING/FAILED) dentro del
    store, sin tracking: Eventarc reintenta, se importa otra copia y quedaría duplicado en el RAG.
    El id de la operación coincide con el id del documento. Mejor esfuerzo: nunca enmascara el error original.
    """
    try:
        document_id = (getattr(operation, "name", "") or "").rsplit("/", 1)[-1]
        if document_id:
            _delete_document(f"{store_name}/documents/{document_id}")
            logger.warning("ingest_orphan_discarded", extra={"document_name": f"{store_name}/documents/{document_id}"})
    except Exception:
        logger.warning("ingest_orphan_discard_failed", exc_info=True)


def import_gcs_object(
    store_name: str,
    bucket: str,
    object_name: str,
    tenant_id: str,
    rel_path: str,
    generation: Optional[int] = None,
) -> Optional[str]:
    """
    Ingresa/reingresa un objeto de GCS al File Search Store, borrando la versión previa si existía.

    `generation` es la generación del objeto que trae el evento. Si ya hay registrada una
    generación igual o más nueva (evento viejo o duplicado fuera de orden), se omite.
    """
    tracked = get_tracked(tenant_id, rel_path)
    tracked_generation = (tracked or {}).get("generation")
    if generation is not None and tracked_generation is not None and tracked_generation >= generation:
        logger.info(
            "ingest_stale_event_skipped",
            extra={"tenant_id": tenant_id, "rel_path": rel_path, "generation": generation, "tracked_generation": tracked_generation},
        )
        return None

    old_document_name = (tracked or {}).get("document_name")
    if old_document_name:
        try:
            _delete_document(old_document_name)
        except Exception as e:
            logger.warning(
                "ingest_delete_old_doc_failed",
                exc_info=True,
                extra={"tenant_id": tenant_id, "document_name": old_document_name, "error": str(e)},
            )

    gcs_uri = f"gs://{bucket}/{object_name}"
    registered = _client().files.register_files(uris=[gcs_uri], auth=_gcp_credentials())

    operation = _client().file_search_stores.import_file(
        file_search_store_name=store_name,
        file_name=registered.files[0].name,
        config=types.ImportFileConfig(
            custom_metadata=[
                types.CustomMetadata(key="tenant_id", string_value=tenant_id),
                types.CustomMetadata(key="path", string_value=rel_path),
            ]
        ),
    )

    # import_file es una long-running operation: hay que hacer polling hasta
    # done=True antes de leer operation.response (si no, .document_name viene None).
    poll_attempts = 0
    while not operation.done and poll_attempts < _IMPORT_POLL_ATTEMPTS:
        time.sleep(_IMPORT_POLL_INTERVAL_SECONDS)
        operation = _client().operations.get(operation)
        poll_attempts += 1

    if not operation.done:
        _discard_orphan_document(store_name, operation)
        raise TimeoutError(
            f"import_file no terminó tras {poll_attempts} intentos de polling (tenant={tenant_id}, path={rel_path})"
        )
    if operation.error:
        _discard_orphan_document(store_name, operation)
        raise RuntimeError(f"import_file falló para tenant={tenant_id} path={rel_path}: {operation.error}")

    # operation.response.document_name es un ID corto; documents.delete() necesita
    # el resource name completo, así que se arma y persiste ya resuelto.
    document_name = f"{store_name}/documents/{operation.response.document_name}"
    save_tracked_document(tenant_id, rel_path, document_name, generation)
    logger.info("ingest_ok", extra={"tenant_id": tenant_id, "rel_path": rel_path, "document_name": document_name})
    return document_name


def remove_gcs_object(tenant_id: str, rel_path: str, generation: Optional[int] = None) -> None:
    """
    Contraparte de import_gcs_object para cuando se borra un archivo del bucket.

    Sobrescribir un objeto hace que GCS emita `finalized` (versión nueva) Y `deleted` (versión
    vieja). Si el `deleted` llega después de importar la versión nueva, NO debe borrarla: se
    omite cuando la generación registrada es más nueva que la del evento.
    """
    tracked = get_tracked(tenant_id, rel_path)
    document_name = (tracked or {}).get("document_name")
    tracked_generation = (tracked or {}).get("generation")
    if generation is not None and tracked_generation is not None and tracked_generation > generation:
        logger.info(
            "ingest_delete_stale_skipped",
            extra={"tenant_id": tenant_id, "rel_path": rel_path, "generation": generation, "tracked_generation": tracked_generation},
        )
        return
    if not document_name:
        logger.warning("ingest_delete_noop", extra={"tenant_id": tenant_id, "rel_path": rel_path})
        return

    _delete_document(document_name)
    delete_tracked_document(tenant_id, rel_path)
    logger.info("ingest_deleted", extra={"tenant_id": tenant_id, "rel_path": rel_path, "document_name": document_name})
