# GCP Video AI Pipeline

An end-to-end pipeline that automatically transcribes, chapters, and indexes video content using Google Cloud AI services — making large video libraries semantically searchable.

Built for enterprise media workflows where content teams need to find specific moments across hundreds of videos without manual tagging.

---

## What It Does

When a video is uploaded to a GCS bucket, the backend pipeline triggers automatically and:

1. **Transcribes** the audio with word-level timestamps using Video Intelligence API
2. **Chapters** the video using Gemini 2.5 Pro — grouping content by theme and ignoring broadcast interruptions like commercial breaks
3. **Chunks and embeds** each chapter's transcript using `text-embedding-005` (768 dimensions)
4. **Stores** chapters, chunks, and word timestamps in BigQuery; embeddings in Vertex AI Vector Search

The Streamlit frontend then lets users:
- **Semantic search** across all processed videos — find a concept, get the exact moment, watch the clip
- **Browse** any video's AI-generated chapter breakdown

---

## Architecture

```
GCS Upload
    │
    ▼
Flask Backend (Cloud Run)
    ├── Video Intelligence API  →  word-level transcript
    ├── Gemini 2.5 Pro          →  chapters + summaries
    ├── text-embedding-005      →  chunk embeddings
    ├── BigQuery                →  chapters / chunks / words tables
    └── Vertex AI Vector Search →  embedding index

Streamlit Frontend (Cloud Run)
    ├── Vector Search           →  find nearest chunks to query
    └── BigQuery                →  fetch metadata for matched chunks
```

---

## Prerequisites

- GCP project with billing enabled
- APIs enabled: Video Intelligence, Vertex AI, BigQuery, Cloud Storage, Cloud Run, Artifact Registry
- Vertex AI Vector Search index (768 dimensions, cosine distance) with a deployed endpoint
- BigQuery dataset with three tables (DDL below)
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
  chunk_id            STRING  NOT NULL,
  source_video_uri    STRING  NOT NULL,
  chapter_number      INTEGER,
  chunk_number        INTEGER,
  chunk_text          STRING
);

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT.YOUR_DATASET.video_transcript_words_v2` (
  source_video_uri    STRING  NOT NULL,
  word                STRING,
  start_time_seconds  FLOAT64,
  end_time_seconds    FLOAT64
);
```

---

## Environment Variables

Both services share most of the same variables.

| Variable | Description |
|---|---|
| `GCP_PROJECT` | Your GCP project ID |
| `GCP_LOCATION` | Region, e.g. `us-central1` |
| `BIGQUERY_DATASET` | BigQuery dataset name |
| `VECTOR_SEARCH_INDEX_ENDPOINT` | Full resource name of the index endpoint |
| `VECTOR_SEARCH_INDEX_ID` | Full resource name of the index |
| `VECTOR_SEARCH_DEPLOYED_INDEX_ID` | Deployed index ID |
| `SIGNING_SERVICE_ACCOUNT` | *(Local only)* Service account email for signing GCS video URLs |

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

> **Note for local video preview:** Your user account needs `roles/iam.serviceAccountTokenCreator` on the signing service account:
> ```bash
> gcloud iam service-accounts add-iam-policy-binding \
>   your-sa@your-project.iam.gserviceaccount.com \
>   --member="user:your-email@gmail.com" \
>   --role="roles/iam.serviceAccountTokenCreator"
> ```

---

## Deploying to Cloud Run

**Authenticate:**

```bash
gcloud auth login
gcloud config set project your-project-id
```

**Backend:**

```bash
# From repo root
docker build -f backend/Dockerfile -t gcr.io/YOUR_PROJECT/video-pipeline-backend .
docker push gcr.io/YOUR_PROJECT/video-pipeline-backend

gcloud run deploy video-pipeline-backend \
  --image gcr.io/YOUR_PROJECT/video-pipeline-backend \
  --region us-central1 \
  --no-allow-unauthenticated \
  --timeout 3600 \
  --set-env-vars GCP_PROJECT=YOUR_PROJECT,GCP_LOCATION=us-central1,BIGQUERY_DATASET=YOUR_DATASET,\
VECTOR_SEARCH_INDEX_ENDPOINT=...,VECTOR_SEARCH_INDEX_ID=...,VECTOR_SEARCH_DEPLOYED_INDEX_ID=...
```

**Frontend:**

```bash
cd frontend
docker build -t gcr.io/YOUR_PROJECT/video-pipeline-frontend .
docker push gcr.io/YOUR_PROJECT/video-pipeline-frontend

gcloud run deploy video-pipeline-frontend \
  --image gcr.io/YOUR_PROJECT/video-pipeline-frontend \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GCP_PROJECT=YOUR_PROJECT,BIGQUERY_DATASET=YOUR_DATASET,\
VECTOR_SEARCH_INDEX_ENDPOINT=...,VECTOR_SEARCH_DEPLOYED_INDEX_ID=...
```

> On Cloud Run the service account attached to the revision is used automatically — `SIGNING_SERVICE_ACCOUNT` is not needed.

---

## Project Structure

```
gcp-video-ai-pipeline/
├── backend/
│   ├── main.py                      # Flask app + pipeline orchestration
│   ├── video_intelligence_util_v2.py # Transcription via Video Intelligence API
│   ├── gemini_util_v2.py            # Chapter generation via Gemini 2.5 Pro
│   ├── embedding_util_v2.py         # Batch embeddings via text-embedding-005
│   ├── bigquery_util_v2.py          # BigQuery read/write helpers
│   ├── vector_search_util.py        # Vertex AI Vector Search upsert + query
│   ├── requirements.txt
│   └── Dockerfile
└── frontend/
    ├── app_v2.py                    # Streamlit search + browse UI
    ├── embedding_util_v2.py         # Query embedding generation
    ├── vector_search_util.py        # Neighbor lookup
    ├── requirements.txt
    └── Dockerfile
```
