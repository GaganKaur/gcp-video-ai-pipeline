# GCP Video AI Pipeline

An end-to-end pipeline that automatically transcribes, chapters, indexes, and highlights video content using Google Cloud AI services — making large video libraries semantically searchable.

Built for enterprise media workflows where content teams need to find specific moments across hundreds of videos without manual tagging.

---

## What It Does

When a video is uploaded to a GCS bucket, an Eventarc trigger fires the backend pipeline automatically:

1. **Transcribes** audio with word-level timestamps using Video Intelligence API
2. **Chapters** the video using Gemini 2.5 Pro — grouping content by theme, ignoring broadcast interruptions like commercial breaks
3. **Chunks and embeds** each chapter's transcript using `text-embedding-005` (768 dimensions, 150-word chunks with exact timestamps stored per chunk)
4. **Identifies highlights** — Gemini multimodally analyzes the video to surface 3–8 memorable moments with labels and reasons
5. **Stores** everything in BigQuery and Vertex AI Vector Search

The Streamlit frontend gives users two tabs:

- **Semantic Search** — type a concept, get the most relevant passages across all videos, click to watch the exact moment
- **Highlights** — select any video and browse its AI-identified memorable moments with one-click playback

---

## Architecture

```
GCS Upload
    │
    ▼ (Eventarc trigger)
Flask Backend (Cloud Run)
    ├── Video Intelligence API  →  word-level transcript with timestamps
    ├── Gemini 2.5 Pro          →  chapters + summaries
    ├── text-embedding-005      →  150-word chunk embeddings
    ├── Gemini 2.5 Pro          →  memorable moment identification (multimodal)
    ├── BigQuery                →  chapters / chunks / words / moments tables
    └── Vertex AI Vector Search →  chunk embedding index

Streamlit Frontend (Cloud Run)
    ├── Tab 1: Semantic Search
    │     ├── text-embedding-005  →  embed the query
    │     ├── Vector Search       →  find nearest chunks
    │     ├── keyword re-ranking  →  surface best matches first
    │     └── BigQuery            →  fetch metadata + exact cue timestamps
    └── Tab 2: Highlights
          └── BigQuery            →  fetch moments for selected video
```

---

## Prerequisites

- GCP project with billing enabled
- APIs enabled: `videointelligence`, `aiplatform`, `bigquery`, `storage`, `run`, `artifactregistry`, `eventarc`
- Vertex AI Vector Search index — **768 dimensions, cosine distance** — with a deployed endpoint
- BigQuery dataset with four tables (DDL below)
- A GCS bucket for video uploads

---

## BigQuery Table Setup

```sql
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT.YOUR_DATASET.video_chapters_v2` (
  source_video_uri    STRING  NOT NULL,
  chapter_number      INTEGER NOT NULL,
  title               STRING,
  summary             STRING,
  start_time_seconds  FLOAT64,
  end_time_seconds    FLOAT64
);

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT.YOUR_DATASET.video_chunks_v2` (
  chunk_id                  STRING  NOT NULL,
  source_video_uri          STRING  NOT NULL,
  chapter_number            INTEGER,
  chunk_number              INTEGER,
  chunk_text                STRING,
  chunk_start_time_seconds  FLOAT64,
  chunk_end_time_seconds    FLOAT64
);

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT.YOUR_DATASET.video_transcript_words_v2` (
  source_video_uri    STRING  NOT NULL,
  word                STRING,
  start_time_seconds  FLOAT64,
  end_time_seconds    FLOAT64
);

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT.YOUR_DATASET.video_moments_v2` (
  source_video_uri    STRING  NOT NULL,
  moment_id           STRING  NOT NULL,
  label               STRING,
  reason              STRING,
  start_time_seconds  FLOAT64,
  end_time_seconds    FLOAT64
);
```

---

## Environment Variables

| Variable | Used by | Description |
|---|---|---|
| `GCP_PROJECT` | Both | GCP project ID |
| `GCP_LOCATION` | Both | Region, e.g. `us-central1` |
| `BIGQUERY_DATASET` | Both | BigQuery dataset name |
| `VECTOR_SEARCH_INDEX_ENDPOINT` | Both | Full resource name of the index endpoint |
| `VECTOR_SEARCH_INDEX_ID` | Backend | Full resource name of the index |
| `VECTOR_SEARCH_DEPLOYED_INDEX_ID` | Both | Deployed index ID |
| `SIGNING_SERVICE_ACCOUNT` | Frontend (local only) | Service account email for signing GCS video URLs |

---

## Running Locally

**Backend**

```bash
cd backend
pip install -r requirements.txt

export GCP_PROJECT=your-project-id
export GCP_LOCATION=us-central1
export BIGQUERY_DATASET=your-dataset
export VECTOR_SEARCH_INDEX_ENDPOINT=projects/PROJECT_NUM/locations/LOCATION/indexEndpoints/ENDPOINT_ID
export VECTOR_SEARCH_INDEX_ID=projects/PROJECT_NUM/locations/LOCATION/indexes/INDEX_ID
export VECTOR_SEARCH_DEPLOYED_INDEX_ID=your-deployed-index-id

python main.py
# Listening on http://localhost:8080
```

Trigger the pipeline manually (simulates a GCS upload event):

```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"bucket": "your-bucket-name", "name": "your-video.mp4"}'
```

**Frontend**

```bash
cd frontend
pip install -r requirements.txt

export GCP_PROJECT=your-project-id
export BIGQUERY_DATASET=your-dataset
export VECTOR_SEARCH_INDEX_ENDPOINT=...
export VECTOR_SEARCH_DEPLOYED_INDEX_ID=...
export SIGNING_SERVICE_ACCOUNT=your-sa@your-project.iam.gserviceaccount.com

streamlit run app_v2.py
# Open http://localhost:8501
```

> **Local video preview** requires your user account to have `roles/iam.serviceAccountTokenCreator` on the signing service account:
> ```bash
> gcloud iam service-accounts add-iam-policy-binding \
>   your-sa@your-project.iam.gserviceaccount.com \
>   --member="user:your-email@gmail.com" \
>   --role="roles/iam.serviceAccountTokenCreator"
> ```

---

## Deploying to Cloud Run

> **Apple Silicon Mac:** Always build with `--platform linux/amd64 --provenance=false` to avoid OCI image index errors on Cloud Run.

### One-time setup

**Enable APIs:**
```bash
gcloud services enable \
  run.googleapis.com artifactregistry.googleapis.com \
  videointelligence.googleapis.com aiplatform.googleapis.com \
  bigquery.googleapis.com storage.googleapis.com \
  iam.googleapis.com eventarc.googleapis.com
```

**Create Artifact Registry repo:**
```bash
gcloud artifacts repositories create video-pipeline \
  --repository-format=docker \
  --location=us-central1

gcloud auth configure-docker us-central1-docker.pkg.dev
```

**Create service accounts:**
```bash
# Backend SA
gcloud iam service-accounts create video-pipeline-backend-sa

for role in roles/aiplatform.user roles/bigquery.dataEditor \
  roles/bigquery.jobUser roles/storage.objectAdmin \
  roles/artifactregistry.reader roles/eventarc.eventReceiver; do
  gcloud projects add-iam-policy-binding YOUR_PROJECT \
    --member="serviceAccount:video-pipeline-backend-sa@YOUR_PROJECT.iam.gserviceaccount.com" \
    --role="$role"
done

# Frontend SA
gcloud iam service-accounts create video-pipeline-frontend-sa

for role in roles/bigquery.dataViewer roles/bigquery.jobUser \
  roles/storage.objectViewer roles/iam.serviceAccountTokenCreator \
  roles/aiplatform.user roles/artifactregistry.reader; do
  gcloud projects add-iam-policy-binding YOUR_PROJECT \
    --member="serviceAccount:video-pipeline-frontend-sa@YOUR_PROJECT.iam.gserviceaccount.com" \
    --role="$role"
done

# Allow your user to attach the frontend SA to Cloud Run
gcloud iam service-accounts add-iam-policy-binding \
  video-pipeline-frontend-sa@YOUR_PROJECT.iam.gserviceaccount.com \
  --member="user:your-email@example.com" \
  --role="roles/iam.serviceAccountUser"
```

### Build and deploy backend

```bash
# From repo root
docker buildx build \
  --platform linux/amd64 --provenance=false \
  -f backend/Dockerfile \
  -t us-central1-docker.pkg.dev/YOUR_PROJECT/video-pipeline/backend:latest \
  --push .

gcloud run deploy video-pipeline-backend \
  --image=us-central1-docker.pkg.dev/YOUR_PROJECT/video-pipeline/backend:latest \
  --region=us-central1 \
  --service-account=video-pipeline-backend-sa@YOUR_PROJECT.iam.gserviceaccount.com \
  --no-allow-unauthenticated \
  --timeout=3600 \
  --memory=2Gi \
  --set-env-vars="GCP_PROJECT=YOUR_PROJECT,GCP_LOCATION=us-central1,\
BIGQUERY_DATASET=YOUR_DATASET,\
VECTOR_SEARCH_INDEX_ENDPOINT=...,\
VECTOR_SEARCH_INDEX_ID=...,\
VECTOR_SEARCH_DEPLOYED_INDEX_ID=..."
```

### Build and deploy frontend

```bash
cd frontend

docker buildx build \
  --platform linux/amd64 --provenance=false \
  -t us-central1-docker.pkg.dev/YOUR_PROJECT/video-pipeline/frontend:latest \
  --push .

gcloud run deploy video-pipeline-frontend \
  --image=us-central1-docker.pkg.dev/YOUR_PROJECT/video-pipeline/frontend:latest \
  --region=us-central1 \
  --service-account=video-pipeline-frontend-sa@YOUR_PROJECT.iam.gserviceaccount.com \
  --allow-unauthenticated \
  --memory=1Gi \
  --set-env-vars="GCP_PROJECT=YOUR_PROJECT,BIGQUERY_DATASET=YOUR_DATASET,\
VECTOR_SEARCH_INDEX_ENDPOINT=...,\
VECTOR_SEARCH_DEPLOYED_INDEX_ID=..."
```

### Set up the Eventarc trigger (automatic GCS → pipeline)

```bash
# Allow GCS to publish events
GCS_SA="service-$(gcloud projects describe YOUR_PROJECT \
  --format='value(projectNumber)')@gs-project-accounts.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding YOUR_PROJECT \
  --member="serviceAccount:${GCS_SA}" \
  --role="roles/pubsub.publisher"

# Allow Pub/Sub to create auth tokens
PUBSUB_SA="service-$(gcloud projects describe YOUR_PROJECT \
  --format='value(projectNumber)')@gcp-sa-pubsub.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding YOUR_PROJECT \
  --member="serviceAccount:${PUBSUB_SA}" \
  --role="roles/iam.serviceAccountTokenCreator"

# Allow backend SA to invoke the backend service
gcloud run services add-iam-policy-binding video-pipeline-backend \
  --region=us-central1 \
  --member="serviceAccount:video-pipeline-backend-sa@YOUR_PROJECT.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# Create the trigger
gcloud eventarc triggers create video-upload-trigger \
  --location=us-central1 \
  --destination-run-service=video-pipeline-backend \
  --destination-run-region=us-central1 \
  --event-filters="type=google.cloud.storage.object.v1.finalized" \
  --event-filters="bucket=YOUR_BUCKET_NAME" \
  --service-account="video-pipeline-backend-sa@YOUR_PROJECT.iam.gserviceaccount.com"
```

Once the trigger is created, uploading any video to the root of the bucket starts the pipeline automatically.

### Test

```bash
gsutil cp your-video.mp4 gs://YOUR_BUCKET_NAME/

# Watch pipeline logs
gcloud run services logs read video-pipeline-backend \
  --region=us-central1 --limit=50
```

---

## Project Structure

```
gcp-video-ai-pipeline/
├── backend/
│   ├── main.py                       # Flask app + pipeline orchestration
│   ├── video_intelligence_util_v2.py # Transcription via Video Intelligence API
│   ├── gemini_util_v2.py             # Chapter generation + highlight identification (Gemini 2.5 Pro)
│   ├── embedding_util_v2.py          # Batch embeddings via text-embedding-005
│   ├── bigquery_util_v2.py           # BigQuery read/write helpers
│   ├── vector_search_util.py         # Vertex AI Vector Search upsert + query
│   ├── requirements.txt
│   └── Dockerfile
└── frontend/
    ├── app_v2.py                     # Streamlit UI — search tab + highlights tab
    ├── embedding_util_v2.py          # Query embedding generation
    ├── vector_search_util.py         # Neighbor lookup
    ├── requirements.txt
    └── Dockerfile
```
