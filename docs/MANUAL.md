# Manual de Gobernanza — ms_chatbot_ingest

Cumplimiento del estándar **GOB-GCP-STD-01**. Última actualización: 2026-07-23.

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
  | QA (`qam`) | `qam-chatbot-ingest` | **No desplegado todavía.** |
  | Preproducción (`prem`) | `prem-chatbot-ingest` | **No desplegado todavía.** |
- **API Gateway:** **intencionalmente NO registrado.** Este servicio no recibe tráfico de ciudadanos ni del Gateway — solo es invocado por Eventarc sobre eventos del bucket de tenants.
- **Dependencias:**
  - Google Gemini (`google-genai`), File Search Store — mismo mecanismo que `ms_ia_chatbot`.
  - Firestore (Native mode) — registro de tenants + tracking de documentos ingestados. **No existe todavía en `pre-qa-functions`**, compartida con `ms_ia_chatbot`.
  - Eventarc trigger sobre `google.cloud.storage.object.v1.finalized` / `...v1.deleted` del bucket de tenants — **por crear**, junto con el servicio.

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
| `run-sa@pre-qa-functions.iam.gserviceaccount.com` | Identidad de ejecución (compartida con `ms_ia_chatbot`) | `roles/datastore.user` + `roles/storage.objectViewer` sobre el bucket de tenants del ambiente | ⚠️ Pendiente de otorgar (ver `ms_ia_chatbot/docs/MANUAL.md` sección 5 — mismos comandos, misma cuenta) |
| `deploy-sa@pre-qa-functions.iam.gserviceaccount.com` | Build + push + deploy | Permisos estándar de Cloud Build/Cloud Run deploy | ✅ Operativo (ya usado por otros servicios del proyecto) |
| Service account del trigger de Eventarc | Invoca `POST /events/gcs` | `roles/run.invoker` sobre este servicio | Por crear junto con el trigger (ver sección 6) |

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

Pasos pendientes antes del primer deploy real:
1. Crear el repo en Azure DevOps (ya solicitado, pendiente de creación por el equipo de plataforma) — hasta entonces, sin `git init` ni push.
2. Firestore + IAM de `run-sa` (compartidos con `ms_ia_chatbot`, ver ese manual).
3. Configurar el trigger de Eventarc una vez el servicio esté desplegado:
   ```bash
   gcloud eventarc triggers create tenant-kb-ingest-qa \
     --location=us-central1 \
     --destination-run-service=qam-chatbot-ingest \
     --destination-run-region=us-central1 \
     --event-filters="type=google.cloud.storage.object.v1.finalized" \
     --event-filters="bucket=nexura-chatbot-tenants-qa" \
     --service-account=run-sa@pre-qa-functions.iam.gserviceaccount.com
   ```
   (repetir para `...v1.deleted` y para el ambiente `prem` con su propio bucket/servicio).

## 7. Observabilidad

Logs JSON estructurados a stdout (`api/core/logging.py`), con `severity`, trace de GCP y `correlation_id`. Filtro sugerido en Cloud Logging:

```
resource.type="cloud_run_revision"
resource.labels.service_name="qam-chatbot-ingest"
```
(sustituir por `prem-chatbot-ingest` según el ambiente a inspeccionar)
