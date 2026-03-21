import subprocess, os, json
from pathlib import Path

# ── FFmpeg via imageio-ffmpeg uniquement ──
import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
# ffprobe est dans le même dossier que ffmpeg
FFPROBE = os.path.join(os.path.dirname(FFMPEG), "ffprobe")
# Si ffprobe n'existe pas, on utilise ffmpeg à la place pour tout
if not os.path.exists(FFPROBE):
    FFPROBE = FFMPEG

print(f"[ClipForge] FFMPEG  = {FFMPEG}", flush=True)
print(f"[ClipForge] FFPROBE = {FFPROBE}", flush=True)

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
    """Durée via ffmpeg -i (fonctionne sans ffprobe)"""
    r = subprocess.run(
        [FFMPEG, "-i", path],
        capture_output=True, text=True
    )
    for line in r.stderr.split("\n"):
        if "Duration:" in line:
            try:
                t = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = t.split(":")
                return float(h)*3600 + float(m)*60 + float(s)
            except: pass
    return 60.0  # fallback

def get_audio_levels(path, duration):
    """Analyse audio via ffmpeg directement"""
    r = subprocess.run([
        FFMPEG, "-i", path,
        "-af", "astats=metadata=1:reset=1",
        "-f", "null", "-"
    ], capture_output=True, text=True, timeout=300)

    levels = []
    for line in r.stderr.split("\n"):
        if "RMS level dB:" in line:
            try:
                val = float(line.split("RMS level dB:")[1].strip().split()[0])
                levels.append(max(-100, val))
            except:
                levels.append(-60)

    if not levels:
        return [-40.0] * max(1, int(duration))
    return levels

def detect_scene_changes(path):
    """Détection de changements de scène via ffmpeg"""
    r = subprocess.run([
        FFMPEG, "-i", path,
        "-vf", "select='gt(scene,0.35)',showinfo",
        "-f", "null", "-"
    ], capture_output=True, text=True, timeout=600)

    times = []
    for line in r.stderr.split("\n"):
        if "pts_time:" in line:
            try:
                t = float(line.split("pts_time:")[1].split()[0])
                times.append(t)
            except: pass
    return times

def score_segment(start, end, audio_levels, scene_changes, content_type):
    score = 50
    seg = audio_levels[int(start):min(int(end), len(audio_levels)-1)]
    if seg:
        max_lvl = max(seg)
        audio_score = min(100, max(0, (max_lvl + 60) / 60 * 100))
        score += (audio_score - 50) * 0.4

    dur = end - start
    sc = sum(1 for t in scene_changes if start <= t <= end)
    scene_score = min(100, sc / max(dur, 1) * 200)
    score += (scene_score - 50) * 0.3

    p = CONTENT_PROFILES.get(content_type, CONTENT_PROFILES["gaming"])
    opt = (p["min"] + p["max"]) / 2
    dur_score = max(0, 100 - abs(dur - opt) / opt * 100)
    score += (dur_score - 50) * 0.3

    return round(min(100, max(0, score)))

def sub_scores(score):
    import random
    r = random.Random(score)
    v = 8
    return tuple(min(100, max(0, score + r.randint(-v, v))) for _ in range(3))

def export_clip_vertical(inp, out, start, end):
    subprocess.run([
        FFMPEG, "-y",
        "-ss", str(start),
        "-i", inp,
        "-t", str(end - start),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out
    ], capture_output=True, timeout=300)

def process_video(video_path, content_type, output_folder, progress_cb=None):
    p = CONTENT_PROFILES.get(content_type, CONTENT_PROFILES["gaming"])

    if progress_cb: progress_cb(5)
    dur = get_duration(video_path)
    print(f"[ClipForge] Durée: {dur}s", flush=True)

    if progress_cb: progress_cb(15)
    levels = get_audio_levels(video_path, dur)
    print(f"[ClipForge] Audio levels: {len(levels)} points", flush=True)

    if progress_cb: progress_cb(40)
    scenes = detect_scene_changes(video_path)
    print(f"[ClipForge] Scènes détectées: {len(scenes)}", flush=True)

    if progress_cb: progress_cb(60)

    seg_len = (p["min"] + p["max"]) / 2
    segs, t = [], 0
    while t + p["min"] < dur:
        e = min(t + seg_len, dur)
        if e - t >= p["min"]: segs.append((t, e))
        t += seg_len * 0.7

    scored = sorted([
        (s, e, score_segment(s, e, levels, scenes, content_type))
        for s, e in segs
    ], key=lambda x: -x[2])

    if progress_cb: progress_cb(65)

    threshold = p["threshold"] * 100
    clips, exported = [], 0
    total = max(1, sum(1 for _, _, s in scored if s >= threshold))

    for i, (start, end, score) in enumerate(scored):
        if score < threshold and exported >= 3: break
        name = f"clip_{Path(video_path).stem}_{i+1:02d}.mp4"
        out_path = os.path.join(output_folder, name)
        export_clip_vertical(video_path, out_path, start, end)

        trend, hook, viral = sub_scores(score)
        v_map = [(90,"Viral potentiel","green"),(80,"Très fort hook","green"),
                 (70,"Tendance haute","amber"),(60,"Solide","blue")]
        verdict, vc = next(((v,c) for thr,v,c in v_map if score>=thr), ("Moyen","muted"))

        clips.append({
            "filename": name, "url": f"/clip/{name}",
            "start": round(start,1), "end": round(end,1),
            "duration": round(end-start,1),
            "score": score, "trend": trend, "hook": hook, "viral": viral,
            "verdict": verdict, "verdictColor": vc,
        })
        exported += 1
        if progress_cb: progress_cb(min(95, 65 + int(exported/total*30)))

    if progress_cb: progress_cb(100)
    print(f"[ClipForge] {len(clips)} clips générés", flush=True)
    return sorted(clips, key=lambda x: -x["score"])
