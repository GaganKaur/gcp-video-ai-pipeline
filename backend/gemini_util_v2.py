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

def identify_memorable_moments(project_id, location, gcs_uri: str, video_duration: float):
    """
    Uses Gemini multimodal to identify 3-5 highlight moments in a video.
    Returns a list of dicts with label, reason, start_sec, end_sec.
    """
    try:
        init_vertex_ai()
        model = GenerativeModel("gemini-2.5-pro")

        moment_schema = {
            "type": "ARRAY", "items": {"type": "OBJECT",
                "properties": {
                    "label":     {"type": "STRING"},
                    "reason":    {"type": "STRING"},
                    "start_sec": {"type": "NUMBER"},
                    "end_sec":   {"type": "NUMBER"},
                }, "required": ["label", "reason", "start_sec", "end_sec"]
            }
        }

        max_moments = 5 if video_duration < 1800 else 8  # more highlights for videos over 30 min
        prompt = f"""You are analyzing a video that is {video_duration:.0f} seconds long.
Identify between 3 and {max_moments} memorable highlight moments — moments that are emotionally resonant,
visually striking, or represent a key narrative turn worth rewatching.
Each moment should span 5–8 seconds.

For each moment return:
- label: a short descriptive title (e.g. "Surprise product reveal")
- reason: one sentence explaining why this moment stands out
- start_sec / end_sec: timestamps in seconds

Return only moments where something genuinely notable happens. Spread them evenly across the full video duration."""

        print(f"Identifying memorable moments in: {gcs_uri}")
        response = model.generate_content(
            [Part.from_uri(gcs_uri, mime_type="video/mp4"), Part.from_text(prompt)],
            generation_config=GenerationConfig(
                temperature=0.3,
                response_mime_type="application/json",
                response_schema=moment_schema,
            )
        )
        moments = json.loads(response.text)
        print(f"Identified {len(moments)} memorable moments.")
        return moments
    except Exception as e:
        print(f"ERROR identifying memorable moments: {e}")
        import traceback; traceback.print_exc()
        return []


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