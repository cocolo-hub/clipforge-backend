import subprocess, os, json, math
from pathlib import Path

# ── Trouver FFmpeg automatiquement ──
def find_ffmpeg():
    """Cherche ffmpeg/ffprobe dans plusieurs endroits"""
    import shutil
    # 1. Dans le PATH
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg"), shutil.which("ffprobe")
    # 2. Via imageio-ffmpeg
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        ffprobe = ff.replace("ffmpeg", "ffprobe")
        if not os.path.exists(ffprobe):
            ffprobe = ff  # fallback
        return ff, ffprobe
    except:
        pass
    # 3. Chemins courants Linux/Railway
    for p in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/nix/var/nix/profiles/default/bin/ffmpeg"]:
        if os.path.exists(p):
            probe = p.replace("ffmpeg", "ffprobe")
            return p, probe if os.path.exists(probe) else p
    return "ffmpeg", "ffprobe"

FFMPEG, FFPROBE = find_ffmpeg()

CONTENT_PROFILES = {
    "gaming":  {"min": 15, "max": 45, "threshold": 0.40},
    "vlog":    {"min": 20, "max": 55, "threshold": 0.35},
    "sport":   {"min": 10, "max": 30, "threshold": 0.45},
    "podcast": {"min": 30, "max": 60, "threshold": 0.30},
    "music":   {"min": 15, "max": 40, "threshold": 0.38},
    "tuto":    {"min": 30, "max": 60, "threshold": 0.28},
    "event":   {"min": 15, "max": 45, "threshold": 0.35},
    "food":    {"min": 20, "max": 50, "threshold": 0.33},
}

def get_duration(path):
    result = subprocess.run([
        FFPROBE, "-v", "quiet", "-print_format", "json",
        "-show_format", path
    ], capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])

def get_audio_levels(path, duration):
    result = subprocess.run([
        FFPROBE, "-v", "quiet", "-f", "lavfi",
        "-i", f"amovie={path},astats=metadata=1:reset=1",
        "-show_entries", "frame_tags=lavfi.astats.Overall.RMS_level",
        "-of", "csv=p=0"
    ], capture_output=True, text=True, timeout=300)

    levels = []
    for line in result.stdout.strip().split("\n"):
        try:
            val = float(line.strip())
            if val < -100: val = -100
            levels.append(val)
        except:
            levels.append(-100)

    if not levels:
        levels = [-30.0] * int(duration)
    return levels

def detect_scene_changes(path):
    result = subprocess.run([
        FFMPEG, "-i", path,
        "-vf", "select='gt(scene,0.3)',showinfo",
        "-f", "null", "-"
    ], capture_output=True, text=True, timeout=600)

    timestamps = []
    for line in result.stderr.split("\n"):
        if "pts_time:" in line:
            try:
                t = float(line.split("pts_time:")[1].split()[0])
                timestamps.append(t)
            except:
                pass
    return timestamps

def score_segment(start, end, audio_levels, scene_changes, content_type):
    score = 50
    seg_start = int(start)
    seg_end = min(int(end), len(audio_levels) - 1)
    if seg_end > seg_start and audio_levels:
        seg_levels = audio_levels[seg_start:seg_end]
        max_level = max(seg_levels) if seg_levels else -60
        audio_score = min(100, max(0, (max_level + 60) / 60 * 100))
        score += (audio_score - 50) * 0.4

    scene_count = sum(1 for t in scene_changes if start <= t <= end)
    duration = end - start
    scene_density = scene_count / max(duration, 1)
    scene_score = min(100, scene_density * 200)
    score += (scene_score - 50) * 0.3

    profile = CONTENT_PROFILES.get(content_type, CONTENT_PROFILES["gaming"])
    optimal = (profile["min"] + profile["max"]) / 2
    duration_diff = abs(duration - optimal) / optimal
    duration_score = max(0, 100 - duration_diff * 100)
    score += (duration_score - 50) * 0.3

    return round(min(100, max(0, score)))

def sub_scores(score):
    import random
    r = random.Random(score)
    v = 8
    return (
        min(100, max(0, score + r.randint(-v, v))),
        min(100, max(0, score + r.randint(-v, v))),
        min(100, max(0, score + r.randint(-v, v)))
    )

def export_clip_vertical(input_path, output_path, start, end):
    duration = end - start
    subprocess.run([
        FFMPEG, "-y",
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
    ], capture_output=True, timeout=300)

def process_video(video_path, content_type, output_folder, progress_cb=None):
    profile = CONTENT_PROFILES.get(content_type, CONTENT_PROFILES["gaming"])
    clips = []

    if progress_cb: progress_cb(5)
    duration = get_duration(video_path)
    if progress_cb: progress_cb(10)

    audio_levels = get_audio_levels(video_path, duration)
    if progress_cb: progress_cb(35)

    scene_changes = detect_scene_changes(video_path)
    if progress_cb: progress_cb(55)

    seg_len = (profile["min"] + profile["max"]) / 2
    overlap = seg_len * 0.3
    segments = []
    t = 0
    while t + profile["min"] < duration:
        end = min(t + seg_len, duration)
        if end - t >= profile["min"]:
            segments.append((t, end))
        t += seg_len - overlap

    scored = []
    for start, end in segments:
        s = score_segment(start, end, audio_levels, scene_changes, content_type)
        scored.append((start, end, s))
    scored.sort(key=lambda x: -x[2])

    if progress_cb: progress_cb(65)

    threshold = profile["threshold"] * 100
    exported = 0
    total_to_export = max(1, sum(1 for _, _, s in scored if s >= threshold))

    for i, (start, end, score) in enumerate(scored):
        if score < threshold and exported >= 3:
            break

        clip_name = f"clip_{Path(video_path).stem}_{i+1:02d}.mp4"
        out_path = os.path.join(output_folder, clip_name)
        export_clip_vertical(video_path, out_path, start, end)

        trend, hook, viral = sub_scores(score)

        if score >= 90:   verdict, vc = "Viral potentiel", "green"
        elif score >= 80: verdict, vc = "Très fort hook",  "green"
        elif score >= 70: verdict, vc = "Tendance haute",  "amber"
        elif score >= 60: verdict, vc = "Solide",          "blue"
        else:             verdict, vc = "Moyen",           "muted"

        clips.append({
            "filename": clip_name,
            "url": f"/clip/{clip_name}",
            "start": round(start, 1),
            "end": round(end, 1),
            "duration": round(end - start, 1),
            "score": score,
            "trend": trend,
            "hook": hook,
            "viral": viral,
            "verdict": verdict,
            "verdictColor": vc,
        })

        exported += 1
        if progress_cb:
            progress_cb(min(95, 65 + int((exported / total_to_export) * 30)))

    if progress_cb: progress_cb(100)
    clips.sort(key=lambda x: -x["score"])
    return clips
