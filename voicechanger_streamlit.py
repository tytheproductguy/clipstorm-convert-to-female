import nest_asyncio
nest_asyncio.apply()

import tempfile
import subprocess
import zipfile
import io
import requests
from pathlib import Path
import traceback
import streamlit as st

# Enable detailed error messages in the UI
st.set_option('server.showErrorDetails', True)

st.set_page_config(page_title="Clipstorm: Debug Mode", layout="centered")
st.title("🎥 Clipstorm Debug Version")

api_key = st.text_input("ElevenLabs API Key", type="password")
vids = st.file_uploader("Upload video clip(s)", type=["mp4","mov"], accept_multiple_files=True)

# Check ffmpeg availability
def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg","-version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

if not check_ffmpeg():
    st.error("⚠️ ffmpeg not found. Install it and ensure it's on your PATH.")

if st.button("Generate with Female Voice"):
    try:
        if not api_key:
            st.error("Enter your ElevenLabs API Key.")
            st.stop()
        if not vids:
            st.error("Upload at least one video.")
            st.stop()

        tmp = Path(tempfile.mkdtemp())
        outputs = []

        for vid in vids:
            in_path = tmp / vid.name
            with open(in_path, "wb") as f:
                f.write(vid.getbuffer())

            st.write(f"Processing: {vid.name}")

            # Extract audio
            audio_in = tmp / f"{in_path.stem}_orig.wav"
            subprocess.run(["ffmpeg","-y","-i",str(in_path),"-vn",str(audio_in)], check=True)
            st.write(f"Extracted audio to {audio_in}")

            # Convert via ElevenLabs API
            resp = requests.post(
                "https://api.elevenlabs.io/v1/voice/convert",
                headers={"xi-api-key": api_key},
                files={"file": open(audio_in, "rb")},
                data={"voice":"female","model":"eleven_multilingual_v1"}
            )
            resp.raise_for_status()
            audio_out = tmp / f"{in_path.stem}_female.wav"
            with open(audio_out, "wb") as f:
                f.write(resp.content)
            st.write(f"Received converted audio: {audio_out}")

            # Merge new audio
            out_vid = tmp / f"{in_path.stem}_female.mp4"
            subprocess.run([
                "ffmpeg","-y","-i",str(in_path),"-i",str(audio_out),
                "-c:v","copy","-map","0:v","-map","1:a","-shortest",str(out_vid)
            ], check=True)
            st.write(f"Merged video: {out_vid}")
            outputs.append(out_vid)

        # Show downloads
        st.markdown("### Downloads")
        for p in outputs:
            with open(p, "rb") as f:
                st.download_button(
                    label=f"Download {p.name}",
                    data=f,
                    file_name=p.name,
                    mime="video/mp4"
                )

        # ZIP all
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for p in outputs:
                z.write(p, arcname=p.name)
        buf.seek(0)
        st.download_button(
            label="Download All as ZIP",
            data=buf,
            file_name="all_videos.zip",
            mime="application/zip"
        )

    except Exception as e:
        st.error("An unexpected error occurred:")
        st.exception(traceback.format_exc())
else:
    st.info("Upload video clip(s) above and click the button.")
