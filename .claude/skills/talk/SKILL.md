---
name: talk
description: Voice/audio-to-prompt for Claude Code. Run /talk once to start recording, run /talk again to stop and transcribe locally with whisper. By default it captures BOTH your mic and system/playback audio (e.g. a Zoom call) as separate, labeled [them]/[you] transcripts, then acts on what was said — grounded in the knowledge-base folder by default (or generally if you say to ignore it). Use whenever the user invokes /talk.
---

# /talk — speak to Claude

The capture engine has just run. Its output is below:

!`bash ~/.claude/skills/talk/voice-capture.sh`

## What to do with that output

Read the engine output above and branch on the marker it contains:

- **`__VOICE_RECORDING_STARTED__`** — Recording has begun. Reply with exactly:

  > 🎙️ Recording… speak now, then run `/talk` again to stop and get your answer.

  Then **stop**. Do not call any tools, do not say anything else.

- **`__VOICE_DIALOGUE__` … `__VOICE_DIALOGUE_END__`** — a two-party transcript of
  a call. Lines tagged `[them]` are the remote party (system/playback audio);
  lines tagged `[you]` are the user (microphone). Either line may be absent
  (e.g. a Bluetooth headset that couldn't capture the mic). Also read the
  `__VOICE_KB_DIR__:` line. Briefly restate who said what (one line each), then
  respond to the user's intent: answer a question that was asked, draft a reply,
  take notes, or follow whatever instruction the `[you]` line gives about the
  `[them]` content. Ground your answer in the knowledge base per the rules below.

- **`__VOICE_TRANSCRIPT__: <text>`** — `<text>` is the user's spoken request;
  treat it as their prompt for this turn. Also read the `__VOICE_KB_DIR__:` line.

  - **Default (grounded):** If a valid knowledge-base directory is given, answer
    the request by searching and reading the files in that directory (use Grep
    and Read), base your answer on what you find, and name the source file(s) you
    relied on. If you can't find relevant material there, say so, then answer
    from general knowledge.
  - **General:** If the transcript clearly asks to skip/ignore the knowledge base
    (e.g. "ignore the knowledge base", "just answer generally", "don't use the
    docs"), OR if `__VOICE_KB_DIR__:` reports no valid directory, answer as a
    normal session response without consulting the folder.

  Briefly restate what you heard (one line) before answering, so the user can
  confirm the transcription was correct.

- **`__VOICE_ARCHIVE__: <path>`** — (may appear alongside a transcript/dialogue)
  the transcript was saved to this file for the searchable call archive. Mention
  the saved path to the user in one short line; do not open or re-read the file.

- **`__VOICE_ERROR__: <reason>`** — Tell the user recording/transcription failed
  with the given reason and suggest running `/talk` again. Take no other action.
