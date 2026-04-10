"""
Time-signature detection.

Strategy
--------
1. Work at the *beat level*: given a list of beat timestamps, compute
   inter-beat intervals (IBIs).
2. For each candidate meter N ∈ {2, 3, 4, 5, 6, 7} and for each possible
   denominator ∈ {4, 8}, score the hypothesis by checking whether every
   Nth beat is preceded by N nearly-equal IBIs whose sum ≈ N × median_IBI.
3. Use a sliding window over beats to detect meter *transitions*.
4. The top-level denominator selection uses the global BPM:
   - BPM < 60  → likely compound meter (8th-note basis → denominator 8)
   - BPM > 160 → likely "felt in 2" → try half the numerator
   otherwise denominator = 4.
5. Return a list of segments: {start_time, end_time, numerator, denominator,
   beats_in_segment, confidence}.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.signal import find_peaks


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CANDIDATE_NUMERATORS = [2, 3, 4, 5, 6, 7]
_WINDOW_BEATS = 32   # beats per analysis window
_HOP_BEATS = 4       # stride in beats
_IBI_TOLERANCE = 0.12  # fraction: IBI can vary ±12 % and still be "equal"


# ---------------------------------------------------------------------------
class TimeSignatureDetector:
    """Detect and track time signature (meter) across a piece of music."""

    def __init__(self, global_bpm: float = 120.0, progress_cb=None):
        self.global_bpm = global_bpm
        self._progress = progress_cb or (lambda p, m: None)

    # ------------------------------------------------------------------
    def analyze(
        self,
        beats: np.ndarray,
        downbeats: Optional[np.ndarray] = None,
        beat_strengths: Optional[np.ndarray] = None,
    ) -> Dict:
        """
        Parameters
        ----------
        beats         : array of beat timestamps (seconds)
        downbeats     : optional array of downbeat timestamps
        beat_strengths: optional onset strength at each beat (same length)

        Returns
        -------
        dict:
            global_numerator    – int
            global_denominator  – int
            global_signature    – str  e.g. "4/4"
            global_confidence   – float
            segments            – list of segment dicts
            changes             – list of change-point dicts
        """
        if len(beats) < 8:
            return self._fallback(len(beats))

        # Default beat strengths to uniform if not provided
        if beat_strengths is None or len(beat_strengths) != len(beats):
            beat_strengths = np.ones(len(beats), dtype=float)

        ibis = np.diff(beats)  # inter-beat intervals

        self._progress(5, "Analyzing time signatures …")

        # Global estimate
        g_num, g_den, g_conf = self._estimate_meter_for_window(ibis, beat_strengths[:-1])

        self._progress(40, "Building time-signature map …")

        # Sliding-window segments
        segments = self._build_segments(beats, ibis, beat_strengths)

        self._progress(80, "Detecting signature changes …")

        changes = self._detect_changes(segments)

        return {
            "global_numerator": int(g_num),
            "global_denominator": int(g_den),
            "global_signature": f"{g_num}/{g_den}",
            "global_confidence": float(g_conf),
            "segments": segments,
            "changes": changes,
        }

    # ------------------------------------------------------------------
    # Segment builder
    # ------------------------------------------------------------------
    def _build_segments(
        self,
        beats: np.ndarray,
        ibis: np.ndarray,
        strengths: np.ndarray,
    ) -> List[Dict]:
        n_beats = len(beats)
        segments: List[Dict] = []
        prev_end = -1

        step = _HOP_BEATS
        for start_b in range(0, max(1, n_beats - _WINDOW_BEATS + 1), step):
            end_b = min(start_b + _WINDOW_BEATS, n_beats)
            win_ibis = ibis[start_b: end_b - 1]
            win_str = strengths[start_b: end_b]

            if len(win_ibis) < 4:
                continue

            num, den, conf = self._estimate_meter_for_window(win_ibis, win_str)

            seg = {
                "start_time": float(beats[start_b]),
                "end_time": float(beats[end_b - 1]),
                "start_beat": int(start_b),
                "end_beat": int(end_b - 1),
                "numerator": int(num),
                "denominator": int(den),
                "signature": f"{num}/{den}",
                "confidence": float(conf),
            }
            segments.append(seg)

        # If no segments were created, add a global one
        if not segments:
            g_num, g_den, g_conf = self._estimate_meter_for_window(ibis, strengths[:-1])
            segments.append({
                "start_time": float(beats[0]),
                "end_time": float(beats[-1]),
                "start_beat": 0,
                "end_beat": len(beats) - 1,
                "numerator": int(g_num),
                "denominator": int(g_den),
                "signature": f"{g_num}/{g_den}",
                "confidence": float(g_conf),
            })

        return segments

    # ------------------------------------------------------------------
    # Core meter estimator for a window of IBIs
    # ------------------------------------------------------------------
    def _estimate_meter_for_window(
        self,
        ibis: np.ndarray,
        strengths: np.ndarray,
    ) -> Tuple[int, int, float]:
        """Return (numerator, denominator, confidence) for a window."""
        if len(ibis) == 0:
            return 4, 4, 0.0

        median_ibi = float(np.median(ibis))
        if median_ibi <= 0:
            return 4, 4, 0.0

        best_num = 4
        best_den = 4
        best_score = -np.inf

        for num in _CANDIDATE_NUMERATORS:
            score = self._score_meter(ibis, strengths, num, median_ibi)
            if score > best_score:
                best_score = score
                best_num = num

        # Denominator heuristic
        best_den = self._choose_denominator(best_num, median_ibi)

        # Confidence: normalized score relative to 4/4
        conf_44 = self._score_meter(ibis, strengths, 4, median_ibi)
        conf_max = max(best_score, conf_44, 1e-6)
        confidence = float(np.clip(best_score / conf_max, 0.0, 1.0))

        return best_num, best_den, confidence

    def _score_meter(
        self,
        ibis: np.ndarray,
        strengths: np.ndarray,
        num: int,
        median_ibi: float,
    ) -> float:
        """
        Score a candidate meter (num beats per bar) for the given IBI window.

        We look for:
        1. Regular IBIs within each bar (variance penalty).
        2. Accent on beat 1 (highest mean strength).
        3. Overall regularity across bars.
        """
        n = len(ibis)
        if n < num:
            return 0.0

        # Pad to multiple of num
        pad = (num - n % num) % num
        ibis_padded = np.concatenate([ibis, np.full(pad, median_ibi)])
        bars = ibis_padded.reshape(-1, num)

        # Regularity within each bar
        bar_sums = bars.sum(axis=1)
        bar_mean = bar_sums.mean()
        bar_std = bar_sums.std()
        regularity = 1.0 / (1.0 + bar_std / (bar_mean + 1e-9))

        # IBI evenness within bars
        beat_stds = bars.std(axis=1).mean()
        evenness = 1.0 / (1.0 + beat_stds / (median_ibi + 1e-9))

        # Strength accent on beat-1
        if len(strengths) >= num:
            str_pad = len(strengths) % num
            s = strengths[: len(strengths) - str_pad if str_pad else len(strengths)]
            if len(s) >= num:
                s_bars = s.reshape(-1, num)
                beat1_mean = s_bars[:, 0].mean()
                other_mean = s_bars[:, 1:].mean() if num > 1 else beat1_mean
                accent = float(np.clip(beat1_mean / (other_mean + 1e-9), 0, 5)) / 5
            else:
                accent = 0.5
        else:
            accent = 0.5

        score = 0.4 * regularity + 0.4 * evenness + 0.2 * accent
        return float(score)

    def _choose_denominator(self, num: int, median_ibi: float) -> int:
        """
        Heuristic: choose denominator (4 or 8) based on the median IBI
        and global BPM.
        """
        bpm = self.global_bpm
        # Compound meters usually felt in 8ths
        if num in (6, 9, 12):
            return 8
        # Very slow quarter-note pulse suggests 8th-note basis
        if bpm < 55:
            return 8
        return 4

    # ------------------------------------------------------------------
    # Change detection
    # ------------------------------------------------------------------
    def _detect_changes(self, segments: List[Dict]) -> List[Dict]:
        changes: List[Dict] = []
        if len(segments) < 2:
            return changes
        prev = segments[0]
        for seg in segments[1:]:
            if seg["signature"] != prev["signature"]:
                # Confirm the change persists for at least 2 consecutive segments
                changes.append({
                    "time": float(seg["start_time"]),
                    "from_signature": prev["signature"],
                    "to_signature": seg["signature"],
                    "from_numerator": prev["numerator"],
                    "from_denominator": prev["denominator"],
                    "to_numerator": seg["numerator"],
                    "to_denominator": seg["denominator"],
                })
            prev = seg
        return changes

    # ------------------------------------------------------------------
    def _fallback(self, n_beats: int) -> Dict:
        return {
            "global_numerator": 4,
            "global_denominator": 4,
            "global_signature": "4/4",
            "global_confidence": 0.0,
            "segments": [],
            "changes": [],
        }
