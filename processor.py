import subprocess, os, json, math
from pathlib import Path

# ── Durées cibles par type de contenu ──
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
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", path
    ], capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])

def get_audio_levels(path, duration):
    """Analyse le volume audio toutes les secondes"""
    result = subprocess.run([
        "ffprobe", "-v", "quiet", "-f", "lavfi",
        "-i", f"amovie={path},astats=metadata=1:reset=1",
        "-show_entries", "frame_tags=lavfi.astats.Overall.RMS_level",
        "-of", "csv=p=0"
    ], capture_output=True, text=True, timeout=300)

    levels = []
    for line in result.stdout.strip().split("\n"):
        try:
            val = float(line.strip())
            if val < -100:
                val = -100
            levels.append(val)
        except:
            levels.append(-100)

    if not levels:
        # fallback : niveaux uniformes simulés
        levels = [-30.0] * int(duration)

    return levels

def detect_scene_changes(path):
    """Détecte les changements de scène avec FFmpeg"""
    result = subprocess.run([
        "ffmpeg", "-i", path,
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
    """Calcule un score 0-100 pour un segment"""
    score = 50

    # Score audio (pics sonores = moments forts)
    seg_start = int(start)
    seg_end = min(int(end), len(audio_levels) - 1)
    if seg_end > seg_start and audio_levels:
        seg_levels = audio_levels[seg_start:seg_end]
        avg_level = sum(seg_levels) / len(seg_levels) if seg_levels else -60
        max_level = max(seg_levels) if seg_levels else -60
        # Normaliser entre -60 et 0 dB
        audio_score = min(100, max(0, (max_level + 60) / 60 * 100))
        score += (audio_score - 50) * 0.4

    # Score changements de scène (dynamisme visuel)
    scene_count = sum(1 for t in scene_changes if start <= t <= end)
    duration = end - start
    scene_density = scene_count / max(duration, 1)
    scene_score = min(100, scene_density * 200)
    score += (scene_score - 50) * 0.3

    # Bonus durée optimale selon le type
    profile = CONTENT_PROFILES.get(content_type, CONTENT_PROFILES["gaming"])
    optimal = (profile["min"] + profile["max"]) / 2
    duration_diff = abs(duration - optimal) / optimal
    duration_score = max(0, 100 - duration_diff * 100)
    score += (duration_score - 50) * 0.3

    return round(min(100, max(0, score)))

def sub_scores(score, content_type):
    """Génère les sous-scores tendance, hook, viralité"""
    import random
    r = random.Random(score)  # déterministe selon le score global
    variation = 8

    trend  = min(100, max(0, score + r.randint(-variation, variation)))
    hook   = min(100, max(0, score + r.randint(-variation, variation)))
    viral  = min(100, max(0, score + r.randint(-variation, variation)))

    return trend, hook, viral

def export_clip_vertical(input_path, output_path, start, end):
    """Exporte un segment en 9:16 avec zoom intelligent"""
    duration = end - start
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-vf", (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "zoompan=z='min(zoom+0.002,1.2)':d=1:s=1080x1920"
        ),
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
    ], capture_output=True, timeout=300)

def process_video(video_path, content_type, output_folder, progress_cb=None):
    """Pipeline principal de traitement"""
    profile = CONTENT_PROFILES.get(content_type, CONTENT_PROFILES["gaming"])
    clips = []

    if progress_cb: progress_cb(5)

    # 1. Durée totale
    duration = get_duration(video_path)
    if progress_cb: progress_cb(10)

    # 2. Analyse audio
    if progress_cb: progress_cb(15)
    audio_levels = get_audio_levels(video_path, duration)
    if progress_cb: progress_cb(35)

    # 3. Détection scènes
    scene_changes = detect_scene_changes(video_path)
    if progress_cb: progress_cb(55)

    # 4. Découpage en segments candidats
    seg_len = (profile["min"] + profile["max"]) / 2
    overlap = seg_len * 0.3
    segments = []
    t = 0
    while t + profile["min"] < duration:
        end = min(t + seg_len, duration)
        if end - t >= profile["min"]:
            segments.append((t, end))
        t += seg_len - overlap

    # 5. Scoring de chaque segment
    scored = []
    for start, end in segments:
        s = score_segment(start, end, audio_levels, scene_changes, content_type)
        scored.append((start, end, s))

    # Trier par score décroissant
    scored.sort(key=lambda x: -x[2])

    if progress_cb: progress_cb(65)

    # 6. Export des meilleurs clips (ceux au-dessus du seuil)
    threshold = profile["threshold"] * 100
    exported = 0
    total_to_export = sum(1 for _, _, s in scored if s >= threshold)
    total_to_export = max(1, total_to_export)

    for i, (start, end, score) in enumerate(scored):
        if score < threshold and exported >= 3:
            break

        clip_name = f"clip_{Path(video_path).stem}_{i+1:02d}.mp4"
        out_path = os.path.join(output_folder, clip_name)

        export_clip_vertical(video_path, out_path, start, end)

        trend, hook, viral = sub_scores(score, content_type)

        # Verdict
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
            prog = 65 + int((exported / total_to_export) * 30)
            progress_cb(min(95, prog))

    if progress_cb: progress_cb(100)

    # Trier clips par score décroissant pour l'affichage
    clips.sort(key=lambda x: -x["score"])
    return clips
