"""Identify an exercise from a short video clip and match it to the
free-exercise-db so we can reuse a real demo animation + canonical data.

Flow: video -> N frames (ffmpeg) -> one vision call (PeakAI gpt-4o) to identify
-> shortlist free-exercise-db candidates -> one cheap text call to VALIDATE the
best match (so we never attach a wrong demo) -> build a proposed library entry
(with the matched demo frames when a real match exists, otherwise no demo).
"""
import base64
import difflib
import json
import os
import re
import subprocess
import tempfile

import requests

PEAKAI_URL = os.environ.get(
    "PEAKAI_CHAT_URL",
    os.environ.get("PEAKAI_URL", "http://192.168.68.33:4000").rstrip("/")
    + "/v1/chat/completions",
)
PEAKAI_KEY = os.environ.get("PEAKAI_API_KEY", "peak-homelab-key")
VISION_MODEL = os.environ.get("PEAKAI_VISION_MODEL", "gpt-4o")
TEXT_MODEL = os.environ.get("PEAKAI_CHEAP_MODEL", "gpt-4o-mini")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
FEDB_PATH = os.path.join(DATA_DIR, "free_exercise_db.json")
FEDB_IMG_BASE = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises"

CATEGORIES = ["legs", "upper", "core", "cardio"]
EQUIPMENT = ["bodyweight", "dumbbells", "barbell", "kettlebell", "resistance_bands",
             "pull_up_bar", "bench_or_chair", "power_cage_cable", "ab_machine"]

_fedb = None


def _load_fedb():
    global _fedb
    if _fedb is None:
        try:
            with open(FEDB_PATH) as f:
                _fedb = json.load(f)
        except Exception:
            _fedb = []
    return _fedb


def extract_frames(video_path, n=5):
    """Up to n base64 JPEG frames evenly spaced across the clip (downscaled)."""
    out_dir = tempfile.mkdtemp(prefix="exframes_")
    try:
        dur = 0.0
        try:
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", video_path],
                capture_output=True, text=True, timeout=30).stdout.strip() or 0)
        except Exception:
            pass
        vf = "scale=512:-1"
        if dur > 0:
            vf = f"fps={max(0.5, n / dur):.4f},scale=512:-1"
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vf", vf, "-frames:v", str(n),
             "-q:v", "3", os.path.join(out_dir, "f_%02d.jpg")],
            capture_output=True, timeout=90)
        frames = []
        for fn in sorted(os.listdir(out_dir)):
            if fn.endswith(".jpg"):
                with open(os.path.join(out_dir, fn), "rb") as fh:
                    frames.append(base64.b64encode(fh.read()).decode())
        return frames[:n]
    finally:
        for fn in os.listdir(out_dir):
            try:
                os.remove(os.path.join(out_dir, fn))
            except OSError:
                pass
        try:
            os.rmdir(out_dir)
        except OSError:
            pass


def _call(messages, model, max_tokens=700):
    resp = requests.post(
        PEAKAI_URL,
        headers={"Authorization": f"Bearer {PEAKAI_KEY}", "Content-Type": "application/json"},
        json={"model": model, "stream": False, "max_tokens": max_tokens, "messages": messages},
        timeout=90)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _json(text):
    text = re.sub(r"^```(json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


VISION_SYS = (
    "You are a fitness expert identifying ONE exercise from sequential video frames. "
    "Respond ONLY with JSON:\n"
    '{"name":"common exercise name","aliases":["alternate names"],'
    '"primary_muscles":["lowercase muscles"],'
    '"equipment":"closest of: ' + ", ".join(EQUIPMENT) + '",'
    '"category":"one of: ' + ", ".join(CATEGORIES) + '",'
    '"description":"one sentence describing the movement",'
    '"est_sets":3,"est_reps":10,"est_unit":"reps or seconds"}\n'
    "No prose, no code fences."
)


def identify(frames_b64):
    content = [{"type": "text", "text": "Identify this exercise from these in-order frames."}]
    for b in frames_b64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}})
    return _json(_call(
        [{"role": "system", "content": VISION_SYS}, {"role": "user", "content": content}],
        VISION_MODEL, 600))


def _shortlist(ident, k=8):
    fedb = _load_fedb()
    names = [ident.get("name", "")] + (ident.get("aliases") or [])
    names = [q.lower() for q in names if q]
    muscles = {m.lower() for m in (ident.get("primary_muscles") or [])}
    scored = []
    for e in fedb:
        nm = e["name"].lower()
        sim = max((difflib.SequenceMatcher(None, q, nm).ratio() for q in names), default=0)
        overlap = 0.15 if muscles & {m.lower() for m in (e.get("primaryMuscles") or [])} else 0
        scored.append((sim + overlap, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:k]]


MATCH_SYS = (
    "You decide whether a described exercise is the SAME movement as one in a "
    "candidate list (so we can reuse its demo). Return ONLY JSON: "
    '{"match": exact candidate name or null, "confidence": 0.0-1.0}. '
    "Only match a genuinely identical movement pattern; if none fits, return null."
)


def match_db(ident):
    cands = _shortlist(ident)
    if not cands:
        return None, 0.0
    listing = "\n".join(
        f"- {e['name']} (muscles: {', '.join(e.get('primaryMuscles') or [])})" for e in cands)
    desc = (f"{ident.get('name', '')}: {ident.get('description', '')} | "
            f"muscles: {', '.join(ident.get('primary_muscles') or [])}")
    res = _json(_call(
        [{"role": "system", "content": MATCH_SYS},
         {"role": "user", "content": f"Movement: {desc}\n\nCandidates:\n{listing}"}],
        TEXT_MODEL, 120))
    name, conf = res.get("match"), float(res.get("confidence") or 0)
    if name:
        for e in cands:
            if e["name"] == name:
                return e, conf
    return None, conf


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]


def build_proposal(ident, db_match, conf):
    name = db_match["name"] if db_match else ident.get("name", "Unknown Exercise")
    demo, instructions = None, ident.get("description", "")
    if db_match:
        imgs = db_match.get("images") or []
        if imgs:
            a = f"{FEDB_IMG_BASE}/{imgs[0]}"
            b = f"{FEDB_IMG_BASE}/{imgs[1]}" if len(imgs) >= 2 else a
            demo = [a, b]
        if db_match.get("instructions"):
            instructions = " ".join(db_match["instructions"])
    cat = ident.get("category") if ident.get("category") in CATEGORIES else "core"
    equip = ident.get("equipment") if ident.get("equipment") in EQUIPMENT else "bodyweight"
    unit = ident.get("est_unit") if ident.get("est_unit") in ("reps", "seconds") else "reps"
    entry = {
        "id": _slug(name),
        "name": name,
        "category": cat,
        "difficulty": 2,
        "equipment": equip,
        "contraindications": [],
        "met": 4.0,
        "default_reps": int(ident.get("est_reps") or (30 if unit == "seconds" else 10)),
        "default_sets": int(ident.get("est_sets") or 3),
        "rest_seconds": 45,
        "instructions": (instructions or "").strip()[:600],
        "muscle_group": (ident.get("primary_muscles") or ["full_body"])[0],
    }
    if unit == "seconds":
        entry["unit"] = "seconds"
    return {
        "exercise": entry,
        "demo": demo,
        "db_match": db_match["name"] if db_match else None,
        "identified_as": ident.get("name"),
        "confidence": round(conf, 2),
    }


def identify_video(video_path):
    frames = extract_frames(video_path, n=5)
    if not frames:
        return {"error": "Could not read frames from the video."}
    ident = identify(frames)
    if not ident.get("name"):
        return {"error": "Could not identify the exercise from the clip."}
    db_match, conf = match_db(ident)
    proposal = build_proposal(ident, db_match, conf)
    proposal["frames_analyzed"] = len(frames)
    return proposal
