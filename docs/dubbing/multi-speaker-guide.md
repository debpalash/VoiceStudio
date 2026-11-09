# Step-by-step: dub a video with several characters

This walkthrough takes a beginner from a video file to an exported dub in
which every character keeps their own voice. The worked example dubs a
20-minute English video with five characters into Hindi, but the same steps
work for any language pair VoiceStudio supports.

Everything runs on your computer. The first run downloads the models it
needs; after that the steps work offline.

## Before you start

- **Install VoiceStudio** and finish first-run setup
  ([macOS](../install/macos.md) · [Windows](../install/windows.md) ·
  [Linux](../install/linux.md) · [Docker](../install/docker.md)).
- **Turn on speaker detection.** Telling characters apart uses the pyannote
  diarization model, which needs a free Hugging Face token and a one-time
  licence click. Follow [Speaker diarization](../features/diarization.md#license-acceptance-flow).
  Without it every line is treated as one speaker.
- **Use a clean source if you can.** Music and effects are separated from the
  voices automatically, but clear dialogue gives better clones. A 20-minute
  video takes a while on a CPU; a GPU (CUDA, Apple Silicon or ROCm) is much
  faster.

## 1. Load the video

1. Open **Dubbing** in the sidebar.
2. Drop the video on the import card, or paste a link.
3. Under the file, set **Spoken language** to English (Auto also works) and
   set **Speakers** to the number of characters, here `5`. Leave it blank to
   let VoiceStudio guess; set it when two characters keep ending up as one.

VoiceStudio separates the voices from the background, transcribes them and
labels each line with a speaker (`Speaker 1`, `Speaker 2`, ...).

## 2. Check who says what

Skim the transcript before translating; fixing speakers now saves a re-dub.

- **One line, wrong speaker:** click the speaker name at the top of the row
  to open its options, then pick the right speaker under **Speaker**.
- **Many lines, wrong speaker:** tick the lines' checkboxes and use
  **Set speaker...** in the selection bar.
- **A character diarization missed:** choose **New speaker** in either place.
  The new speaker starts with the default voice; give them one in step 4.
- **Two characters in one line:** open the row's **...** menu, put the cursor
  where the second character starts and choose **Split at cursor**, then set the
  speaker of the new line. **Insert line below** adds an empty line after the
  current one, which you can give its own speaker and timing.

A line that followed its old speaker's voice moves to the new speaker's
voice automatically. A voice you picked for that one line on purpose stays.

## 3. Translate into Hindi

1. Choose **Hindi** as the target language.
2. Click **Translate** and read through the result. Click any line to edit it.
3. Optional: add names and recurring terms to the **Glossary** so they are
   translated the same way every time.

## 4. Give each character a consistent voice

Open **Cast** (the casting board). Every detected speaker is listed with the
voice it will use:

- **Auto · Speaker N** clones that character's own voice from the video and
  speaks Hindi with it. This is the default and usually what you want.
- Pick a saved voice from the list, or drag a voice chip onto a speaker, to
  use a voice from your library instead (for example a voice you designed).

For steady character voices across a long video, set **Voice match** to
**Consistent**: every line of a character then clones from the same
reference. **Per line** follows each line's own delivery more closely but can
drift between lines.

## 5. Generate and review

1. Click **Generate**. Progress shows in the footer.
2. Use the headphones button on a row to hear one line, and **Verify** to
   check timing against the source.
3. Changed a line, its speaker or its voice? Generate again: only the changed
   lines are re-rendered, and they keep using each character's voice.

If a line runs too long for its slot, the row shows a warning. Shorten the
Hindi text, use the suggested shorter wording, or choose a timing option such
as Smart Fit in the Timing section.

## 6. Export

Open **Export options**:

- **MP4** muxes the dub into the video. Keep **Original** too if viewers
  should be able to switch back. **Background audio** keeps music and
  effects under the dub.
- **SRT / VTT** export subtitles. **Burn subtitles into picture** draws them
  onto the video instead.

You can also export before any dub exists: SRT/VTT export the transcript (or
its translation), and MP4 with burned-in subtitles keeps the original audio.

## Fixing robotic or flat output

- Clone references need a few seconds of clean speech. A character who only
  speaks in very short lines may fall back to the default voice; assign them
  a saved voice on the casting board.
- For emotion and style, see [Expressive speech](../expressive-speech.md).
  In the dubbing editor each line's options also have a **Direction** field
  (for example "whispered" or "excited"), and **Mirror source delivery** in
  the transcript menu copies the original actor's delivery onto each line.

## More walkthroughs

- [Exporting dubbed video](export.md)
- [Translation engines](translation-engines.md)
- [Dubbing editor reference](../electron-dubbing.md)
- [Troubleshooting installs](../install/troubleshooting.md)
