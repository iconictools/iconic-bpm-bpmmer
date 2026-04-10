"""
Song section / structure detection.

Algorithm
---------
1. Extract a combined feature matrix: MFCC (20 coefficients, Δ, ΔΔ) +
   chroma + spectral contrast + zero-crossing rate + RMS energy.
2. Build a recurrence (self-similarity) matrix from the feature matrix.
3. Apply a checkerboard-kernel novelty function along the diagonal to
   detect structural boundaries.
4. Peak-pick the novelty curve (with a minimum segment duration constraint)
   to yield boundary candidates.
5. Cluster the segments by feature similarity (KMeans) to discover
   repeating sections (A, B, C …).
6. Map cluster labels to musical section names using simple heuristics:
   - Intro / Outro: first / last segment
   - Chorus: most-repeated, highest energy cluster
   - Verse: second most-repeated cluster
   - Bridge: low-repetition, unique cluster
   - Pre-chorus / Drop / Break: by energy and position
7. Return segments with start/end times, label, cluster ID, and confidence.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import librosa
import numpy as np
from scipy.signal import find_peaks
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


# ---------------------------------------------------------------------------
_HOP = 512          # hop for feature extraction (larger → faster, coarser)
_MIN_SEG_S = 4.0    # minimum section length in seconds
_CHECKERBOARD_K = 32  # half-width of checkerboard kernel in frames


# ---------------------------------------------------------------------------
class SectionDetector:
    """Detect musical sections / structure in an audio signal."""

    def __init__(self, min_segment_s: float = _MIN_SEG_S, progress_cb=None):
        self.min_segment_s = min_segment_s
        self._progress = progress_cb or (lambda p, m: None)

    # ------------------------------------------------------------------
    def analyze(self, y: np.ndarray, sr: int) -> Dict:
        """
        Parameters
        ----------
        y  : mono float32 audio array
        sr : sample rate

        Returns
        -------
        dict:
            sections    – list of section dicts
            boundaries  – list of boundary times (seconds)
            n_sections  – int
        """
        self._progress(5, "Extracting features for section analysis …")
        features = self._extract_features(y, sr)

        self._progress(30, "Computing self-similarity matrix …")
        R = self._recurrence_matrix(features)

        self._progress(45, "Detecting section boundaries …")
        boundaries = self._detect_boundaries(R, sr, features.shape[1])

        self._progress(60, "Clustering sections …")
        sections = self._build_sections(features, boundaries, sr)

        self._progress(85, "Labelling sections …")
        sections = self._label_sections(sections)

        self._progress(100, "Section analysis complete")

        return {
            "sections": sections,
            "boundaries": [float(b) for b in boundaries],
            "n_sections": len(sections),
        }

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------
    def _extract_features(self, y: np.ndarray, sr: int) -> np.ndarray:
        hop = _HOP

        # MFCC + deltas (60 dims)
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=hop)
        mfcc_d = librosa.feature.delta(mfcc)
        mfcc_d2 = librosa.feature.delta(mfcc, order=2)

        # Chroma (12 dims)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)

        # Spectral contrast (7 dims)
        contrast = librosa.feature.spectral_contrast(y=y, sr=sr, hop_length=hop)

        # RMS energy (1 dim)
        rms = librosa.feature.rms(y=y, hop_length=hop)

        # ZCR (1 dim)
        zcr = librosa.feature.zero_crossing_rate(y, hop_length=hop)

        # Align all to the shortest
        min_len = min(
            mfcc.shape[1], mfcc_d.shape[1], mfcc_d2.shape[1],
            chroma.shape[1], contrast.shape[1], rms.shape[1], zcr.shape[1],
        )
        feat = np.vstack([
            mfcc[:, :min_len],
            mfcc_d[:, :min_len],
            mfcc_d2[:, :min_len],
            chroma[:, :min_len],
            contrast[:, :min_len],
            rms[:, :min_len],
            zcr[:, :min_len],
        ])  # shape: (n_features, n_frames)

        # L2-normalize each frame
        norms = np.linalg.norm(feat, axis=0, keepdims=True) + 1e-9
        feat = feat / norms
        return feat.astype(np.float32)

    # ------------------------------------------------------------------
    # Recurrence matrix
    # ------------------------------------------------------------------
    def _recurrence_matrix(self, feat: np.ndarray) -> np.ndarray:
        # Cosine similarity via dot product (features already L2-normalized)
        R = feat.T @ feat  # (n_frames, n_frames)
        # Lag-based: suppress main diagonal and near-diagonal (self-repetition)
        np.fill_diagonal(R, 0)
        # Gaussian blur to reduce noise
        from scipy.ndimage import gaussian_filter
        R = gaussian_filter(R, sigma=2.0)
        return R.astype(np.float32)

    # ------------------------------------------------------------------
    # Boundary detection via checkerboard kernel
    # ------------------------------------------------------------------
    def _detect_boundaries(
        self,
        R: np.ndarray,
        sr: int,
        n_frames: int,
    ) -> List[float]:
        n = R.shape[0]
        k = min(_CHECKERBOARD_K, n // 4)
        if k < 2:
            return [0.0]

        # Checkerboard kernel
        cb = np.array([
            [1 if (i < k) == (j < k) else -1
             for j in range(2 * k)]
            for i in range(2 * k)
        ], dtype=float)

        novelty = np.zeros(n)
        for t in range(k, n - k):
            block = R[t - k: t + k, t - k: t + k]
            if block.shape == cb.shape:
                novelty[t] = float(np.sum(cb * block))

        # Normalize
        nmax = novelty.max()
        if nmax > 0:
            novelty /= nmax

        # Minimum segment length in frames
        min_dist = max(4, int(self.min_segment_s * sr / _HOP))

        # Peak picking
        peaks, props = find_peaks(novelty, height=0.05, distance=min_dist)

        # Frame → time
        hop_time = _HOP / sr
        boundary_times = [0.0] + [float(p * hop_time) for p in peaks]
        return sorted(set(boundary_times))

    # ------------------------------------------------------------------
    # Segment feature vectors and clustering
    # ------------------------------------------------------------------
    def _build_sections(
        self,
        features: np.ndarray,
        boundaries: List[float],
        sr: int,
    ) -> List[Dict]:
        hop_time = _HOP / sr
        n_frames = features.shape[1]
        duration = n_frames * hop_time

        # Add end sentinel
        times = sorted(set(boundaries + [duration]))

        sections: List[Dict] = []
        for i in range(len(times) - 1):
            t_start = times[i]
            t_end = times[i + 1]
            if t_end - t_start < 0.5:
                continue
            f_start = int(round(t_start / hop_time))
            f_end = min(n_frames, int(round(t_end / hop_time)))
            if f_end <= f_start:
                continue
            seg_feat = features[:, f_start:f_end].mean(axis=1)  # mean pooling
            rms_val = float(features[-2, f_start:f_end].mean())  # RMS row

            sections.append({
                "start_time": float(t_start),
                "end_time": float(t_end),
                "duration": float(t_end - t_start),
                "mean_feature": seg_feat,
                "mean_energy": rms_val,
                "cluster": -1,
                "label": "section",
                "confidence": 0.5,
            })

        if not sections:
            sections.append({
                "start_time": 0.0,
                "end_time": float(duration),
                "duration": float(duration),
                "mean_feature": features.mean(axis=1),
                "mean_energy": float(features[-2].mean()),
                "cluster": 0,
                "label": "full track",
                "confidence": 0.5,
            })
            return sections

        # KMeans clustering on mean feature vectors
        feat_matrix = np.vstack([s["mean_feature"] for s in sections])
        scaler = StandardScaler()
        feat_scaled = scaler.fit_transform(feat_matrix)

        n_clusters = min(max(2, len(sections) // 2), 8, len(sections))
        try:
            km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
            labels = km.fit_predict(feat_scaled)
        except Exception:
            labels = np.zeros(len(sections), dtype=int)

        for i, sec in enumerate(sections):
            sec["cluster"] = int(labels[i])
            # Remove numpy array from output
            del sec["mean_feature"]

        return sections

    # ------------------------------------------------------------------
    # Section labelling heuristics
    # ------------------------------------------------------------------
    def _label_sections(self, sections: List[Dict]) -> List[Dict]:
        if not sections:
            return sections

        n = len(sections)
        # Count cluster occurrences
        from collections import Counter
        cluster_counts = Counter(s["cluster"] for s in sections)
        # Energy per cluster
        cluster_energy: Dict[int, float] = {}
        for s in sections:
            c = s["cluster"]
            cluster_energy[c] = cluster_energy.get(c, 0.0) + s["mean_energy"]

        # Sort clusters by count (desc) then energy (desc)
        sorted_clusters = sorted(
            cluster_counts.keys(),
            key=lambda c: (cluster_counts[c], cluster_energy[c]),
            reverse=True,
        )

        # Assign roles
        role_map: Dict[int, str] = {}
        available_roles = [
            "chorus",      # most repeated + high energy
            "verse",       # second most repeated
            "pre-chorus",  # third
            "bridge",      # low repetition
            "drop",        # high energy, less repeated
            "break",       # low energy
            "outro",       # end
            "section",     # fallback
        ]
        for i, c in enumerate(sorted_clusters):
            role_map[c] = available_roles[min(i, len(available_roles) - 1)]

        # Override first segment → intro, last → outro if they're unique
        if sections[0]["cluster"] not in [s["cluster"] for s in sections[1:]]:
            role_map[sections[0]["cluster"]] = "intro"
        if sections[-1]["cluster"] not in [s["cluster"] for s in sections[:-1]]:
            role_map[sections[-1]["cluster"]] = "outro"

        section_letters = {}
        letter_counter = {}
        for s in sections:
            c = s["cluster"]
            role = role_map.get(c, "section")
            # Assign letter (A, B, C …) per cluster
            if c not in section_letters:
                section_letters[c] = chr(65 + len(section_letters))  # A, B, C …
            letter = section_letters[c]
            # Count how many times this cluster has appeared
            letter_counter[letter] = letter_counter.get(letter, 0) + 1
            count = letter_counter[letter]

            s["label"] = role
            s["section_id"] = f"{letter}{count}"  # e.g. A1, B1, A2 …
            s["confidence"] = min(
                1.0,
                float(cluster_counts[c]) / max(1, n / len(cluster_counts)),
            )

        return sections
