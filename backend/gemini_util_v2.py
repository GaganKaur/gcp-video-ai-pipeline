import json
import vertexai
from vertexai.generative_models import GenerativeModel, Part, GenerationConfig
import os  

_is_vertex_initialized = False

def init_vertex_ai():
    """Initializes the Vertex AI SDK. Ensures it only runs once."""
    global _is_vertex_initialized
    if not _is_vertex_initialized:
        print("Initializing Vertex AI SDK for Gemini...")
        GCP_PROJECT = os.environ.get("GCP_PROJECT")
        GCP_LOCATION = os.environ.get("GCP_LOCATION")
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
        _is_vertex_initialized = True

def generate_consolidated_chapters(project_id, location, full_transcript_with_timestamps: str):
    """
    Uses the stable vertexai SDK to generate consolidated chapters.
    """
    try:
        init_vertex_ai() # Ensure SDK is initialized
        
        model = GenerativeModel("gemini-2.5-pro")

        chapter_schema = {
            "type": "ARRAY", "items": { "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "summary": {"type": "STRING"},
                    "start_time": {"type": "NUMBER"},
                    "end_time": {"type": "NUMBER"},
                }, "required": ["title", "summary", "start_time", "end_time"]
            }
        }
        prompt = f"""
        You are a world-class video editor and content strategist with exceptional narrative sense. Your task is to analyze the following video transcript and divide it into a logical series of broad, thematically-cohesive chapters.

        **Core Principle 1: Thematic Consolidation**
        Your primary goal is to identify an overarching theme or strategic narrative and group all related parts of the conversation under it. A new chapter should only begin when there is a significant pivot to a new, distinct narrative.

        **Core Principle 2: Ignore Broadcast-Specific Interruptions**
        News broadcasts contain structural elements like commercial breaks, teases for upcoming segments, and re-introductions. You MUST recognize and ignore these. If a discussion on a single topic is interrupted by a commercial break and then resumes, you must treat it as one continuous chapter.

        **CRITICAL EXAMPLE of what to do:**
        Imagine the transcript discusses Elf Beauty's acquisition of the Rhode brand, covering both the $1B price and the future plans for Sephora. You must consolidate these into ONE chapter.
        - **Correct Title:** "Elf Beauty's Strategic Acquisition and Vision for Rhode"
        - **Correct Summary:** "Elf Beauty's CFO discusses the company's largest-ever acquisition of Hailey Bieber's brand, Rhode, a strategic play to expand into skincare. The conversation covers the $1B valuation and future plans to launch the brand in Sephora as part of a larger vision to build a 'different kind of beauty company'."

        Now, apply these core principles to the following transcript. Your output must be a valid JSON array conforming to the schema.

        **Full Transcript with Timestamps:**
        ---
        {full_transcript_with_timestamps}
        ---
        """

        print("Generating consolidated chapters with stable vertexai SDK...")
        
        response = model.generate_content(
            [Part.from_text(prompt)],
            generation_config=GenerationConfig(
                temperature=0.2,
                response_mime_type="application/json",
                response_schema=chapter_schema,
            )
        )
        
        chapters = json.loads(response.text)
        print(f"Successfully generated and parsed {len(chapters)} consolidated chapters.")
        return chapters

    except Exception as e:
        print(f"ERROR: An exception occurred while generating chapters: {e}")
        import traceback
        traceback.print_exc()
        return []