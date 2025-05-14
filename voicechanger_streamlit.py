import os, shutil, subprocess, tempfile, platform
from pathlib import Path
from pydub import AudioSegment, silence
import streamlit as st
from datetime import datetime
import zipfile
import io
import whisper
import json
import re

st.set_page_config(page_title="Clipstorm", layout="centered")

st.title("🎥 Clipstorm Video Generator")

def sanitize_filename(name):
    # Replace spaces and apostrophes with underscores, remove non-ASCII
    name = re.sub(r"[’'\"\\s]", "_", name)
    name = re.sub(r"[^a-zA-Z0-9._-]", "", name)
    return name

def trim_silence(fp: Path, tmp: Path):
    try:
        audio = AudioSegment.from_file(fp)
    except Exception as e:
        # Fallback: convert to wav with ffmpeg and try again
        if fp.suffix.lower() == ".m4a":
            converted = tmp / f"{fp.stem}_converted.wav"
            subprocess.run([
                "ffmpeg", "-y", "-i", str(fp), str(converted)
            ], check=True)
            audio = AudioSegment.from_file(converted)
            fp = converted
        else:
            raise e
    # Endpoint-only trimming: only trim silence at start/end, not in the middle
    silence_thresh = audio.dBFS - 20
    min_silence_len = 100  # short, just to detect endpoints
    nonsilent = silence.detect_nonsilent(audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh)
    if not nonsilent:
        return fp, len(audio)/1000
    # Only trim the start and end
    start_trim = max(nonsilent[0][0] - 50, 0)
    end_trim = min(nonsilent[-1][1] + 50, len(audio))
    trimmed = audio[start_trim:end_trim]
    out = tmp / f"{fp.stem}_trimmed.wav"
    trimmed.export(out, format="wav")
    return out, len(trimmed)/1000

def get_duration(fp: Path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nokey=1:noprint_wrappers=1", str(fp)],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return float(r.stdout) if r.stdout else 0.0

def ff(cmd): subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def write_srt(segments, out_path):
    def format_srt_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds - int(seconds)) * 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"
    with open(out_path, "w") as f:
        for i, seg in enumerate(segments):
            start = seg['start']
            end = seg['end']
            text = seg['text'].strip()
            # Remove trailing period
            if text.endswith('.'):
                text = text[:-1]
            f.write(f"{i+1}\n")
            f.write(f"{format_srt_time(start)} --> {format_srt_time(end)}\n")
            f.write(f"{text}\n\n")

def get_video_height(fp: Path):
    r = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "json", str(fp)
    ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    info = json.loads(r.stdout)
    return info['streams'][0]['height'] if 'streams' in info and info['streams'] else 720

prefix = st.text_input("Filename prefix", "")
# Accept all files, filter manually
hooks = st.file_uploader("Upload hook videos", accept_multiple_files=True)
voices = st.file_uploader("Upload voiceovers", accept_multiple_files=True)
bodies = st.file_uploader("Optional: upload body videos", accept_multiple_files=True)

allowed_video_exts = {".mp4", ".mov"}
allowed_audio_exts = {".wav", ".mp3", ".m4a"}

# Store trimmed voiceover paths and durations
trimmed_voices = []

# Show uploaded file durations immediately after upload
if hooks:
    st.markdown("#### Hook video durations:")
    for h in hooks:
        ext = Path(h.name).suffix.lower()
        if ext not in allowed_video_exts:
            st.error(f"Unsupported video file type: {h.name}")
            continue
        h_name = sanitize_filename(h.name)
        if "." in h_name:
            base, ext2 = h_name.rsplit(".", 1)
            h_name = f"{base}.{ext2.lower()}"
        h_path = Path(tempfile.gettempdir()) / h_name
        with open(h_path, "wb") as f: f.write(h.getbuffer())
        dur = get_duration(h_path)
        st.write(f"{h_name}: {dur:.2f} seconds")
if voices:
    st.markdown("#### Voiceover durations (original → trimmed):")
    for v in voices:
        ext = Path(v.name).suffix.lower()
        if ext not in allowed_audio_exts:
            st.error(f"Unsupported audio file type: {v.name}")
            continue
        v_name = sanitize_filename(v.name)
        if "." in v_name:
            base, ext2 = v_name.rsplit(".", 1)
            v_name = f"{base}.{ext2.lower()}"
        v_path = Path(tempfile.gettempdir()) / v_name
        with open(v_path, "wb") as f: f.write(v.getbuffer())
        orig_dur = get_duration(v_path)
        # Trim immediately after upload
        trimmed_path, trimmed_dur = trim_silence(v_path, Path(tempfile.gettempdir()))
        trimmed_voices.append((trimmed_path, trimmed_dur))
        percent_trimmed = 100 * (orig_dur - trimmed_dur) / orig_dur if orig_dur > 0 else 0
        st.write(f"{v_name}: {orig_dur:.2f}s → {trimmed_dur:.2f}s ({percent_trimmed:.1f}% trimmed)")
if bodies:
    for b in bodies:
        ext = Path(b.name).suffix.lower()
        if ext not in allowed_video_exts:
            st.error(f"Unsupported body video file type: {b.name}")
            continue
        b_name = sanitize_filename(b.name)
        if "." in b_name:
            base, ext2 = b_name.rsplit(".", 1)
            b_name = f"{base}.{ext2.lower()}"
        b_path = Path(tempfile.gettempdir()) / b_name
        with open(b_path, "wb") as f: f.write(b.getbuffer())

if "exported_videos" not in st.session_state:
    st.session_state["exported_videos"] = []

processing = False
if st.button("Generate"):
    processing = True
    if not prefix: st.error("Enter a prefix"); st.stop()
    if not hooks or not voices: st.error("Upload at least one hook and voice"); st.stop()

    tmp = Path(tempfile.mkdtemp())
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path("rendered_videos") / f"{prefix}_{timestamp}"
    out.mkdir(parents=True, exist_ok=True)
    total = len(hooks) * len(voices) * max(1, len(bodies))
    progress = st.progress(0)
    idx = 0
    exported_videos = []
    short_hook_warnings = []

    # Standardize video/audio properties for concat compatibility
    standard_args = ["-vf", "scale=1080:1920", "-r", "30", "-ar", "44100", "-ac", "2"]

    for h in hooks:
        h_sanitized = sanitize_filename(h.name)
        h_path = tmp / h_sanitized
        with open(h_path, "wb") as f: f.write(h.getbuffer())
        for v_idx, v in enumerate(voices):
            idx += 1
            progress.progress(idx/total)
            v_sanitized = sanitize_filename(v.name)
            st.write(f"{h_sanitized} + {v_sanitized}")
            # Use pre-trimmed audio
            trimmed, dur = trimmed_voices[v_idx]
            v_path = Path(tempfile.gettempdir()) / v_sanitized
            # (No need to write v.getbuffer() again)
            try:
                hook_dur = get_duration(h_path)
                if hook_dur < dur:
                    short_hook_warnings.append(f"Warning: Hook video '{h_sanitized}' ({hook_dur:.2f}s) is shorter than trimmed audio '{v_sanitized}' ({dur:.2f}s). Video will be padded to match audio.")
                if get_duration(h_path) < dur: continue

                h_cut = tmp / f"{h_path.stem}_cut.mp4"
                ff(["ffmpeg","-y","-i",str(h_path),"-t",str(dur),"-c:v","libx264","-preset","veryfast","-c:a","aac",str(h_cut)])
                h_vo = tmp / f"{h_path.stem}_{v_path.stem}_ov.mp4"
                ff(["ffmpeg","-y","-i",str(h_cut),"-i",str(trimmed),"-c:v","copy","-map","0:v","-map","1:a","-shortest",str(h_vo)])

                if bodies:
                    for b in bodies:
                        b_sanitized = sanitize_filename(b.name)
                        if "." in b_sanitized:
                            base, ext = b_sanitized.rsplit(".", 1)
                            b_sanitized = f"{base}.{ext.lower()}"
                        b_path = tmp / b_sanitized
                        with open(b_path, "wb") as f: f.write(b.getbuffer())
                        # Always use robust concat filter for body+hook
                        h_vo_reenc = tmp / f"{h_vo.stem}_reenc.mp4"
                        ff([
                            "ffmpeg", "-y", "-i", str(h_vo),
                            *standard_args,
                            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(h_vo_reenc)
                        ])
                        body_reenc = tmp / f"{b_path.stem}_reenc.mp4"
                        ff([
                            "ffmpeg", "-y", "-i", str(b_path),
                            *standard_args,
                            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(body_reenc)
                        ])
                        prefix_sanitized = sanitize_filename(prefix)
                        concat_out = tmp / f"{prefix_sanitized}_{h_sanitized}_{v_sanitized}_{b_sanitized}_concat.mp4"
                        ff([
                            "ffmpeg", "-y",
                            "-i", str(h_vo_reenc),
                            "-i", str(body_reenc),
                            "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
                            "-map", "[v]", "-map", "[a]",
                            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                            str(concat_out)
                        ])
                        try:
                            clean_name = sanitize_filename(f"{prefix_sanitized}_{h_sanitized}_{v_sanitized}_{b_sanitized}") + ".mp4"
                        except Exception:
                            clean_name = f"output_{idx}.mp4"
                        final = out / clean_name
                        shutil.copy(concat_out, final)
                        if final.exists():
                            exported_videos.append(str(final.resolve()))
                        else:
                            st.error(f"Failed to generate video: {final}")
                else:
                    # Use fast concat for hook+voiceover only
                    try:
                        clean_name = sanitize_filename(f"{sanitize_filename(prefix)}_{h_sanitized}_{v_sanitized}") + ".mp4"
                    except Exception:
                        clean_name = f"output_{idx}.mp4"
                    final = out / clean_name
                    cat = tmp / "list.txt"
                    with open(cat, "w") as f: f.write(f"file '{h_vo}'\n")
                    try:
                        ff([
                            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(cat), "-c", "copy", str(final)
                        ])
                    except Exception as e:
                        st.warning(f"Fast concat failed for {final.name}, falling back to re-encoding. Reason: {e}")
                        h_vo_reenc = tmp / f"{h_vo.stem}_reenc.mp4"
                        ff([
                            "ffmpeg", "-y", "-i", str(h_vo),
                            *standard_args,
                            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(h_vo_reenc)
                        ])
                        shutil.copy(h_vo_reenc, final)
                    if final.exists():
                        exported_videos.append(str(final.resolve()))
                    else:
                        st.error(f"Failed to generate video: {final}")

            except Exception as e:
                st.error(f"Error: {e}")

    st.session_state["exported_videos"] = exported_videos
    st.success(f"Done! Your videos are ready to download below.")

    if short_hook_warnings:
        for w in short_hook_warnings:
            st.warning(w)

    processing = False
    st.session_state["generate_pressed"] = True
elif st.button("Generate with Captions"):
    processing = True
    if not prefix: st.error("Enter a prefix"); st.stop()
    if not hooks or not voices: st.error("Upload at least one hook and voice"); st.stop()

    tmp = Path(tempfile.mkdtemp())
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path("rendered_videos") / f"{prefix}_captions_{timestamp}"
    out.mkdir(parents=True, exist_ok=True)
    total = len(hooks) * len(voices) * max(1, len(bodies))
    progress = st.progress(0)
    idx = 0
    exported_videos = []
    short_hook_warnings = []
    model = whisper.load_model("base")

    # Standardize video/audio properties for concat compatibility
    standard_args = ["-vf", "scale=1080:1920", "-r", "30", "-ar", "44100", "-ac", "2"]

    for h in hooks:
        h_sanitized = sanitize_filename(h.name)
        h_path = tmp / h_sanitized
        with open(h_path, "wb") as f: f.write(h.getbuffer())
        for v_idx, v in enumerate(voices):
            idx += 1
            progress.progress(idx/total)
            v_sanitized = sanitize_filename(v.name)
            st.write(f"{h_sanitized} + {v_sanitized} (with captions)")
            trimmed, dur = trimmed_voices[v_idx]
            v_path = Path(tempfile.gettempdir()) / v_sanitized
            try:
                hook_dur = get_duration(h_path)
                if hook_dur < dur:
                    short_hook_warnings.append(f"Warning: Hook video '{h_sanitized}' ({hook_dur:.2f}s) is shorter than trimmed audio '{v_sanitized}' ({dur:.2f}s). Video will be padded to match audio.")
                if get_duration(h_path) < dur: continue

                h_cut = tmp / f"{h_path.stem}_cut.mp4"
                ff(["ffmpeg","-y","-i",str(h_path),"-t",str(dur),"-c:v","libx264","-preset","veryfast","-c:a","aac",str(h_cut)])
                h_vo = tmp / f"{h_path.stem}_{v_path.stem}_ov.mp4"
                ff(["ffmpeg","-y","-i",str(h_cut),"-i",str(trimmed),"-c:v","copy","-map","0:v","-map","1:a","-shortest",str(h_vo)])

                # Transcribe trimmed audio with Whisper
                result = model.transcribe(str(trimmed), word_timestamps=False)
                srt_path = tmp / f"{h_path.stem}_{v_path.stem}.srt"
                write_srt(result['segments'], srt_path)

                # Dynamically set font size, outline, and y-position for captions
                video_h = get_video_height(h_vo)
                font_size = int(video_h * 0.05)
                stroke_width = int(video_h * 0.003)
                margin_v = int(video_h - (0.85 * video_h))  # ffmpeg MarginV is from bottom
                captioned = tmp / f"{h_path.stem}_{v_path.stem}_captioned.mp4"
                ff([
                    "ffmpeg", "-y", "-i", str(h_vo),
                    "-vf", f"subtitles='{srt_path}':force_style='Fontname=Arial,Fontsize={font_size},PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,BorderStyle=1,Outline={stroke_width},Shadow=0,Alignment=2,Bold=1,MarginV={margin_v}'",
                    "-c:a", "copy", str(captioned)
                ])

                if bodies:
                    for b in bodies:
                        b_sanitized = sanitize_filename(b.name)
                        if "." in b_sanitized:
                            base, ext = b_sanitized.rsplit(".", 1)
                            b_sanitized = f"{base}.{ext.lower()}"
                        b_path = tmp / b_sanitized
                        with open(b_path, "wb") as f: f.write(b.getbuffer())
                        h_vo_reenc = tmp / f"{captioned.stem}_reenc.mp4"
                        ff([
                            "ffmpeg", "-y", "-i", str(captioned),
                            *standard_args,
                            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(h_vo_reenc)
                        ])
                        body_reenc = tmp / f"{b_path.stem}_reenc.mp4"
                        ff([
                            "ffmpeg", "-y", "-i", str(b_path),
                            *standard_args,
                            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(body_reenc)
                        ])
                        prefix_sanitized = sanitize_filename(prefix)
                        concat_out = tmp / f"{prefix_sanitized}_{h_sanitized}_{v_sanitized}_{b_sanitized}_captioned_concat.mp4"
                        ff([
                            "ffmpeg", "-y",
                            "-i", str(h_vo_reenc),
                            "-i", str(body_reenc),
                            "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
                            "-map", "[v]", "-map", "[a]",
                            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                            str(concat_out)
                        ])
                        try:
                            clean_name = sanitize_filename(f"{prefix_sanitized}_{h_sanitized}_{v_sanitized}_{b_sanitized}_captioned") + ".mp4"
                        except Exception:
                            clean_name = f"output_{idx}_captioned.mp4"
                        final = out / clean_name
                        shutil.copy(concat_out, final)
                        if final.exists():
                            exported_videos.append(str(final.resolve()))
                        else:
                            st.error(f"Failed to generate video: {final}")
                else:
                    try:
                        clean_name = sanitize_filename(f"{sanitize_filename(prefix)}_{h_sanitized}_{v_sanitized}_captioned") + ".mp4"
                    except Exception:
                        clean_name = f"output_{idx}_captioned.mp4"
                    final = out / clean_name
                    shutil.copy(captioned, final)
                    if final.exists():
                        exported_videos.append(str(final.resolve()))
                    else:
                        st.error(f"Failed to generate video: {final}")
            except Exception as e:
                st.error(f"Error: {e}")
    st.session_state["exported_videos"] = exported_videos
    st.success(f"Done! Your captioned videos are ready to download below.")
    if short_hook_warnings:
        for w in short_hook_warnings:
            st.warning(w)
    processing = False
    st.session_state["generate_pressed"] = True
else:
    st.session_state["generate_pressed"] = False

# After processing, always show download buttons if videos exist
st.markdown("### Download your videos:")

if processing:
    with st.spinner("Processing videos, please wait..."):
        pass
elif st.session_state["exported_videos"]:
    st.info("Click the download icon next to each video to download it. They will be saved to your browser's default downloads folder.")
    for i, video_path in enumerate(st.session_state["exported_videos"]):
        video_path = Path(video_path)
        if video_path.exists():
            cols = st.columns([0.08, 0.72, 0.2])
            with cols[0]:
                st.markdown(":arrow_down:", unsafe_allow_html=True)
            with cols[1]:
                st.markdown(f"**{video_path.name}**")
            with cols[2]:
                with open(video_path, "rb") as video_file:
                    st.download_button(
                        label="Download",
                        data=video_file.read(),
                        file_name=video_path.name,
                        mime="video/mp4",
                        key=f"download_{i}"
                    )
        else:
            st.error(f"File not found: {video_path}")
    # Download all as ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zipf:
        for video_path in st.session_state["exported_videos"]:
            video_path = Path(video_path)
            if video_path.exists():
                zipf.write(video_path, arcname=video_path.name)
    zip_buffer.seek(0)
    st.download_button(
        label="Download All Videos as ZIP",
        data=zip_buffer,
        file_name="all_videos.zip",
        mime="application/zip",
        key="download_zip"
    )
elif st.session_state.get("generate_pressed", False):
    st.warning("No videos were generated. Please check your inputs and try again.")
else:
    st.info("Upload your files and click Generate to create videos.")

