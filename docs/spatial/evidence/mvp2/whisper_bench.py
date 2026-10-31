import json, sys, time
from pathlib import Path
from faster_whisper import WhisperModel
folder = Path(sys.argv[1]); rows = []
for size in ("base", "small"):
    t = time.perf_counter(); model = WhisperModel(size, device="cpu", compute_type="int8"); load = time.perf_counter() - t
    model.transcribe(str(folder / "en_ok.wav"))  # warm-up
    for wav in sorted(folder.glob("*.wav")):
        lang = wav.name[:2]
        t = time.perf_counter()
        segs, info = model.transcribe(str(wav), language=lang, beam_size=1, vad_filter=False)
        text = " ".join(s.text.strip() for s in segs)
        ms = round((time.perf_counter() - t) * 1000)
        rows.append({"model": size, "file": wav.name, "ms": ms, "audio_s": round(info.duration, 2), "text": text})
        print(f"{size:5} {wav.name:12} {ms:5} ms  audio {info.duration:.1f}s  {text}", flush=True)
    print(f"{size} load {load:.1f}s", flush=True)
(folder / "bench.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
