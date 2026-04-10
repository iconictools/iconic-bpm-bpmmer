"""
BPM Normalizer: time-stretch audio to a stable target BPM and re-export
at the same quality (WAV, FLAC, or MP3 320 kbps).

Strategy
--------
1. Accept the beat positions found by BPMDetector.
2. The user specifies a target BPM (or we use the median of the tempo map).
3. Between every consecutive pair of beats:
   - Measure the actual duration of the beat interval.
   - Compute the ideal duration at the target BPM.
   - Time-stretch the interval by ratio = actual / ideal using a phase-
     vocoder (librosa) or WSOLA to preserve pitch.
4. Concatenate all stretched chunks with cross-fades to avoid clicks.
5. Export the result using soundfile (WAV/FLAC) or pydub + ffmpeg (MP3).
"""

from __future__ import annotations

import io
import os
import tempfile
from typing import Dict, List, Optional

import librosa
import numpy as np
import soundfile as sf


# ---------------------------------------------------------------------------
_CROSSFADE_MS = 10   # cross-fade duration between chunks (ms)


# ---------------------------------------------------------------------------
class BPMNormalizer:
    """Time-stretch audio to a stable target BPM."""

    def __init__(self, progress_cb=None):
        self._progress = progress_cb or (lambda p, m: None)

    # ------------------------------------------------------------------
    def normalize(
        self,
        y: np.ndarray,
        sr: int,
        beats: List[float],
        target_bpm: float,
        output_format: str = "wav",
        output_path: Optional[str] = None,
    ) -> Dict:
        """
        Parameters
        ----------
        y             : mono or stereo float32 array (shape (n,) or (2, n))
        sr            : sample rate
        beats         : list of beat timestamps (seconds) from BPMDetector
        target_bpm    : desired stable BPM
        output_format : 'wav', 'flac', or 'mp3'
        output_path   : if given, write file here; otherwise write to a temp file

        Returns
        -------
        dict with keys: output_path, duration, target_bpm, format
        """
        if len(beats) < 2:
            raise ValueError("Need at least 2 beats to normalise.")

        self._progress(5, "Preparing audio for normalisation …")
        is_stereo = y.ndim == 2
        if is_stereo:
            y_mono = y.mean(axis=0)
        else:
            y_mono = y

        ideal_interval = 60.0 / target_bpm
        cf_samples = int(_CROSSFADE_MS * sr / 1000)

        self._progress(10, "Time-stretching beat intervals …")
        chunks: List[np.ndarray] = []
        n_beats = len(beats)

        # Process each inter-beat interval
        for i in range(n_beats - 1):
            pct = 10 + int(70 * i / (n_beats - 1))
            if i % 20 == 0:
                self._progress(pct, f"Stretching beat {i+1}/{n_beats-1} …")

            t_start = beats[i]
            t_end = beats[i + 1]
            actual_dur = t_end - t_start
            if actual_dur <= 0:
                continue

            ratio = actual_dur / ideal_interval
            ratio = float(np.clip(ratio, 0.25, 4.0))

            s_start = int(round(t_start * sr))
            s_end = int(round(t_end * sr))
            s_start = max(0, s_start)
            s_end = min(len(y_mono), s_end)

            if s_end <= s_start:
                continue

            chunk = y_mono[s_start:s_end].copy()

            if abs(ratio - 1.0) < 0.001:
                stretched = chunk
            else:
                try:
                    stretched = librosa.effects.time_stretch(chunk, rate=ratio)
                except Exception:
                    stretched = chunk

            chunks.append(stretched.astype(np.float32))

        # Handle audio after the last beat
        last_sample = int(round(beats[-1] * sr))
        if last_sample < len(y_mono):
            tail = y_mono[last_sample:].copy()
            if len(tail) > 0:
                chunks.append(tail.astype(np.float32))

        if not chunks:
            raise ValueError("No audio chunks produced during normalisation.")

        self._progress(82, "Assembling normalised audio …")
        normalised = self._crossfade_join(chunks, cf_samples)

        # If stereo input, apply same stretch ratio to right channel
        if is_stereo:
            # Re-stretch right channel using the same overall ratio
            overall_ratio = len(y_mono) / max(1, len(normalised))
            try:
                right = librosa.effects.time_stretch(
                    y[1].astype(np.float32), rate=overall_ratio
                )
            except Exception:
                right = y[1][: len(normalised)]
            # Trim / pad to match
            if len(right) > len(normalised):
                right = right[: len(normalised)]
            elif len(right) < len(normalised):
                right = np.pad(right, (0, len(normalised) - len(right)))
            output_audio = np.stack([normalised, right], axis=0)
        else:
            output_audio = normalised

        self._progress(88, "Exporting normalised audio …")

        output_format = output_format.lower().strip(".")
        if output_path is None:
            suffix = f".{output_format}"
            tmp = tempfile.NamedTemporaryFile(
                delete=False, suffix=suffix, prefix="bpmmer_norm_"
            )
            output_path = tmp.name
            tmp.close()

        try:
            self._write_audio(output_audio, sr, output_path, output_format)
        except Exception as e:
            raise RuntimeError(f"Failed to write normalised audio: {e}") from e

        duration = len(normalised) / sr
        self._progress(100, "Normalisation complete")

        return {
            "output_path": output_path,
            "duration": float(duration),
            "target_bpm": float(target_bpm),
            "format": output_format,
            "sample_rate": int(sr),
            "channels": 2 if is_stereo else 1,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _crossfade_join(
        self, chunks: List[np.ndarray], cf_samples: int
    ) -> np.ndarray:
        """Concatenate chunks with linear cross-fades."""
        if not chunks:
            return np.array([], dtype=np.float32)
        if len(chunks) == 1:
            return chunks[0]

        cf = max(0, min(cf_samples, min(len(c) for c in chunks) // 2))
        if cf == 0:
            return np.concatenate(chunks)

        fade_out = np.linspace(1.0, 0.0, cf, dtype=np.float32)
        fade_in = np.linspace(0.0, 1.0, cf, dtype=np.float32)

        result = chunks[0].copy()
        for next_chunk in chunks[1:]:
            overlap = result[-cf:] * fade_out + next_chunk[:cf] * fade_in
            result = np.concatenate([result[:-cf], overlap, next_chunk[cf:]])

        return result

    def _write_audio(
        self,
        audio: np.ndarray,
        sr: int,
        path: str,
        fmt: str,
    ) -> None:
        """Write audio to file in the requested format."""
        if audio.ndim == 2:
            # soundfile expects (n_samples, n_channels)
            audio_sf = audio.T
        else:
            audio_sf = audio

        if fmt in ("wav", "flac"):
            subtype = "PCM_24" if fmt == "flac" else "PCM_16"
            sf.write(path, audio_sf, sr, subtype=subtype)
        elif fmt == "mp3":
            # Write WAV to buffer then convert via pydub + ffmpeg
            wav_buf = io.BytesIO()
            sf.write(wav_buf, audio_sf, sr, format="WAV", subtype="PCM_16")
            wav_buf.seek(0)
            try:
                from pydub import AudioSegment
                seg = AudioSegment.from_wav(wav_buf)
                seg.export(path, format="mp3", bitrate="320k")
            except Exception as e:
                # Fallback: write WAV instead
                fallback = path.replace(".mp3", ".wav")
                sf.write(fallback, audio_sf, sr, subtype="PCM_16")
                raise RuntimeError(
                    f"MP3 export failed (ffmpeg required). WAV saved to {fallback}. "
                    f"Original error: {e}"
                )
        else:
            # Generic fallback via soundfile
            sf.write(path, audio_sf, sr)
