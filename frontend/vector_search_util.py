from google.cloud import aiplatform
import os

# --- Constants ---
GCP_PROJECT = os.environ.get("GCP_PROJECT")
GCP_LOCATION = os.environ.get("GCP_LOCATION")

INDEX_ENDPOINT_NAME = os.environ.get("VECTOR_SEARCH_INDEX_ENDPOINT")
INDEX_ID = os.environ.get("VECTOR_SEARCH_INDEX_ID") 
DEPLOYED_INDEX_ID = os.environ.get("VECTOR_SEARCH_DEPLOYED_INDEX_ID")

# --- Helper functions to get the objects ---
_index_endpoint = None
_index = None

def get_index_endpoint():
    global _index_endpoint
    if _index_endpoint is None:
        _index_endpoint = aiplatform.MatchingEngineIndexEndpoint(index_endpoint_name=INDEX_ENDPOINT_NAME)
    return _index_endpoint

def get_index():
    global _index
    if _index is None:
        _index = aiplatform.MatchingEngineIndex(index_name=INDEX_ID)
    return _index

# --- Upsert Function ---
def upsert_data_to_vector_search(datapoints: list):
    """
    Uploads or updates data points (embeddings) to the Vector Search index
    using the correct 'upsert_datapoints' method.
    """
    print(f"Upserting {len(datapoints)} datapoints to Vector Search index...")
    try:
        index = get_index()
        index.upsert_datapoints(datapoints=datapoints)
        
        print("Upsert successful.")
        return True
    except Exception as e:
        print(f"Error upserting data to Vector Search: {e}")
        return False

# --- Find Neighbors Function ---
def find_neighbors(query_embedding: list, num_neighbors: int = 10) -> list:
    """
    Performs a similarity search on the Vector Search index.
    This function correctly uses the ENDPOINT object.
    """
    print("Finding neighbors in Vector Search...")
    try:
        index_endpoint = get_index_endpoint()
        response = index_endpoint.find_neighbors(
            queries=[query_embedding],
            deployed_index_id=DEPLOYED_INDEX_ID, 
            num_neighbors=num_neighbors
        )
        return response[0] if response else []
    except Exception as e:
        print(f"Error finding neighbors: {e}")
        return []