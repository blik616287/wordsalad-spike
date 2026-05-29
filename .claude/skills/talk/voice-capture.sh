#!/usr/bin/env bash
# voice-capture.sh — toggle audio capture + local whisper transcription for the
# Claude Code /talk skill.
#
#   First run  : starts a detached ffmpeg recording, prints __VOICE_RECORDING_STARTED__
#   Second run : stops it, transcribes with whisper, prints the transcript markers
#
# Captures your microphone AND the system/playback audio (e.g. the remote party
# on a Zoom/Meet call, which is the .monitor of your output sink). The default
# `dual` mode records the two as separate streams and labels them [them]/[you],
# which is what you want for meeting notes.
#
# Output is consumed by ~/.claude/skills/talk/SKILL.md via the !-prefix.

# ----------------------------- config ---------------------------------------
KB_DIR="${VOICE_KB_DIR:-$HOME/voice-kb}"          # <-- point this at your docs folder
WHISPER_MODEL="${VOICE_WHISPER_MODEL:-small.en}"  # base.en (fast) | small.en | medium.en (accurate)
WHISPER_BIN="${VOICE_WHISPER_BIN:-$HOME/miniconda3/bin/whisper}"
WORK="${VOICE_WORK_DIR:-$HOME/.cache/claude-voice}"
# Where to archive finished transcripts (timestamped, searchable). Set
# VOICE_ARCHIVE_DIR= (empty) to disable archiving.
ARCHIVE_DIR="${VOICE_ARCHIVE_DIR-$KB_DIR/calls}"

# What to record:
#   dual   -> mic + system as TWO separate transcripts, labeled [them]/[you] [default]
#   system -> only system playback (the other person on a call)
#   mic    -> only your microphone (classic dictation)
#   both   -> mic + system mixed into one stream (flaky on Bluetooth headsets)
MODE="${VOICE_CAPTURE_MODE:-dual}"
MIC_SOURCE="${VOICE_MIC_SOURCE:-default}"          # PulseAudio source for your mic
# System audio = the monitor of the current default output sink, resolved at runtime.
if [ -n "$VOICE_MONITOR_SOURCE" ]; then
  MONITOR_SOURCE="$VOICE_MONITOR_SOURCE"
else
  _SINK="$(pactl get-default-sink 2>/dev/null)"
  MONITOR_SOURCE="${_SINK:+${_SINK}.monitor}"
fi
# -----------------------------------------------------------------------------

PIDFILE="$WORK/rec.pid"
MODEFILE="$WORK/rec.mode"
RAW="$WORK/audio.wav"          # single-stream modes (system/mic/both)
RAW_YOU="$WORK/audio_you.wav"  # dual: your mic
RAW_THEM="$WORK/audio_them.wav" # dual: system / remote party
mkdir -p "$WORK"

# Clear a stale pidfile (process no longer alive).
if [ -f "$PIDFILE" ]; then
  OLDPID="$(cat "$PIDFILE" 2>/dev/null)"
  if [ -z "$OLDPID" ] || ! kill -0 "$OLDPID" 2>/dev/null; then
    rm -f "$PIDFILE"
  fi
fi

# transcribe <wav-file> -> echoes the cleaned transcript text (empty if none).
transcribe() {
  local src="$1"
  [ -s "$src" ] || { echo ""; return; }
  local base clean
  base="$(basename "${src%.*}")"
  clean="$WORK/${base}.clean.wav"
  # Normalize to 16 kHz mono; repairs a possibly-truncated header.
  ffmpeg -nostdin -y -i "$src" -ac 1 -ar 16000 "$clean" >/dev/null 2>&1
  [ -s "$clean" ] || clean="$src"
  rm -f "$WORK/${base}.clean.txt" "$WORK/${base}.txt"
  "$WHISPER_BIN" "$clean" --model "$WHISPER_MODEL" --language en --task transcribe \
    --fp16 False --output_format txt --output_dir "$WORK" >/dev/null 2>&1
  local txt="$WORK/$(basename "${clean%.*}").txt"
  [ -f "$txt" ] && tr '\n' ' ' < "$txt" | sed 's/  */ /g; s/^ //; s/ $//'
}

emit_kb() {
  if [ -d "$KB_DIR" ]; then
    echo "__VOICE_KB_DIR__: $KB_DIR"
  else
    echo "__VOICE_KB_DIR__: (none — '$KB_DIR' does not exist; answer generally)"
  fi
}

# archive <markdown-body...> — write a timestamped transcript to ARCHIVE_DIR and
# echo its path on a __VOICE_ARCHIVE__ line. No-op if archiving is disabled.
archive() {
  [ -n "$ARCHIVE_DIR" ] || return 0
  mkdir -p "$ARCHIVE_DIR" 2>/dev/null || return 0
  local stamp file
  stamp="$(date '+%Y-%m-%d_%H-%M-%S')"
  file="$ARCHIVE_DIR/$stamp.md"
  {
    echo "# Transcript — $(date '+%Y-%m-%d %H:%M:%S')"
    echo "_mode: ${REC_MODE}_"
    echo
    printf '%s\n' "$@"
  } > "$file" 2>/dev/null && echo "__VOICE_ARCHIVE__: $file"
}

# ============================ STOP PATH ======================================
if [ -f "$PIDFILE" ]; then
  PID="$(cat "$PIDFILE")"
  REC_MODE="$(cat "$MODEFILE" 2>/dev/null)"; [ -n "$REC_MODE" ] || REC_MODE="$MODE"
  # SIGINT lets ffmpeg write the WAV trailer / finalize the file(s) cleanly.
  kill -INT "$PID" 2>/dev/null
  for _ in $(seq 1 50); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.1
  done
  kill -0 "$PID" 2>/dev/null && kill -TERM "$PID" 2>/dev/null
  rm -f "$PIDFILE" "$MODEFILE"

  if [ ! -x "$WHISPER_BIN" ]; then
    WHISPER_BIN="$(command -v whisper || echo whisper)"
  fi

  if [ "$REC_MODE" = "dual" ]; then
    if [ ! -s "$RAW_THEM" ] && [ ! -s "$RAW_YOU" ]; then
      echo "__VOICE_ERROR__: no audio was captured (empty recording)."
      exit 0
    fi
    THEM="$(transcribe "$RAW_THEM")"
    YOU="$(transcribe "$RAW_YOU")"
    if [ -z "$THEM" ] && [ -z "$YOU" ]; then
      echo "__VOICE_ERROR__: transcription produced no text (silence or whisper failure)."
      exit 0
    fi
    echo "__VOICE_DIALOGUE__"
    [ -n "$THEM" ] && echo "[them] $THEM"
    [ -n "$YOU" ]  && echo "[you] $YOU"
    echo "__VOICE_DIALOGUE_END__"
    DIALOG=""
    [ -n "$THEM" ] && DIALOG="**them:** $THEM"
    [ -n "$YOU" ]  && DIALOG="$DIALOG${DIALOG:+$'\n\n'}**you:** $YOU"
    archive "$DIALOG"
    emit_kb
    exit 0
  fi

  # ---- single-stream modes ----
  if [ ! -s "$RAW" ]; then
    echo "__VOICE_ERROR__: no audio was captured (empty recording)."
    exit 0
  fi
  TRANSCRIPT="$(transcribe "$RAW")"
  if [ -z "$TRANSCRIPT" ]; then
    echo "__VOICE_ERROR__: transcription produced no text (silence or whisper failure)."
    exit 0
  fi
  echo "__VOICE_TRANSCRIPT__: $TRANSCRIPT"
  archive "$TRANSCRIPT"
  emit_kb
  exit 0
fi

# ============================ START PATH =====================================
rm -f "$RAW" "$CLEAN" "$RAW_YOU" "$RAW_THEM"

# Decide which mode is actually achievable given the resolved sources.
EFFECTIVE_MODE="$MODE"
if { [ "$MODE" = "both" ] || [ "$MODE" = "system" ] || [ "$MODE" = "dual" ]; } \
   && [ -z "$MONITOR_SOURCE" ]; then
  # No monitor source available -> degrade gracefully to mic only.
  EFFECTIVE_MODE="mic"
fi

start_ffmpeg() {
  case "$EFFECTIVE_MODE" in
    dual)
      # One process, two outputs: mic -> _you, system -> _them. thread_queue +
      # async resampling keep the two pulse streams from starving each other.
      setsid ffmpeg -nostdin \
        -thread_queue_size 1024 -f pulse -i "$MIC_SOURCE" \
        -thread_queue_size 1024 -f pulse -i "$MONITOR_SOURCE" \
        -map 0:a -af aresample=async=1 -ac 1 -ar 16000 -y "$RAW_YOU" \
        -map 1:a -af aresample=async=1 -ac 1 -ar 16000 -y "$RAW_THEM" \
        </dev/null >/dev/null 2>&1 &
      ;;
    both)
      setsid ffmpeg -nostdin \
        -thread_queue_size 1024 -f pulse -i "$MIC_SOURCE" \
        -thread_queue_size 1024 -f pulse -i "$MONITOR_SOURCE" \
        -filter_complex "[0:a]aresample=async=1[a0];[1:a]aresample=async=1[a1];[a0][a1]amix=inputs=2:duration=longest:normalize=0,dynaudnorm" \
        -ac 1 -ar 16000 -y "$RAW" </dev/null >/dev/null 2>&1 &
      ;;
    system)
      setsid ffmpeg -nostdin -f pulse -i "$MONITOR_SOURCE" \
        -ac 1 -ar 16000 -y "$RAW" </dev/null >/dev/null 2>&1 &
      ;;
    *) # mic
      setsid ffmpeg -nostdin -f pulse -i "$MIC_SOURCE" \
        -ac 1 -ar 16000 -y "$RAW" </dev/null >/dev/null 2>&1 &
      ;;
  esac
  PID=$!
  disown 2>/dev/null
}

start_ffmpeg
# Give it a moment; verify it actually started.
sleep 0.5
if ! kill -0 "$PID" 2>/dev/null; then
  # Dual can fail if a Bluetooth headset won't capture mic + playback at once.
  # Fall back to system-only (single source, most robust), then mic via parecord.
  if [ -n "$MONITOR_SOURCE" ] && [ "$EFFECTIVE_MODE" != "mic" ]; then
    EFFECTIVE_MODE="system"
    setsid ffmpeg -nostdin -f pulse -i "$MONITOR_SOURCE" \
      -ac 1 -ar 16000 -y "$RAW" </dev/null >/dev/null 2>&1 &
    PID=$!
    disown 2>/dev/null
    sleep 0.4
  fi
fi
if ! kill -0 "$PID" 2>/dev/null; then
  EFFECTIVE_MODE="mic"
  setsid parecord --file-format=wav "$RAW" </dev/null >/dev/null 2>&1 &
  PID=$!
  disown 2>/dev/null
  sleep 0.4
fi

if ! kill -0 "$PID" 2>/dev/null; then
  echo "__VOICE_ERROR__: could not start audio capture (ffmpeg/parecord failed)."
  exit 0
fi

echo "$PID" > "$PIDFILE"
echo "$EFFECTIVE_MODE" > "$MODEFILE"
echo "__VOICE_RECORDING_STARTED__"
exit 0
