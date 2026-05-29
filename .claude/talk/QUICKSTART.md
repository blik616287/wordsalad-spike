# Voice Toolkit — Quick Start

Two local, whisper-powered skills for capturing and acting on audio. Everything
runs on your machine; the only thing that spends model tokens is `/listen` ticks.

---

## /talk — dictate or transcribe a call

Record, then transcribe with one command run twice.

```
/talk          # starts recording
   …speak, or let a call play…
/talk          # stops, transcribes, acts on it
```

- **Default (`dual`)** captures BOTH your mic (`[you]`) and system audio (`[them]`)
  as separate, labeled transcripts.
- Each transcript is archived to `~/voice-kb/calls/<timestamp>.md`.

**Modes** (set before recording): `VOICE_CAPTURE_MODE=` `dual` | `system` | `mic` | `both`

---

## /listen — real-time call assistant

Continuously transcribes the **remote party** and, each tick, surfaces KB context
+ ready-to-say answers. This is the one that costs tokens (one model turn per tick).

### Start it
```
/listen                 # sets up the session; I launch the background recorder
/loop 10s /listen       # the live loop — context + answers as they speak
```
Then on a second screen, watch the answers live:
```
tail -f ~/voice-kb/calls/live-<timestamp>.md
```
(I print the exact path when you start.)

### During the call
- Each tick I post a card:  **Heard** → **Context** (KB-cited) → **Say** (suggested wording).
- I only answer from what's actually been transcribed — never ahead of it.

### Resync if you lose the thread
```
/listen catchup         # full transcript + answers so far → fresh synthesis
```

### Stop it
```
/listen stop            # stops the recorder, finalizes the saved transcript
```
…and cancel the `/loop` (Esc, or tell me to stop the loop).
The loop also **auto-stops after 5 silent ticks** so an abandoned call can't churn tokens.

---

## Cost model (important)

| Thing | Cost |
|---|---|
| The recorder daemon (ffmpeg + whisper) | **Free** — local CPU only, zero tokens |
| `/talk` transcription | **Free** — local whisper |
| Each `/listen` tick (the `/loop`) | **~1 model turn** — this is the spend |

So the safe habit: keep the loop running only while the call is live; stop it after.

---

## Knowledge base

Drop reference docs (markdown) in your KB folder. I search them to ground answers
and cite the file I used. Call transcripts auto-archive to `<kb>/calls/`.

**Which folder is the KB?** When you run `/listen` for the first time, it **asks
you** which directory to use (suggesting any `./documentation`, `./docs`, `./kb`
folders it spots in your launch dir, plus a "no knowledge base" option). You can
skip the prompt by naming it up front:
- `/listen ./documentation` — relative to where you launched Claude Code
- `/listen ~/some/kb` — absolute or `~` path
- `/listen none` — no knowledge base; answers stay ungrounded

Resolution order: explicit path (or `none`) > `VOICE_KB_DIR` env > the KB chosen
when the session started. It never silently guesses — if you don't choose one, it
asks. Whatever you pick is remembered for the whole session (every `catchup`
reuses it). With no KB, answers are general-knowledge only and won't cite or
invent KB facts.

---

## Tuning (env vars, optional)

| Variable | Default | What it does |
|---|---|---|
| `VOICE_KB_DIR` | ask | Knowledge-base folder (relative or absolute); if unset, `/listen` asks |
| `VOICE_LIVE_MODEL` | `base.en` | Transcription: `base.en` (fast) → `small.en` → `medium.en` (accurate) |
| `VOICE_FAST_MODEL` | `claude-haiku-4-5-20251001` | Model for the in-script grounded answer (~5s) |
| `VOICE_FAST_ENABLE` | `1` | `0` = skip the fast answer, let the chat session answer instead |
| `VOICE_KB_MAX_CHARS` | `8000` | KB under this size is sent whole; larger is keyword-filtered |
| `VOICE_LIVE_GAP` | `0.8` | Trailing silence (s) that ends an utterance |
| `VOICE_LIVE_SILENCE_DB` | `-40` | dBFS below which audio counts as silence |
| `VOICE_CAPTURE_MODE` | `dual` | `/talk` capture mode |

---

## Gotchas

- **Bluetooth headsets** (e.g. Poly BT700) can't always capture mic + playback at
  once, so `/talk` `both`/`dual` may be flaky. `/listen` records system-only, so
  it's solid on Bluetooth.
- **First run each session** may prompt once for permission — approve it; silent after.
- Whisper occasionally hallucinates "you"/"thank you" on silence — `/listen`
  filters these automatically.
