#!/usr/bin/env python3
"""Persistent VAD + whisper worker for the /listen live call assistant.

Reads fixed-length audio frames that ffmpeg writes into a frame dir, groups them
into utterances using a simple energy gate (speech vs. silence), and transcribes
each utterance the moment the speaker pauses — appending a timestamped line to
the transcript file. The whisper model is loaded ONCE and kept resident, so
per-utterance latency is just the transcribe time, not a cold model load.

Stdlib only (array/wave/glob/math) except `whisper` itself.

Env (all optional, env-overridable):
  FRAME_DIR        dir ffmpeg writes chunk_*.wav into          (required)
  TRANSCRIPT       path to append "- [HH:MM:SS] text" lines to (required)
  WORK             work dir holding the stopfile               (req for stop)
  WHISPER_MODEL    default base.en
  FRAME_SEC        seconds per frame (must match ffmpeg segment_time)
  SILENCE_DB       dBFS below which a frame is "silence" (default -40)
  GAP_SEC          trailing silence that ends an utterance (default 0.8s)
  MIN_SPEECH_SEC   ignore utterances shorter than this (default 0.4s)
  MAX_UTT_SEC      force-flush a long monologue after this many seconds (15)
"""
import os, sys, glob, time, wave, array, math

FRAME_DIR    = os.environ.get("FRAME_DIR")
TRANSCRIPT   = os.environ.get("TRANSCRIPT")
WORK         = os.environ.get("WORK", os.path.dirname(TRANSCRIPT or "."))
MODEL_NAME   = os.environ.get("WHISPER_MODEL", "base.en")
FRAME_SEC    = float(os.environ.get("FRAME_SEC", "0.5"))
SILENCE_DB   = float(os.environ.get("SILENCE_DB", "-40"))
GAP_SEC      = float(os.environ.get("GAP_SEC", "0.8"))
MIN_SPEECH   = float(os.environ.get("MIN_SPEECH_SEC", "0.4"))
MAX_UTT_SEC  = float(os.environ.get("MAX_UTT_SEC", "15"))

GAP_FRAMES = max(1, round(GAP_SEC / FRAME_SEC))
MIN_FRAMES = max(1, round(MIN_SPEECH / FRAME_SEC))
MAX_FRAMES = max(2, round(MAX_UTT_SEC / FRAME_SEC))

HALLUC = {"", "you", "thank you", "thanks", "thanks for watching",
          "thank you for watching", "bye", "bye bye", "please subscribe",
          "you you", "thank you for watching don't forget to subscribe"}
HALLUC_NS = {h.replace(" ", "") for h in HALLUC}

def norm(s):
    return "".join(c for c in s.lower() if c.isalpha() or c == " ").strip()

def is_halluc(s):
    n = norm(s)
    return (n == "") or (n in HALLUC) or (n.replace(" ", "") in HALLUC_NS)

def frame_dbfs(path):
    """RMS of a 16-bit mono wav, in dBFS. -99 on error/empty."""
    try:
        with wave.open(path, "rb") as w:
            data = w.readframes(w.getnframes())
        a = array.array("h")
        a.frombytes(data[: len(data) - (len(data) % 2)])
        if not a:
            return -99.0
        ms = sum(x * x for x in a) / len(a)
        if ms <= 0:
            return -99.0
        return 20 * math.log10(math.sqrt(ms) / 32768.0)
    except Exception:
        return -99.0

def read_pcm(path):
    try:
        with wave.open(path, "rb") as w:
            return w.readframes(w.getnframes())
    except Exception:
        return b""

def write_utt(pcm, path):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(pcm)

def append_line(text):
    ts = time.strftime("%H:%M:%S")
    with open(TRANSCRIPT, "a") as f:
        f.write(f"- [{ts}] {text}\n")

def main():
    if not FRAME_DIR or not TRANSCRIPT:
        print("FRAME_DIR and TRANSCRIPT required", file=sys.stderr); sys.exit(2)
    import whisper
    model = whisper.load_model(MODEL_NAME)
    utt_wav = os.path.join(WORK, "utt.wav")
    stopfile = os.path.join(WORK, "stop")

    buf = []            # raw PCM bytes of buffered frames (speech + trailing sil)
    nspeech = 0         # count of speech frames in buf
    silence_run = 0     # consecutive trailing silence frames
    done = set()        # frame paths already consumed

    def flush():
        nonlocal buf, nspeech, silence_run
        if nspeech >= MIN_FRAMES and buf:
            write_utt(b"".join(buf), utt_wav)
            try:
                r = model.transcribe(utt_wav, language="en", fp16=False)
                text = " ".join(r.get("text", "").split())
                if text and not is_halluc(text):
                    append_line(text)
            except Exception:
                pass
        buf, nspeech, silence_run = [], 0, 0

    while not os.path.exists(stopfile):
        frames = sorted(glob.glob(os.path.join(FRAME_DIR, "chunk_*.wav")))
        ready = [f for f in frames[:-1] if f not in done]  # skip last (in-progress)
        for f in ready:
            done.add(f)
            db = frame_dbfs(f)
            if db > SILENCE_DB:                # speech
                buf.append(read_pcm(f)); nspeech += 1; silence_run = 0
                if nspeech >= MAX_FRAMES:
                    flush()
            else:                               # silence
                if nspeech:
                    buf.append(read_pcm(f))     # keep trailing silence for context
                    silence_run += 1
                    if silence_run >= GAP_FRAMES:
                        flush()
            try: os.remove(f)
            except OSError: pass
        time.sleep(0.15)

    flush()

if __name__ == "__main__":
    main()
