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

