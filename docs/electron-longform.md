# Electron Stories and Audiobooks

Stories and Audiobook are available from the sidebar and command search. Each keeps a separate draft. Choose a saved default voice, enter or import text, then render. Stories supports per-line voice overrides; chapter headings and pause markup use the same `storyToSpans` compiler as Tauri. Audiobook submits the original script to the existing backend parser.

Both editors use the shared longform backend, real chapter progress, assembly status, and an explicit Stop action. Stop aborts the HTTP stream so the backend stops scheduling chapters. A truncated stream cannot replace the last successful output. Interrupted server manifests can be resumed explicitly without sending the edited manuscript again. Navigation leaves a render running; a renderer restart disconnects it and recovery uses the server manifest inventory.

Playback and export remain independent of generation. Vidstack plays the output; long-form audio uses a seek bar without decoding an entire book into a waveform. Play waits until the native media provider is ready. MP3 and M4B are supported by the existing backend. The native export dialog saves a local copy.

Draft writes reuse the coalesced persistence helper and flush on lifecycle events. Working drafts currently use browser storage. Named Stories and Audiobook projects use the shared native IndexedDB adapter in a separate Electron database. Saves await the committed transaction, concurrent writes are serialized, and opening or deleting a project requires confirmation. Projects include script/lines, voices, settings and the last output reference. Storage failures are surfaced. Book details include author, narrator, year, genre, description and an uploaded JPEG/PNG cover. Loudness offers off, ACX and podcast presets. Audiobook pronunciation rows reach the existing lexicon engine; duplicate words are flagged before rendering. These settings persist with the draft. Full cast management and other advanced controls remain tracked in `electron/PARITY.md`.

`electron/tests/longform-smoke.mjs` verifies the main flows with mocked generation and real audio playback. Runtime tests cover stop, duplicate prevention, truncated responses, explicit resume and shared Stories compilation. Real installed-model verification passed for one English chapter: MP3 rendering, cached WAV chapter audition, and Electron-driven M4B rendering/playback. Multi-voice, multi-chapter, cancellation/recovery and other engines still require real runtime coverage.

Script voice tags now expose saved-profile assignments in the Cast section. Only names present in the current script are sent as `voice_map`; assignments persist for reuse if a tag is reintroduced. An assignment to a removed profile blocks rendering until changed or reset to Default. Chapter, word and estimated runtime counts reuse the Tauri script helpers.

Audiobook Preview plan uses the backend parser and exposes per-chapter auditions. Auditions send the same voice, cast, language and pronunciation inputs as full renders, warming the shared cache. Editing those inputs clears stale auditions. Preview requests are aborted on navigation, never replace a full render, and expose their own Vidstack playback.

Production overrides now expose synthesis steps, guidance, sampling temperatures, postprocessing, seed and repeat variation. Emotion controls appear when the active engine advertises support. Reset restores the shared Tauri defaults; untouched fields are omitted from requests. The extracted `longformOverrides` helper is used by both apps, and chapter auditions carry the same overrides as full renders.

Run `electron/tests/longform-live.mjs` with `VOICESTUDIO_LIVE_PROFILE` set to a local saved profile for an explicit real-render check. It verifies the active model is already installed before synthesis. The real run exposed missing JSON request headers in the shared client and array-shaped failed-chapter results; regression tests now cover both.

Stories now includes named characters with saved voice assignments, character selection per line, explicit per-line voice overrides, shared/global speed and per-line speed overrides. Voice resolution and chapter compilation reuse the Tauri helpers. Lines move up/down with keyboard-accessible buttons; chapter insertion uses the same heading grammar. A story with every spoken span assigned can render without an unnecessary default-voice selection. Removed profiles block rendering rather than silently changing the voice.

Stories Auto-cast accepts tagged dialogue, screenplay text and attributed prose. The shared local parser appends lines, reuses existing characters/voices, and assigns available profiles to new speakers. Distinct names that normalize to the same identifier remain separate characters. No network or LLM call is involved.

Stories imports TXT, Markdown and SRT locally; EPUB/PDF still use the shared backend importer. The EPUB importer skips marked pagination and ancillary sections such as cover, title, dedication, contents and copyright pages. Unmarked numeric paragraphs and page-break layout classes remain narration. Chapter titles prefer the EPUB 3 table of contents, then NCX labels and headings; namespace aliases and declared encodings are supported. Malformed optional navigation falls back to headings, while malformed package/container XML reports an import error. Imported text goes into a persisted review buffer, then Auto-cast or Split into lines appends to existing work. The splitter is shared with Tauri and offers three presets: Sentences (the sentence-aware splitter with the same 40-2000 character limit), Paragraphs (the default; one line per blank-line paragraph, or per line when the text has no blank lines at all; LF, CRLF and lone-CR endings are all understood) and Chapters (one line per chapter body, split only at chapter headings — a non-empty `# Title`, the same grammar the renderer uses). A chapter heading is always its own line so it renders as a chapter marker.

The Electron workspace keeps title, default voice, language and output format together in a fixed setup card. Advanced cast, project, production and book controls use separate collapsed cards. The editor keeps its full working width; an empty Story shows one Add First Line action, while an empty Audiobook shows the supported chapter and voice markup directly in the script field.

Stories line auditions reuse the shared WAV assembler and canonical voice/pause/speed parser. Auditions resolve inline names with the same fallback as full renders, expose Stop, and play through Vidstack independently. Changing synthesis inputs clears stale playback; navigation aborts pending auditions. Browser coverage verifies actual playback with mocked generation.

The optional Stories Stems section renders one WAV per character with the shared Tauri assembler. It reports completed character groups, supports cancellation, and offers explicit individual downloads after completion. No automatic multi-file download is triggered. Input changes discard stale download links. Browser regression checks all character groups and the downloaded WAV bytes. The shared native Save As bridge writes renderer-generated bytes in packaged Windows and Linux builds; macOS remains the native export gate.

EPUB chapters honor byte-order marks and XML/HTML encoding declarations, including
legacy Latin and CJK encodings. Unsupported or invalid declarations use the shared
text-import fallback so one bad declaration does not abort the book.

EPUB decompression limits include required metadata as well as navigation and chapter content. A navigation document without a navigation element falls back to chapter headings.

### Long-script layout

The Stories editor grows with the manuscript inside the page scroll container. Long scripts push generation progress and Generate/Stop controls below their content, so lines do not paint over those controls during rendering.

### Clear a script

Stories and Audiobook offer **Clear script** with confirmation. Stories removes all lines, chapter markers and pending import text while retaining the cast; Audiobook clears its manuscript. The web Stories editor clears pending pasted text too, stops playback, ignores late preview results and releases preview audio when clearing, removing or replacing lines. Confirmation also names pending imported text. Clearing an imported script does not recreate the demo story.
