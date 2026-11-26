import argparse
import json
import resource
import time
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx


def decode(recognizer, samples):
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, samples)
    started = time.perf_counter()
    recognizer.decode_stream(stream)
    return time.perf_counter() - started, stream.result.text.strip()


parser = argparse.ArgumentParser()
parser.add_argument("label")
parser.add_argument("model_dir", type=Path)
args = parser.parse_args()

with wave.open("/tmp/open-xiaoai-kws-live-snapshot.wav", "rb") as wav:
    metadata = {
        "channels": wav.getnchannels(),
        "sample_rate": wav.getframerate(),
        "sample_width": wav.getsampwidth(),
        "frames": wav.getnframes(),
        "duration": wav.getnframes() / wav.getframerate(),
    }
    assert metadata["channels"] == 1
    assert metadata["sample_rate"] == 16000
    assert metadata["sample_width"] == 2
    assert metadata["duration"] >= 10
    pcm = wav.readframes(wav.getnframes())

samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
started = time.perf_counter()
recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model=str(args.model_dir / "model.int8.onnx"),
    tokens=str(args.model_dir / "tokens.txt"),
    language="auto",
    use_itn=True,
    num_threads=2,
    debug=False,
    provider="cpu",
)
load_seconds = time.perf_counter() - started
warmup_seconds, warmup_text = decode(recognizer, samples[:16000])

runs = {}
for duration in (3, 5, 10):
    duration_runs = []
    for _ in range(3):
        elapsed, text = decode(recognizer, samples[: duration * 16000])
        duration_runs.append({"seconds": elapsed, "text": text})
    runs[str(duration)] = duration_runs

mem_available_kib = 0
with open("/proc/meminfo", encoding="utf-8") as meminfo:
    for line in meminfo:
        if line.startswith("MemAvailable:"):
            mem_available_kib = int(line.split()[1])
            break

print(
    json.dumps(
        {
            "label": args.label,
            "sherpa_onnx": sherpa_onnx.__version__,
            "audio": metadata,
            "load_seconds": load_seconds,
            "warmup_seconds": warmup_seconds,
            "warmup_text": warmup_text,
            "runs": runs,
            "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "mem_available_kib": mem_available_kib,
        },
        ensure_ascii=False,
        indent=2,
    )
)
