import tempfile
import subprocess
import zipfile
import io
import requests
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Clipstorm Simple", layout="centered")
st.title("🎥 Clipstorm: Video Voice Conversion")

api_key = st.text_input("ElevenLabs API Key", type="password")
vids = st.file_uploader("Upload video clip(s)", type=["mp4","mov"], accept_multiple_files=True)

if st.button("Generate with Female Voice"):
    if not api_key:
        st.error("Enter your ElevenLabs API Key.")
    elif not vids:
        st.error("Upload at least one video.")
    else:
        tmp = Path(tempfile.mkdtemp())
        outputs = []
        for vid in vids:
            in_path = tmp/vid.name
            open(in_path,"wb").write(vid.getbuffer())

            # extract
            audio_in = tmp/f"{in_path.stem}_orig.wav"
            subprocess.run(["ffmpeg","-y","-i",str(in_path),"-vn",str(audio_in)], check=True)

            # call ElevenLabs
            resp = requests.post(
                "https://api.elevenlabs.io/v1/voice/convert",
                headers={"xi-api-key":api_key},
                files={"file": open(audio_in,"rb")},
                data={"voice":"female","model":"eleven_multilingual_v1"}
            )
            resp.raise_for_status()
            audio_out = tmp/f"{in_path.stem}_female.wav"
            open(audio_out,"wb").write(resp.content)

            # merge back
            out_vid = tmp/f"{in_path.stem}_female.mp4"
            subprocess.run([
                "ffmpeg","-y","-i",str(in_path),"-i",str(audio_out),
                "-c:v","copy","-map","0:v","-map","1:a","-shortest",str(out_vid)
            ], check=True)
            outputs.append(out_vid)

        st.markdown("### Downloads")
        for p in outputs:
            st.download_button(f"Download {p.name}", data=open(p,"rb"), file_name=p.name, mime="video/mp4")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf,"w") as z:
            for p in outputs: z.write(p, arcname=p.name)
        buf.seek(0)
        st.download_button("Download All as ZIP", data=buf, file_name="all_videos.zip", mime="application/zip")
else:
    st.info("Upload video clip(s) above and click the button.")
