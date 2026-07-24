# api/routers/events.py
#
# Sin prefijo /v1: no es una API de negocio versionada de cara al ciudadano,
# es un webhook interno de Eventarc (decisión tomada explícitamente al aplicar
# el estándar de gobernanza a este repo).

import logging

from cloudevents.http import from_http
from fastapi import APIRouter, Request, Response

from api.services import ingestion_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


@router.post("/events/gcs")
async def handle_gcs_event(request: Request):
    """
    Recibe eventos de Eventarc sobre el bucket de tenants
    (google.cloud.storage.object.v1.finalized / ...v1.deleted) y sincroniza el
    File Search Store del tenant correspondiente. Ver plan de multitenencia,
    sección 4, para el diseño completo.
    """
    body = await request.body()
    event = from_http(dict(request.headers), body)

    data = event.data or {}
    bucket = data.get("bucket")
    object_name = data.get("name")

    if not bucket or not object_name:
        logger.warning("event_missing_fields", extra={"data": data})
        return Response(status_code=204)

    try:
        tenant_id, rel_path = _parse_object_name(object_name)
    except ValueError:
        logger.warning("event_ignored_unparseable_path", extra={"object_name": object_name})
        return Response(status_code=204)

    is_delete = event["type"].endswith(".deleted")

    try:
        if is_delete:
            _handle_delete(tenant_id, rel_path)
        else:
            _handle_upsert(tenant_id, rel_path, bucket, object_name)
    except Exception as e:
        # Devolvemos 500 para que Eventarc reintente; el flujo borrar-luego-importar
        # de ingestion_service.import_gcs_object es idempotente ante reintentos.
        logger.error(
            "event_processing_failed",
            exc_info=True,
            extra={"tenant_id": tenant_id, "rel_path": rel_path, "error": str(e)},
        )
        return Response(status_code=500)

    return Response(status_code=204)


def _parse_object_name(object_name: str) -> tuple[str, str]:
    """'floridablanca/knowledge/secretaria_hacienda/flujo_predial.md' -> ('floridablanca', 'knowledge/...')."""
    parts = object_name.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(object_name)
    return parts[0], parts[1]


def _handle_upsert(tenant_id: str, rel_path: str, bucket: str, object_name: str) -> None:
    if not rel_path.startswith("knowledge/"):
        # identity.json / protocol.json / predetermined_answers.json cambiaron: no hay
        # nada que ingestar en File Search. El servicio de chat recoge el cambio solo
        # cuando vence el TTL de su cache en memoria (tenant_service.py).
        logger.info("event_config_changed", extra={"tenant_id": tenant_id, "rel_path": rel_path})
        return

    tenant = ingestion_service.get_tenant(tenant_id)
    if tenant is None:
        # Alta explícita requerida: un tenant_id mal escrito no debe auto-provisionar
        # un "cliente" fantasma solo porque alguien subió un archivo a esa carpeta.
        logger.warning("event_unknown_tenant", extra={"tenant_id": tenant_id, "object_name": object_name})
        return

    store_name = ingestion_service.ensure_store(tenant_id, tenant)
    ingestion_service.import_gcs_object(store_name, bucket, object_name, tenant_id, rel_path)


def _handle_delete(tenant_id: str, rel_path: str) -> None:
    if not rel_path.startswith("knowledge/"):
        return
    ingestion_service.remove_gcs_object(tenant_id, rel_path)
