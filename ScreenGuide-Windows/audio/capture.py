import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS    = 1
BLOCK_SIZE  = 1024


class MicCapture:
    """
    Real-time microphone capture using sounddevice (PortAudio wrapper).
    No C compilation required — ships prebuilt wheels on Windows.
    """

    def __init__(
        self,
        on_audio_chunk: Callable[[bytes], None],
        on_level: Callable[[float], None],
    ):
        self._on_chunk = on_audio_chunk
        self._on_level = on_level
        self._stream: Optional[sd.InputStream] = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=BLOCK_SIZE,
            callback=self._callback,
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

    def _callback(self, indata: np.ndarray, frames: int, time_info, status):
        if not self._running:
            return
        pcm_bytes = indata.tobytes()
        pcm_float = indata.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(pcm_float ** 2)))
        self._on_level(rms)
        self._on_chunk(pcm_bytes)


def apply_noise_gate(pcm_data: bytes, threshold: float = 0.008) -> bytes:
    """
    Suppresses low-level background noise (fans, hum, AC) by zeroing out
    blocks quieter than `threshold`, computed per ~30ms block so actual
    speech isn't affected — only steady background hiss between words.
    """
    audio = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
    if len(audio) == 0:
        return pcm_data
    block = max(1, SAMPLE_RATE * 30 // 1000)  # ~30ms
    out = audio.copy()
    for i in range(0, len(audio), block):
        chunk = audio[i:i + block]
        if len(chunk) == 0:
            continue
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        if rms < threshold:
            out[i:i + block] = 0.0
    return (out * 32767).astype(np.int16).tobytes()


def trim_silence(pcm_data: bytes, threshold: float = 0.008, pad_ms: int = 150) -> bytes:
    """
    Trims leading/trailing silence from a recorded utterance before it goes
    to Whisper — reduces hallucinated/garbled transcriptions caused by
    long silent stretches at the start or end of a push-to-talk clip.
    Keeps a small padding around detected speech.
    """
    audio = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
    if len(audio) == 0:
        return pcm_data
    block = max(1, SAMPLE_RATE * 30 // 1000)
    n_blocks = len(audio) // block
    if n_blocks == 0:
        return pcm_data
    is_speech = [
        float(np.sqrt(np.mean(audio[i * block:(i + 1) * block] ** 2))) > threshold
        for i in range(n_blocks)
    ]
    if not any(is_speech):
        return pcm_data  # all silence — let caller decide (e.g. skip/re-record)
    first = next(i for i, s in enumerate(is_speech) if s)
    last = len(is_speech) - 1 - next(i for i, s in enumerate(reversed(is_speech)) if s)
    pad_blocks = max(1, pad_ms * SAMPLE_RATE // 1000 // block)
    start = max(0, (first - pad_blocks) * block)
    end = min(len(audio), (last + 1 + pad_blocks) * block)
    trimmed = audio[start:end]
    return (trimmed * 32767).astype(np.int16).tobytes()

