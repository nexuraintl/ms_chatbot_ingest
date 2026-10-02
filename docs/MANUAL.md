# Manual de Gobernanza — ms_chatbot_ingest

Cumplimiento del estándar **GOB-GCP-STD-01**. Última actualización: 2026-10-02 (pruebas end-to-end en QA y corrección de la carrera de eventos). Documentación completa de ambos servicios: `ms_ia_chatbot/docs/20261002_manual-ia-chatbot-y-chatbot-ingest_v2.docx`.

## 1. Descripción funcional

- **Nombre del servicio:** `ms_chatbot_ingest`
- **Propósito:** Servicio Cloud Run interno (sin tráfico público) que sincroniza el contenido `knowledge/` de cada tenant, subido al bucket de GCS, hacia su File Search Store de Gemini. Recibe eventos de Eventarc sobre el bucket de tenants; complemento de ingesta de `ms_ia_chatbot`.
- **Módulo / iniciativa:** Plataforma de IA — chatbots institucionales multitenant.
- **Responsable:** Santiago Valenzuela López (svalenzuela@nexura.com)

## 2. Arquitectura

- **Proyecto GCP:** `pre-qa-functions`.
- **Región:** `us-central1`.
- **Servicios Cloud Run:**
  | Ambiente | Nombre del servicio | Estado |
  |---|---|---|
  | QA (`qam`) | `qam-chatbot-ingest` | ✅ Desplegado y validado de punta a punta (2026-10-02). URL: `https://qam-chatbot-ingest-ghlnutfdwq-uc.a.run.app`. |
  | Preproducción (`prem`) | `prem-chatbot-ingest` | **No desplegado todavía** (junto con sus triggers e IAM). |
- **API Gateway:** **intencionalmente NO registrado.** Este servicio no recibe tráfico de ciudadanos ni del Gateway — solo es invocado por Eventarc sobre eventos del bucket de tenants.
- **Dependencias:**
  - Google Gemini (`google-genai`), File Search Store — mismo mecanismo que `ms_ia_chatbot`.
  - Firestore (Native mode) — registro de tenants + tracking de documentos ingestados en `tenants/{tenant_id}/kb_documents/{rel_path con "/" → "__"}` (`document_name`, `rel_path`, `generation`). Compartida con `ms_ia_chatbot`.
  - Eventarc: triggers `tenant-kb-ingest-qa` (`...v1.finalized`) y `tenant-kb-ingest-qa-deleted` (`...v1.deleted`) sobre `nexura-chatbot-tenants-qa`, ambos activos. Para `prem`, por crear.

## 3. Endpoints

| Método | Path | Auth | Descripción |
|---|---|---|---|
| GET | `/health` | No | `{"status": "UP"}` |
| GET | `/version` | No | `{"service", "version", "environment"}` |
| POST | `/events/gcs` | Invocación de Eventarc (IAM `run.invoker`, no Gateway) | Webhook interno; sin prefijo `/v1` (no es API de negocio versionada). |

## 4. Variables y secretos

| Variable | Origen en Cloud Run | Valor / Secreto |
|---|---|---|
| `SERVICE_NAME` | `--set-env-vars` | `qam-chatbot-ingest` / `prem-chatbot-ingest` |
| `ENVIRONMENT` | `--set-env-vars` | `qa` / `prem` |
| `GOOGLE_CLOUD_PROJECT`, `GCP_PROJECT` | `--set-env-vars` | `pre-qa-functions` |
| `LOG_LEVEL` | `--set-env-vars` | `INFO` |
| `GEMINI_API_KEY` | **Secret Manager** | Secreto `GEMINI_API_KEY`, versión `latest` — mismo secreto que usa `ms_ia_chatbot`. |

## 5. IAM

| Cuenta | Rol en este servicio | Roles requeridos | Estado |
|---|---|---|---|
| `run-sa@pre-qa-functions.iam.gserviceaccount.com` | Identidad de ejecución (compartida con `ms_ia_chatbot`) | `roles/datastore.user` + `roles/storage.objectViewer` sobre el bucket de tenants del ambiente | ✅ Otorgado (confirmado funcionando en el alta real del tenant `floridablanca` desde `ms_ia_chatbot`, mismo código de `ingestion_service.py`, 2026-07-24) |
| `service-<PROJECT_NUMBER>@gcp-sa-generativelanguage.iam.gserviceaccount.com` (service agent gestionado por Google) | Lee el objeto de GCS **en nombre de Google** durante `file_search_stores.import_file()` | `roles/storage.objectViewer` sobre el bucket de tenants | ✅ Otorgado sobre `nexura-chatbot-tenants-qa`/`-prem` (ver `ms_ia_chatbot/docs/MANUAL.md` sección 5 — mismo bucket, mismo permiso). Sin esto, `import_file()` falla con 403 aunque `run-sa` esté bien configurada. |
| `deploy-sa@pre-qa-functions.iam.gserviceaccount.com` | Build + push + deploy | Permisos estándar de Cloud Build/Cloud Run deploy | ✅ Operativo (ya usado por otros servicios del proyecto) |
| `run-sa@pre-qa-functions.iam.gserviceaccount.com` (identidad del trigger de Eventarc, reutiliza la misma SA de ejecución) | Invoca `POST /events/gcs` cuando dispara el trigger | `roles/run.invoker` sobre este servicio + `roles/eventarc.eventReceiver` a nivel de proyecto | ✅ Otorgados (2026-07-31) |
| `service-<PROJECT_NUMBER>@gs-project-accounts.iam.gserviceaccount.com` (service agent de GCS, gestionado por Google) | Publica los eventos de cambios del bucket a Pub/Sub (requisito one-time de Eventarc para triggers con origen GCS) | `roles/pubsub.publisher` a nivel de proyecto | ✅ Otorgado (2026-07-31) |

## 6. Despliegue

Definido en `cloudbuild.yaml` (defaults = QA; `prem` sobreescribe `_SERVICE_NAME`/`_ENVIRONMENT` a nivel de trigger de Cloud Build).

| Parámetro | Valor |
|---|---|
| Min instances | 0 |
| Max instances | 2 |
| CPU | 1 |
| Memoria | 256Mi |
| Concurrency | 10 |
| Timeout | 120s |
| Ingress | `all` (mismo criterio que `ms_ia_chatbot`; podría restringirse más adelante ya que solo Eventarc necesita alcanzarlo) |
| Artifact Registry | `gcr.io/pre-qa-functions/<service>` |

Historial de los pasos previos al primer deploy real (todos cumplidos en QA):
1. ✅ Repo creado en Azure DevOps — hecho.
2. ✅ Firestore + IAM de `run-sa` y del service agent de Generative Language (compartidos con `ms_ia_chatbot`) — hecho, ver sección 5.
3. ✅ Mecánica de `google-genai`/File Search Store validada de punta a punta contra la API real, incluyendo `import_file()` contra un bucket real — hecho (2026-07-24), 9 discrepancias reales encontradas y corregidas (ver `ms_ia_chatbot/README.md` sección 10 para el detalle completo); el mismo `api/services/ingestion_service.py` de este repo ya quedó actualizado con esas correcciones.
4. ✅ Trigger de Eventarc configurado y validado end-to-end — hecho (2026-07-31). Dos triggers en `qa` (`tenant-kb-ingest-qa` para `v1.finalized`, `tenant-kb-ingest-qa-deleted` para `v1.deleted`), ambos sobre `nexura-chatbot-tenants-qa`:
   ```bash
   gcloud eventarc triggers create tenant-kb-ingest-qa \
     --location=us-central1 \
     --destination-run-service=qam-chatbot-ingest \
     --destination-run-region=us-central1 \
     --destination-run-path=/events/gcs \
     --event-filters="type=google.cloud.storage.object.v1.finalized" \
     --event-filters="bucket=nexura-chatbot-tenants-qa" \
     --service-account=run-sa@pre-qa-functions.iam.gserviceaccount.com

   gcloud eventarc triggers create tenant-kb-ingest-qa-deleted \
     --location=us-central1 \
     --destination-run-service=qam-chatbot-ingest \
     --destination-run-region=us-central1 \
     --destination-run-path=/events/gcs \
     --event-filters="type=google.cloud.storage.object.v1.deleted" \
     --event-filters="bucket=nexura-chatbot-tenants-qa" \
     --service-account=run-sa@pre-qa-functions.iam.gserviceaccount.com
   ```
   ⚠️ **`--destination-run-path=/events/gcs` es obligatorio.** Sin ese flag, Eventarc entrega el evento a la raíz `/` del servicio (`POST /?__GCP_CloudEventsMode=GCS_NOTIFICATION`), que no tiene ruta registrada — el servicio responde `404` y Eventarc reintenta con backoff exponencial indefinidamente sin nunca procesar el evento. Se encontró este bug real al crear el trigger por primera vez (los primeros ~5 reintentos fallaron con 404 antes de corregir con `gcloud eventarc triggers update ... --destination-run-path=/events/gcs`).

   Requisitos de IAM adicionales para que el trigger funcione (ver sección 5): `run-sa` necesita `roles/eventarc.eventReceiver` (proyecto) y `roles/run.invoker` (sobre este servicio); el service agent de GCS necesita `roles/pubsub.publisher` (proyecto, setup one-time de Eventarc+GCS).

   Validado con una subida y un borrado de archivo real en `floridablanca/knowledge/`: ambos eventos procesados automáticamente (`ingest_ok` / `ingest_deleted` en los logs), documento confirmado `STATE_ACTIVE` en el File Search Store vía la API real, sin correr ningún script a mano.

   Pendiente: repetir todo esto (triggers + IAM) para el ambiente `prem` con su propio bucket/servicio.

## 7. Comportamiento ante eventos fuera de orden, duplicados y fallos (2026-10-02)

Hallazgos de la prueba real contra QA (ver sección 8) y su corrección:

1. **Sobrescribir un objeto emite DOS eventos**: `finalized` (versión nueva) y `deleted` (versión vieja). Si el `deleted` se procesaba **después** de importar la versión nueva, borraba el documento recién importado (el estado final solo quedaba bien por los reintentos de Eventarc). **Corrección:** el registro de tracking (`kb_documents`) guarda la `generation` del objeto y se compara con la del evento — un `deleted` de una generación **más vieja** que la registrada se ignora (`ingest_delete_stale_skipped`), igual que un `finalized` de generación igual o menor (`ingest_stale_event_skipped`). Los registros previos sin `generation` se comportan como antes.
2. **Import atascado → documento huérfano.** Si `import_file()` no termina en 30 intentos × 2 s (Gemini tardó >60 s en una prueba) o devuelve error, el documento queda a medias (`STATE_PENDING`) fuera del tracking; Eventarc reintenta e importa otra copia, y el RAG queda con contenido duplicado. **Corrección:** ante timeout o error se intenta borrar ese documento (el id de la operación coincide con el del documento) y se loguea `ingest_orphan_discarded`; nunca enmascara el error original. Eventarc reintenta como antes (500).
3. **Borrar un documento que ya no existe (404)** se considera éxito (`ingest_delete_already_gone`): la operación es idempotente ante reintentos.

Logs nuevos: `ingest_stale_event_skipped`, `ingest_delete_stale_skipped`, `ingest_orphan_discarded`, `ingest_orphan_discard_failed`, `ingest_delete_already_gone`.

## 8. Pruebas

**Unitarias** (`pytest`, sin llamadas reales): 30 pruebas — health/correlation-id, `POST /events/gcs` (alta, borrado, archivos fuera de `knowledge/`, tenant desconocido, path inválido, evento sin nombre, error → 500) y `ingestion_service` (generaciones, eventos viejos/duplicados, limpieza de huérfanos, 404 tolerado).

**End-to-end contra QA (2026-10-02)**, con archivos de prueba bajo `floridablanca/knowledge/_test_ingest/` (borrados al terminar):

| Prueba | Resultado |
|---|---|
| Backfill: archivo del bucket no presente en el store (`flujo_pqrsd_atencion.md`, 20 de 21 indexados) | ✅ re-subirlo lo ingestó solo; el store pasó a 21 documentos |
| Crear archivo nuevo | ✅ `ingest_ok`, documento `STATE_ACTIVE`, registro de tracking creado |
| Actualizar (sobrescribir) | ✅ versión vieja reemplazada, sin duplicados; el `deleted` viejo ignorado (`ingest_delete_stale_skipped`) |
| RAG con el dato nuevo (vía `qam-ia-chatbot`) | ✅ el chatbot respondió con el contenido recién ingestado (`source: knowledge_base`) |
| Borrar archivo | ✅ `ingest_deleted`, documento y tracking eliminados (store de nuevo en 21) |
| Archivo de configuración (`identity`/`protocol`/`predetermined_answers`) | ✅ `event_config_changed`, no se ingesta |
| Tenant inexistente (`tenantfantasma/…`) | ✅ `event_unknown_tenant`, descartado sin crear nada |
| Sobrescritura con import lento (>60 s) | ✅ detectado el huérfano (corregido, ver sección 7); estado final consistente |

Latencia observada de un import: ~3–5 s normal; ~15 s con arranque en frío; una vez >60 s (lado Gemini).

## 9. Deuda técnica conocida

- `ingestion_service.py` está **duplicado** en `ms_ia_chatbot` (lo usa `scripts/sync_tenant_kb.py`) y **no** tiene los fixes de la sección 7. Una reingesta manual desde ese script no guarda `generation`; unificar en un paquete compartido o portar los cambios.
- Import sincrónico dentro del request HTTP (alerta de latencia p95, sección "Estado" del README). Alternativa: responder 204 y procesar en segundo plano.
- Sin tests de integración automatizados contra GCP: la prueba end-to-end de la sección 8 es manual.
- `prem-chatbot-ingest`, sus triggers de Eventarc y el IAM del ambiente `prem` no existen.
- Ingress en `all` (solo lo invoca Eventarc; con `--no-allow-unauthenticated`): evaluar restringirlo.
- Un archivo borrado y vuelto a crear con el mismo nombre dentro de la ventana de reintentos podría resolverse por generación pero no está probado.

## 10. Observabilidad

Logs JSON estructurados a stdout (`api/core/logging.py`), con `severity`, trace de GCP y `correlation_id`. Filtro sugerido en Cloud Logging:

```
resource.type="cloud_run_revision"
resource.labels.service_name="qam-chatbot-ingest"
```
(sustituir por `prem-chatbot-ingest` según el ambiente a inspeccionar)
