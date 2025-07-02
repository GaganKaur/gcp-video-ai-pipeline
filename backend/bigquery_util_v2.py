from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

def delete_existing_chapters(project_id, dataset_id, table_id, video_uri):
    """Deletes rows from a BigQuery table that match a specific video URI."""
    client = bigquery.Client(project=project_id)
    query = f"""
        DELETE FROM `{project_id}.{dataset_id}.{table_id}`
        WHERE source_video_uri = @video_uri
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("video_uri", "STRING", video_uri),
        ]
    )
    try:
        query_job = client.query(query, job_config=job_config)
        query_job.result() 
        print(f"Deleted existing chapters for {video_uri} from {table_id}.")
    except GoogleAPICallError as e:
        print(f"Could not delete existing chapters for {video_uri}. This might be the first run. Error: {e}")

def save_chapters_to_bigquery(project_id, dataset_id, table_id, chapters_data):
    """Saves chapter data to the specified BigQuery table."""
    client = bigquery.Client(project=project_id)
    full_table_id = f"{project_id}.{dataset_id}.{table_id}"
    
    try:
        errors = client.insert_rows_json(full_table_id, chapters_data)
        if not errors:
            print(f"Successfully inserted {len(chapters_data)} chapters into {full_table_id}")
        else:
            print(f"Encountered errors while inserting chapters: {errors}")
    except Exception as e:
        print(f"A BigQuery error occurred while saving chapters: {e}")


def save_transcript_words(project_id, dataset_id, table_id, words_data):
    """
    Saves word-level transcript data to BigQuery in batches to avoid timeouts.
    """
    client = bigquery.Client(project=project_id)
    full_table_id = f"{project_id}.{dataset_id}.{table_id}"
    
    BATCH_SIZE = 500
    
    print(f"Preparing to insert {len(words_data)} words into {full_table_id} in batches of {BATCH_SIZE}...")
    
    # Loop through the data in chunks of BATCH_SIZE
    for i in range(0, len(words_data), BATCH_SIZE):
        batch = words_data[i:i + BATCH_SIZE]
        print(f"Inserting batch {i // BATCH_SIZE + 1}...")
        
        try:
            errors = client.insert_rows_json(full_table_id, batch)
            if not errors:
                print(f"Successfully inserted batch of {len(batch)} words.")
            else:
                print(f"Encountered errors while inserting a batch of words: {errors}")
        except Exception as e:
            print(f"A critical BigQuery error occurred during batch insert: {e}")
            
    
    print("Finished inserting all word data.")

def save_chunks_to_bigquery(project_id, dataset_id, table_id, chunks_data):
    """Saves chunk data to the specified BigQuery table in batches."""
    client = bigquery.Client(project=project_id)
    full_table_id = f"{project_id}.{dataset_id}.{table_id}"
    
    BATCH_SIZE = 400 
    
    for i in range(0, len(chunks_data), BATCH_SIZE):
        batch = chunks_data[i:i + BATCH_SIZE]
        try:
            errors = client.insert_rows_json(full_table_id, batch)
            if not errors:
                print(f"Successfully inserted batch of {len(batch)} chunks.")
            else:
                print(f"Encountered errors while inserting chunks: {errors}")
        except Exception as e:
            print(f"A critical BigQuery error occurred during chunk insert: {e}")