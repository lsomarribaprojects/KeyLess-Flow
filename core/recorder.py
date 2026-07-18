import io
import os
import threading
import wave
import queue
import time
import numpy as np
import sounddevice as sd
from config import SAMPLE_RATE, CHANNELS, AUDIO_DTYPE, BLOCK_SIZE

# --- Optional dep: soundcard for system-audio (WASAPI loopback) capture ---
# sounddevice's WasapiSettings(loopback=True) was removed after 0.4.x, so we
# use `soundcard` instead — its API handles WASAPI loopback cleanly on
# Windows for any output device, including Bluetooth (AirPods etc). Missing
# import is not fatal: mic mode keeps working, system mode raises when used.
try:
    import soundcard as _soundcard
except Exception:  # pragma: no cover — user hasn't installed it
    _soundcard = None


class AudioRecorder:
    def __init__(self):
        self.audio_queue = queue.Queue()  # For UI visualization
        self.frames: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.is_recording = False
        self._start_time = 0.0
        # Per-recording health counters — used by the UI tick to surface a
        # warning if the mic stream is dropping frames (USB disconnect, system
        # under load, etc.). Reset on every start().
        self.dropout_count = 0
        self.overflow_count = 0
        # Rolling disk checkpoint — when active, every audio block also goes
        # through this queue to a background writer thread that appends to a
        # WAV file on disk. If the app crashes mid-recording (OOM, OS kill,
        # power loss), the partial WAV is still recoverable from the Hub.
        # Decoupled from the audio callback via a queue + thread so disk
        # latency never stalls the audio path.
        self._checkpoint_path: str | None = None
        self._checkpoint_wf: wave.Wave_write | None = None
        self._checkpoint_queue: queue.Queue | None = None
        self._checkpoint_thread: threading.Thread | None = None

    def _callback(self, indata: np.ndarray, frames: int, time_info, status):
        # `status` is a sounddevice CallbackFlags. Non-empty means SOMETHING
        # off — we differentiate "data was lost" (dropouts/overflow) from
        # harmless underflow so the UI only warns when audio quality is
        # actually at risk.
        if status:
            try:
                if status.input_overflow:
                    self.overflow_count += 1
                # `priming_output` exists on the flags but is irrelevant for input.
                if getattr(status, "input_underflow", False):
                    # Underflow on an input stream is rare; treat as a soft signal.
                    pass
            except Exception:
                pass
            # Always log so we can debug from hotkey.log if needed.
            print(f"Audio status: {status}")
        # System-audio (loopback) arrives as 48 kHz stereo int16. Fold to
        # mono + take every 3rd sample for a cheap 3:1 decimation to 16 kHz.
        # Speech is <8 kHz — the frequencies we drop are the ones Whisper
        # ignores anyway. Everything downstream (queue, frames, MP3 encoder)
        # keeps its 16 kHz mono assumption intact.
        if getattr(self, "_system_mode", False) and indata.ndim > 1:
            mono = indata.mean(axis=1).astype(np.int16)
            data = mono[::3].reshape(-1, 1)
        else:
            data = indata
        self.audio_queue.put(data.copy())
        self.frames.append(data.copy())
        # Best-effort hand-off to the disk checkpointer. Putting bytes (not
        # ndarray) is a touch faster on the writer side and avoids holding a
        # reference to a numpy buffer in the disk thread.
        if self._checkpoint_queue is not None:
            try:
                self._checkpoint_queue.put_nowait(indata.tobytes())
            except queue.Full:
                # Disk is slower than expected — drop the checkpoint chunk
                # (the in-memory `frames` still has it; we only lose crash
                # recoverability for this block).
                pass

    def start(self, checkpoint_path: str | None = None, mode: str = "mic"):
        """Begin capturing audio. If `checkpoint_path` is given, every block
        is also appended to a WAV at that path via a background thread so the
        recording is recoverable if the app dies mid-capture.

        `mode`:
          - "mic" (default): opens the default input device at SAMPLE_RATE mono.
          - "system": opens the default OUTPUT device via WASAPI loopback so we
            capture whatever the PC is playing (WhatsApp audio, YouTube, Zoom).
            Windows-only. Output devices default to 48 kHz stereo — we decimate
            + downmix to 16 kHz mono in the callback so downstream code
            (get_mp3_buffer, chunking, disk checkpoint) needs zero changes.

        Caller (main.py) is responsible for generating the path BEFORE the
        recording starts (so the path is known on both press AND release)."""
        self.frames.clear()
        self.dropout_count = 0
        self.overflow_count = 0
        self._system_mode = (mode == "system")
        # Drain any old data from the queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        # Rolling-checkpoint setup — open the wave file BEFORE starting the
        # stream so the very first callback can write into it. Bounded queue
        # (~10s of audio at 16kHz mono) keeps memory predictable; the
        # callback drops if disk is unable to keep up.
        if checkpoint_path:
            try:
                os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
                wf = wave.open(checkpoint_path, "wb")
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(SAMPLE_RATE)
                self._checkpoint_wf = wf
                self._checkpoint_path = checkpoint_path
                self._checkpoint_queue = queue.Queue(maxsize=512)
                self._checkpoint_thread = threading.Thread(
                    target=self._checkpoint_writer, daemon=True,
                )
                self._checkpoint_thread.start()
            except Exception as e:
                # Checkpoint is best-effort — failure here must not break
                # recording. Keep going with in-memory only.
                print(f"checkpoint setup failed: {e}")
                self._cleanup_checkpoint(delete_file=True)
        self.is_recording = True
        self._start_time = time.time()
        if self._system_mode:
            # WASAPI loopback via `soundcard`: record what the default OUTPUT
            # is playing (YouTube, WhatsApp audio, Zoom, etc.). Works with
            # Bluetooth outputs too — sounddevice's own loopback API stopped
            # working in 0.5.x so we route through soundcard instead.
            self._start_system_loopback()
        else:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=AUDIO_DTYPE,
                blocksize=BLOCK_SIZE,
                callback=self._callback,
            )
            self.stream.start()

    def _start_system_loopback(self):
        """Open a WASAPI loopback capture on the default output device and
        pump audio into `self._callback` from a background thread so the
        rest of the recorder pipeline (checkpoint, MP3 encode, chunking)
        keeps its existing callback-driven contract.

        soundcard delivers float32 in [-1, 1] at whatever samplerate we ask;
        we ask for SAMPLE_RATE (16 kHz) mono so the callback's downstream
        code sees the same shape as the mic path.
        """
        if _soundcard is None:
            raise RuntimeError(
                "soundcard no está instalado — instala con: pip install soundcard"
            )
        speaker = _soundcard.default_speaker()
        try:
            loopback_mic = _soundcard.get_microphone(
                id=str(speaker.name), include_loopback=True,
            )
        except Exception as e:
            raise RuntimeError(
                f"No pude abrir loopback para '{speaker.name}': {e}"
            )
        # Enter the recorder context manually so it lives across start/stop.
        self._loopback_cm = loopback_mic.recorder(
            samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK_SIZE,
        )
        self._loopback_rec = self._loopback_cm.__enter__()
        self._loopback_stop = False
        self._loopback_thread = threading.Thread(
            target=self._loopback_pump, daemon=True, name="KFLoopbackPump",
        )
        self._loopback_thread.start()

    def _loopback_pump(self):
        """Bg thread: pull blocks from soundcard and hand them to _callback.

        Squeezes the (frames, 1) array to 1D int16 so _callback's system-mode
        decimation branch (which expects 48 kHz stereo) is NOT triggered — we
        already deliver at SAMPLE_RATE mono.
        """
        try:
            while not getattr(self, "_loopback_stop", False):
                audio = self._loopback_rec.record(numframes=BLOCK_SIZE)
                if audio is None:
                    continue
                # Downmix to mono 1D and convert to int16.
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
                # Fake sounddevice's callback signature so we reuse _callback
                # unchanged. ndim=1 → the system-mode 3:1 decimation is skipped.
                try:
                    self._callback(pcm, len(pcm), None, None)
                except Exception as e:
                    print(f"loopback callback error (suppressed): {e}")
        except Exception as e:
            # Device unplugged mid-capture, etc. Downgrade to log; the caller
            # will see zero frames in `self.frames` and abort cleanly.
            print(f"loopback pump crashed: {e}")

    @staticmethod
    def _find_wasapi_output_device() -> int:
        """Return the device index of the default OUTPUT under the WASAPI
        host API. Kept for legacy callers / diagnostics; the live loopback
        path now uses soundcard (see _start_system_loopback)."""
        hostapis = sd.query_hostapis()
        wasapi = next(
            (i for i, api in enumerate(hostapis) if "wasapi" in api["name"].lower()),
            None,
        )
        if wasapi is None:
            raise RuntimeError("WASAPI host API not available")
        return sd.query_hostapis(wasapi)["default_output_device"]

    def _checkpoint_writer(self):
        """Drains the checkpoint queue to the WAV file. Sentinel `None` ends it."""
        while True:
            try:
                chunk = self._checkpoint_queue.get(timeout=5.0)
            except queue.Empty:
                # Stream may have ended without a sentinel; bail.
                if not self.is_recording:
                    return
                continue
            if chunk is None:
                return
            wf = self._checkpoint_wf
            if wf is None:
                return
            try:
                wf.writeframes(chunk)
            except Exception as e:
                print(f"checkpoint write failed: {e}")
                return

    def _cleanup_checkpoint(self, delete_file: bool = False):
        """Close the wave file + reset checkpoint state. Optionally delete the
        partial file (used when the recording was a sub-0.3s tap)."""
        path = self._checkpoint_path
        if self._checkpoint_queue is not None:
            try:
                self._checkpoint_queue.put_nowait(None)
            except Exception:
                pass
        if self._checkpoint_thread is not None:
            self._checkpoint_thread.join(timeout=2.0)
        if self._checkpoint_wf is not None:
            try:
                self._checkpoint_wf.close()
            except Exception:
                pass
        self._checkpoint_wf = None
        self._checkpoint_queue = None
        self._checkpoint_thread = None
        self._checkpoint_path = None
        if delete_file and path:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except Exception:
                pass

    def stop(self) -> float:
        """Stop recording and return duration in seconds.

        Closes the disk checkpoint file (if active). To discard the partial
        file for sub-threshold taps, call `discard_checkpoint()` afterward.
        """
        self.is_recording = False
        duration = time.time() - self._start_time
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        # System-audio path: signal the pump thread + close the soundcard
        # recorder context. Order matters — set the flag BEFORE joining so
        # the thread exits its while-loop this iteration.
        if getattr(self, "_loopback_thread", None) is not None:
            self._loopback_stop = True
            try:
                self._loopback_thread.join(timeout=2.0)
            except Exception:
                pass
            self._loopback_thread = None
        if getattr(self, "_loopback_cm", None) is not None:
            try:
                self._loopback_cm.__exit__(None, None, None)
            except Exception:
                pass
            self._loopback_cm = None
            self._loopback_rec = None
        # Finalize the checkpoint WAV — file becomes the canonical recording
        # so save_audio_for_retry doesn't need a second write.
        self._cleanup_checkpoint(delete_file=False)
        return duration

    @staticmethod
    def discard_checkpoint(path: str | None):
        """Delete a checkpoint WAV — used by callers to clean up after a
        sub-threshold tap. Safe to call with None or a non-existent path."""
        if not path:
            return
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception:
            pass

    @staticmethod
    def encode_wav_to_mp3_chunks(
        wav_path: str,
        max_seconds: int = 600,
        bitrate_kbps: int = 32,
    ) -> "list[io.BytesIO] | io.BytesIO":
        """Load a saved WAV and produce upload-ready MP3(s), chunking by the
        same silence-aligned logic as a live recording. Used by Hub retry so
        re-transcribing a long WAV survives Groq's 25 MB / 60 s timeout."""
        with wave.open(wav_path, "rb") as wf:
            n_channels = wf.getnchannels()
            samp_width = wf.getsampwidth()
            framerate = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        # Assume 16-bit mono — matches everything the recorder writes.
        if samp_width != 2:
            raise ValueError(f"unsupported WAV: {samp_width * 8}-bit")
        audio = np.frombuffer(raw, dtype=np.int16)
        if n_channels > 1:
            audio = audio.reshape(-1, n_channels).mean(axis=1).astype(np.int16)

        # Borrow a transient recorder solely for its chunking + MP3 encoder.
        # Cheap: __init__ doesn't open any audio device.
        shim = AudioRecorder()
        shim.frames = [audio]
        if (audio.shape[0] / framerate) > max_seconds:
            # Override sample rate inside the chunker via the actual constant
            # if the source WAV used a different one. Our recordings always
            # match SAMPLE_RATE, so this branch is the common path.
            return shim.get_mp3_chunks(max_seconds, bitrate_kbps)
        return shim.get_mp3_buffer(bitrate_kbps)

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

    def max_amplitude(self) -> int:
        """Peak absolute int16 sample across the whole recording (0..32767).

        Used to catch *effectively silent* captures — e.g. system-audio
        loopback when nothing is playing, or a dead/wrong mic. On silence
        Whisper tends to hallucinate the vocabulary prompt (the personal
        dictionary), so the caller skips transcription instead of pasting
        that garbage. Cheap: one pass over the in-memory frames."""
        if not self.frames:
            return 0
        peak = 0
        for f in self.frames:
            if f.size:
                # int16 min is -32768; abs() of that overflows int16, so widen.
                m = int(np.abs(f.astype(np.int32)).max())
                if m > peak:
                    peak = m
        return peak
