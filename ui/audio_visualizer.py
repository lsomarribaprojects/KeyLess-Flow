"""Audio visualizer for the pill — pro-grade spectrum bars.

Design choices (vs. naive approach that caused saturation):
- **Log-spaced bins** (geomspace 80Hz–8kHz). Speech energy concentrates in
  100–1000Hz; linear bins put 90% of action in the first 2 bars.
- **Hann window** before FFT to reduce spectral leakage.
- **dBFS scale** with -55 dB floor → 0 dB ceiling. Maps to perceptual loudness,
  the same way pro audio meters work (Audacity / Logic).
- **Dynamic peak normalization**: tracks running peak with slow decay so the
  bars stay alive across whisper-soft and loud speech without saturating.
- **BAR_GAIN as fine-tune**, not raw multiplier — values around 1.0 are sane.
- Spring physics retained for organic motion.
"""
import math
import queue
import numpy as np
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import QTimer, Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QLinearGradient
from config import NUM_BARS, VIZ_FPS, BAR_DECAY, BAR_GAIN, SAMPLE_RATE


# Frequency band of interest (covers the whole speech range with headroom).
_F_MIN_HZ = 80.0
_F_MAX_HZ = 8000.0

# dBFS mapping. Anything quieter than _DB_FLOOR is silence; map to 0..1.
# Tuned so normal speech sits around 0.5–0.85 (visible motion, no saturation),
# and loud peaks reach 0.95–1.0 only on the loudest band.
_DB_FLOOR = -50.0
_DB_CEIL = 0.0
_DB_RANGE = _DB_CEIL - _DB_FLOOR

# Running peak smoothing — controls how aggressively the bars adapt to
# sudden loudness shifts. Higher → calmer, slower to recover.
_PEAK_DECAY = 0.985  # per-frame decay (60fps → ~0.4s half-life)
_PEAK_FLOOR = 0.04   # noise floor — never normalize against pure silence


class AudioVisualizer(QWidget):
    """Premium audio visualizer. Log-spaced spectrum, dB scale, peak-normalized."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.num_bars = NUM_BARS
        self.bar_values = [0.0] * self.num_bars
        self._velocities = [0.0] * self.num_bars
        self._peak = _PEAK_FLOOR  # running peak for normalization
        self.audio_queue: queue.Queue | None = None

        # Lazy-built FFT band masks — depend on chunk length, computed on first use.
        self._mask_cache: dict[int, list[np.ndarray]] = {}

        self._timer = QTimer()
        self._timer.setInterval(1000 // VIZ_FPS)
        self._timer.timeout.connect(self._update_bars)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_audio_queue(self, q: queue.Queue):
        self.audio_queue = q

    def start(self):
        self.bar_values = [0.0] * self.num_bars
        self._velocities = [0.0] * self.num_bars
        self._peak = _PEAK_FLOOR
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.bar_values = [0.0] * self.num_bars
        self._velocities = [0.0] * self.num_bars
        self._peak = _PEAK_FLOOR
        self.update()

    def _band_masks(self, n: int) -> list[np.ndarray]:
        """Log-spaced FFT bin masks for `n`-sample chunks. Cached per n."""
        cached = self._mask_cache.get(n)
        if cached is not None:
            return cached
        freqs = np.fft.rfftfreq(n, d=1.0 / SAMPLE_RATE)
        edges = np.geomspace(_F_MIN_HZ, _F_MAX_HZ, self.num_bars + 1)
        masks = []
        for i in range(self.num_bars):
            mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
            # Ensure no band is empty (small n at low freq) — borrow nearest bin.
            if not mask.any():
                idx = int(np.argmin(np.abs(freqs - edges[i])))
                mask = np.zeros_like(freqs, dtype=bool)
                mask[idx] = True
            masks.append(mask)
        self._mask_cache[n] = masks
        return masks

    def _update_bars(self):
        if not self.audio_queue:
            return

        chunks = []
        while True:
            try:
                chunks.append(self.audio_queue.get_nowait())
            except queue.Empty:
                break

        targets = [0.0] * self.num_bars

        if chunks:
            # Concatenate the latest few chunks for a longer FFT window
            # (better low-freq resolution for speech fundamentals).
            recent = chunks[-3:]
            audio = np.concatenate([
                (c[:, 0] if c.ndim > 1 else c).astype(np.float32) for c in recent
            ])
            n = len(audio)
            if n >= 64:
                audio = audio / 32768.0
                # Hann window reduces spectral leakage between bars.
                window = np.hanning(n)
                fft = np.abs(np.fft.rfft(audio * window))
                # Normalize FFT magnitudes to compensate for window energy loss.
                fft *= 2.0 / np.sum(window)

                masks = self._band_masks(n)

                # Compute RMS energy per band first — normalize against the
                # peak band energy of this frame (or running peak, whichever
                # is larger). This is the correct domain (band RMS), not the
                # raw FFT max which would understate every band massively.
                band_energies = np.empty(self.num_bars, dtype=np.float32)
                for i in range(self.num_bars):
                    band = fft[masks[i]]
                    band_energies[i] = float(np.sqrt(np.mean(band * band))) if band.size else 0.0

                frame_peak = float(np.max(band_energies)) if band_energies.size else 0.0
                self._peak = max(self._peak * _PEAK_DECAY, frame_peak, _PEAK_FLOOR)

                for i in range(self.num_bars):
                    norm = band_energies[i] / self._peak if self._peak > 0 else 0.0
                    # dBFS conversion. Silence → -inf, current peak → 0 dB.
                    db = 20.0 * math.log10(norm + 1e-9)
                    # Map [_DB_FLOOR, _DB_CEIL] dB → [0, 1].
                    val = (db - _DB_FLOOR) / _DB_RANGE
                    val *= BAR_GAIN  # fine-tune knob (~1.0 = neutral)
                    targets[i] = max(0.0, min(1.0, val))

        # Spring physics: smooth attack, graceful release.
        dt = 1.0 / VIZ_FPS
        stiffness = 28.0  # was 35; calmer rise
        damping = 9.5     # was 8; less bouncy

        for i in range(self.num_bars):
            diff = targets[i] - self.bar_values[i]
            self._velocities[i] += diff * stiffness * dt
            self._velocities[i] *= max(0.0, 1.0 - damping * dt)
            self.bar_values[i] += self._velocities[i] * dt

            if self.bar_values[i] < 0.005:
                self.bar_values[i] = 0.0
                self._velocities[i] = 0.0
            elif self.bar_values[i] > 1.0:
                self.bar_values[i] = 1.0

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            painter.end()
            return

        bar_w = 1.8
        gap = 2.0
        total_w = self.num_bars * bar_w + (self.num_bars - 1) * gap
        x_off = (w - total_w) / 2.0
        min_h = 2.0
        center_idx = self.num_bars / 2.0
        # Cap bar height so it never escapes the pill — max 90% of pill height.
        max_bar_h = h * 0.9

        painter.setPen(Qt.PenStyle.NoPen)

        for i, val in enumerate(self.bar_values):
            dist = abs(i - center_idx + 0.5) / center_idx
            # Gaussian bell-curve taper from center
            taper = math.exp(-dist * dist * 1.2)
            bar_h = max(min_h, min(max_bar_h, val * h * 1.1 * taper))
            x = x_off + i * (bar_w + gap)
            cy = h / 2.0
            y = cy - bar_h / 2.0

            rect = QRectF(x, y, bar_w, bar_h)

            # Glow layer — soft halo behind each bar. Dimmed vs. previous
            # version (was 40); too much glow read as "muddy" at small sizes.
            if val > 0.04:
                glow_alpha = int(val * 28 * taper)
                glow_spread = bar_w + 3.0
                glow_rect = QRectF(
                    x - (glow_spread - bar_w) / 2, y - 1,
                    glow_spread, bar_h + 2,
                )
                painter.setBrush(QColor(255, 255, 255, glow_alpha))
                painter.drawRoundedRect(glow_rect, glow_spread / 2, glow_spread / 2)

            # Main bar — vertical gradient fading at tips
            gradient = QLinearGradient(x, y, x, y + bar_h)
            peak_alpha = int((90 + val * 150) * taper)
            edge_alpha = int(peak_alpha * 0.3)
            gradient.setColorAt(0.0, QColor(255, 255, 255, edge_alpha))
            gradient.setColorAt(0.35, QColor(255, 255, 255, peak_alpha))
            gradient.setColorAt(0.65, QColor(255, 255, 255, peak_alpha))
            gradient.setColorAt(1.0, QColor(255, 255, 255, edge_alpha))

            painter.setBrush(gradient)
            painter.drawRoundedRect(rect, bar_w / 2, bar_w / 2)

        painter.end()
