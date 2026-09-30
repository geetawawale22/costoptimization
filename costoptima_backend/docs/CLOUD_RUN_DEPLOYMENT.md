# CostOptima Backend Cloud Run Deployment

This folder contains the backend-only deployment files for Google Cloud Run.

## Files Added

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the FastAPI backend image and runs `uvicorn app.main:app` on Cloud Run's `$PORT`. |
| `.dockerignore` | Keeps local virtualenvs, uploads, logs, test files, Excel samples, and secrets out of the image. |
| `cloudbuild.yaml` | Example Cloud Build pipeline to build, push, and deploy the backend container. |

## Required GCP Resources

1. Artifact Registry repository, for example:

```bash
gcloud artifacts repositories create costoptima \
  --repository-format=docker \
  --location=asia-south1
```

2. MySQL database reachable from Cloud Run.

Recommended options:

| Option | Notes |
|---|---|
| Cloud SQL for MySQL | Best production option. Use Cloud SQL connector / instance attachment. |
| External MySQL | Must allow Cloud Run egress IP/network access. |

3. Runtime environment variables or Secret Manager values.

## Required Environment Variables

Set these on the Cloud Run service:

| Variable | Required | Example / Notes |
|---|---:|---|
| `DATABASE_URL` | Yes | `mysql+pymysql://USER:PASSWORD@HOST:3306/costoptima` |
| `SECRET_KEY` | Yes | Long random string. Store in Secret Manager. |
| `FRONTEND_ORIGIN` | Yes | Deployed frontend origin, for example `https://costoptima-ui.example.com` |
| `SESSION_COOKIE_SECURE` | Yes | `true` for Cloud Run HTTPS |
| `UPLOAD_DIR` | No | Default `/app/uploads`; temporary on Cloud Run unless changed to object storage logic |
| `LOG_DIR` | No | Default `/app/logs`; Cloud Run stdout is the durable log destination |
| `LOG_FILE` | No | Default `costoptima.log` |
| `PCM_VERBOSE_LOGS` | No | `true` while debugging PCM migration |
| `SSO_ENABLED` | No | `true` if CC Services SSO should be visible |
| `SSO_LOGIN_URL` | If SSO enabled | CC Services login URL |
| `SSO_CALLBACK_URL` | If SSO enabled | Cloud Run callback URL, for example `https://SERVICE_URL/auth/sso/callback` |
| `SSO_AUTO_CREATE_USERS` | No | Keep `false`; SSO users must already exist in CostOptima DB |

## Manual Build and Deploy

From `costoptima_backend`:

```bash
gcloud builds submit --config cloudbuild.yaml .
```

Or build/deploy manually:

```bash
gcloud builds submit \
  --tag asia-south1-docker.pkg.dev/PROJECT_ID/costoptima/backend:latest .

gcloud run deploy costoptima-backend \
  --image asia-south1-docker.pkg.dev/PROJECT_ID/costoptima/backend:latest \
  --region asia-south1 \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 900
```

## Production Notes

- Cloud Run container storage is temporary. Uploaded Excel files and generated output files should be moved to Cloud Storage for durable production use.
- Application logs are printed to stdout and will appear in Cloud Logging. The local file log path is still created inside the container, but it is not durable.
- The app creates/migrates tables on startup through `Base.metadata.create_all()` and `ensure_schema_columns()`. Seed data still needs to be installed separately using the backend seed commands or SQL scripts.
- The backend must be deployed with a real `FRONTEND_ORIGIN`; browser session cookies will not work correctly with wildcard CORS.

## Backend Smoke Test

After deployment:

```bash
curl https://SERVICE_URL/
```

Expected response:

```json
{"status":"ok","service":"CostOptima API","docs":"/docs"}
```
