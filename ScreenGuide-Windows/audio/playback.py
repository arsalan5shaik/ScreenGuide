"""
Shared audio playback helper — decodes MP3 bytes via PyAV and plays them
through sounddevice with **mid-stream cancellation** so Esc / Stop kills
TTS instantly instead of waiting for the buffer to drain.

The cancel mechanism is a module-level threading.Event the manager flips
via stop_audio(). All in-flight playback loops poll it between chunks.
"""

import asyncio
import io
import threading
from typing import Optional

import numpy as np
import sounddevice as sd
import av


# Single global flag — flipping this stops every active playback in this process
_stop_event = threading.Event()


def stop_audio() -> None:
    """Cancel any in-progress playback immediately. Safe to call from any thread."""
    _stop_event.set()
    try:
        sd.stop()                # boots the underlying PortAudio stream
    except Exception:
        pass


def _arm_audio() -> None:
    """Reset the stop flag at the start of a new playback."""
    _stop_event.clear()


def decode_mp3_to_pcm(mp3_bytes: bytes) -> tuple[np.ndarray, int]:
    """Decode MP3 (or any container PyAV supports) to float32 mono PCM."""
    container = av.open(io.BytesIO(mp3_bytes))
    stream = container.streams.audio[0]
    sample_rate = stream.rate

    chunks = []
    resampler = av.audio.resampler.AudioResampler(
        format="flt", layout="mono", rate=sample_rate
    )

    for frame in container.decode(stream):
        resampled = resampler.resample(frame)
        for rf in resampled:
            arr = rf.to_ndarray().flatten()
            chunks.append(arr)

