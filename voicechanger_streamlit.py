import os
import tempfile
import subprocess
import zipfile
import io
import requests
import streamlit as st

# Streamlit config
st.set_page_config(page_title="Clipstorm Simple", layout="centered")
st.title("🎥 Clipstorm: Video Voice Conversion")

# ElevenLabs API key input
api_key = st.text_input("ElevenLabs API Key", type="password")

# Video uploader
uploaded_videos = st.file_uploader("Upload video clip(s)", type=["mp4", "mov"], accept_multiple_files=True)

if st.button("Generate with Female Voice"):
    if not api_key:
        st.error("Enter your ElevenLabs API Key.")
    elif not uploaded_videos:
        st.error("Upload at least one video.")
    else:
        output_paths = []
        tmp_dir = Path(tempfile.mkdtemp())
        for vid in uploaded_videos:
            # Save uploaded video
            in_path = tmp_dir / vid.name
            with open(in_path, "wb") as f:
                f.write(vid.getbuffer())

            # Extract audio
            audio_path = tmp_dir / f"{in_path.stem}_orig.wav"
            subprocess.run([
                "ffmpeg", "-y", "-i", str(in_path), "-vn", str(audio_path)
            ], check=True)

            # Convert voice via ElevenLabs
            convert_url = "https://api.elevenlabs.io/v1/voice/convert"
            headers = {
                "xi-api-key": api_key
            }
            files = {
                "file": open(audio_path, "rb")
            }
            data = {
                "voice": "female",
                "model": "eleven_multilingual_v1"
            }
            resp = requests.post(convert_url, headers=headers, files=files, data=data)
            resp.raise_for_status()

            # Save converted audio
            new_audio = tmp_dir / f"{in_path.stem}_female.wav"
            with open(new_audio, "wb") as f:
                f.write(resp.content)

            # Merge new audio into video
            out_path = tmp_dir / f"{in_path.stem}_female.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-i", str(in_path), "-i", str(new_audio),
                "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0", "-shortest", str(out_path)
            ], check=True)

            output_paths.append(out_path)

        # Show individual downloads
        st.markdown("### Downloads")
        for path in output_paths:
            with open(path, "rb") as f:
                st.download_button(
                    label=f"Download {path.name}",
                    data=f,
                    file_name=path.name,
                    mime="video/mp4"
                )

        # Zip and download all
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zipf:
            for path in output_paths:
                zipf.write(path, arcname=path.name)
        zip_buffer.seek(0)
        st.download_button(
            label="Download All as ZIP",
            data=zip_buffer,
            file_name="all_videos.zip",
            mime="application/zip"
        )

# Fallback message
if not uploaded_videos:
    st.info("Upload your video clip(s) above to convert voice.")
