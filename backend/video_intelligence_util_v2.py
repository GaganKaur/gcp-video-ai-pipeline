from google.cloud import videointelligence_v1 as videointelligence

def transcribe_video(gcs_uri: str):
    """
    Analyzes a video in GCS and transcribes the audio using the modern client.
    """
    print(f"Starting transcription for video: {gcs_uri}")

    with videointelligence.VideoIntelligenceServiceClient() as client:
        features = [videointelligence.Feature.SPEECH_TRANSCRIPTION]

        speech_config = videointelligence.SpeechTranscriptionConfig(
            language_code="en-US",
            enable_automatic_punctuation=True,
        )

        video_context = videointelligence.VideoContext(
            speech_transcription_config=speech_config
        )

        request = videointelligence.AnnotateVideoRequest(
            input_uri=gcs_uri,
            features=features,
            video_context=video_context,
        )

        print("Submitting video transcription request to API...")
        operation = client.annotate_video(request=request)

        print("Waiting for transcription to complete (this can take several minutes)...")
        response = operation.result(timeout=1800) # 30 minute timeout
        print("Transcription complete.")

        full_transcript = ""
        transcript_words = []

        for result in response.annotation_results:
            speech_transcriptions = result.speech_transcriptions
            if not speech_transcriptions:
                continue

            for transcription in speech_transcriptions:
                if not transcription.alternatives: continue
                
                best_alternative = transcription.alternatives[0]
                full_transcript += best_alternative.transcript
                
                for word_info in best_alternative.words:
                    start_time = word_info.start_time
                    end_time = word_info.end_time
                    transcript_words.append({
                        "word": word_info.word,
                        "start_time_seconds": start_time.total_seconds(),
                        "end_time_seconds": end_time.total_seconds(),
                    })

        return full_transcript, transcript_words