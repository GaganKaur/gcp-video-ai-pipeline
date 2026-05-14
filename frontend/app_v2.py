import os 
import streamlit as st
import pandas as pd
from google.cloud import bigquery
import google.auth
import google.auth.transport.requests
from datetime import timedelta

try:
    from embedding_util_v2 import generate_embedding
    from vector_search_util import find_neighbors
except ImportError as e:
    st.error(f"Failed to import utility functions. Please ensure 'embedding_util_v2.py' and 'vector_search_util.py' are in the same directory as the Streamlit app. Details: {e}")
    st.stop()

# --- Constants: Pointing to all V2 tables and resources ---
GCP_PROJECT = os.environ.get("GCP_PROJECT", "yahoo-editorial")
BIGQUERY_DATASET = os.environ.get("BIGQUERY_DATASET", "yfinance")
BIGQUERY_CHAPTERS_TABLE_V2 = f"{GCP_PROJECT}.{BIGQUERY_DATASET}.video_chapters_v2"
BIGQUERY_CHUNKS_TABLE_V2 = f"{GCP_PROJECT}.{BIGQUERY_DATASET}.video_chunks_v2"
BIGQUERY_WORDS_TABLE_V2 = f"{GCP_PROJECT}.{BIGQUERY_DATASET}.video_transcript_words_v2"


# --- Page Configuration & BQ Client ---
st.set_page_config(page_title="AI Content Intelligence Engine v2", layout="wide")
st.title("🎬 Yahoo's AI Content Intelligence Engine")
st.markdown("This solution uses a dedicated Vector Search index for faster and accurate results.")


@st.cache_resource
def get_bq_client():
    """Initializes and caches a BigQuery client."""
    try:
        return bigquery.Client(project=GCP_PROJECT)
    except Exception as e:
        st.error(f"Fatal Error: Could not authenticate with Google Cloud. Details: {e}")
        return None

@st.cache_data(ttl=3600)
def get_signed_video_url(gcs_uri: str):
    """
    Generates a temporary, public HTTPS URL for a private GCS video file
    by explicitly using an access token for signing. This is the correct method
    for environments without a local private key, like Cloud Run.
    """
    from google.cloud import storage 
    try:
        creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        auth_req = google.auth.transport.requests.Request()
        creds.refresh(auth_req)
        storage_client = storage.Client(credentials=creds)
        bucket_name, blob_name = gcs_uri.replace("gs://", "").split("/", 1)
        blob = storage_client.bucket(bucket_name).blob(blob_name)
        # Service account creds (Cloud Run) have this attribute; user ADC creds don't.
        sa_email = getattr(creds, 'service_account_email', None) or os.environ.get("SIGNING_SERVICE_ACCOUNT")
        if not sa_email:
            st.warning("Set the SIGNING_SERVICE_ACCOUNT env var to your service account email to enable video preview when running locally.")
            return None
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=1),
            method="GET",
            service_account_email=sa_email,
            access_token=creds.token
        )
        return url
    except Exception as e:
        st.error(f"Could not generate signed URL. Error: {e}")
        return None

# --- Main Application Logic ---
client = get_bq_client()
if client:
    # --- SEARCH FEATURE ---
    st.subheader("🔎 Semantic Search Across All Videos")

    def clear_search_results():
        """Callback to clear old video players from session state."""
        for key in list(st.session_state.keys()):
            if key.startswith('url_') or key.startswith('start_time_'):
                del st.session_state[key]
    
    search_query = st.text_input(
        "Search for a concept...", 
        key="search_input",
        on_change=clear_search_results
    )

    if search_query:
        with st.spinner(f"Searching for '{search_query}'..."):
            try:
                # Step 1: Generate an embedding for the user's query
                query_embedding = generate_embedding(search_query)
                if not query_embedding:
                    st.error("Could not generate an embedding for the search query.")
                    st.stop()

                # Step 2: Find the nearest neighbors (most similar chunks) in Vector Search
                potential_neighbors = find_neighbors(query_embedding, num_neighbors=5)
            
                if not potential_neighbors:
                    st.warning("No relevant moments found.")
                    st.stop()

                RELEVANCE_THRESHOLD = 0.75 

                good_neighbors = [
                neighbor for neighbor in potential_neighbors 
                if neighbor.distance <= RELEVANCE_THRESHOLD
                ]

                # Extract the unique IDs of the matched chunks. The order is important.
                matched_chunk_ids = [neighbor.id for neighbor in good_neighbors]
                
                # Step 3: Use the retrieved IDs to get full metadata from BigQuery
                query = f"""
                    SELECT
                        chunks.chunk_id,
                        chunks.source_video_uri,
                        chunks.chapter_number,
                        chunks.chunk_text AS matched_chunk,
                        chapters.title,
                        chapters.start_time_seconds AS chapter_start_time,
                        -- Subquery to find the precise start time of the matched chunk
                        (SELECT MIN(w.start_time_seconds)
                         FROM `{BIGQUERY_WORDS_TABLE_V2}` w
                         WHERE w.source_video_uri = chunks.source_video_uri
                           AND STRPOS(chunks.chunk_text, w.word) = 1
                           AND w.start_time_seconds >= chapters.start_time_seconds
                           AND w.start_time_seconds < chapters.end_time_seconds
                        ) as chunk_start_time
                    FROM `{BIGQUERY_CHUNKS_TABLE_V2}` AS chunks
                    JOIN `{BIGQUERY_CHAPTERS_TABLE_V2}` AS chapters
                      ON chunks.source_video_uri = chapters.source_video_uri
                      AND chunks.chapter_number = chapters.chapter_number
                    WHERE chunks.chunk_id IN UNNEST(@chunk_ids)
                """
                
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ArrayQueryParameter("chunk_ids", "STRING", matched_chunk_ids),
                    ]
                )
                search_results_df = client.query(query, job_config=job_config).to_dataframe()

                # Preserve the relevance order returned by Vector Search
                search_results_df = search_results_df.assign(
                    chunk_id=lambda df: pd.Categorical(df['chunk_id'], categories=matched_chunk_ids, ordered=True)
                ).sort_values('chunk_id')

                st.success(f"Found {len(search_results_df)} relevant passages for '{search_query}'")

                # Display the results
                for index, row in search_results_df.iterrows():
                    st.markdown(f"**Chapter {row['chapter_number']}: {row['title']}**")
                    st.markdown(f"*{row['source_video_uri'].split('/')[-1]}*")
                    st.info(f"**Relevant Passage:** ...{row['matched_chunk']}...")

                    with st.expander("Show Relevant Video Moment"):
                        button_key = f"preview_{index}_{search_query}"
                        if st.button("Cue Video to Relevant Moment", key=button_key):
                            # Use the precise chunk start time if available
                            start_time = row.get('chunk_start_time', row['chapter_start_time'])
                            st.session_state[f'start_time_{index}'] = start_time if pd.notna(start_time) else row['chapter_start_time']
                            st.session_state[f'url_{index}'] = get_signed_video_url(row['source_video_uri'])

                    if f'url_{index}' in st.session_state and st.session_state[f'url_{index}']:
                    # --- Place video in a column to control its size ---
                        col1, col2 = st.columns([1, 2])
                        with col1:
                            st.video(st.session_state[f'url_{index}'], start_time=int(st.session_state.get(f'start_time_{index}', 0)))
                    st.markdown("---")

            except Exception as e:
                st.error(f"An error occurred during search: {e}")

    # --- BROWSE FEATURE ---
    st.subheader("📖 Browse Chapters by a Specific Video")
    
    @st.cache_data(ttl=300)
    def get_processed_video_list():
        # Query the V2 chapters table
        query = f"""
            SELECT DISTINCT source_video_uri 
            FROM `{BIGQUERY_CHAPTERS_TABLE_V2}` 
            WHERE source_video_uri LIKE '%/processed/%'
            ORDER BY source_video_uri DESC
        """
        try:
            query_job = client.query(query)
            return {row.source_video_uri.split('/')[-1]: row.source_video_uri for row in query_job}
        except Exception as e:
            st.warning(f"Could not fetch video list: {e}")
            return {}

    video_map = get_processed_video_list()
    if not video_map:
        st.info("No V2 processed videos found yet.")
    else:
        selected_display_name = st.selectbox("Select a video to view its chapters:", video_map.keys(), key="browse_select")
        if selected_display_name:
            selected_video_uri = video_map[selected_display_name]
            with st.spinner(f"Loading chapters for {selected_display_name}..."):
                browse_query = f"""
                    SELECT chapter_number, title, summary, start_time_seconds, end_time_seconds
                    FROM `{BIGQUERY_CHAPTERS_TABLE_V2}`
                    WHERE source_video_uri = @video_uri ORDER BY chapter_number ASC
                """
                job_config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("video_uri", "STRING", selected_video_uri)])
                try:
                    browse_results_df = client.query(browse_query, job_config=job_config).to_dataframe()
                    st.dataframe(browse_results_df, width='stretch', hide_index=True)
                except Exception as e:
                    st.error(f"Failed to fetch chapters: {e}")
