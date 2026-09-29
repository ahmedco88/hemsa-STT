"""Decode an audio/video file to 16 kHz mono int16 PCM with Windows' OWN codecs
(Media Foundation), so the packaged build can import phone .m4a recordings with no
ffmpeg in the installer. Nothing is bundled: the AAC decoder ships with Windows, so
there is no licence to carry. Formats Windows can play (m4a/aac, mp3, wav, wma, mp4)
work; anything it cannot (ogg, flac, webm) is reported as unreadable.

Media Foundation is COM, driven here through ctypes vtable calls. Only the handful of
methods needed are declared; the indexes are the slot order of the documented
interfaces (IUnknown = 0-2, IMFAttributes = 3-32, IMFSample/IMFMediaType add from 33).
"""

import ctypes
import sys
import uuid
from ctypes import POINTER, byref, c_void_p, c_uint32, c_int64, c_wchar_p

import numpy as np

from .engine import SAMPLE_RATE

_FIRST_AUDIO = 0xFFFFFFFD          # MF_SOURCE_READER_FIRST_AUDIO_STREAM
_ENDOFSTREAM = 0x2                 # MF_SOURCE_READERF_ENDOFSTREAM
_MF_VERSION = 0x00020070


class _GUID(ctypes.Structure):
    _fields_ = [("b", ctypes.c_ubyte * 16)]


def _guid(text: str) -> _GUID:
    g = _GUID()
    g.b[:] = uuid.UUID(text).bytes_le
    return g


_MAJOR = _guid("48eba18e-f8c9-4687-bf11-0a74c9f96a8f")
_SUBTYPE = _guid("f7e34c9a-42e8-4714-b74b-cb29d72c35e5")
_AUDIO = _guid("73647561-0000-0010-8000-00aa00389b71")
_PCM = _guid("00000001-0000-0010-8000-00aa00389b71")
_CHANNELS = _guid("37e48bf5-645e-4c5b-89de-ada9e29b696a")
_RATE = _guid("5faeeae7-0290-4c31-9e8a-c534f68d9dba")
_BITS = _guid("f2deb57f-40fa-4764-aa33-ed4f2d1ff669")
_ALIGN = _guid("322de230-9eeb-43bd-ab7a-ff412251541d")
_BYTERATE = _guid("1aab75c8-cfef-451c-ab95-ac034b8e1731")


def available() -> bool:
    return sys.platform == "win32"


def _call(obj, slot, restype, *argtypes):
    """Bind method `slot` of COM object `obj` (a c_void_p) as a callable."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[slot])
    return lambda *a: fn(obj, *a)


def _ok(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} failed (0x{hr & 0xFFFFFFFF:08X})")


def _release(obj) -> None:
    if obj and obj.value:
        _call(obj, 2, c_uint32)()


def decode(path: str) -> np.ndarray:
    """int16 mono samples at SAMPLE_RATE. Raises OSError if Windows can't read it."""
    ole32, mf = ctypes.windll.ole32, ctypes.windll.mfplat
    rdr = ctypes.windll.mfreadwrite
    ole32.CoInitializeEx(None, 0)                     # MTA; S_FALSE if already set
    _ok(mf.MFStartup(_MF_VERSION, 0), "MFStartup")
    reader, mtype = c_void_p(), c_void_p()
    chunks: list[bytes] = []
    try:
        _ok(rdr.MFCreateSourceReaderFromURL(c_wchar_p(path), None, byref(reader)),
            "open")
        _ok(mf.MFCreateMediaType(byref(mtype)), "MFCreateMediaType")
        set_guid = _call(mtype, 24, ctypes.HRESULT, POINTER(_GUID), POINTER(_GUID))
        set_u32 = _call(mtype, 21, ctypes.HRESULT, POINTER(_GUID), c_uint32)
        set_guid(byref(_MAJOR), byref(_AUDIO))
        set_guid(byref(_SUBTYPE), byref(_PCM))
        for key, val in ((_CHANNELS, 1), (_RATE, SAMPLE_RATE), (_BITS, 16),
                         (_ALIGN, 2), (_BYTERATE, SAMPLE_RATE * 2)):
            set_u32(byref(key), val)
        # Asking for PCM makes the source reader insert the AAC decoder AND the
        # resampler / channel mixer, so what comes back is already 16 kHz mono.
        _ok(_call(reader, 7, ctypes.HRESULT, c_uint32, c_void_p, c_void_p)(
            _FIRST_AUDIO, None, mtype), "no audio stream Windows can decode")
        read = _call(reader, 9, ctypes.HRESULT, c_uint32, c_uint32,
                     POINTER(c_uint32), POINTER(c_uint32), POINTER(c_int64),
                     POINTER(c_void_p))
        while True:
            stream, flags, stamp, sample = c_uint32(), c_uint32(), c_int64(), c_void_p()
            _ok(read(_FIRST_AUDIO, 0, byref(stream), byref(flags), byref(stamp),
                     byref(sample)), "read")
            if sample.value:
                buf = c_void_p()
                try:
                    _ok(_call(sample, 41, ctypes.HRESULT, POINTER(c_void_p))(
                        byref(buf)), "ConvertToContiguousBuffer")
                    data, _max, cur = c_void_p(), c_uint32(), c_uint32()
                    _ok(_call(buf, 3, ctypes.HRESULT, POINTER(c_void_p),
                              POINTER(c_uint32), POINTER(c_uint32))(
                        byref(data), byref(_max), byref(cur)), "Lock")
                    chunks.append(ctypes.string_at(data.value, cur.value))
                    _call(buf, 4, ctypes.HRESULT)()
                finally:
                    _release(buf)
                    _release(sample)
            if flags.value & _ENDOFSTREAM:
                break
    finally:
        _release(mtype)
        _release(reader)
        mf.MFShutdown()
    return np.frombuffer(b"".join(chunks), dtype=np.int16)
