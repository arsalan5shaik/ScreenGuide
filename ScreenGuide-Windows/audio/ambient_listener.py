"""
Always-on ambient audio listener.
Handles:
  - Continuous mic stream (single sounddevice input)
  - Energy-based VAD to detect speech segments
  - Wake-word detection via faster-whisper tiny model (triggers on "screenguide" / "hey screenguide")
  - Push-to-talk buffering when hotkey is held
  - Streams RMS level to UI (cursor waveform + panel)
"""

import threading
import time
from enum import Enum, auto
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from audio.capture import pcm16_to_wav, resample_pcm, SAMPLE_RATE


class Mode(Enum):
    STANDBY       = auto()   # wake-word scanning
    RECORDING     = auto()   # actively buffering user utterance


# ── Tuning knobs ──────────────────────────────────────────────────────────────
BLOCK_MS           = 30              # mic callback granularity
FRAMES_PER_BLOCK   = int(SAMPLE_RATE * BLOCK_MS / 1000)
ENERGY_THRESHOLD   = 0.006           # lower = catches quieter speech
MIN_SPEECH_BLOCKS  = 3               # ~90ms of speech to start a segment
SILENCE_BLOCKS_END = 20              # ~600ms of silence ends a segment
MAX_SEGMENT_BLOCKS = 120             # ~3.6s max wake-word segment
PRE_ROLL_BLOCKS    = 18              # ~540ms of pre-roll for the wake word

# Wake phrases — whisper tiny often mis-transcribes "screenguide" so we cover variants
WAKE_WORDS = (
    "screenguide", "click e", "click he", "click me", "clickie", "clicki",
    "cliki", "klicki", "klicky", "kilicky", "clickey", "clickity",
    "hey screenguide", "hi screenguide", "hey click", "ok screenguide", "yo screenguide",
    "hey clicki", "hey klicki", "hey clickie",
)


class AmbientListener:
    """
    Single sounddevice input stream with three outputs:
      1. level callback (always): drives cursor/panel waveform
      2. wake-word callback (standby): transcribes VAD segments with tiny whisper
      3. recording buffer (recording): full PCM buffer returned on stop_recording()
    """

    def __init__(
        self,
        on_level: Callable[[float], None],
        on_wake: Callable[[], None],
        device: Optional[int] = None,
    ):
        self._on_level = on_level
        self._on_wake = on_wake
        self._device = device       # None = system default input device
        self._stream_rate = SAMPLE_RATE   # actual rate the stream opens at

        self._mode: Mode = Mode.STANDBY
        self._stream: Optional[sd.InputStream] = None
        self._running = False

        # Rolling pre-roll ring buffer (small)
        self._preroll: list[np.ndarray] = []
        # Current speech segment buffer (for wake-word transcription)
        self._seg_buffer: list[np.ndarray] = []
        self._seg_speech_blocks = 0
        self._seg_silence_blocks = 0
        self._in_segment = False

        # Recording buffer (hotkey push-to-talk OR post-wake capture)
        self._rec_buffer: list[bytes] = []

        # Lazy tiny whisper for wake word
        self._wake_model = None
        self._wake_lock = threading.Lock()
        self._wake_inflight = False

        # Enable/disable toggle
        self._wake_word_enabled = True

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=FRAMES_PER_BLOCK,
                callback=self._callback,
                device=self._device,
            )
            self._stream_rate = SAMPLE_RATE
        except Exception:
            # Device doesn't support 16kHz directly — open at its native
            # rate and resample every block to 16kHz for Whisper.
            info = sd.query_devices(self._device, "input")
            native_rate = int(info["default_samplerate"])
            self._stream_rate = native_rate
            self._stream = sd.InputStream(
                samplerate=native_rate,
                channels=1,
                dtype="int16",
                blocksize=int(native_rate * BLOCK_MS / 1000),
                callback=self._callback,
                device=self._device,
            )
        self._stream.start()

    def stop(self):
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def start_recording(self) -> None:
        """Switch to RECORDING mode; all audio buffered for STT."""
        self._rec_buffer = []
        self._mode = Mode.RECORDING

    def stop_recording(self) -> bytes:
        """Return buffered PCM16 bytes and resume standby."""
        pcm = b"".join(self._rec_buffer)
        self._rec_buffer = []
        self._mode = Mode.STANDBY
        self._reset_segment()
        return pcm

    def set_wake_word_enabled(self, enabled: bool):
        self._wake_word_enabled = enabled

    @property
    def wake_word_enabled(self) -> bool:
        return self._wake_word_enabled

    # ── Audio callback ────────────────────────────────────────────────────────

    def _callback(self, indata: np.ndarray, frames: int, time_info, status):
        if not self._running:
            return

        pcm_int16 = indata[:, 0] if indata.ndim == 2 else indata
        if self._stream_rate != SAMPLE_RATE:
            pcm_int16 = np.frombuffer(
                resample_pcm(pcm_int16.tobytes(), self._stream_rate, SAMPLE_RATE),
                dtype=np.int16,
            )
        pcm_float = pcm_int16.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(pcm_float ** 2)))
        self._on_level(rms)

        if self._mode == Mode.RECORDING:
            self._rec_buffer.append(pcm_int16.tobytes())
            return

