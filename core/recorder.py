import io
import wave
import queue
import time
import numpy as np
import sounddevice as sd
from config import SAMPLE_RATE, CHANNELS, AUDIO_DTYPE, BLOCK_SIZE


class AudioRecorder:
    def __init__(self):
        self.audio_queue = queue.Queue()  # For UI visualization
        self.frames: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.is_recording = False
        self._start_time = 0.0

    def _callback(self, indata: np.ndarray, frames: int, time_info, status):
        if status:
            print(f"Audio status: {status}")
        self.audio_queue.put(indata.copy())
        self.frames.append(indata.copy())

    def start(self):
        self.frames.clear()
        # Drain any old data from the queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        self.is_recording = True
        self._start_time = time.time()
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=AUDIO_DTYPE,
            blocksize=BLOCK_SIZE,
            callback=self._callback,
        )
        self.stream.start()

    def stop(self) -> float:
        """Stop recording and return duration in seconds."""
        self.is_recording = False
        duration = time.time() - self._start_time
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        return duration

    def get_wav_buffer(self) -> io.BytesIO:
        """Convert recorded frames to in-memory WAV buffer (used for disk save)."""
        if not self.frames:
            return io.BytesIO()
        audio_data = np.concatenate(self.frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())
        buf.seek(0)
        return buf

    def _encode_mp3(self, audio_data: np.ndarray, bitrate_kbps: int = 32) -> io.BytesIO:
        """Encode an int16 PCM array (mono) to an in-memory MP3 buffer."""
        import lameenc
        enc = lameenc.Encoder()
        enc.set_bit_rate(bitrate_kbps)
        enc.set_in_sample_rate(SAMPLE_RATE)
        enc.set_channels(CHANNELS)
        enc.set_quality(2)  # 2 = high quality, 7 = fast/low

        buf = io.BytesIO()
        buf.write(enc.encode(audio_data.tobytes()))
        buf.write(enc.flush())
        buf.seek(0)
        return buf

    def get_mp3_buffer(self, bitrate_kbps: int = 32) -> io.BytesIO:
        """Convert recorded frames to in-memory MP3 buffer for upload.

        Why MP3 not WAV: voice at 32 kbps mono is ~8x smaller than 16-bit WAV
        and indistinguishable for ASR. Encoding 30s of audio takes <20ms.

        Whisper accepts mp3 natively — Groq decodes server-side.
        """
        if not self.frames:
            return io.BytesIO()
        audio_data = np.concatenate(self.frames, axis=0)
        return self._encode_mp3(audio_data, bitrate_kbps)

    def get_mp3_chunks(self, max_seconds: int = 600, bitrate_kbps: int = 32) -> list[io.BytesIO]:
        """Split the recording into <=max_seconds MP3 chunks for upload.

        Long recordings (30 min+) would blow past Groq's 25 MB file cap and the
        backend's serverless timeout in a single request. We slice the raw PCM
        into windows, snapping each cut to the quietest point within ±3s so a
        word is never chopped mid-syllable, then encode each window to MP3.

        Returns [] if empty, or a single-item list when the recording already
        fits in one window.
        """
        if not self.frames:
            return []
        audio_data = np.concatenate(self.frames, axis=0)
        mono = audio_data.reshape(-1)
        n = mono.shape[0]
        window = int(max_seconds * SAMPLE_RATE)
        if n <= window:
            return [self._encode_mp3(audio_data, bitrate_kbps)]

        chunks: list[io.BytesIO] = []
        search = int(3 * SAMPLE_RATE)              # snap within ±3s of target
        kernel = max(1, int(0.02 * SAMPLE_RATE))   # 20ms energy window
        start = 0
        while start < n:
            target = start + window
            if target >= n:
                cut = n
            else:
                lo = max(start + 1, target - search)
                hi = min(n, target + search)
                seg = np.abs(mono[lo:hi].astype(np.int32))
                energy = np.convolve(seg, np.ones(kernel, dtype=np.int32), mode="same")
                cut = lo + int(np.argmin(energy))
            chunks.append(self._encode_mp3(audio_data[start:cut], bitrate_kbps))
            start = cut
        return chunks

    def save_wav_to(self, path: str) -> str | None:
        """Write the current recording to disk at `path`. Returns path on success."""
        if not self.frames:
            return None
        audio_data = np.concatenate(self.frames, axis=0)
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())
        return path

    def get_duration(self) -> float:
        if not self.frames:
            return 0.0
        total_samples = sum(f.shape[0] for f in self.frames)
        return total_samples / SAMPLE_RATE
