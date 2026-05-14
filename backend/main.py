import os
import base64
import json
import re
from flask import Flask, request

from video_intelligence_util_v2 import transcribe_video
from gemini_util_v2 import generate_consolidated_chapters, identify_memorable_moments
from bigquery_util_v2 import delete_existing_chapters, save_chapters_to_bigquery, save_chunks_to_bigquery, save_transcript_words, save_moments_to_bigquery
from embedding_util_v2 import generate_embeddings_batch
from vector_search_util import upsert_data_to_vector_search
from google.cloud import storage
import google.api_core.exceptions

app = Flask(__name__)

# --- Environment Variables ---
GCP_PROJECT = os.environ.get("GCP_PROJECT")
GCP_LOCATION = os.environ.get("GCP_LOCATION")
BIGQUERY_DATASET = os.environ.get("BIGQUERY_DATASET")
CHAPTERS_TABLE_V2 = os.environ.get("CHAPTERS_TABLE_V2", "video_chapters_v2")
CHUNKS_TABLE_V2 = os.environ.get("CHUNKS_TABLE_V2", "video_chunks_v2")
WORDS_TABLE_V2 = os.environ.get("WORDS_TABLE_V2", "video_transcript_words_v2")
MOMENTS_TABLE_V2 = os.environ.get("MOMENTS_TABLE_V2", "video_moments_v2")

# --- File Handling Functions ---
def sanitize_filename(filename: str):
    base, ext = os.path.splitext(filename)
    sanitized_base = base.replace(' ', '_').replace("'", "")
    sanitized_base = re.sub(r'[^a-zA-Z0-9_.-]', '', sanitized_base)
    return f"{sanitized_base}{ext}"

def lease_file_for_processing(bucket_name, original_file_name, sanitized_file_name):
    storage_client = storage.Client()
    source_bucket = storage_client.bucket(bucket_name)
    source_blob = source_bucket.blob(original_file_name)
    new_name = f"processing/{sanitized_file_name}"
    print(f"Attempting to lease file by renaming '{original_file_name}' to: {new_name}")
    try:
        return source_bucket.rename_blob(source_blob, new_name)
    except google.api_core.exceptions.NotFound:
        return None

def archive_processed_file(processing_blob):
    bucket = processing_blob.bucket
    sanitized_file_name = processing_blob.name.split('processing/')[-1]
    archive_name = f"processed/{sanitized_file_name}"
    print(f"Archiving file to: {archive_name}")
    bucket.rename_blob(processing_blob, archive_name)

# --- Main V2 Pipeline ---
def _run_processing_pipeline_v2(event):
    bucket_name = event['bucket']
    original_file_name = event['name']

    if '/' in original_file_name:
        print(f"File '{original_file_name}' is not in root. Ignoring.")
        return

    sanitized_name = sanitize_filename(original_file_name)
    processing_blob = lease_file_for_processing(bucket_name, original_file_name, sanitized_name)
    if not processing_blob: return

    source_gcs_uri = f"gs://{bucket_name}/{processing_blob.name}"
    final_uri = f"gs://{bucket_name}/processed/{sanitized_name}"

    try:
        print(f"Deleting any stale V2 data for URI: {final_uri}")
        delete_existing_chapters(GCP_PROJECT, BIGQUERY_DATASET, CHAPTERS_TABLE_V2, final_uri)
        delete_existing_chapters(GCP_PROJECT, BIGQUERY_DATASET, CHUNKS_TABLE_V2, final_uri)
        delete_existing_chapters(GCP_PROJECT, BIGQUERY_DATASET, WORDS_TABLE_V2, final_uri)

        print("Step 1/4: Transcribing video...")
        _, transcript_words = transcribe_video(source_gcs_uri)
        if not transcript_words: raise ValueError("Transcription returned no word data.")

        print(f"Step 2/4: Saving raw transcript to BigQuery table '{WORDS_TABLE_V2}'...")
        for word in transcript_words: word['source_video_uri'] = final_uri
        save_transcript_words(GCP_PROJECT, BIGQUERY_DATASET, WORDS_TABLE_V2, transcript_words)

        
        # 1. Create the full transcript string that the powerful prompt expects
        print("Constructing full transcript string for Gemini...")
        transcript_with_timestamps = " ".join(
            [f"{word.get('word', '')}({word.get('start_time_seconds', 0)})" for word in transcript_words]
        )
        
        # 2. Call the AI to get the consolidated chapters
        print("Step 3/4: Generating consolidated chapters from full transcript...")
        chapters = generate_consolidated_chapters(GCP_PROJECT, GCP_LOCATION, transcript_with_timestamps)
        if not chapters: raise ValueError("Gemini returned no chapters.")

        chapters_for_bq, chunks_for_bq, vector_search_datapoints = [], [], []

        # 3. Loop through the chapters that the AI returned
        print("Step 4/4: Chunking and embedding text for each AI-defined chapter...")
        for i, chapter_data in enumerate(chapters):
            start_time = chapter_data.get('start_time')
            end_time = chapter_data.get('end_time')

            if start_time is None or end_time is None:
                print(f"Warning: Skipping chapter {i+1} due to missing timestamps.")
                continue
            
            chapters_for_bq.append({
                "source_video_uri": final_uri, "chapter_number": i + 1,
                "title": chapter_data.get('title'), "summary": chapter_data.get('summary'),
                "start_time_seconds": float(start_time), "end_time_seconds": float(end_time)
            })

            chapter_words = [w['word'] for w in transcript_words if start_time <= w.get('start_time_seconds', float('inf')) < end_time]
            chapter_text = " ".join(chapter_words)
            if not chapter_text.strip(): continue

            words_in_chapter = chapter_text.split()
            CHUNK_SIZE, CHUNK_OVERLAP = 50, 10
            text_chunks = [" ".join(words_in_chapter[i:i+CHUNK_SIZE]) for i in range(0, len(words_in_chapter), CHUNK_SIZE - CHUNK_OVERLAP)]
            
            if text_chunks:
                chunk_embeddings = generate_embeddings_batch(text_chunks)
                if chunk_embeddings:
                    for j, chunk in enumerate(text_chunks):
                        if j < len(chunk_embeddings) and chunk_embeddings[j]:
                            chunk_id = f"{final_uri}|{i + 1}|{j + 1}"
                            chunks_for_bq.append({"chunk_id": chunk_id, "source_video_uri": final_uri, "chapter_number": i + 1, "chunk_number": j + 1, "chunk_text": chunk})
                            vector_search_datapoints.append({
                                "datapoint_id": chunk_id,
                                "feature_vector": chunk_embeddings[j]
                            })
        

        print("Step 5/6: Identifying memorable moments...")
        delete_existing_chapters(GCP_PROJECT, BIGQUERY_DATASET, MOMENTS_TABLE_V2, final_uri)
        video_duration = max((w.get('end_time_seconds', 0) for w in transcript_words), default=0)
        moments = identify_memorable_moments(GCP_PROJECT, GCP_LOCATION, source_gcs_uri, video_duration)
        moments_for_bq = [
            {
                "source_video_uri": final_uri,
                "moment_id": f"{final_uri}|moment|{i}",
                "label": m.get('label', f"Moment {i + 1}"),
                "reason": m.get('reason', ''),
                "start_time_seconds": float(m.get('start_sec', 0)),
                "end_time_seconds": float(m.get('end_sec', 10)),
            }
            for i, m in enumerate(moments)
        ]

        print("Step 6/6: Saving all generated data...")
        if chapters_for_bq: save_chapters_to_bigquery(GCP_PROJECT, BIGQUERY_DATASET, CHAPTERS_TABLE_V2, chapters_for_bq)
        if chunks_for_bq: save_chunks_to_bigquery(GCP_PROJECT, BIGQUERY_DATASET, CHUNKS_TABLE_V2, chunks_for_bq)
        if vector_search_datapoints: upsert_data_to_vector_search(vector_search_datapoints)
        if moments_for_bq: save_moments_to_bigquery(GCP_PROJECT, BIGQUERY_DATASET, MOMENTS_TABLE_V2, moments_for_bq)

        print("Pipeline successful. Archiving file.")
        archive_processed_file(processing_blob)

    except Exception as e:
        print(f"CRITICAL ERROR during pipeline for {source_gcs_uri}. Error: {e}")
        import traceback; traceback.print_exc()
        raise

# --- Flask Entrypoint ---
@app.route("/", methods=["POST"])
def index():
    event_payload = request.get_json()
    if not event_payload or "bucket" not in event_payload or "name" not in event_payload:
        print(f"Bad Request: Invalid direct event payload. Received: {event_payload}")
        return ("Bad Request: Invalid Cloud Storage event format", 400)
    try:
        print(f"Received valid direct event for file: {event_payload.get('name')} in bucket: {event_payload.get('bucket')}")
        _run_processing_pipeline_v2(event_payload)
        return ("Processing started successfully.", 204)
    except Exception as e:
        print(f"Pipeline failed with error: {e}")
        import traceback; traceback.print_exc()
        return ("Internal Server Error", 500)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))