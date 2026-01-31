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

