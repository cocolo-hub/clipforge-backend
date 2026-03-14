from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os, uuid, threading, json
from processor import process_video

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "outputs"
JOBS = {}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

@app.route("/upload", methods=["POST"])
def upload():
    if "video" not in request.files:
        return jsonify({"error": "No video file"}), 400

    file = request.files["video"]
    content_type = request.form.get("content_type", "gaming")
    job_id = str(uuid.uuid4())

    path = os.path.join(UPLOAD_FOLDER, f"{job_id}_{file.filename}")
    file.save(path)

    JOBS[job_id] = {"status": "processing", "clips": [], "progress": 0}

    thread = threading.Thread(target=run_job, args=(job_id, path, content_type))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})

def run_job(job_id, video_path, content_type):
    try:
        JOBS[job_id]["status"] = "processing"
        clips = process_video(video_path, content_type, OUTPUT_FOLDER,
                              progress_cb=lambda p: update_progress(job_id, p))
        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["clips"] = clips
        JOBS[job_id]["progress"] = 100
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)

def update_progress(job_id, progress):
    if job_id in JOBS:
        JOBS[job_id]["progress"] = progress

@app.route("/status/<job_id>")
def status(job_id):
    if job_id not in JOBS:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(JOBS[job_id])

@app.route("/clip/<filename>")
def get_clip(filename):
    path = os.path.join(OUTPUT_FOLDER, filename)
    if not os.path.exists(path):
        return jsonify({"error": "Clip not found"}), 404
    return send_file(path, mimetype="video/mp4")

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
