import os
import re
import uuid
import glob
import shutil
import tempfile
import threading
import subprocess
import urllib.request
from flask import Flask, render_template, request, jsonify, send_file
import yt_dlp

app = Flask(__name__)

# CORS support for browser extension
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return response

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Track conversion progress: {task_id: {"status": "..." , "progress": "...", "filename": "...", "title": "..."}}
tasks = {}


def is_valid_youtube_url(url):
    pattern = r'^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+'
    return re.match(pattern, url) is not None


def convert_audio(task_id, url):
    try:
        tasks[task_id]["status"] = "downloading"
        tasks[task_id]["progress"] = "Fetching video info..."

        output_template = os.path.join(DOWNLOAD_DIR, f"{task_id}.%(ext)s")

        def progress_hook(d):
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                downloaded = d.get("downloaded_bytes", 0)
                if total:
                    percent = int(downloaded / total * 100)
                    tasks[task_id]["progress"] = f"Downloading... {percent}%"
            elif d["status"] == "finished":
                tasks[task_id]["progress"] = "Converting to MP3..."

        ydl_opts = {
            "format": "bestaudio*/best",
            "outtmpl": output_template,
            "progress_hooks": [progress_hook],
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
            "no_warnings": True,
            "js_runtimes": {"deno": {}},
        }

        proxy = os.environ.get("PROXY_URL")
        if proxy:
            ydl_opts["proxy"] = proxy

        cookiefile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies_master.txt")
        tmp_cookiefile = None
        if os.path.exists(cookiefile):
            tmp_cookiefile = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", prefix="cookies_")
            tmp_cookiefile.close()
            shutil.copy2(cookiefile, tmp_cookiefile.name)
            os.chmod(tmp_cookiefile.name, 0o644)
            ydl_opts["cookiefile"] = tmp_cookiefile.name

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "audio")

        # Find the output mp3 file
        mp3_file = os.path.join(DOWNLOAD_DIR, f"{task_id}.mp3")
        if os.path.exists(mp3_file):
            # Sanitize title for display
            safe_title = re.sub(r'[^\w\s\-\(\)]', '', title)[:80]
            tasks[task_id]["status"] = "done"
            tasks[task_id]["filename"] = f"{task_id}.mp3"
            tasks[task_id]["title"] = safe_title
            tasks[task_id]["progress"] = "Done!"
        else:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["progress"] = "Conversion failed - output file not found"

    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["progress"] = str(e)[:200]
    finally:
        if tmp_cookiefile and os.path.exists(tmp_cookiefile.name):
            os.unlink(tmp_cookiefile.name)


def convert_stream_audio(task_id, stream_url, title):
    """Download audio from a direct stream URL and convert to MP3.
    Used by the browser extension which provides the CDN audio URL."""
    tmp_input = None
    try:
        tasks[task_id]["status"] = "downloading"
        tasks[task_id]["progress"] = "Downloading audio stream..."

        # Download the audio stream
        tmp_input = tempfile.NamedTemporaryFile(delete=False, suffix=".webm", prefix="stream_")
        tmp_input.close()

        req = urllib.request.Request(stream_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": "https://www.youtube.com",
            "Referer": "https://www.youtube.com/",
        })
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 65536
            with open(tmp_input.name, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        percent = int(downloaded / total * 100)
                        tasks[task_id]["progress"] = f"Downloading... {percent}%"

        tasks[task_id]["progress"] = "Converting to MP3..."

        # Convert to MP3 using ffmpeg
        mp3_file = os.path.join(DOWNLOAD_DIR, f"{task_id}.mp3")
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_input.name, "-codec:a", "libmp3lame", "-b:a", "192k", mp3_file],
            capture_output=True, timeout=120
        )

        if os.path.exists(mp3_file) and os.path.getsize(mp3_file) > 0:
            safe_title = re.sub(r'[^\w\s\-\(\)]', '', title)[:80]
            tasks[task_id]["status"] = "done"
            tasks[task_id]["filename"] = f"{task_id}.mp3"
            tasks[task_id]["title"] = safe_title
            tasks[task_id]["progress"] = "Done!"
        else:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["progress"] = f"ffmpeg conversion failed: {result.stderr.decode()[:200]}"

    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["progress"] = str(e)[:200]
    finally:
        if tmp_input and os.path.exists(tmp_input.name):
            os.unlink(tmp_input.name)


@app.route("/api/convert-url", methods=["POST", "OPTIONS"])
def convert_url():
    """Accept a direct audio stream URL from the browser extension."""
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json()
    stream_url = data.get("url", "").strip()
    title = data.get("title", "audio").strip()
    video_id = data.get("videoId", "").strip()

    if not stream_url:
        return jsonify({"error": "No URL provided"}), 400

    # Validate it's a googlevideo URL (YouTube CDN)
    if "googlevideo.com" not in stream_url and "youtube.com" not in stream_url:
        return jsonify({"error": "Invalid stream URL"}), 400

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "queued", "progress": "Queued...", "filename": None, "title": title}

    thread = threading.Thread(target=convert_stream_audio, args=(task_id, stream_url, title), daemon=True)
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert():
    data = request.get_json()
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "queued", "progress": "Queued...", "filename": None, "title": None}

    thread = threading.Thread(target=convert_audio, args=(task_id, url), daemon=True)
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/status/<task_id>")
def status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@app.route("/download/<task_id>")
def download(task_id):
    task = tasks.get(task_id)
    if not task or task["status"] != "done":
        return jsonify({"error": "File not ready"}), 404

    filepath = os.path.join(DOWNLOAD_DIR, task["filename"])
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404

    display_name = f"{task['title']}.mp3"

    def cleanup():
        import time
        time.sleep(5)
        try:
            os.remove(filepath)
        except OSError:
            pass
        tasks.pop(task_id, None)

    threading.Thread(target=cleanup, daemon=True).start()

    return send_file(filepath, as_attachment=True, download_name=display_name, mimetype="audio/mpeg")


EXTENSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extension")


@app.route("/extension/<path:filename>")
def serve_extension(filename):
    """Serve Chrome extension files for download/installation."""
    filepath = os.path.join(EXTENSION_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    return send_file(filepath)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"YouTube to MP3 Converter running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
