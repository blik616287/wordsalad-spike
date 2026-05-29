#!/usr/bin/env bash
# live-listen.sh — continuous transcription of the remote party on a call, for
# the Claude Code /listen skill. Records the system/playback audio (the .monitor
# of your default sink = "them") in fixed chunks, transcribes each with whisper,
# and appends timestamped lines to a running transcript file. A /loop'd /listen
# tick reads the new lines; the model adds KB context + answers.
#
# IMPORTANT (harness constraint): detached/setsid processes are reaped when a
# synchronous Bash call returns, so the recorder can't be backgrounded with `&`.
# The `daemon` subcommand runs in the FOREGROUND and is meant to be launched by
# the model as a run_in_background Bash task, which the harness keeps alive
# across turns. It exits when it sees the stopfile.
#
# Subcommands:
#   tick           emit RECENT (already-seen, for continuity) + NEW transcript
#                  lines; if no daemon, init a session and ask the model to launch
#   catchup        dump the FULL transcript + answers so far for a state synthesis
#   daemon         FOREGROUND record+transcribe loop (run via run_in_background)
#   answer <text>  append an assistant answer block to the live answers file
#   stop           signal the daemon to exit and kill the recorder
#   status         report whether the daemon is running

# ----------------------------- config ---------------------------------------
# (KB_DIR / ARCHIVE_DIR are resolved lower down — they can depend on a path
#  argument, the launch cwd, or a stored session value.)
WHISPER_BIN="${VOICE_WHISPER_BIN:-$HOME/miniconda3/bin/whisper}"
LIVE_MODEL="${VOICE_LIVE_MODEL:-base.en}"     # base.en keeps up with live audio
RECENT_LINES="${VOICE_LIVE_RECENT:-5}"        # already-seen lines shown for continuity
IDLE_LIMIT="${VOICE_LIVE_IDLE_LIMIT:-5}"      # consecutive silent ticks before auto-stop (0=never)
WORK="${VOICE_LIVE_DIR:-$HOME/.cache/claude-voice/live}"
SELF="$HOME/.claude/skills/talk/live-listen.sh"
WORKER="${VOICE_LIVE_WORKER:-$HOME/.claude/skills/talk/live-transcribe.py}"
PYBIN="${VOICE_PYBIN:-$HOME/miniconda3/bin/python}"
# Fast grounded answer (decoupled from the slow main session): catchup/ask call a
# headless Haiku directly so the answer is ready in ~4s, not ~60s.
FAST_MODEL="${VOICE_FAST_MODEL:-claude-haiku-4-5-20251001}"
FAST_BIN="${VOICE_FAST_BIN:-claude}"
FAST_TIMEOUT="${VOICE_FAST_TIMEOUT:-20}"      # seconds before giving up on the fast call
KB_MAX_CHARS="${VOICE_KB_MAX_CHARS:-8000}"    # whole KB sent if under this; else keyword-grep
FAST_ENABLE="${VOICE_FAST_ENABLE:-1}"         # 0 disables the in-script Haiku call
# VAD worker tuning (utterance segmentation):
FRAME_SEC="${VOICE_LIVE_FRAME:-0.5}"          # ffmpeg frame granularity (s)
SILENCE_DB="${VOICE_LIVE_SILENCE_DB:--40}"    # below this dBFS a frame is silence
GAP_SEC="${VOICE_LIVE_GAP:-0.8}"              # trailing silence that ends an utterance
MIN_SPEECH="${VOICE_LIVE_MIN_SPEECH:-0.4}"    # ignore utterances shorter than this
MAX_UTT="${VOICE_LIVE_MAX_UTT:-15}"           # force-flush a long monologue after this

if [ -n "$VOICE_MONITOR_SOURCE" ]; then
  MONITOR_SOURCE="$VOICE_MONITOR_SOURCE"
else
  _SINK="$(pactl get-default-sink 2>/dev/null)"
  MONITOR_SOURCE="${_SINK:+${_SINK}.monitor}"
fi
# -----------------------------------------------------------------------------

CHUNKS="$WORK/chunks"
TRANSCRIPT="$WORK/transcript.md"
OFFSET="$WORK/offset"            # count of transcript LINES already emitted as NEW
IDLEFILE="$WORK/idle"            # consecutive ticks with no new speech
PIDFILE="$WORK/daemon.pid"       # the daemon writes its own PID here while alive
FFPIDFILE="$WORK/ff.pid"         # the daemon's ffmpeg child
WPIDFILE="$WORK/worker.pid"      # the daemon's python VAD/whisper worker
STOPFILE="$WORK/stop"
SESSION="$WORK/session"          # holds the live answers-file path
KBFILE="$WORK/kb"                # resolved KB dir for the active session
mkdir -p "$CHUNKS"
[ -x "$WHISPER_BIN" ] || WHISPER_BIN="$(command -v whisper || echo whisper)"

# --- knowledge-base directory resolution --------------------------------------
# Make a path absolute against the CURRENT cwd (where /listen was launched),
# without requiring it to exist yet.
abspath() {
  if command -v realpath >/dev/null 2>&1; then realpath -m -- "$1" 2>/dev/null && return; fi
  case "$1" in /*) printf '%s\n' "$1" ;; *) printf '%s\n' "$PWD/$1" ;; esac
}
# A start command can carry a KB path as its first arg, e.g. `/listen ./docs`,
# or the literal `none` to start with NO knowledge base (answer ungrounded).
KB_ARG=""
case "$1" in
  tick|catchup|ask|stop|status|daemon|answer|"") ;;    # known subcommands
  none|--no-kb|-) KB_ARG="none"; set -- tick ;;        # explicit: no KB
  *) KB_ARG="$1"; set -- tick ;;                        # path → start a session
esac
# Is a session currently live? (inline so this works before daemon_alive() exists)
_session_live() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; }
# Resolve the knowledge base. No silent default and no auto-detect: on a fresh
# start with nothing chosen, KB_DECIDED stays 0 and the tick path asks the user.
# Precedence: explicit arg (path or `none`) > VOICE_KB_DIR env > cached session KB.
KB_DECIDED=1
if [ "$KB_ARG" = "none" ]; then
  KB_DIR=""                                   # explicit: ungrounded
elif [ -n "$KB_ARG" ]; then
  KB_DIR="$(abspath "$KB_ARG")"
elif [ -n "$VOICE_KB_DIR" ]; then
  KB_DIR="$(abspath "$VOICE_KB_DIR")"
elif _session_live && [ -f "$KBFILE" ]; then
  KB_DIR="$(cat "$KBFILE")"                    # cached for the session (may be "")
else
  KB_DECIDED=0                                 # must ask the user
  KB_DIR=""
fi
ARCHIVE_DIR="${VOICE_ARCHIVE_DIR-${KB_DIR:-$HOME/voice-kb}/calls}"
# ------------------------------------------------------------------------------

# Whisper emits these on silent/near-silent chunks — drop them as noise.
# Normalize (lowercase, strip non-letters) and compare against a known set.
HALLUCINATIONS=" you thankyou thanksforwatching thankyouforwatching bye byebye pleasesubscribe youyou thanks thankyouforwatchingdontforgettosubscribe "
is_hallucination() {
  local norm
  norm="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z')"
  [ -z "$norm" ] && return 0
  case "$HALLUCINATIONS" in
    *" $norm "*) return 0 ;;
  esac
  return 1
}

daemon_alive() {
  [ -f "$PIDFILE" ] || return 1
  local p; p="$(cat "$PIDFILE" 2>/dev/null)"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

init_session() {
  rm -f "$CHUNKS"/chunk_*.wav "$CHUNKS"/chunk_*.txt "$TRANSCRIPT" "$OFFSET" "$STOPFILE" "$IDLEFILE"
  : > "$TRANSCRIPT"
  printf '0' > "$OFFSET"
  printf '0' > "$IDLEFILE"
  printf '%s' "$KB_DIR" > "$KBFILE"     # remember KB for the rest of this session
  local answers=""
  if [ -n "$ARCHIVE_DIR" ] && mkdir -p "$ARCHIVE_DIR" 2>/dev/null; then
    answers="$ARCHIVE_DIR/live-$(date '+%Y-%m-%d_%H-%M-%S').md"
    {
      echo "# Live call assistant — $(date '+%Y-%m-%d %H:%M:%S')"
      echo
      echo "_Remote party transcript with computed context/answers. \`tail -f\` this during the call._"
      echo
    } > "$answers"
  fi
  printf '%s' "$answers" > "$SESSION"
}

emit_session() {
  echo "__LISTEN_ANSWERS_FILE__: $(cat "$SESSION" 2>/dev/null)"
  echo "__LISTEN_TRANSCRIPT_FILE__: $TRANSCRIPT"
  if [ -z "$KB_DIR" ]; then
    echo "__LISTEN_KB_DIR__: (none — no knowledge base; answer WITHOUT grounding, don't invent KB facts)"
  elif [ -d "$KB_DIR" ]; then
    echo "__LISTEN_KB_DIR__: $KB_DIR"
  else
    echo "__LISTEN_KB_DIR__: (none — '$KB_DIR' does not exist; answer WITHOUT grounding)"
  fi
}

# kb_context <question> — echo KB text relevant to the question, capped. Small KBs
# are sent whole; large ones are keyword-grepped (question words >3 chars) with a
# few lines of surrounding context.
kb_context() {
  local q="$1" all size
  [ -n "$KB_DIR" ] && [ -d "$KB_DIR" ] || return 1
  all="$(find "$KB_DIR" -type f \( -name '*.md' -o -name '*.txt' \) \
          -not -path '*/calls/*' -print0 2>/dev/null | xargs -0 cat 2>/dev/null)"
  [ -n "$all" ] || return 1
  size=${#all}
  if [ "$size" -le "$KB_MAX_CHARS" ]; then
    printf '%s' "$all"; return 0
  fi
  # Large KB: build a grep alternation from question content words.
  local words pat
  words="$(printf '%s' "$q" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '\n' \
            | awk 'length>3' | sort -u | head -20 | paste -sd'|')"
  if [ -n "$words" ]; then
    printf '%s' "$all" | grep -iE -- "$words" 2>/dev/null | head -c "$KB_MAX_CHARS"
  else
    printf '%s' "$all" | head -c "$KB_MAX_CHARS"
  fi
}

# fast_answer <question> — print a 1-2 sentence rep-ready answer grounded ONLY in
# the KB, via a headless Haiku call (~4s). Returns nonzero (prints nothing) if
# disabled, no KB, no claude CLI, or the model says it's not covered.
fast_answer() {
  local q="$1" kb prompt out
  [ "$FAST_ENABLE" = 1 ] || return 1
  [ -n "$q" ] || return 1
  command -v "$FAST_BIN" >/dev/null 2>&1 || return 1
  kb="$(kb_context "$q")" || return 1
  [ -n "$kb" ] || return 1
  prompt="You are a live call assistant. Knowledge base:
<kb>
$kb
</kb>
On a live call the other party just asked: \"$q\"
Answer ONLY from the KB above. Output ONLY the 1-2 sentence answer the user should
say out loud — no preamble, no markdown. If the KB does not cover it, output
exactly: NOT_IN_KB"
  out="$(timeout "$FAST_TIMEOUT" "$FAST_BIN" -p "$prompt" --model "$FAST_MODEL" </dev/null 2>/dev/null)"
  out="$(printf '%s' "$out" | sed '/^[[:space:]]*$/d')"   # trim blank lines
  [ -n "$out" ] || return 1
  case "$out" in *NOT_IN_KB*) return 1 ;; esac
  printf '%s' "$out"
}

# ----------------------------- daemon loop ----------------------------------
# Architecture: ffmpeg writes short FRAME_SEC frames; a resident python VAD
# worker (live-transcribe.py) groups them into utterances and transcribes each
# the instant the speaker pauses — so a line appears ~1-2s after they stop,
# instead of waiting a full chunk. The whisper model loads once and stays warm.
if [ "$1" = "daemon" ]; then
  # Guard: never run two daemons against the same dir (orphans + corrupt chunks).
  if [ -f "$PIDFILE" ]; then
    oldp="$(cat "$PIDFILE" 2>/dev/null)"
    if [ -n "$oldp" ] && kill -0 "$oldp" 2>/dev/null; then
      echo "__LISTEN_ERROR__: a daemon (pid $oldp) is already running; refusing to start a second." >&2
      exit 0
    fi
  fi
  rm -f "$STOPFILE"
  echo $$ > "$PIDFILE"
  # Short frames via the segment muxer (FRAME_SEC granularity).
  ffmpeg -nostdin -f pulse -i "$MONITOR_SOURCE" \
    -f segment -segment_time "$FRAME_SEC" -reset_timestamps 1 \
    -ac 1 -ar 16000 "$CHUNKS/chunk_%05d.wav" \
    </dev/null >/dev/null 2>&1 &
  FFPID=$!
  echo "$FFPID" > "$FFPIDFILE"

  # Resident VAD + whisper worker.
  FRAME_DIR="$CHUNKS" TRANSCRIPT="$TRANSCRIPT" WORK="$WORK" \
  WHISPER_MODEL="$LIVE_MODEL" FRAME_SEC="$FRAME_SEC" SILENCE_DB="$SILENCE_DB" \
  GAP_SEC="$GAP_SEC" MIN_SPEECH_SEC="$MIN_SPEECH" MAX_UTT_SEC="$MAX_UTT" \
    "$PYBIN" "$WORKER" >/dev/null 2>&1 &
  WPID=$!
  echo "$WPID" > "$WPIDFILE"

  cleanup() {
    kill "$FFPID" 2>/dev/null; kill "$WPID" 2>/dev/null
    rm -f "$PIDFILE" "$FFPIDFILE" "$WPIDFILE"; exit 0
  }
  trap cleanup TERM INT

  # Supervise: exit if ffmpeg dies, the worker dies, or a stop is requested.
  while kill -0 "$FFPID" 2>/dev/null; do
    [ -f "$STOPFILE" ] && cleanup
    kill -0 "$WPID" 2>/dev/null || cleanup
    sleep 1
  done
  cleanup
fi

# ------------------------------- control ------------------------------------
case "$1" in
  tick)
    if ! daemon_alive; then
      # Fresh start with no KB chosen yet → ask the user before doing anything.
      if [ "$KB_DECIDED" = 0 ]; then
        echo "__LISTEN_NEED_KB__"
        echo "__LISTEN_CWD__: $PWD"
        # Offer any obvious doc folders in the launch dir as suggestions.
        for _d in documentation docs kb knowledge-base voice-kb; do
          [ -d "$PWD/$_d" ] && echo "__LISTEN_KB_SUGGESTION__: ./$_d"
        done
        echo "__LISTEN_START_WITH_KB__: bash $SELF <path>      # start grounded in <path>"
        echo "__LISTEN_START_NO_KB__:   bash $SELF none        # start without a knowledge base"
        exit 0
      fi
      init_session
      echo "__LISTEN_NEED_DAEMON__"
      echo "__LISTEN_DAEMON_CMD__: bash $SELF daemon"
      emit_session
      exit 0
    fi
    off="$(cat "$OFFSET" 2>/dev/null)"; [ -n "$off" ] || off=0
    total="$(wc -l < "$TRANSCRIPT" 2>/dev/null)"; [ -n "$total" ] || total=0
    # Recent already-seen lines, for continuity when a thought spans ticks.
    rstart=$(( off - RECENT_LINES + 1 )); [ "$rstart" -lt 1 ] && rstart=1
    echo "__LISTEN_RECENT_BEGIN__"
    if [ "$off" -gt 0 ]; then
      sed -n "${rstart},${off}p" "$TRANSCRIPT"
    fi
    echo "__LISTEN_RECENT_END__"
    echo "__LISTEN_NEW_BEGIN__"
    [ "$total" -gt "$off" ] && sed -n "$((off+1)),${total}p" "$TRANSCRIPT"
    echo "__LISTEN_NEW_END__"
    printf '%s' "$total" > "$OFFSET"
    # Idle auto-stop: count consecutive ticks with no new speech; once we hit the
    # limit, tell the model to end the /loop (and stop the recorder) so an
    # abandoned call doesn't churn tokens forever. IDLE_LIMIT=0 disables this.
    idle="$(cat "$IDLEFILE" 2>/dev/null)"; [ -n "$idle" ] || idle=0
    if [ "$total" -le "$off" ]; then idle=$((idle+1)); else idle=0; fi
    printf '%s' "$idle" > "$IDLEFILE"
    if [ "$IDLE_LIMIT" -gt 0 ] && [ "$idle" -ge "$IDLE_LIMIT" ]; then
      echo "__LISTEN_IDLE_STOP__: ${idle} silent ticks (limit ${IDLE_LIMIT}) — call looks over; end the /loop and run '/listen stop'."
    fi
    emit_session
    ;;

  catchup)
    if ! daemon_alive; then echo "__LISTEN_NOT_RUNNING__"; emit_session; exit 0; fi
    total="$(wc -l < "$TRANSCRIPT" 2>/dev/null)"; [ -n "$total" ] || total=0
    # Structural anti-fabrication: the SCRIPT extracts the latest question from
    # the transcript (last line containing '?') and hands it over verbatim, so the
    # answer is anchored to text that actually exists — not reconstructed from
    # memory. If this is empty, there is no captured question to answer.
    latest_q="$(grep '?' "$TRANSCRIPT" 2>/dev/null | tail -1)"
    echo "__LISTEN_LATEST_QUESTION__: ${latest_q:-(none captured yet)}"
    # FAST PATH: compute the grounded answer here via Haiku (~4s) so it's ready
    # without waiting on the slow main session. Also appended to the answers file
    # so a `tail -f` watcher sees it instantly. The model should relay this
    # verbatim and only elaborate if asked.
    if [ -n "$latest_q" ]; then
      fa="$(fast_answer "$latest_q")"
      if [ -n "$fa" ]; then
        echo "__LISTEN_FAST_ANSWER_BEGIN__"
        printf '%s\n' "$fa"
        echo "__LISTEN_FAST_ANSWER_END__"
        ANSWERS="$(cat "$SESSION" 2>/dev/null)"
        if [ -n "$ANSWERS" ]; then
          { echo "### $(date '+%H:%M:%S')  ⏩ $latest_q"; echo; printf '%s\n' "$fa"; echo; } >> "$ANSWERS"
        fi
      fi
    fi
    echo "__LISTEN_FULL_BEGIN__"
    [ "$total" -gt 0 ] && cat "$TRANSCRIPT"
    echo "__LISTEN_FULL_END__"
    ANSWERS="$(cat "$SESSION" 2>/dev/null)"
    echo "__LISTEN_ANSWERS_SO_FAR_BEGIN__"
    [ -n "$ANSWERS" ] && [ -f "$ANSWERS" ] && cat "$ANSWERS"
    echo "__LISTEN_ANSWERS_SO_FAR_END__"
    # A catchup consumes everything: future ticks only show what comes next.
    printf '%s' "$total" > "$OFFSET"
    emit_session
    ;;

  ask)
    # Minimal fast path: latest question -> Haiku grounded answer, printed and
    # appended to the answers file. Meant to be run directly in a terminal for a
    # sub-5s answer with NO model session involved, or by the skill.
    if ! daemon_alive; then echo "__LISTEN_NOT_RUNNING__"; emit_session; exit 0; fi
    latest_q="$(grep '?' "$TRANSCRIPT" 2>/dev/null | tail -1)"
    if [ -z "$latest_q" ]; then
      echo "__LISTEN_NO_QUESTION__: nothing with a '?' captured yet."
      last="$(tail -1 "$TRANSCRIPT" 2>/dev/null)"
      [ -n "$last" ] && echo "__LISTEN_LATEST_LINE__: $last"
      exit 0
    fi
    echo "__LISTEN_LATEST_QUESTION__: $latest_q"
    fa="$(fast_answer "$latest_q")"
    if [ -n "$fa" ]; then
      echo "__LISTEN_FAST_ANSWER_BEGIN__"
      printf '%s\n' "$fa"
      echo "__LISTEN_FAST_ANSWER_END__"
      ANSWERS="$(cat "$SESSION" 2>/dev/null)"
      [ -n "$ANSWERS" ] && { echo "### $(date '+%H:%M:%S')  ⏩ $latest_q"; echo; printf '%s\n' "$fa"; echo; } >> "$ANSWERS"
    else
      echo "__LISTEN_FAST_ANSWER_UNAVAILABLE__: no KB match or fast model unavailable; use catchup for a full answer."
    fi
    exit 0
    ;;

  answer)
    shift
    ANSWERS="$(cat "$SESSION" 2>/dev/null)"
    [ -n "$ANSWERS" ] || { echo "__LISTEN_ERROR__: no active session"; exit 0; }
    {
      echo "### $(date '+%H:%M:%S')"
      echo
      printf '%s\n' "$*"
      echo
    } >> "$ANSWERS"
    echo "__LISTEN_ANSWER_SAVED__: $ANSWERS"
    ;;

  stop)
    : > "$STOPFILE"
    [ -f "$FFPIDFILE" ] && kill "$(cat "$FFPIDFILE")" 2>/dev/null
    [ -f "$WPIDFILE" ] && kill "$(cat "$WPIDFILE")" 2>/dev/null
    if daemon_alive; then
      p="$(cat "$PIDFILE")"
      kill -TERM "$p" 2>/dev/null
      for _ in $(seq 1 30); do kill -0 "$p" 2>/dev/null || break; sleep 0.1; done
      kill -KILL "$p" 2>/dev/null
    fi
    # Sweep ANY orphaned ffmpeg/worker still pointed at our dirs (defends against
    # a previously-orphaned daemon whose PIDs we no longer have on file).
    for orphan in $(pgrep -f "ffmpeg.*$CHUNKS" 2>/dev/null) \
                  $(pgrep -f "live-transcribe.py" 2>/dev/null); do
      kill "$orphan" 2>/dev/null
    done
    rm -f "$PIDFILE" "$FFPIDFILE" "$WPIDFILE" "$IDLEFILE" "$KBFILE" "$CHUNKS"/chunk_*.wav "$CHUNKS"/chunk_*.txt
    echo "__LISTEN_STOPPED__"
    emit_session
    ;;

  status)
    daemon_alive && echo "__LISTEN_RUNNING__" || echo "__LISTEN_NOT_RUNNING__"
    ;;

  *)
    echo "usage: live-listen.sh {tick|catchup|daemon|answer <text>|stop|status}"
    ;;
esac
