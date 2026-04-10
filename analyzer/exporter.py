"""
DAW-compatible tempo/marker export.

Supported formats
-----------------
MIDI tempo map  : Standard MIDI file (.mid) with tempo-change meta-events.
                  Readable by FL Studio, Ableton Live, Logic, Reaper, etc.
Ableton Live    : A text-based .als-markers or .asd (analysis) style JSON
                  that Ableton recognises when placed next to the audio file.
FL Studio       : A plain-text tempo-map (.txt) in the "TS:BPM@beat" format
                  used by FL Studio's tempo import (ZGameEditor / plugin).
CSV             : Universal spreadsheet-friendly tempo log.
JSON            : Machine-readable full analysis dump.
Beat markers    : Simple text list of beat timestamps, one per line.
"""

from __future__ import annotations

import json
import struct
import os
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
def _safe_write_path(output_path: str) -> str:
    """
    Resolve and return the real output path.
    Raises ValueError if the resolved path escapes safe write locations
    (i.e. it must live under /tmp, the cwd, or any path containing 'outputs').
    """
    resolved = os.path.realpath(output_path)
    # Accept paths inside common safe output areas
    safe_roots = (
        os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "outputs")),
        os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "uploads")),
        "/tmp",
    )
    if not any(resolved.startswith(root) for root in safe_roots):
        raise ValueError(f"Unsafe export path rejected: {output_path}")
    return resolved


# ---------------------------------------------------------------------------
class TempoExporter:
    """Export tempo maps and analysis results to various DAW formats."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def export_midi(
        self,
        tempo_map: List[Dict],
        beats: List[float],
        duration: float,
        output_path: str,
        ticks_per_beat: int = 960,
    ) -> str:
        """
        Write a Standard MIDI file containing only tempo-change meta-events
        and a click-track (note events) on channel 10.

        Parameters
        ----------
        tempo_map   : list of {time, bpm} dicts from BPMDetector
        beats       : list of beat timestamps (seconds)
        duration    : total audio duration (seconds)
        output_path : destination .mid file path
        ticks_per_beat : MIDI resolution

        Returns
        -------
        path to written file
        """
        if not tempo_map:
            tempo_map = [{"time": 0.0, "bpm": 120.0}]

        midi_bytes = self._build_midi(tempo_map, beats, duration, ticks_per_beat)
        safe_path = _safe_write_path(output_path)
        with open(safe_path, "wb") as f:
            f.write(midi_bytes)
        return safe_path

    # ------------------------------------------------------------------
    def export_ableton(
        self, analysis: Dict, output_path: str
    ) -> str:
        """
        Write an Ableton-compatible warp/analysis JSON file (.asd-like).
        Ableton Live 10+ can auto-detect an .asd file alongside an audio clip.

        Parameters
        ----------
        analysis    : full analysis dict from the server
        output_path : destination file path (recommend .asd)

        Returns
        -------
        path to written file
        """
        tempo_map = analysis.get("bpm", {}).get("tempo_map", [])
        global_bpm = analysis.get("bpm", {}).get("global_bpm", 120.0)
        beats = analysis.get("bpm", {}).get("beats", [])
        sr = analysis.get("bpm", {}).get("analysis_sr", 44100)
        duration = analysis.get("bpm", {}).get("duration", 0.0)

        # Build warp markers: list of (beat_time_in_seconds, beat_number)
        warp_markers = []
        for i, bt in enumerate(beats):
            warp_markers.append({
                "_type": "WarpMarker",
                "BeatTime": float(bt * global_bpm / 60.0),  # beat position
                "SecTime": float(bt),
            })

        asd = {
            "version": "1.0",
            "tool": "iconic-bpm-bpmmer",
            "DefaultWarpType": 4,
            "LongSample": 0,
            "Tempo": {
                "Value": float(global_bpm),
            },
            "WarpMarkers": warp_markers[:512],  # Ableton limit
            "TempoAutomation": [
                {"time": float(e["time"]), "bpm": float(e["bpm"])}
                for e in tempo_map
            ],
            "Duration": float(duration),
            "SampleRate": int(sr),
        }

        safe_path = _safe_write_path(output_path)
        with open(safe_path, "w", encoding="utf-8") as f:
            json.dump(asd, f, indent=2)
        return safe_path

    # ------------------------------------------------------------------
    def export_fl_studio(
        self, tempo_map: List[Dict], beats: List[float], output_path: str
    ) -> str:
        """
        Write an FL Studio-compatible tempo map text file.

        Format:
          # FL Studio Tempo Map
          # beat_number  bpm
          0  120.453721
          4  121.000000
          ...

        This can be imported via FL Studio's "Import tempo map" feature,
        or pasted into the automation editor.
        """
        lines = [
            "# FL Studio Tempo Map - generated by iconic-bpm-bpmmer",
            "# Format: BEAT_NUMBER  BPM",
            "# Import via: Tempo → right-click → Paste value",
            "",
        ]
        # Convert time-based map to beat-based map
        if beats and tempo_map:
            global_bpm = tempo_map[0]["bpm"] if tempo_map else 120.0
            for entry in tempo_map:
                t = entry["time"]
                bpm = entry["bpm"]
                # Approximate beat number from time and BPM
                beat_num = t * global_bpm / 60.0
                lines.append(f"{beat_num:.4f}\t{bpm:.10f}")
        else:
            lines.append(f"0\t{tempo_map[0]['bpm']:.10f}" if tempo_map else "0\t120.0")

        safe_path = _safe_write_path(output_path)
        with open(safe_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return safe_path

    # ------------------------------------------------------------------
    def export_csv(
        self, analysis: Dict, output_path: str
    ) -> str:
        """Write a comprehensive CSV tempo log."""
        tempo_map = analysis.get("bpm", {}).get("tempo_map", [])
        changes = analysis.get("bpm", {}).get("tempo_changes", [])
        ts_segments = analysis.get("time_signature", {}).get("segments", [])

        lines = ["time_s,bpm,bpm_precise,confidence,time_signature"]

        # Build a merged time-sorted list
        ts_by_time: Dict[float, str] = {}
        for seg in ts_segments:
            ts_by_time[seg["start_time"]] = seg["signature"]

        def get_ts(t: float) -> str:
            keys = sorted(ts_by_time.keys())
            sig = "4/4"
            for k in keys:
                if k <= t:
                    sig = ts_by_time[k]
            return sig

        for entry in tempo_map:
            t = entry["time"]
            bpm = entry["bpm"]
            conf = entry.get("confidence", 0.0)
            bpm_str = entry.get("bpm_str", f"{bpm:.10f}")
            ts = get_ts(t)
            lines.append(f"{t:.6f},{bpm:.6f},{bpm_str},{conf:.4f},{ts}")

        safe_path = _safe_write_path(output_path)
        with open(safe_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return safe_path

    # ------------------------------------------------------------------
    def export_json(
        self, analysis: Dict, output_path: str
    ) -> str:
        """Write the full analysis as a pretty-printed JSON file."""
        # Remove non-serialisable items
        clean = _deep_clean(analysis)
        safe_path = _safe_write_path(output_path)
        with open(safe_path, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False)
        return safe_path

    # ------------------------------------------------------------------
    def export_beat_markers(
        self, beats: List[float], output_path: str
    ) -> str:
        """Write one beat timestamp per line (seconds)."""
        safe_path = _safe_write_path(output_path)
        with open(safe_path, "w", encoding="utf-8") as f:
            for b in beats:
                f.write(f"{b:.10f}\n")
        return safe_path

    # ------------------------------------------------------------------
    # MIDI builder (no external dependencies)
    # ------------------------------------------------------------------
    def _build_midi(
        self,
        tempo_map: List[Dict],
        beats: List[float],
        duration: float,
        tpb: int,
    ) -> bytes:
        # Sort tempo map by time
        tmap = sorted(tempo_map, key=lambda e: e["time"])

        # --- Helper: variable-length quantity ---
        def vlq(n: int) -> bytes:
            n = int(n)
            result = [n & 0x7F]
            n >>= 7
            while n:
                result.append((n & 0x7F) | 0x80)
                n >>= 7
            return bytes(reversed(result))

        def bpm_to_mpqn(bpm: float) -> int:
            return int(round(60_000_000 / max(bpm, 1e-3)))

        # --- Tempo track (track 0) ---
        tempo_events: List[bytes] = []
        prev_tick = 0
        prev_time = 0.0

        # Convert bpm-map to running tick positions
        def time_to_tick(t: float, running_mpqn: int, base_tick: int, base_time: float) -> int:
            dt = t - base_time
            ticks = int(dt * 1_000_000 / running_mpqn * tpb)
            return base_tick + max(0, ticks)

        mpqn = bpm_to_mpqn(tmap[0]["bpm"])
        tick = 0
        for i, entry in enumerate(tmap):
            t = entry["time"]
            new_mpqn = bpm_to_mpqn(entry["bpm"])
            if i == 0:
                delta_ticks = 0
            else:
                delta_ticks = time_to_tick(t, mpqn, tick, prev_time) - tick
                delta_ticks = max(0, delta_ticks)
            tick += delta_ticks
            # Tempo meta-event: FF 51 03 tt tt tt
            ev = (
                vlq(delta_ticks)
                + b"\xff\x51\x03"
                + struct.pack(">I", new_mpqn)[1:]   # 3 bytes big-endian
            )
            tempo_events.append(ev)
            prev_time = t
            mpqn = new_mpqn

        # End-of-track
        tempo_events.append(b"\x00\xff\x2f\x00")
        tempo_track_data = b"".join(tempo_events)
        tempo_track = (
            b"MTrk"
            + struct.pack(">I", len(tempo_track_data))
            + tempo_track_data
        )

        # --- Click track (track 1): note on/off at each beat ---
        click_events: List[bytes] = []
        prev_tick2 = 0
        mpqn2 = bpm_to_mpqn(tmap[0]["bpm"])
        base_tick2 = 0
        base_time2 = 0.0
        tmap_idx = 0

        for bi, bt in enumerate(beats):
            # Advance mpqn to current position
            while tmap_idx + 1 < len(tmap) and tmap[tmap_idx + 1]["time"] <= bt:
                tmap_idx += 1
                base_time2 = tmap[tmap_idx]["time"]
                base_tick2 = time_to_tick(base_time2, mpqn2, base_tick2, base_time2 - 0.001)
                mpqn2 = bpm_to_mpqn(tmap[tmap_idx]["bpm"])

            cur_tick = time_to_tick(bt, mpqn2, base_tick2, base_time2)
            delta = max(0, cur_tick - prev_tick2)

            # Note-on: channel 10 (0x99), MIDI note 37 (snare) or 36 (bass)
            pitch = 37 if bi % 4 != 0 else 36
            velocity = 100 if bi % 4 == 0 else 80
            click_events.append(vlq(delta) + bytes([0x99, pitch, velocity]))

            # Note-off after 20 ticks
            click_events.append(vlq(20) + bytes([0x89, pitch, 0]))
            prev_tick2 = cur_tick + 20

        click_events.append(b"\x00\xff\x2f\x00")
        click_data = b"".join(click_events)
        click_track = (
            b"MTrk"
            + struct.pack(">I", len(click_data))
            + click_data
        )

        # --- MIDI header ---
        header = (
            b"MThd"
            + struct.pack(">I", 6)   # header length
            + struct.pack(">H", 1)   # format 1 (multi-track)
            + struct.pack(">H", 2)   # 2 tracks
            + struct.pack(">H", tpb) # ticks per beat
        )

        return header + tempo_track + click_track


# ---------------------------------------------------------------------------
def _deep_clean(obj):
    """Recursively convert numpy types to Python native types."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _deep_clean(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_deep_clean(v) for v in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj
