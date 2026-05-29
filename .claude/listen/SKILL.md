---
name: listen
description: Answer-on-demand call assistant. A background daemon continuously transcribes the REMOTE party on a call (system/playback audio) to a live file — listening the whole time, but silent until you ask. Run `/listen` to start; then when they ask you something, run `/listen catchup` (or just ask "what did they ask?") and it answers their MOST RECENT question first, KB-grounded; `/listen stop` to end. Use whenever the user invokes /listen OR asks about the current call.
---

# /listen — answer-on-demand call assistant

## FIRST: run the control script

Your FIRST action this turn is to run the control script via the Bash tool (it is
pre-approved). Pick the argument from what the user did:

- `/listen` with no argument → `tick`. On a FIRST start this returns
  `__LISTEN_NEED_KB__` (the script is asking you to ask the user for a KB) — that
  is expected, not an error; handle it per the branch below.
- `/listen catchup`, `/listen stop`, `/listen status` → that word
- `/listen <path>` (e.g. `/listen ./documentation`, `/listen ~/kb`, or the user
  says "use ./docs as the knowledge base") → pass that path verbatim as the arg.
  It starts a session with that folder as the KB, resolved against the directory
  the user launched from. Pass it EXACTLY as given (keep `./` / `~`), do not
  pre-resolve it — the script resolves it against the launch cwd.
- `/listen none` (or the user says "no knowledge base" / "don't ground") → start a
  session with NO knowledge base; answers will be ungrounded.
- The user asked a plain-language question about the call ("who's the customer?",
  "what do they want?", "catch me up", "what did they just say?") → `catchup`

```
bash ~/.claude/skills/talk/live-listen.sh <arg>
```

Then read the markers it prints and branch as below. (The script can't be
auto-run from this file because the harness blocks `!`-lines with shell expansion
— so YOU invoke it.)

**Knowledge base resolution** (the script handles this; `__LISTEN_KB_DIR__:`
reports the result): explicit `<path>` arg (or `none`) > `VOICE_KB_DIR` env > the
KB cached when this session started. On a FRESH start with none of those, the
script does NOT guess — it emits `__LISTEN_NEED_KB__` so you ASK the user (see the
branch below). Once a session starts with a chosen KB, every later `catchup`/`tick`
reuses it — you don't repeat the path. If the user chose no KB, the session stays
ungrounded and you must NOT invent KB facts.

## How this works (answer-on-demand)

A background daemon records the remote party (the `.monitor` of your output sink
— what *they* say, not your mic) and transcribes it **continuously** to a live
file using a resident VAD+whisper worker (a line appears ~1–2s after they pause).

**It listens the whole call, but stays silent until asked.** There is NO `/loop`
and no per-interval token spend. You pull the assistant in on demand:
- Ask in plain English ("who is this customer and what do they want?") → catchup.
- `/listen catchup` → full synthesis of the whole call.
- `/listen` → just the new speech since last time, with an answer.

The user can also watch the raw transcript stream with `tail -f <transcript>`.

**Constraint:** detached processes die when a normal command returns, so the
daemon CANNOT be started by an `!` line. YOU launch it as a background Bash task
(see `__LISTEN_NEED_DAEMON__`); that's the only way it survives across turns.

## Answer ONLY from the transcript

The text inside the markers is your ONLY window into the call. Never answer
something you think you heard but that is not in the markers — if it's not there
yet, it hasn't been transcribed; say so or wait. Do not use side knowledge of the
audio. When `__LISTEN_KB_DIR__:` names a real directory, ground every answer in it
and cite the file(s); if the KB lacks the fact, say so and answer from general
knowledge, clearly marked. When `__LISTEN_KB_DIR__:` says `(none …)`, there is NO
knowledge base — answer from general knowledge only and do NOT invent or cite KB
facts.

## Branch on the marker(s) above

- **`__LISTEN_NEED_KB__`** — a fresh start with no KB chosen. **Ask the user which
  knowledge-base directory to ground answers in**, using the AskUserQuestion tool.
  Read `__LISTEN_CWD__:` (their launch dir) and any `__LISTEN_KB_SUGGESTION__:`
  lines (doc folders detected there) — offer each suggestion as an option, plus a
  **"No knowledge base"** option. Then re-run the script with their choice:
  - a path → `bash ~/.claude/skills/talk/live-listen.sh <path>` (pass verbatim,
    keep `./`/`~`; relative paths resolve against their launch dir)
  - no KB  → `bash ~/.claude/skills/talk/live-listen.sh none`
  That re-run returns `__LISTEN_NEED_DAEMON__`; handle it as below. If the user
  picks a KB, also **read its files now** (Grep/Read the dir) so you understand
  what's available before the call gets going.

- **`__LISTEN_NEED_DAEMON__`** — no recorder running; a fresh session was just
  initialized. Read `__LISTEN_DAEMON_CMD__:`, then **launch it as a background
  task**: call Bash with `run_in_background: true` running that command verbatim
  (`bash ~/.claude/skills/talk/live-listen.sh daemon`). Read
  `__LISTEN_ANSWERS_FILE__:` / `__LISTEN_TRANSCRIPT_FILE__:` and tell the user:

  > 🎧 Listening to the call — I'll stay quiet until you need me. Ask anytime ("who's the customer? what do they want?"), or `tail -f <transcript-file>` to watch it live. `/listen stop` when done.

  Do NOT process transcript this turn (there is none yet). Stop after launching.

- **`__LISTEN_RECENT_BEGIN__` … `__LISTEN_RECENT_END__`** then
  **`__LISTEN_NEW_BEGIN__` … `__LISTEN_NEW_END__`** (from a bare `/listen`) —
  RECENT is recent already-seen context (don't re-answer it); NEW is what they
  said since last time. Then:
  1. If NEW is empty, reply with one short line ("…(no new speech)…") and stop.
  2. If NEW ends mid-thought (unfinished question), say "…(waiting for them to
     finish)…" and stop — it'll complete next time.
  3. Otherwise post a compact card and persist it (see "Answer card" below).

- **`__LISTEN_FULL_BEGIN__` … `__LISTEN_FULL_END__`** plus
  **`__LISTEN_ANSWERS_SO_FAR_BEGIN__` … `__LISTEN_ANSWERS_SO_FAR_END__`** (from
  `catchup` — including when the user asked a plain-language question about the
  call) — FULL is the entire transcript; ANSWERS_SO_FAR is what you've posted.

  **LEAD WITH THE LATEST QUESTION.** The #1 job of catchup is to answer what the
  remote party MOST RECENTLY asked. The script does the extraction for you and
  hands it over on the **`__LISTEN_LATEST_QUESTION__:`** line — that text is the
  exact last transcript line containing a "?".

  **ANTI-FABRICATION RULE (this is the one you keep breaking — obey it):**
  - The ONLY question you may headline is the verbatim text on
    `__LISTEN_LATEST_QUESTION__:`. Do NOT answer any question you remember,
    expected, or think you heard — only what is on that line / in FULL.
  - If `__LISTEN_LATEST_QUESTION__:` is `(none captured yet)` or empty, there is
    NO captured question. Say exactly that — "No question has been transcribed
    yet; the latest captured speech is: <quote last FULL line>" — and STOP. Do
    not invent one.
  - Quote the captured question back before answering, so any mismatch is visible.

  **FAST ANSWER (speed path):** if the output contains
  `__LISTEN_FAST_ANSWER_BEGIN__ … __LISTEN_FAST_ANSWER_END__`, the script ALREADY
  computed the grounded answer (via a fast model) and saved it. RELAY THAT TEXT
  VERBATIM as the `Say:` line — do NOT re-derive it, do NOT second-guess it, and
  keep your own output minimal so the user sees it immediately. (It's already
  appended to the answers file too.) Only use FULL to add the brief synthesis
  below if the user wants more. If there is NO fast-answer block, fall back to
  composing the answer yourself from FULL + the KB.

  Format:
  1. Headline the captured latest question + its answer:
     > **⏩ Latest captured question — "<verbatim from __LISTEN_LATEST_QUESTION__>"**
     > **Say:** <the FAST_ANSWER text verbatim, or your KB-grounded answer if none>
  2. Then a brief supporting synthesis from FULL only (skip if the user just wants
     the quick answer):
     - **Customer / situation:** who they are and what they're after.
     - **Other open questions:** anything else in FULL still unresolved — answer each.
     - **Where we are:** 1–2 line recap.
  If a FAST_ANSWER was already saved, do NOT call `answer` again (avoid dupes);
  otherwise persist your answer with one `... answer "<markdown>"` call.

- **`__LISTEN_FAST_ANSWER_BEGIN__ … _END__`** (from `ask`, or inside catchup) — the
  script's precomputed grounded answer for the latest question. Relay verbatim,
  minimally. **`__LISTEN_FAST_ANSWER_UNAVAILABLE__`** / **`__LISTEN_NO_QUESTION__`**
  — no fast answer (no KB match / no captured question); fall back to `catchup`.

- **`__LISTEN_IDLE_STOP__: <reason>`** — appeared after several silent `/listen`
  checks (call likely over). Handle any NEW speech, then suggest `/listen stop`.

- **`__LISTEN_STOPPED__`** — session ended. Tell the user; point them at the
  `__LISTEN_ANSWERS_FILE__:` saved record.

- **`__LISTEN_RUNNING__` / `__LISTEN_NOT_RUNNING__`** — status; relay it.

- **`__LISTEN_ERROR__: <reason>`** — report it; suggest `/listen stop` then `/listen`.

## Answer card

When you answer (from a bare `/listen` with new speech), post a compact card here
so the user can glance at it, then persist it with ONE call:
`bash ~/.claude/skills/talk/live-listen.sh answer "<same card as markdown>"`

```
Heard:   <one-line paraphrase of what they said/asked>
Context: <KB-backed facts, cite the file>
Say:     <suggested wording the user can speak>
```

## Arguments
- `/listen` → first start asks you for a KB directory (`__LISTEN_NEED_KB__`), then
  starts; later it shows new speech + answers it.
- `/listen <path>` → start a session using `<path>` as the knowledge base (e.g.
  `/listen ./documentation`), resolved against the launch directory.
- `/listen none` → start a session with no knowledge base (ungrounded answers).
- `/listen catchup` → answers the remote party's MOST RECENT question first
  (precomputed by the script via a fast model, ~5s), then a brief supporting
  synthesis. Run this whenever the user just got asked something — or asks about
  the call in plain English.
- `/listen ask` → fastest path: just the latest question's grounded answer, no
  synthesis. Also runnable directly in a terminal (`bash
  ~/.claude/skills/talk/live-listen.sh ask`) for a sub-5s answer with no model
  session at all, since it's also appended to the answers file.
- `/listen stop` → stop the recorder and finalize the saved transcript.
- `/listen status` → is it running?

## Speed
The grounded answer is computed inside the script by a fast model (Haiku, ~4–5s),
not by this main session — so `catchup`/`ask` return a ready-to-say answer quickly
and you just relay it. For the absolute fastest experience during a call, the user
can `tail -f <answers-file>` and run `bash ~/.claude/skills/talk/live-listen.sh ask`
in a terminal: the answer lands in that file in ~5s without any model turn.
