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

# --- Constants ---
GCP_PROJECT = os.environ.get("GCP_PROJECT", "yahoo-editorial")
BIGQUERY_DATASET = os.environ.get("BIGQUERY_DATASET", "yfinance")
BIGQUERY_CHAPTERS_TABLE_V2 = f"{GCP_PROJECT}.{BIGQUERY_DATASET}.video_chapters_v2"
BIGQUERY_CHUNKS_TABLE_V2 = f"{GCP_PROJECT}.{BIGQUERY_DATASET}.video_chunks_v2"
BIGQUERY_WORDS_TABLE_V2 = f"{GCP_PROJECT}.{BIGQUERY_DATASET}.video_transcript_words_v2"
BIGQUERY_MOMENTS_TABLE_V2 = f"{GCP_PROJECT}.{BIGQUERY_DATASET}.video_moments_v2"

# --- Page Configuration ---
st.set_page_config(page_title="AI Content Intelligence Engine v2", layout="wide")
st.title("🎬 AI Content Intelligence Engine")
st.markdown("Semantic search and AI-generated highlights powered by Vertex AI and Gemini.")


@st.cache_resource
def get_bq_client():
    try:
        return bigquery.Client(project=GCP_PROJECT)
    except Exception as e:
        st.error(f"Fatal Error: Could not authenticate with Google Cloud. Details: {e}")
        return None


@st.cache_data(ttl=3600)
def get_signed_video_url(gcs_uri: str):
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
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=1),
            method="GET",
            service_account_email=sa_email,
            access_token=creds.token
        )
    except Exception as e:
        st.error(f"Could not generate signed URL. Error: {e}")
        return None


# --- Main Application Logic ---
client = get_bq_client()
if client:
    @st.cache_data(ttl=300)
    def get_processed_video_list():
        query = f"""
            SELECT DISTINCT source_video_uri
            FROM `{BIGQUERY_CHAPTERS_TABLE_V2}`
            WHERE source_video_uri LIKE '%/processed/%'
            ORDER BY source_video_uri DESC
        """
        try:
            return {row.source_video_uri.split('/')[-1]: row.source_video_uri for row in client.query(query)}
        except Exception as e:
            st.warning(f"Could not fetch video list: {e}")
            return {}

    tab_search, tab_highlights = st.tabs(["🔎 Semantic Search", "✨ Highlights"])

    # ------------------------------------------------------------------ #
    # TAB 1: Semantic Search + Chapter Browse                             #
    # ------------------------------------------------------------------ #
    with tab_search:
        st.subheader("🔎 Semantic Search Across All Videos")

        def clear_search_results():
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
                    query_embedding = generate_embedding(search_query)
                    if not query_embedding:
                        st.error("Could not generate an embedding for the search query.")
                        st.stop()

                    potential_neighbors = find_neighbors(query_embedding, num_neighbors=10)
                    if not potential_neighbors:
                        st.warning("No relevant moments found.")
                        st.stop()

                    RELEVANCE_THRESHOLD = 0.35
                    good_neighbors = [n for n in potential_neighbors if n.distance <= RELEVANCE_THRESHOLD]
                    matched_chunk_ids = [n.id for n in good_neighbors]

                    bq_query = f"""
                        SELECT
                            chunks.chunk_id,
                            chunks.source_video_uri,
                            chunks.chapter_number,
                            chunks.chunk_text AS matched_chunk,
                            chunks.chunk_start_time_seconds AS chunk_start_time,
                            chapters.title,
                            chapters.start_time_seconds AS chapter_start_time
                        FROM `{BIGQUERY_CHUNKS_TABLE_V2}` AS chunks
                        JOIN `{BIGQUERY_CHAPTERS_TABLE_V2}` AS chapters
                          ON chunks.source_video_uri = chapters.source_video_uri
                         AND chunks.chapter_number = chapters.chapter_number
                        WHERE chunks.chunk_id IN UNNEST(@chunk_ids)
                    """
                    job_config = bigquery.QueryJobConfig(
                        query_parameters=[bigquery.ArrayQueryParameter("chunk_ids", "STRING", matched_chunk_ids)]
                    )
                    search_results_df = client.query(bq_query, job_config=job_config).to_dataframe()

                    # Re-rank: results containing all query terms surface first,
                    # then partial matches, preserving vector distance order within each tier.
                    query_terms = [t.lower() for t in search_query.split() if len(t) > 2]
                    def keyword_score(text):
                        text_lower = text.lower()
                        return sum(1 for t in query_terms if t in text_lower)

                    search_results_df = search_results_df.assign(
                        chunk_id=lambda df: pd.Categorical(df['chunk_id'], categories=matched_chunk_ids, ordered=True),
                        keyword_score=lambda df: df['matched_chunk'].apply(keyword_score)
                    ).sort_values(['keyword_score', 'chunk_id'], ascending=[False, True])

                    st.success(f"Found {len(search_results_df)} relevant passages for '{search_query}'")

                    for index, row in search_results_df.iterrows():
                        st.markdown(f"**Chapter {row['chapter_number']}: {row['title']}**")
                        st.markdown(f"*{row['source_video_uri'].split('/')[-1]}*")
                        st.info(f"**Relevant Passage:** ...{row['matched_chunk']}...")

                        with st.expander("Show Relevant Video Moment"):
                            if st.button("Cue Video to Relevant Moment", key=f"preview_{index}_{search_query}"):
                                start_time = row.get('chunk_start_time', row['chapter_start_time'])
                                st.session_state[f'start_time_{index}'] = start_time if pd.notna(start_time) else row['chapter_start_time']
                                st.session_state[f'url_{index}'] = get_signed_video_url(row['source_video_uri'])

                        if f'url_{index}' in st.session_state and st.session_state[f'url_{index}']:
                            col1, col2 = st.columns([1, 2])
                            with col1:
                                st.video(st.session_state[f'url_{index}'], start_time=int(st.session_state.get(f'start_time_{index}', 0)))
                        st.markdown("---")

                except Exception as e:
                    st.error(f"An error occurred during search: {e}")

        st.subheader("📖 Browse Chapters by a Specific Video")
        video_map = get_processed_video_list()
        if not video_map:
            st.info("No processed videos found yet.")
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
                    job_config = bigquery.QueryJobConfig(
                        query_parameters=[bigquery.ScalarQueryParameter("video_uri", "STRING", selected_video_uri)]
                    )
                    try:
                        browse_results_df = client.query(browse_query, job_config=job_config).to_dataframe()
                        st.dataframe(browse_results_df, width='stretch', hide_index=True)
                    except Exception as e:
                        st.error(f"Failed to fetch chapters: {e}")

    # ------------------------------------------------------------------ #
    # TAB 2: Highlights                                                   #
    # ------------------------------------------------------------------ #
    with tab_highlights:
        st.subheader("✨ AI-Identified Highlights")
        st.markdown("Select a video to see the moments Gemini flagged as most memorable.")

        video_map_hl = get_processed_video_list()
        if not video_map_hl:
            st.info("No processed videos found yet.")
        else:
            selected_hl = st.selectbox("Select a video:", video_map_hl.keys(), key="highlights_select")
            if selected_hl:
                selected_uri = video_map_hl[selected_hl]
                with st.spinner(f"Loading highlights for {selected_hl}..."):
                    moments_query = f"""
                        SELECT moment_id, label, reason, start_time_seconds, end_time_seconds
                        FROM `{BIGQUERY_MOMENTS_TABLE_V2}`
                        WHERE source_video_uri = @video_uri
                        ORDER BY start_time_seconds ASC
                    """
                    job_config = bigquery.QueryJobConfig(
                        query_parameters=[bigquery.ScalarQueryParameter("video_uri", "STRING", selected_uri)]
                    )
                    try:
                        moments_df = client.query(moments_query, job_config=job_config).to_dataframe()
                    except Exception as e:
                        st.error(f"Failed to fetch highlights: {e}")
                        moments_df = pd.DataFrame()

                if moments_df.empty:
                    st.info("No highlights found for this video. Re-process it to generate highlights.")
                else:
                    for _, moment in moments_df.iterrows():
                        start = int(moment['start_time_seconds'])
                        end = int(moment['end_time_seconds'])
                        st.markdown(f"### {moment['label']}")
                        st.caption(f"{start}s – {end}s")
                        st.markdown(f"*{moment['reason']}*")

                        if st.button("▶ Watch this moment", key=f"hl_{moment['moment_id']}"):
                            st.session_state[f"hl_url_{moment['moment_id']}"] = get_signed_video_url(selected_uri)
                            st.session_state[f"hl_start_{moment['moment_id']}"] = start

                        url_key = f"hl_url_{moment['moment_id']}"
                        if url_key in st.session_state and st.session_state[url_key]:
                            col1, col2 = st.columns([1, 2])
                            with col1:
                                st.video(
                                    st.session_state[url_key],
                                    start_time=st.session_state.get(f"hl_start_{moment['moment_id']}", start)
                                )
                        st.markdown("---")
