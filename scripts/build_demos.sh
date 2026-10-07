#!/bin/bash
# Build all demo assets for VoiceStudio v0.3.0.
#
# Two render paths:
#   1. macOS `say` (default) — fast, deterministic, ships immediately.
#      Used to bootstrap the demo bundle so v0.3.0 has working demos on day one.
#   2. VoiceStudio engine (--engine omnivoice) — production-quality re-render
#      once model weights are cached. Recipes documented but not executed
#      until the user opts in.
#
# All outputs are committed to the repo so end-users never need to re-render.
#
# Usage:
#   scripts/build_demos.sh                # render via `say`, overwrite all assets
#   scripts/build_demos.sh --skip-existing  # only render files that don't exist
#   scripts/build_demos.sh --engine omnivoice  # re-render via the real engine
#                                          # (requires .venv + weights)
#
# Outputs land in:
#   backend/assets/samples/demo_voice.wav              (clone reference)
#   backend/assets/samples/demo_clone_output.wav       (clone pre-rendered out)
#   backend/assets/samples/voice_design/demo_voice_design_*.wav  (7 design presets)
#   backend/assets/samples/dictation/{en_conversational,en_technical,fr_reservation}.wav
#
# Generated speech is not automatically covered by the application's licence.
# Review the exact voice/model and output terms before redistribution; this
# script does not establish commercial rights for any generated asset.

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SAMPLES_DIR="${REPO_ROOT}/backend/assets/samples"
DESIGN_DIR="${SAMPLES_DIR}/voice_design"
DICT_DIR="${SAMPLES_DIR}/dictation"

ENGINE="say"
SKIP_EXISTING=0

while [ $# -gt 0 ]; do
  case "$1" in
    --engine) ENGINE="$2"; shift 2 ;;
    --skip-existing) SKIP_EXISTING=1; shift ;;
    --help|-h)
      sed -n '/^#/p' "$0" | head -40
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ "$ENGINE" = "omnivoice" ]; then
  # Delegate to the Python script that talks to the real engine.
  PY_ARGS=""
  [ "$SKIP_EXISTING" = 1 ] && PY_ARGS="--skip-existing"
  echo "Rendering cloning + voice-design demos via VoiceStudio engine…"
  if [ -d "${REPO_ROOT}/.venv" ]; then
    "${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/scripts/render_demos_omnivoice.py" $PY_ARGS
  else
    echo "WARN: .venv missing; trying system python3" >&2
    python3 "${REPO_ROOT}/scripts/render_demos_omnivoice.py" $PY_ARGS
  fi
  echo ""
  echo "Note: dictation samples are still rendered via 'say' — re-running for them now."
  # Fall through to render dictation; --skip-existing will preserve the
  # VoiceStudio-rendered cloning + design outputs we just produced.
  SKIP_EXISTING=1
  ENGINE="say"
fi

if ! command -v say >/dev/null; then
  echo "ERROR: 'say' not found. This script currently requires macOS." >&2
  echo "TODO: add espeak-ng path for Linux contributors." >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null; then
  echo "ERROR: 'ffmpeg' not found. Install via 'brew install ffmpeg'." >&2
  exit 1
fi
# We use plain heredocs + python3 for JSON, so bash 3.2 (macOS default) is
# fine. No need for bash 4 features like ${var@Q} or associative arrays.

# ── render(voice, text, out_path, sample_rate_hz) ─────────────────────────
render() {
  local voice="$1" text="$2" out="$3" sr="${4:-24000}"
  if [ "$SKIP_EXISTING" = 1 ] && [ -f "$out" ]; then
    echo "  · skip (exists): $out"
    return
  fi
  local tmp_aiff
  tmp_aiff="$(mktemp -t omni-demo).aiff"
  say -v "$voice" -o "$tmp_aiff" "$text"
  ffmpeg -y -loglevel error -i "$tmp_aiff" \
    -ar "$sr" -ac 1 -sample_fmt s16 "$out"
  rm -f "$tmp_aiff"
  local size
  size="$(du -h "$out" | awk '{print $1}')"
  echo "  ✓ $(basename "$out") ($size, $voice @ ${sr}Hz)"
}

echo ""
echo "── Voice cloning demo (24kHz mono 16-bit) ─────────────────"
# Reference clip — replaces the existing 3-second "bleep" file.
# Voice: Samantha (en_US adult female, the macOS default — clean, neutral,
# warm). Reference text from the cloning spec.
render "Samantha" \
  "Hey. I'm the VoiceStudio demo voice. I was made right here, on your machine: private, local, and ready whenever you are." \
  "${SAMPLES_DIR}/demo_voice.wav" 24000

# Pre-rendered clone output — same voice, different text. Used when the user
# hits Preview but no TTS engine has weights cached yet.
render "Samantha" \
  "Welcome aboard. I was just a three-second clip a moment ago. Now I can say anything you'd like, in your voice or mine." \
  "${SAMPLES_DIR}/demo_clone_output.wav" 24000

echo ""
echo "── Voice design demo (24kHz mono 16-bit, 7 presets) ───────"
# Each preset showcases a different axis (age / register / accent / use case).
# Voice picks aim for distinctness on first listen, not 1:1 spec fidelity —
# they'll be re-rendered via the real engine later. The two character voices
# (Captain Crusty + Junior Quacks) capture the cartoon-style vibe the user
# wanted without touching copyrighted IP.

render "Daniel" \
  "The clock tower struck thirteen, and for the first time in her life, Eleanor wondered if she had been counting wrong all along." \
  "${DESIGN_DIR}/demo_voice_design_audiobook_uk_narrator.wav" 24000

render "Ralph" \
  "Good evening. Topping our broadcast tonight: scientists at the coastal observatory have confirmed the signal is, in fact, repeating." \
  "${DESIGN_DIR}/demo_voice_design_us_news_anchor.wav" 24000

render "Rishi" \
  "Thank you for calling VoiceStudio support. I can see your account here. Let's get this sorted out together." \
  "${DESIGN_DIR}/demo_voice_design_indian_support_agent.wav" 24000

# Captain Crusty — gravelly cartoon-sailor villain. Inspired by the "tough old
# seafarer" archetype, original character. Bad News is a low novelty voice
# that lands the gravelly-villain register.
render "Bad News" \
  "You came a long way for an answer you already had. Sit. The fire is warm, and the truth is not." \
  "${DESIGN_DIR}/demo_voice_design_gravelly_villain.wav" 24000

render "Karen" \
  "Right, so here's the wild bit. Nobody told the engineers the satellite was supposed to be in orbit by Tuesday. Tuesday came and went." \
  "${DESIGN_DIR}/demo_voice_design_aussie_podcaster.wav" 24000

# Junior Quacks — anxious cartoon-nephew character. "Bahh" is a squawky
# novelty voice that approximates the high-strung sidekick archetype.
render "Bahh" \
  "Once, in a town where every street was named after a kind of bread, a small fox decided she was going to learn to play the cello." \
  "${DESIGN_DIR}/demo_voice_design_bedtime_storyteller.wav" 24000

render "Tingting" \
  "今天天气巴适得很，我们去吃火锅嘛！记得多加点豆芽。" \
  "${DESIGN_DIR}/demo_voice_design_mandarin_sichuan.wav" 24000

echo ""
echo "── Dictation demo (16kHz mono 16-bit, 3 scripts) ──────────"
# 16 kHz matches WhisperX's preferred ingest rate.
render "Samantha" \
  "Schedule a meeting with Pat for Tuesday at three PM and remind me to bring the quarterly report." \
  "${DICT_DIR}/en_conversational.wav" 16000

render "Fred" \
  "Patch the WebGPU shader in renderer dot tsx, then bump pnpm to nine point fifteen and rerun the Vitest suite." \
  "${DICT_DIR}/en_technical.wav" 16000

render "Thomas" \
  "Bonjour, je voudrais réserver une table pour deux personnes à vingt heures." \
  "${DICT_DIR}/fr_reservation.wav" 16000

echo ""
echo "── Manifest ───────────────────────────────────────────────"
cat > "${SAMPLES_DIR}/demo/manifest.json" <<EOF
{
  "version": "0.3.0",
  "rendered_by": "macOS say (bootstrap)",
  "rendered_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "license": "NOASSERTION",
  "license_review_required": true,
  "assets": {
    "clone": {
      "reference": "samples/demo_voice.wav",
      "prerendered_output": "samples/demo_clone_output.wav"
    },
    "voice_design": {
      "audiobook_uk_narrator": {
        "wav": "samples/voice_design/demo_voice_design_audiobook_uk_narrator.wav",
        "display_name": "The Librarian",
        "instruct": "female, middle-aged, low pitch, british accent",
        "use_case": "Audiobook narrator"
      },
      "us_news_anchor": {
        "wav": "samples/voice_design/demo_voice_design_us_news_anchor.wav",
        "display_name": "The Anchor",
        "instruct": "male, middle-aged, moderate pitch, american accent",
        "use_case": "News broadcast"
      },
      "indian_support_agent": {
        "wav": "samples/voice_design/demo_voice_design_indian_support_agent.wav",
        "display_name": "The Helpdesk",
        "instruct": "female, young adult, moderate pitch, indian accent",
        "use_case": "Customer-service / IVR"
      },
      "gravelly_villain": {
        "wav": "samples/voice_design/demo_voice_design_gravelly_villain.wav",
        "display_name": "Captain Crusty",
        "instruct": "male, elderly, very low pitch",
        "use_case": "Video-game NPC / cartoon villain"
      },
      "aussie_podcaster": {
        "wav": "samples/voice_design/demo_voice_design_aussie_podcaster.wav",
        "display_name": "The Podcaster",
        "instruct": "female, young adult, high pitch, australian accent",
        "use_case": "Podcast / explainer"
      },
      "bedtime_storyteller": {
        "wav": "samples/voice_design/demo_voice_design_bedtime_storyteller.wav",
        "display_name": "Junior Quacks",
        "instruct": "young, anxious, high pitch, squawky",
        "use_case": "Cartoon sidekick / children's storyteller"
      },
      "mandarin_sichuan": {
        "wav": "samples/voice_design/demo_voice_design_mandarin_sichuan.wav",
        "display_name": "The Sichuan Friend",
        "instruct": "female, young adult, moderate pitch, 四川话",
        "use_case": "Non-English showcase"
      }
    },
    "dictation": {
      "en_conversational": {
        "wav": "samples/dictation/en_conversational.wav",
        "expected_transcript": "Schedule a meeting with Pat for Tuesday at three PM and remind me to bring the quarterly report.",
        "language": "en"
      },
      "en_technical": {
        "wav": "samples/dictation/en_technical.wav",
        "expected_transcript": "Patch the WebGPU shader in renderer.tsx, then bump pnpm to nine point fifteen and rerun the Vitest suite.",
        "language": "en"
      },
      "fr_reservation": {
        "wav": "samples/dictation/fr_reservation.wav",
        "expected_transcript": "Bonjour, je voudrais réserver une table pour deux personnes à vingt heures.",
        "language": "fr"
      }
    }
  },
  "rerender_with_omnivoice": "scripts/build_demos.sh --engine omnivoice (TODO)"
}
EOF
echo "  ✓ demo/manifest.json"

echo ""
echo "── Totals ─────────────────────────────────────────────────"
du -sh "${SAMPLES_DIR}" | awk '{print "  Bundle size: " $1}'
find "${SAMPLES_DIR}" -name "*.wav" | wc -l | awk '{print "  WAV count:   " $1}'
echo ""
echo "Done. To re-render with the VoiceStudio engine later:"
echo "  scripts/build_demos.sh --engine omnivoice"
