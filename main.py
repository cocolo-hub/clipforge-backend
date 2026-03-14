from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os, uuid, threading
from processor import process_video

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

UPLOAD_FOLDER = "uploads"
CHUNKS_FOLDER = "chunks"
OUTPUT_FOLDER = "outputs"
JOBS = {}

for d in [UPLOAD_FOLDER, CHUNKS_FOLDER, OUTPUT_FOLDER]:
    os.makedirs(d, exist_ok=True)

@app.route("/upload_chunk", methods=["POST"])
def upload_chunk():
    chunk = request.files.get("chunk")
    upload_id = request.form.get("upload_id")
    chunk_index = int(request.form.get("chunk_index", 0))
    if not chunk or not upload_id:
        return jsonify({"error": "Missing data"}), 400
    chunk_dir = os.path.join(CHUNKS_FOLDER, upload_id)
    os.makedirs(chunk_dir, exist_ok=True)
    chunk.save(os.path.join(chunk_dir, f"chunk_{chunk_index:06d}"))
    return jsonify({"ok": True})

@app.route("/finalize_upload", methods=["POST"])
def finalize_upload():
    data = request.json
    upload_id = data.get("upload_id")
    filename = data.get("filename", "video.mp4")
    content_type_val = data.get("content_type", "gaming")
    total_chunks = int(data.get("total_chunks", 1))
    chunk_dir = os.path.join(CHUNKS_FOLDER, upload_id)
    job_id = str(uuid.uuid4())
    final_path = os.path.join(UPLOAD_FOLDER, f"{job_id}_{filename}")
    with open(final_path, "wb") as out:
        for i in range(total_chunks):
            cp = os.path.join(chunk_dir, f"chunk_{i:06d}")
            with open(cp, "rb") as c:
                out.write(c.read())
            os.remove(cp)
    try: os.rmdir(chunk_dir)
    except: pass
    JOBS[job_id] = {"status": "processing", "clips": [], "progress": 0}
    t = threading.Thread(target=run_job, args=(job_id, final_path, content_type_val))
    t.daemon = True
    t.start()
    return jsonify({"job_id": job_id})

@app.route("/upload", methods=["POST"])
def upload():
    if "video" not in request.files:
        return jsonify({"error": "No video file"}), 400
    file = request.files["video"]
    content_type_val = request.form.get("content_type", "gaming")
    job_id = str(uuid.uuid4())
    path = os.path.join(UPLOAD_FOLDER, f"{job_id}_{file.filename}")
    file.save(path)
    JOBS[job_id] = {"status": "processing", "clips": [], "progress": 0}
    t = threading.Thread(target=run_job, args=(job_id, path, content_type_val))
    t.daemon = True
    t.start()
    return jsonify({"job_id": job_id})

def run_job(job_id, video_path, content_type_val):
    try:
        clips = process_video(video_path, content_type_val, OUTPUT_FOLDER,
                              progress_cb=lambda p: update_progress(job_id, p))
        JOBS[job_id].update({"status": "done", "clips": clips, "progress": 100})
    except Exception as e:
        JOBS[job_id].update({"status": "error", "error": str(e)})

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
