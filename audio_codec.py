"""Small dependency-free IMA ADPCM packer for short distress clips."""

from __future__ import annotations

import array
import io
import wave


_STEP_TABLE = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
               34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
               143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
               494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
               1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026,
               4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
               11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
               27086, 29794, 32767]
_INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8]


def _encode_nibble(sample: int, predictor: int, index: int) -> tuple[int, int, int]:
    step = _STEP_TABLE[index]
    diff = sample - predictor
    code = 8 if diff < 0 else 0
    diff = abs(diff)
    delta = step >> 3
    if diff >= step:
        code |= 4
        diff -= step
        delta += step
    if diff >= step >> 1:
        code |= 2
        diff -= step >> 1
        delta += step >> 1
    if diff >= step >> 2:
        code |= 1
        delta += step >> 2
    predictor += -delta if code & 8 else delta
    predictor = max(-32768, min(32767, predictor))
    index = max(0, min(88, index + _INDEX_TABLE[code & 7]))
    return code, predictor, index


def _decode_nibble(code: int, predictor: int, index: int) -> tuple[int, int]:
    step = _STEP_TABLE[index]
    delta = step >> 3
    if code & 4:
        delta += step
    if code & 2:
        delta += step >> 1
    if code & 1:
        delta += step >> 2
    predictor += -delta if code & 8 else delta
    predictor = max(-32768, min(32767, predictor))
    index = max(0, min(88, index + _INDEX_TABLE[code & 7]))
    return predictor, index


def encode_wav(wav_bytes: bytes, max_seconds: float = 5.0) -> tuple[bytes, int, int]:
    """Encode PCM 16-bit WAV as a compact ADPCM body; return bytes, rate, duration ms."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as source:
        if source.getcomptype() != "NONE" or source.getsampwidth() != 2:
            raise ValueError("audio must be an uncompressed 16-bit PCM WAV")
        channels, rate, frames = source.getnchannels(), source.getframerate(), source.getnframes()
        if channels not in (1, 2):
            raise ValueError("audio must be mono or stereo")
        if frames / rate > max_seconds:
            raise ValueError(f"audio clip must be {max_seconds:g} seconds or shorter")
        raw = source.readframes(frames)
    duration_ms = round(frames * 1000 / rate)
    samples = array.array("h")
    samples.frombytes(raw)
    if __import__("sys").byteorder != "little":
        samples.byteswap()
    if channels == 2:
        samples = array.array("h", ((samples[i] + samples[i + 1]) // 2
                                     for i in range(0, len(samples), 2)))
    if rate > 8000:
        target_count = max(1, round(len(samples) * 8000 / rate))
        source_count = len(samples)
        samples = array.array("h", (
            round(samples[min(source_count - 1, int(i * source_count / target_count))] *
                  (1 - (i * source_count / target_count % 1)) +
                  samples[min(source_count - 1, int(i * source_count / target_count) + 1)] *
                  (i * source_count / target_count % 1))
            for i in range(target_count)
        ))
        rate = 8000
    if not samples:
        raise ValueError("audio clip is empty")
    predictor, index = samples[0], 0
    output = bytearray(predictor.to_bytes(2, "little", signed=True) + bytes([index]))
    pending = None
    for sample in samples[1:]:
        code, predictor, index = _encode_nibble(sample, predictor, index)
        if pending is None:
            pending = code
        else:
            output.append(pending | (code << 4))
            pending = None
    if pending is not None:
        output.append(pending)
    return bytes(output), rate, duration_ms


def decode_adpcm(data: bytes, sample_rate: int) -> bytes:
    """Decode the custom ADPCM body to a standard 16-bit mono PCM WAV."""
    if len(data) < 3:
        raise ValueError("ADPCM body is truncated")
    predictor = int.from_bytes(data[:2], "little", signed=True)
    index = data[2]
    if index > 88:
        raise ValueError("invalid ADPCM step index")
    samples = [predictor]
    for byte in data[3:]:
        for code in (byte & 0x0F, byte >> 4):
            predictor, index = _decode_nibble(code, predictor, index)
            samples.append(predictor)
    pcm = array.array("h", samples)
    if __import__("sys").byteorder != "little":
        pcm.byteswap()
    stream = io.BytesIO()
    with wave.open(stream, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm.tobytes())
    return stream.getvalue()
