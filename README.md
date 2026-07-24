# ms_chatbot_ingest

Servicio Cloud Run interno (sin tráfico público) que sincroniza el contenido de `knowledge/` de cada tenant, subido a GCS, hacia su File Search Store de Gemini. Es el complemento de ingesta de [`ms_ia_chatbot`](../ms_ia_chatbot) — ver el README de ese repo (sección "Ingesta de contenido") para el diseño completo.

> Estructura de repositorio alineada al estándar de gobernanza **GOB-GCP-STD-01** de NEXURA.

## Qué hace

Expone `POST /events/gcs` (`api/routers/events.py`), pensado como destino de un trigger de **Eventarc** sobre el bucket de tenants (`google.cloud.storage.object.v1.finalized` y `...v1.deleted`):

- Si el objeto cambiado está bajo `<tenant_id>/knowledge/...`: registra el archivo en la Files API de Gemini, lo importa al File Search Store del tenant (creándolo si es el primero), y borra la versión anterior del mismo archivo si existía (evita contenido duplicado).
- Si el objeto está fuera de `knowledge/` (p. ej. `identity.json`, `protocol.json`): no hace nada — el servicio de chat recoge esos cambios solo por el vencimiento del TTL de su propio cache.
- Si el `tenant_id` no está dado de alta en Firestore (o está inactivo): descarta el evento con un log de advertencia. No auto-provisiona tenants.

`/events/gcs` no lleva prefijo `/v1` — es un webhook interno de Eventarc, no una API de negocio versionada de cara al ciudadano (decisión tomada explícitamente al aplicar el estándar de gobernanza a este repo).

## Estructura del proyecto

```
ms_chatbot_ingest/
├── Dockerfile                  # Multi-stage, usuario no-root
├── .dockerignore
├── .env.example
├── requirements.txt
├── requirements-dev.txt
├── cloudbuild.yaml             # Build + push + deploy a Cloud Run (pre-qa-functions)
├── .azure-pipelines.yml        # Bridge ADO -> GitHub (nexuraintl), dispara Cloud Build
├── tests/
│   ├── conftest.py
│   └── test_health.py          # health, version, correlation-id (generado y propagado)
└── api/
    ├── main.py                 # setup_logging() + CorrelationMiddleware + registro de routers
    ├── core/
    │   ├── config.py            # Settings (pydantic-settings) + get_settings() con @lru_cache
    │   ├── logging.py            # JsonFormatter: severity, trace, correlation_id
    │   └── middleware.py         # CorrelationMiddleware + ContextVar de trace
    ├── routers/
    │   ├── health.py             # GET /health, GET /version
    │   └── events.py             # POST /events/gcs (handler de Eventarc)
    └── services/
        └── ingestion_service.py  # register_files + import_file + borrado de duplicados
```

`api/services/ingestion_service.py` está intencionalmente duplicado respecto al de `ms_ia_chatbot` — son Cloud Run services separados (repos/deploys propios), no comparten proceso ni paquete.

## Por qué es un servicio aparte

- **Permisos**: necesita escritura en Firestore y en File Search Store, y lectura del bucket. El chat público solo necesita lectura de Firestore.
- **Perfil de tráfico**: esporádico (cuando se sube contenido) vs. tráfico público constante — no comparte configuración de escalado/concurrencia con el chat.

## Variables de entorno

Ver `.env.example` para la lista completa. Resumen:

| Variable | Requerida | Descripción |
|---|---|---|
| `SERVICE_NAME`, `SERVICE_VERSION`, `ENVIRONMENT`, `LOG_LEVEL`, `GOOGLE_CLOUD_PROJECT` | No | Variables base de gobernanza/observabilidad. |
| `GEMINI_API_KEY` | **Sí** | API key de Gemini con acceso a File Search. En Cloud Run se inyecta vía Secret Manager. |
| `GCP_PROJECT` | No | Proyecto GCP donde vive Firestore. |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Despliegue (`pre-qa-functions`)

`cloudbuild.yaml` ya trae los datos reales de `pre-qa-functions` (proyecto, `run-sa`/`deploy-sa`, `gcr.io` como registry). Detalle completo en [`docs/MANUAL.md`](docs/MANUAL.md). `run-sa@pre-qa-functions.iam.gserviceaccount.com` (compartida con `ms_ia_chatbot`) todavía **no tiene** `roles/datastore.user` ni `roles/storage.objectViewer` — pendiente de otorgar antes del primer deploy.

```bash
gcloud eventarc triggers create tenant-kb-ingest-qa \
  --location=us-central1 \
  --destination-run-service=qam-chatbot-ingest \
  --destination-run-region=us-central1 \
  --event-filters="type=google.cloud.storage.object.v1.finalized" \
  --event-filters="bucket=nexura-chatbot-tenants-qa" \
  --service-account=run-sa@pre-qa-functions.iam.gserviceaccount.com
```
(repetir para el evento `...v1.deleted` y para el ambiente `prem` con `prem-chatbot-ingest` / `nexura-chatbot-tenants-prem`)

Requiere el setup one-time de Eventarc para triggers de origen GCS (permiso `pubsub.publisher` al service agent de GCS del proyecto).

## Estado

- ✅ **La mecánica de `google-genai`/File Search Store que usa `api/services/ingestion_service.py` ya se validó contra la API real** (2026-07-24, spike corrido desde `ms_ia_chatbot`, mismo código duplicado aquí). Se encontraron y corrigieron 5 discrepancias reales entre la documentación y el SDK instalado — detalle completo en `ms_ia_chatbot/README.md` sección 10. Entre otras cosas obligó a subir `google-genai` a `2.14.0` y, en cascada, todo el stack de FastAPI/Starlette/httpx (ver `requirements.txt`).
- ⚠️ **Pendiente:** el paso específico `files.register_files(uris=["gs://..."])` contra un bucket real — necesita Application Default Credentials locales contra `pre-qa-functions`, que todavía no están configuradas en esta máquina. La firma del método (`auth=` requerido) ya se corrigió según la documentación del SDK, pero no se ejecutó en vivo.
- Firestore y los buckets de tenants (`nexura-chatbot-tenants-qa` / `-prem`) — el usuario reporta que ya fueron creados en `pre-qa-functions`.
- El repo ya existe en Azure DevOps (`https://nexura.visualstudio.com/Nexura%20Platform%20IA/_git/ms_chatbot_ingest`, ramas `dev`/`qa`/`master`/`main`) y este trabajo ya está pusheado en `dev`.
- A diferencia de `qam-ia-chatbot`/`prem-ia-chatbot` (ya desplegados), `qam-chatbot-ingest`/`prem-chatbot-ingest` **no existen desplegados todavía** — no hay riesgo de romper algo en caliente con el primer deploy.
