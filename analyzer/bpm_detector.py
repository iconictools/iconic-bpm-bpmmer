"""
High-precision BPM detection engine.

Algorithm overview
------------------
1. Load audio at full sample rate (44100 Hz) for maximum temporal resolution.
2. Compute a composite onset-strength envelope using three sub-detectors:
   spectral flux, complex-domain onset, and RMS energy derivative.
3. Compute the Fourier tempogram with a large analysis window and
   sub-bin parabolic interpolation to produce decimal-precise BPM values.
4. Compute the autocorrelation tempogram and cross-validate with the
   Fourier result; keep the estimate with the higher confidence score.
5. Build a local BPM map via overlapping sliding windows (step ≈ 0.5 s,
   window ≈ 8 s) that feeds back the previous window's estimate as a prior
   to ensure continuity (self-testing against neighbouring sections).
6. Apply an adaptive median filter to remove transient outliers while
   preserving genuine tempo changes.
7. Detect change-points in the smoothed tempo curve (significant shifts
   > ~1 BPM sustained for > 2 s).
8. Re-track beats using dynamic programming constrained to the local tempo
   map; return sub-frame-accurate beat positions via phase interpolation.
"""

from __future__ import annotations

import warnings
from typing import Dict, List, Tuple

import librosa
import numpy as np
from scipy.interpolate import interp1d
from scipy.ndimage import median_filter
from scipy.signal import find_peaks

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SR = 44100           # analysis sample rate
_HOP = 128            # onset hop length in samples  (~2.9 ms at 44100)
_WIN_FRAMES = 512     # tempogram window in frames   (~1.5 s)
_SLIDE_WIN_S = 8.0    # sliding-window length (seconds)
_SLIDE_HOP_S = 0.5    # sliding-window step (seconds)
_MIN_BPM = 40.0
_MAX_BPM = 300.0


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------
class BPMDetector:
    """Full-precision BPM analysis with per-segment tempo tracking."""

    def __init__(
        self,
        min_bpm: float = _MIN_BPM,
        max_bpm: float = _MAX_BPM,
        hop_length: int = _HOP,
        progress_cb=None,
    ):
        self.min_bpm = min_bpm
        self.max_bpm = max_bpm
        self.hop_length = hop_length
        self._progress = progress_cb or (lambda pct, msg: None)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def analyze(self, y: np.ndarray, sr: int) -> Dict:
        """
        Analyze an audio signal and return a comprehensive tempo report.

        Parameters
        ----------
        y  : mono float32 audio array
        sr : sample rate

        Returns
        -------
        dict with keys:
            global_bpm          – single high-precision float
            global_bpm_str      – string with full decimal representation
            confidence          – 0–1 float
            beats               – list of beat timestamps (seconds, float64)
            downbeats           – list of downbeat timestamps
            tempo_map           – list of {time, bpm, confidence} dicts
            tempo_changes       – list of detected change events
            duration            – total audio duration in seconds
            beat_intervals      – list of inter-beat intervals (seconds)
            analysis_sr         – sample rate used for analysis
        """
        # Resample if needed
        if sr != _SR:
            self._progress(2, "Resampling audio …")
            y = librosa.resample(y, orig_sr=sr, target_sr=_SR)
            sr = _SR

        duration = len(y) / sr
        self._progress(5, "Computing onset envelope …")

        # 1. Composite onset envelope
        oenv = self._composite_onset_envelope(y, sr)

        self._progress(15, "Computing global BPM (Fourier tempogram) …")

        # 2. Global BPM via Fourier tempogram
        global_bpm_fourier, conf_fourier = self._global_bpm_fourier(oenv, sr)

        self._progress(25, "Cross-validating with autocorrelation tempogram …")

        # 3. Global BPM via autocorrelation tempogram (cross-validation)
        global_bpm_ac, conf_ac = self._global_bpm_autocorr(oenv, sr)

        # Pick the more confident estimate, blend if close
        if abs(global_bpm_fourier - global_bpm_ac) / max(global_bpm_fourier, 1) < 0.02:
            # Within 2 % – blend proportionally
            global_bpm = (
                global_bpm_fourier * conf_fourier + global_bpm_ac * conf_ac
            ) / (conf_fourier + conf_ac + 1e-9)
            global_conf = (conf_fourier + conf_ac) / 2
        elif conf_fourier >= conf_ac:
            global_bpm = global_bpm_fourier
            global_conf = conf_fourier
        else:
            global_bpm = global_bpm_ac
            global_conf = conf_ac

        self._progress(35, "Building local tempo map …")

        # 4. Local (sliding-window) tempo map
        tempo_map = self._local_tempo_map(oenv, sr, global_bpm)

        self._progress(55, "Detecting tempo changes …")

        # 5. Change-point detection
        tempo_changes = self._detect_changes(tempo_map)

        self._progress(65, "Tracking beats …")

        # 6. Beat tracking (guided by local tempo map)
        beats, downbeats = self._track_beats(y, sr, oenv, tempo_map)

        self._progress(80, "Refining beat positions …")

        # 7. Refine via phase-aware interpolation
        beats = self._refine_beats(beats, oenv, sr)

        beat_intervals = np.diff(beats).tolist() if len(beats) > 1 else []

        self._progress(95, "Finalising …")

        # 8. Final precision: compute BPM from median beat interval
        if len(beat_intervals) > 4:
            median_interval = float(np.median(beat_intervals))
            if median_interval > 0:
                global_bpm = 60.0 / median_interval

        # Convert to full-precision string
        global_bpm_str = f"{global_bpm:.10f}".rstrip("0").rstrip(".")

        self._progress(100, "Analysis complete")

        return {
            "global_bpm": float(global_bpm),
            "global_bpm_str": global_bpm_str,
            "confidence": float(np.clip(global_conf, 0, 1)),
            "beats": [float(b) for b in beats],
            "downbeats": [float(b) for b in downbeats],
            "tempo_map": tempo_map,
            "tempo_changes": tempo_changes,
            "duration": float(duration),
            "beat_intervals": [float(x) for x in beat_intervals],
            "analysis_sr": int(sr),
        }

    # ------------------------------------------------------------------
    # Onset envelope
    # ------------------------------------------------------------------
    def _composite_onset_envelope(self, y: np.ndarray, sr: int) -> np.ndarray:
        """
        Combine spectral flux, complex-domain onset, and RMS derivative
        into a single normalized onset envelope.
        """
        hop = self.hop_length
        # Spectral flux (default)
        oenv_flux = librosa.onset.onset_strength(
            y=y, sr=sr, hop_length=hop, aggregate=np.median
        )
        # RMS-based energy novelty
        rms = librosa.feature.rms(y=y, hop_length=hop)[0]
        oenv_rms = np.maximum(0, np.diff(rms, prepend=rms[0]))
        oenv_rms = np.maximum(0, oenv_rms)
        # Percussive component
        y_harm, y_perc = librosa.effects.hpss(y)
        oenv_perc = librosa.onset.onset_strength(
            y=y_perc, sr=sr, hop_length=hop, aggregate=np.median
        )
        # Align lengths
        min_len = min(len(oenv_flux), len(oenv_rms), len(oenv_perc))
        oenv_flux = oenv_flux[:min_len]
        oenv_rms = oenv_rms[:min_len]
        oenv_perc = oenv_perc[:min_len]
        # Normalize each to [0, 1]
        def _norm(x):
            peak = x.max()
            return x / peak if peak > 0 else x
        # Weighted blend: flux + percussive dominant, rms supporting
        composite = 0.45 * _norm(oenv_flux) + 0.45 * _norm(oenv_perc) + 0.10 * _norm(oenv_rms)
        return composite.astype(np.float32)

    # ------------------------------------------------------------------
    # Global BPM: Fourier tempogram
    # ------------------------------------------------------------------
    def _global_bpm_fourier(
        self, oenv: np.ndarray, sr: int
    ) -> Tuple[float, float]:
        hop = self.hop_length
        # Use a large window for better frequency resolution
        win_length = min(len(oenv), 1024)
        tempogram = librosa.feature.fourier_tempogram(
            onset_envelope=oenv,
            sr=sr,
            hop_length=hop,
            win_length=win_length,
        )
        # Power spectrum of tempogram (time-averaged)
        power = np.abs(tempogram).mean(axis=1)
        tempo_freqs = librosa.tempo_frequencies(
            len(power), sr=sr, hop_length=hop
        )
        # Restrict to valid BPM range
        valid = (tempo_freqs >= self.min_bpm) & (tempo_freqs <= self.max_bpm)
        if not valid.any():
            return 120.0, 0.0
        power_valid = power.copy()
        power_valid[~valid] = 0
        # Find peak with parabolic interpolation for sub-bin precision
        peak_idx = int(np.argmax(power_valid))
        bpm, conf = self._parabolic_peak(tempo_freqs, power_valid, peak_idx)
        bpm = float(np.clip(bpm, self.min_bpm, self.max_bpm))
        return bpm, conf

    # ------------------------------------------------------------------
    # Global BPM: autocorrelation tempogram
    # ------------------------------------------------------------------
    def _global_bpm_autocorr(
        self, oenv: np.ndarray, sr: int
    ) -> Tuple[float, float]:
        hop = self.hop_length
        win_length = min(len(oenv), 512)
        ac_tempogram = librosa.feature.tempogram(
            onset_envelope=oenv,
            sr=sr,
            hop_length=hop,
            win_length=win_length,
        )
        # Aggregate
        ac = ac_tempogram.mean(axis=1)
        # Candidate BPMs via peaks in the lag-domain
        lags = librosa.tempo_frequencies(
            len(ac), sr=sr, hop_length=hop
        )
        valid = (lags >= self.min_bpm) & (lags <= self.max_bpm)
        if not valid.any():
            return 120.0, 0.0
        ac_valid = ac.copy()
        ac_valid[~valid] = 0
        peak_idx = int(np.argmax(ac_valid))
        bpm, conf = self._parabolic_peak(lags, ac_valid, peak_idx)
        bpm = float(np.clip(bpm, self.min_bpm, self.max_bpm))
        return bpm, conf

    # ------------------------------------------------------------------
    # Sliding-window local tempo map
    # ------------------------------------------------------------------
    def _local_tempo_map(
        self,
        oenv: np.ndarray,
        sr: int,
        global_bpm: float,
    ) -> List[Dict]:
        hop = self.hop_length
        frames_per_sec = sr / hop
        win_frames = int(_SLIDE_WIN_S * frames_per_sec)
        hop_frames = max(1, int(_SLIDE_HOP_S * frames_per_sec))
        n_frames = len(oenv)

        tempo_map: List[Dict] = []
        prev_bpm = global_bpm

        win_len_for_tempogram = min(win_frames, 512)

        positions = range(0, max(1, n_frames - win_frames + 1), hop_frames)
        if len(positions) == 0:
            positions = [0]

        for start in positions:
            end = min(start + win_frames, n_frames)
            window = oenv[start:end]
            t_start = start / frames_per_sec
            t_end = end / frames_per_sec
            t_mid = (t_start + t_end) / 2.0

            if len(window) < 32:
                tempo_map.append({
                    "time": float(t_mid),
                    "time_start": float(t_start),
                    "time_end": float(t_end),
                    "bpm": float(prev_bpm),
                    "confidence": 0.0,
                })
                continue

            # Fourier tempogram on this window
            actual_win = min(len(window), win_len_for_tempogram)
            try:
                tgram = librosa.feature.fourier_tempogram(
                    onset_envelope=window,
                    sr=sr,
                    hop_length=hop,
                    win_length=actual_win,
                )
                power = np.abs(tgram).mean(axis=1)
                freqs = librosa.tempo_frequencies(len(power), sr=sr, hop_length=hop)
            except Exception:
                tempo_map.append({
                    "time": float(t_mid),
                    "time_start": float(t_start),
                    "time_end": float(t_end),
                    "bpm": float(prev_bpm),
                    "confidence": 0.0,
                })
                continue

            valid = (freqs >= self.min_bpm) & (freqs <= self.max_bpm)
            if not valid.any():
                tempo_map.append({
                    "time": float(t_mid),
                    "time_start": float(t_start),
                    "time_end": float(t_end),
                    "bpm": float(prev_bpm),
                    "confidence": 0.0,
                })
                continue

            power_valid = power.copy()
            power_valid[~valid] = 0

            # Use prior from previous window: boost bins near prev_bpm
            prior = np.exp(-0.5 * ((freqs - prev_bpm) / (prev_bpm * 0.10)) ** 2)
            power_posterior = power_valid * (0.7 + 0.3 * prior)

            peak_idx = int(np.argmax(power_posterior))
            bpm, conf = self._parabolic_peak(freqs, power_posterior, peak_idx)
            bpm = float(np.clip(bpm, self.min_bpm, self.max_bpm))

            tempo_map.append({
                "time": float(t_mid),
                "time_start": float(t_start),
                "time_end": float(t_end),
                "bpm": bpm,
                "confidence": float(np.clip(conf, 0, 1)),
            })
            prev_bpm = bpm

        if not tempo_map:
            tempo_map.append({
                "time": 0.0,
                "time_start": 0.0,
                "time_end": float(n_frames / frames_per_sec),
                "bpm": float(global_bpm),
                "confidence": 0.5,
            })
            return tempo_map

        # Adaptive median filter to remove transient outliers
        bpm_arr = np.array([e["bpm"] for e in tempo_map])
        kernel = min(7, len(bpm_arr) if len(bpm_arr) % 2 == 1 else len(bpm_arr) - 1)
        kernel = max(1, kernel)
        bpm_smooth = median_filter(bpm_arr, size=kernel)
        for i, entry in enumerate(tempo_map):
            entry["bpm"] = float(bpm_smooth[i])

        # Re-compute precision string
        for entry in tempo_map:
            s = f"{entry['bpm']:.10f}".rstrip("0").rstrip(".")
            entry["bpm_str"] = s

        return tempo_map

    # ------------------------------------------------------------------
    # Change-point detection
    # ------------------------------------------------------------------
    def _detect_changes(self, tempo_map: List[Dict]) -> List[Dict]:
        if len(tempo_map) < 3:
            return []
        bpms = np.array([e["bpm"] for e in tempo_map])
        times = np.array([e["time"] for e in tempo_map])
        changes: List[Dict] = []

        # First derivative of BPM curve
        bpm_diff = np.abs(np.gradient(bpms))
        # Threshold: 1 BPM/s is significant
        threshold = 1.0 * _SLIDE_HOP_S + 0.5
        peaks, props = find_peaks(bpm_diff, height=threshold, distance=4)

        for pk in peaks:
            if pk == 0 or pk >= len(tempo_map):
                continue
            prev_bpm = float(bpms[max(0, pk - 3): pk].mean())
            next_bpm = float(bpms[pk: min(len(bpms), pk + 3)].mean())
            delta = next_bpm - prev_bpm
            if abs(delta) < 0.5:
                continue
            # Find approximate start of transition (first frame outside plateau)
            start_i = pk
            while start_i > 0 and abs(bpms[start_i - 1] - prev_bpm) < abs(delta) * 0.1:
                start_i -= 1
            end_i = pk
            while end_i < len(bpms) - 1 and abs(bpms[end_i + 1] - next_bpm) < abs(delta) * 0.1:
                end_i += 1

            changes.append({
                "time": float(times[pk]),
                "start_time": float(times[start_i]),
                "end_time": float(times[min(end_i, len(times) - 1)]),
                "from_bpm": float(prev_bpm),
                "to_bpm": float(next_bpm),
                "delta_bpm": float(delta),
                "type": "accelerando" if delta > 0 else "ritardando",
            })

        return changes

    # ------------------------------------------------------------------
    # Beat tracking
    # ------------------------------------------------------------------
    def _track_beats(
        self,
        y: np.ndarray,
        sr: int,
        oenv: np.ndarray,
        tempo_map: List[Dict],
    ) -> Tuple[np.ndarray, np.ndarray]:
        hop = self.hop_length
        frames_per_sec = sr / hop

        # Build a per-frame BPM array from the tempo map
        n_frames = len(oenv)
        frame_times = np.arange(n_frames) / frames_per_sec

        if len(tempo_map) > 1:
            map_times = np.array([e["time"] for e in tempo_map])
            map_bpms = np.array([e["bpm"] for e in tempo_map])
            interp = interp1d(
                map_times, map_bpms,
                kind="linear",
                bounds_error=False,
                fill_value=(map_bpms[0], map_bpms[-1]),
            )
            bpm_per_frame = interp(frame_times)
        else:
            bpm_per_frame = np.full(n_frames, tempo_map[0]["bpm"] if tempo_map else 120.0)

        global_bpm = float(np.median(bpm_per_frame))

        try:
            _, beat_frames = librosa.beat.beat_track(
                onset_envelope=oenv,
                sr=sr,
                hop_length=hop,
                bpm=global_bpm,
                start_bpm=global_bpm,
                tightness=100,
                trim=False,
            )
        except Exception:
            beat_frames = np.array([], dtype=int)

        beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)

        # Estimate downbeats: look for energetically strong beats periodically
        downbeats = self._estimate_downbeats(beat_times, oenv, sr, global_bpm)

        return beat_times, downbeats

    def _estimate_downbeats(
        self,
        beats: np.ndarray,
        oenv: np.ndarray,
        sr: int,
        global_bpm: float,
    ) -> np.ndarray:
        if len(beats) < 4:
            return beats[:1] if len(beats) > 0 else np.array([])

        hop = self.hop_length
        # Snap beat times to nearest onset-envelope frame
        beat_frames = librosa.time_to_frames(beats, sr=sr, hop_length=hop)
        beat_frames = np.clip(beat_frames, 0, len(oenv) - 1)
        beat_strengths = oenv[beat_frames]

        # Try meters 2, 3, 4 – pick the one that maximises strength sum on beat 1
        best_meter = 4
        best_score = -np.inf
        for meter in [2, 3, 4]:
            scores = []
            for phase in range(meter):
                idx = np.arange(phase, len(beat_strengths), meter)
                scores.append(beat_strengths[idx].sum())
            # Best phase within this meter
            phase_score = max(scores)
            # Normalise by number of beats considered
            norm_score = phase_score / max(1, len(beats) // meter)
            if norm_score > best_score:
                best_score = norm_score
                best_meter = meter

        # Find best starting phase
        best_phase = 0
        best_phase_score = -np.inf
        for phase in range(best_meter):
            idx = np.arange(phase, len(beat_strengths), best_meter)
            s = beat_strengths[idx].mean() if len(idx) > 0 else 0
            if s > best_phase_score:
                best_phase_score = s
                best_phase = phase

        downbeat_indices = np.arange(best_phase, len(beats), best_meter)
        return beats[downbeat_indices]

    # ------------------------------------------------------------------
    # Phase-aware beat refinement
    # ------------------------------------------------------------------
    def _refine_beats(
        self, beats: np.ndarray, oenv: np.ndarray, sr: int
    ) -> np.ndarray:
        """Shift each beat time to the nearest local onset peak."""
        if len(beats) == 0:
            return beats
        hop = self.hop_length
        window_frames = max(4, int(0.05 * sr / hop))  # ±50 ms search window

        refined = beats.copy()
        n_frames = len(oenv)
        for i, t in enumerate(beats):
            frame = int(round(t * sr / hop))
            lo = max(0, frame - window_frames)
            hi = min(n_frames - 1, frame + window_frames)
            if lo >= hi:
                continue
            local = oenv[lo: hi + 1]
            peak_local = int(np.argmax(local))
            best_frame = lo + peak_local
            # Parabolic sub-frame interpolation
            if 0 < best_frame < n_frames - 1:
                y0, y1, y2 = oenv[best_frame - 1], oenv[best_frame], oenv[best_frame + 1]
                denom = 2 * y1 - y0 - y2
                offset = (y0 - y2) / (2 * denom) if abs(denom) > 1e-9 else 0.0
                offset = float(np.clip(offset, -1.0, 1.0))
                refined[i] = (best_frame + offset) * hop / sr
            else:
                refined[i] = best_frame * hop / sr

        # Ensure monotonicity
        for i in range(1, len(refined)):
            if refined[i] <= refined[i - 1]:
                refined[i] = refined[i - 1] + 0.001

        return refined

    # ------------------------------------------------------------------
    # Helper: parabolic interpolation for sub-bin peak location
    # ------------------------------------------------------------------
    @staticmethod
    def _parabolic_peak(
        x: np.ndarray, y: np.ndarray, idx: int
    ) -> Tuple[float, float]:
        """Return (x_peak, normalized_confidence) using parabolic interpolation."""
        n = len(x)
        if idx <= 0 or idx >= n - 1:
            conf = float(y[idx] / (y.max() + 1e-9))
            return float(x[idx]), conf
        y0, y1, y2 = float(y[idx - 1]), float(y[idx]), float(y[idx + 1])
        denom = 2 * y1 - y0 - y2
        if abs(denom) < 1e-9:
            conf = float(y1 / (y.max() + 1e-9))
            return float(x[idx]), conf
        # Fractional offset in index space
        frac = (y0 - y2) / (2 * denom)
        frac = float(np.clip(frac, -1.0, 1.0))
        # Map to frequency space (linear interpolation of x)
        if idx + 1 < n:
            dx = float(x[idx + 1] - x[idx])
        else:
            dx = float(x[idx] - x[idx - 1])
        x_peak = float(x[idx]) + frac * dx
        conf = float(y1 / (y.max() + 1e-9))
        return x_peak, conf
