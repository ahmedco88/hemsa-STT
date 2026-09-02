"""Decode an imported audio/video file to 16 kHz mono WAV via PyAV. Anything ffmpeg
reads works: Zoom .m4a, Teams .mp4, .mp3, .wav. The source file is read, never kept -
only the decoded WAV lands in the meeting folder.

NOT IN THE PACKAGED BUILD, deliberately (2026-09-02). BSD-3 covers PyAV's Python
wrapper ONLY. The wheel also bundles ~25 compiled DLLs: FFmpeg itself (LGPLv3,
built --enable-version3), libiconv and libmp3lame (LGPL), and libx264 / libx265,
whose free builds are GPL - which does not sit inside an MIT installer. Hemsa only
ever DEMUXES and DECODES here, never encodes, so the encoders are dead weight, but
they cannot simply be deleted from the bundle: avcodec imports them through its PE
import table, so removing them fails PyAV at `import av` with "DLL load failed
while importing _core" (measured, not assumed). So `hemsa.spec` bundles no av at
all and file import is a run-from-source feature. Everything else about a meeting
(recording, transcription, summary) needs no ffmpeg. `available()` is the gate and
`tests/test_packaging_licence.py` fails the build if av returns to the spec.
See THIRD-PARTY-NOTICES.md.
"""

import wave
from pathlib import Path

import numpy as np

from .engine import SAMPLE_RATE


class ImportUnsupported(Exception):
    """File could not be read as audio - message is shown to the user."""


def available() -> bool:
    """True when PyAV can actually be loaded. Not the same as "av is installed":
    the ffmpeg DLLs sit beside the wrapper and a broken set fails at import."""
    try:
        import av                                             # noqa: F401
    except Exception:
        return False
    return True


def to_wav(src: Path, dest: Path) -> float:
    if not available():
        raise ImportUnsupported(
            "File import needs ffmpeg, which is not in the installer (its "
            "encoders are GPL and Hemsa is MIT). Run Hemsa from source with "
            "'pip install av' to import files. Recording still works.")
    try:
        import av
        with av.open(str(src)) as container:
            resampler = av.AudioResampler(format="s16", layout="mono",
                                          rate=SAMPLE_RATE)
            written = 0

            def _write(resampled) -> int:
                count = 0
                for r in resampled:
                    pcm = r.to_ndarray().reshape(-1).astype(np.int16)
                    out.writeframes(pcm.tobytes())
                    count += len(pcm)
                return count

            with wave.open(str(dest), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(SAMPLE_RATE)
                for frame in container.decode(audio=0):
                    written += _write(resampler.resample(frame))
                # libswresample buffers samples for its filter delay - without
                # this final flush (resample(None)) the tail of the audio is
                # silently dropped from both the WAV and the returned duration.
                written += _write(resampler.resample(None))
        if written == 0:
            raise ImportUnsupported(f"{src.name} contains no audio")
        return written / SAMPLE_RATE
    except ImportUnsupported:
        raise
    except Exception as exc:
        raise ImportUnsupported(
            f"Couldn't read {src.name} as audio ({exc})") from exc
