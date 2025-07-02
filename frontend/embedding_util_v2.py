from typing import List, Optional
from vertexai.language_models import TextEmbeddingModel
import vertexai
import os

_is_vertex_initialized = False

def init_vertex_ai():
    """Initializes the Vertex AI SDK. Ensures it only runs once."""
    global _is_vertex_initialized
    if not _is_vertex_initialized:
        print("Initializing Vertex AI SDK for Embeddings...")
        GCP_PROJECT = os.environ.get("GCP_PROJECT")
        GCP_LOCATION = os.environ.get("GCP_LOCATION")
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
        _is_vertex_initialized = True

MODEL_NAME = "text-embedding-005"

def generate_embeddings_batch(texts: List[str]) -> Optional[List[List[float]]]:
    """Generates embeddings for a batch of documents using the stable vertexai SDK."""
    try:
        init_vertex_ai() # Ensure SDK is initialized
        model = TextEmbeddingModel.from_pretrained(MODEL_NAME)
        
        BATCH_SIZE = 5
        all_embeddings = []

        for i in range(0, len(texts), BATCH_SIZE):
            batch_of_texts = texts[i:i + BATCH_SIZE]
            response = model.get_embeddings(batch_of_texts)
            batch_embeddings = [embedding.values for embedding in response]
            all_embeddings.extend(batch_embeddings)
        
        return all_embeddings

    except Exception as e:
        print(f"Error generating batch embedding: {e}")
        return None

def generate_embedding(text: str) -> Optional[List[float]]:
    """
    Generates an embedding for a single document.
    This is a convenience wrapper for the Streamlit app.
    """
    if not text or not text.strip():
        return None
    
    embeddings = generate_embeddings_batch([text])
    if embeddings and len(embeddings) > 0:
        return embeddings[0]
    return None