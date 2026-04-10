"""
iconic-bpm-bpmmer — Flask web application
=========================================
Routes
------
GET  /                     → main UI
POST /api/analyze           → upload audio, run full analysis, return JSON
GET  /api/progress/<job_id> → SSE stream of progress updates
POST /api/normalize         → normalize BPM and stream back download link
GET  /api/export/<job_id>/<fmt> → download export file
GET  /api/download/<filename>  → serve a generated file
"""

from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Dict

import librosa
import numpy as np
import soundfile as sf
from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
    stream_with_context,
)
from werkzeug.utils import secure_filename

from analyzer.bpm_detector import BPMDetector
from analyzer.exporter import TempoExporter
from analyzer.normalizer import BPMNormalizer
from analyzer.section_detector import SectionDetector
from analyzer.time_signature import TimeSignatureDetector

# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {
    "wav", "flac", "mp3", "aiff", "aif", "ogg", "m4a",
    "mp4", "wma", "opus", "au", "raw",
}
MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500 MB

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# In-memory job store
_JOBS: Dict[str, Dict] = {}
_PROGRESS: Dict[str, queue.Queue] = {}
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _push(job_id: str, pct: int, msg: str) -> None:
    with _LOCK:
        q = _PROGRESS.get(job_id)
    if q:
        q.put({"pct": pct, "msg": msg})


def _deep_clean(obj):
    if isinstance(obj, dict):
        return {k: _deep_clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_deep_clean(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename or not _allowed(f.filename):
        return jsonify({"error": "Unsupported file type"}), 400

    analyze_sections = request.form.get("sections", "true").lower() == "true"
    min_bpm = float(request.form.get("min_bpm", 40))
    max_bpm = float(request.form.get("max_bpm", 300))

    # Save upload
    job_id = str(uuid.uuid4())
    ext = Path(secure_filename(f.filename)).suffix
    upload_path = UPLOAD_DIR / f"{job_id}{ext}"
    f.save(str(upload_path))

    # Create progress queue
    q: queue.Queue = queue.Queue()
    with _LOCK:
        _PROGRESS[job_id] = q
        _JOBS[job_id] = {
            "status": "queued",
            "upload_path": str(upload_path),
            "original_filename": f.filename,
        }

    # Run analysis in background thread
    thread = threading.Thread(
        target=_run_analysis,
        args=(job_id, str(upload_path), analyze_sections, min_bpm, max_bpm),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


def _run_analysis(
    job_id: str,
    audio_path: str,
    analyze_sections: bool,
    min_bpm: float,
    max_bpm: float,
) -> None:
    def prog(pct: int, msg: str):
        _push(job_id, pct, msg)

    try:
        with _LOCK:
            _JOBS[job_id]["status"] = "loading"

        prog(1, "Loading audio file …")
        try:
            y, sr = librosa.load(audio_path, sr=None, mono=True)
        except Exception as e:
            raise RuntimeError(f"Cannot load audio: {e}") from e

        prog(3, f"Loaded {len(y)/sr:.1f}s of audio at {sr} Hz")

        # ── BPM Analysis ──────────────────────────────────────────────
        with _LOCK:
            _JOBS[job_id]["status"] = "bpm"

        def bpm_prog(pct, msg):
            prog(5 + int(pct * 0.45), msg)

        bpm_det = BPMDetector(
            min_bpm=min_bpm,
            max_bpm=max_bpm,
            progress_cb=bpm_prog,
        )
        bpm_result = bpm_det.analyze(y, sr)

        # ── Time Signature ────────────────────────────────────────────
        prog(52, "Detecting time signatures …")
        with _LOCK:
            _JOBS[job_id]["status"] = "time_signature"

        beats = np.array(bpm_result["beats"])
        if len(beats) > 1:
            beat_frames = librosa.time_to_frames(
                beats, sr=sr, hop_length=128
            )
            oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=128)
            beat_frames = np.clip(beat_frames, 0, len(oenv) - 1)
            beat_strengths = oenv[beat_frames]
        else:
            beat_strengths = None

        ts_det = TimeSignatureDetector(
            global_bpm=bpm_result["global_bpm"]
        )
        ts_result = ts_det.analyze(beats, beat_strengths=beat_strengths)

        # ── Section Detection ─────────────────────────────────────────
        section_result = {"sections": [], "boundaries": [], "n_sections": 0}
        if analyze_sections:
            prog(60, "Analysing song structure …")
            with _LOCK:
                _JOBS[job_id]["status"] = "sections"

            def sec_prog(pct, msg):
                prog(60 + int(pct * 0.30), msg)

            sec_det = SectionDetector(progress_cb=sec_prog)
            section_result = sec_det.analyze(y, sr)

        prog(92, "Compiling results …")

        # ── Assemble result ───────────────────────────────────────────
        result = {
            "job_id": job_id,
            "original_filename": _JOBS[job_id]["original_filename"],
            "bpm": bpm_result,
            "time_signature": ts_result,
            "sections": section_result,
            "analysis_complete": True,
        }

        result = _deep_clean(result)

        with _LOCK:
            _JOBS[job_id]["status"] = "done"
            _JOBS[job_id]["result"] = result

        prog(100, "Analysis complete ✓")

        # Signal completion
        with _LOCK:
            q = _PROGRESS.get(job_id)
        if q:
            q.put({"pct": 100, "msg": "done", "result": result})

    except Exception:
        err_msg = "Analysis failed"
        with _LOCK:
            _JOBS[job_id]["status"] = "error"
            _JOBS[job_id]["error"] = err_msg
        with _LOCK:
            q = _PROGRESS.get(job_id)
        if q:
            q.put({"pct": -1, "msg": f"Error: {err_msg}", "error": err_msg})


@app.route("/api/progress/<job_id>")
def progress(job_id: str):
    """Server-Sent Events stream for analysis progress."""
    with _LOCK:
        q = _PROGRESS.get(job_id)
    if q is None:
        return jsonify({"error": "Unknown job"}), 404

    @stream_with_context
    def generate():
        while True:
            try:
                event = q.get(timeout=30)
            except queue.Empty:
                # Keep-alive
                yield f"data: {json.dumps({'pct': -2, 'msg': 'ping'})}\n\n"
                continue

            yield f"data: {json.dumps(event)}\n\n"

            if event.get("pct") in (100, -1) or "error" in event:
                break

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/result/<job_id>")
def result(job_id: str):
    """Return the full analysis result JSON."""
    with _LOCK:
        job = _JOBS.get(job_id, {})
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    if job.get("status") == "error":
        return jsonify({"error": job.get("error", "Unknown error")}), 500
    if job.get("status") != "done":
        return jsonify({"status": job.get("status", "unknown")}), 202
    return jsonify(job["result"])


@app.route("/api/normalize", methods=["POST"])
def normalize():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    target_bpm = float(data.get("target_bpm", 120.0))
    output_format = data.get("format", "wav").lower()

    with _LOCK:
        job = _JOBS.get(job_id, {})
    if not job or job.get("status") != "done":
        return jsonify({"error": "Analysis not complete"}), 400

    result_data = job.get("result", {})
    bpm_data = result_data.get("bpm", {})
    beats = bpm_data.get("beats", [])
    audio_path = job.get("upload_path", "")

    if not beats or not os.path.exists(audio_path):
        return jsonify({"error": "Invalid job data"}), 400

    try:
        y, sr = librosa.load(audio_path, sr=None, mono=False)
        if y.ndim == 1:
            y_input = y
        else:
            y_input = y
    except Exception as e:
        return jsonify({"error": "Cannot reload audio file"}), 500

    norm_job_id = str(uuid.uuid4())
    norm_path = str(OUTPUT_DIR / f"{norm_job_id}_norm.{output_format}")

    q: queue.Queue = queue.Queue()
    with _LOCK:
        _PROGRESS[norm_job_id] = q
        _JOBS[norm_job_id] = {"status": "normalizing"}

    def _do_norm():
        def prog(pct, msg):
            _push(norm_job_id, pct, msg)
        try:
            normalizer = BPMNormalizer(progress_cb=prog)
            norm_result = normalizer.normalize(
                y=y_input,
                sr=sr,
                beats=beats,
                target_bpm=target_bpm,
                output_format=output_format,
                output_path=norm_path,
            )
            with _LOCK:
                _JOBS[norm_job_id]["status"] = "done"
                _JOBS[norm_job_id]["norm_path"] = norm_path
                _JOBS[norm_job_id]["result"] = norm_result
            q.put({"pct": 100, "msg": "done", "download_id": norm_job_id})
        except Exception:
            with _LOCK:
                _JOBS[norm_job_id]["status"] = "error"
                _JOBS[norm_job_id]["error"] = "Normalisation failed"
            q.put({"pct": -1, "msg": "Normalisation failed", "error": "Normalisation failed"})

    threading.Thread(target=_do_norm, daemon=True).start()
    return jsonify({"norm_job_id": norm_job_id})


@app.route("/api/export/<job_id>/<fmt>")
def export_file(job_id: str, fmt: str):
    """Generate and download an export file."""
    # Validate job_id is a UUID (prevents path traversal)
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({"error": "Invalid job ID"}), 400

    # Allowlist for export formats (prevents path traversal via fmt)
    _ALLOWED_EXPORT_FMTS = {"mid", "midi", "asd", "txt", "csv", "json", "markers"}
    fmt = fmt.lower()
    if fmt not in _ALLOWED_EXPORT_FMTS:
        return jsonify({"error": f"Unknown export format: {fmt}"}), 400

    with _LOCK:
        job = _JOBS.get(job_id, {})
    if not job or job.get("status") != "done":
        return jsonify({"error": "Analysis not complete or job not found"}), 400

    result_data = job.get("result", {})
    bpm_data = result_data.get("bpm", {})

    export_path = str(OUTPUT_DIR / f"{job_id}_export.{fmt}")
    exporter = TempoExporter()

    try:
        if fmt in ("mid", "midi"):
            exporter.export_midi(
                tempo_map=bpm_data.get("tempo_map", []),
                beats=bpm_data.get("beats", []),
                duration=bpm_data.get("duration", 0),
                output_path=export_path.replace(".midi", ".mid"),
            )
            export_path = export_path.replace(".midi", ".mid")
        elif fmt == "asd":
            exporter.export_ableton(result_data, export_path)
        elif fmt == "txt":
            exporter.export_fl_studio(
                tempo_map=bpm_data.get("tempo_map", []),
                beats=bpm_data.get("beats", []),
                output_path=export_path,
            )
        elif fmt == "csv":
            exporter.export_csv(result_data, export_path)
        elif fmt == "json":
            exporter.export_json(result_data, export_path)
        elif fmt == "markers":
            export_path = str(OUTPUT_DIR / f"{job_id}_export_beats.txt")
            exporter.export_beat_markers(bpm_data.get("beats", []), export_path)
    except Exception:
        return jsonify({"error": "Export failed"}), 500

    if not os.path.exists(export_path):
        return jsonify({"error": "Export file not created"}), 500

    return send_file(
        export_path,
        as_attachment=True,
        download_name=Path(export_path).name,
    )


@app.route("/api/download/<path:filename>")
def download_file(filename: str):
    """Serve a previously generated output file."""
    file_path = OUTPUT_DIR / secure_filename(filename)
    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(str(file_path), as_attachment=True)


@app.route("/api/download_norm/<norm_job_id>")
def download_norm(norm_job_id: str):
    """Download normalised audio."""
    with _LOCK:
        job = _JOBS.get(norm_job_id, {})
    if not job or job.get("status") != "done":
        return jsonify({"error": "Normalisation not complete"}), 400
    norm_path = job.get("norm_path", "")
    if not os.path.exists(norm_path):
        return jsonify({"error": "File not found"}), 404
    return send_file(norm_path, as_attachment=True)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="iconic-bpm-bpmmer server")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    args = parser.parse_args()

    print("=" * 60)
    print("  iconic-bpm-bpmmer — Ultimate BPM Finder")
    print(f"  Open http://localhost:{args.port} in your browser")
    print("=" * 60)
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
