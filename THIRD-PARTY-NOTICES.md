# Third-party notices

Hemsa itself is MIT (see [LICENSE](LICENSE)). The installed application also
contains components written by other people, under their own licences. This file
lists them.

## Speech model

**Parakeet TDT 0.6B v2** (NVIDIA, int8 conversion by csukuangfj) - **CC-BY-4.0**.
Not bundled: it is downloaded on first run to `%LOCALAPPDATA%\Hemsa\models\`.

## Speech engine

**sherpa-onnx** (k2-fsa) - **Apache-2.0**. Bundled as DLLs.

## Audio capture

- **sounddevice** (Matthias Geier) - MIT, with **PortAudio** - MIT.
- **PyAudioWPatch** (s0d3s) - **Apache-2.0**, a patched PyAudio adding WASAPI
  loopback capture. Used for the "them" channel of a meeting recording.

## Media import (meetings) - NOT in the installer

Meeting **file import** uses **PyAV**, and PyAV is deliberately **not bundled**.
The installer contains no ffmpeg.

PyAV's Python wrapper is BSD-3-Clause, but its wheel also ships around 25
pre-built ffmpeg DLLs, and those are not BSD: ffmpeg itself is LGPL-3.0-or-later
(built `--enable-version3`), libiconv and libmp3lame are LGPL, and **libx264 and
libx265 are GPL-2.0-or-later** in their free builds. GPL libraries inside an MIT
installer would change the licence of the binary people download.

Hemsa only ever demuxes and decodes, never encodes, so those two encoders are
never called - but they cannot be removed one at a time. `avcodec` imports them
through its PE import table, so deleting them fails PyAV at `import av` with
"DLL load failed while importing _core" (measured, not assumed).

So the packaged app ships without it. Recording a meeting, transcribing it and
summarising it all work with no ffmpeg. Only importing an existing audio or video
file is affected, and that works when you run Hemsa from source:

```
.venv\Scripts\python.exe -m pip install av
```

Doing that puts GPL libraries on your own machine, which is your business; it is
redistributing them that this project avoids.

## Other Python dependencies

`requests` (Apache-2.0), `numpy` (BSD-3), `Pillow` (MIT-CMU), `pystray` (LGPL-3.0),
`pyperclip` (BSD-3), `keyboard` (MIT), `certifi` (MPL-2.0).

`pystray` is LGPL-3.0 and is used as an unmodified library through its public API.
