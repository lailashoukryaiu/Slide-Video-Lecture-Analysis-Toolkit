# LectureSeek - Lecture Video Analysis

[![ACM MM 2025](https://img.shields.io/badge/ACMMM-2025-1B427D)](https://dl.acm.org/doi/pdf/10.1145/3746027.3756873) 
[![Python](https://img.shields.io/badge/python-3.7+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.68+-green.svg)](https://fastapi.tiangolo.com)

## News
[31.10.2025] We got **Best Open-Source SW Paper Award at ACM Multimedia 2025**!

[03.07.2025] Video Lecture Analysis Toolkit got accepted **ACM Multimedia 2025 Open Source Track**!

## Overview

**LectureSeek** is a lecture video analysis platform for educational content processing, particularly slide-based lecture videos and presentations. It works with uploaded video recordings; YouTube import is an optional source, not the application's purpose. The system combines computer vision, natural language processing, and machine learning techniques to provide automated video analysis, transcript generation, slide text extraction, and content relationships discovery.

![Demo](demo.gif)

## About this fork

This repository builds on [NimaZahedi's Slide-Video-Lecture-Analysis-Toolkit](https://github.com/NimaZahedi/Slide-Video-Lecture-Analysis-Toolkit), which is itself based on [travisseng/svla-toolkit](https://github.com/travisseng/svla-toolkit). The sections below distinguish early fixes needed to run the inherited project from fixes and development added later in this repository.

Compared with the shared base commit `94750bc`, this repository had **190 commits not present in its parent fork's shared history**, as of `3222940` (72 files changed, with 18,828 additions and 1,513 deletions). This count includes maintenance and iterative fixes; it is not a count of 190 separate features. The initial compatibility work below is part of that total.

### Initial fixes to the inherited project

The first changes addressed compatibility and runtime problems in the code inherited by this fork:

- Updated Gemini and PySceneDetect integration, and adapted FastAPI/Starlette template responses to the installed API.
- Fixed Gemini credential initialization for Colab Secrets and local environment variables.
- Made Whisper device selection account for the available hardware instead of assuming a GPU.
- Corrected runtime paths and GPU assumptions across video, scene, OCR, transcript, embedding, and export processing.
- Updated dependencies and addressed early performance, security, and compatibility issues.

These are the changes specifically described as fixes to the inherited project. Later fixes listed below address problems encountered or introduced while developing this fork; they should not be read as claims about bugs in the original project.

### Recent fixes in this repository

- Preserve existing chapters when chapter generation fails, and recover from malformed model-generated JSON before accepting or saving a result.
- Avoid losing transcript work during speaker processing; show transcription progress and allow users to stop or browse other saved transcripts.
- Prevent outdated OCR controls from appearing and keep the primary and optional OCR passes in the correct order.
- Keep transcript translations, chapter titles, timeline labels, and export suggestions consistent with the selected video and language.
- Improve saved-video loading, Unicode handling, export media paths, section titles, and generated filenames.
- Group sections around genuine topic changes without imposing an arbitrary section-count limit.
- Verify chapter cuts after slide grouping, then verify internal section starts against transcript evidence, preserving millisecond timestamps and rejecting overlapping cuts.

### Improvements over the inherited project

- More observable and recoverable processing: clearer progress, retries for recoverable failures, and controls to stop or resume browsing while work continues.
- Running actions and their progress indicators share an amber highlight in light and dark themes; the highlight clears when work finishes, fails, or is cancelled.
- Chapter editing keeps title fields and compact actions on one row. Chapter and section rows support single-click and keyboard seeking, and playback follow-scrolling pauses while the chapter list is hovered or focused.
- Chapter titles have sequential display numbers without changing saved titles. The Chapters tab highlights the playing chapter and section and shows their names even when sections are collapsed. Turn this off with **Chapter options → Highlight current chapter and section during playback**; the preference is remembered in this browser.
- The **+** between section rows offers **Add section** or **Start new chapter here**. Starting a chapter uses the following section's boundary and title by default and moves all remaining sections into the new chapter, up to the next existing chapter. Repeat to split a chapter into several chapters; timestamps and section content are preserved.
- Grey, unassigned chapters and sections can have their ends adjusted directly without renaming or clearing review flags. Any remaining time stays covered by grey parts; normal boundary and video-duration validation still applies.
- Quick outline editing keeps chapter/section summaries collapsed by default. Expand **Section summary** to edit it; end-time explanations are hidden behind **?**.
- More flexible slide analysis: configurable detection modes and detail, diagnostics, optional OCR, and slide-aware navigation.
- More useful course exports: selectable grouping and formats, sentence-aware boundaries, persistent export jobs, progress reporting, and saved-export links.
- A more adaptable interface with a compact responsive layout, resizable panels, collapsible options, and synchronized transcript, chapter, slide, and timeline navigation.

### Model and provider changes

- Added Groq and OpenAI fallbacks for chapter generation and translation, with explicit model choices where supported.
- Added hosted transcription choices alongside local Whisper, and selected local Whisper settings based on available hardware.
- Updated model compatibility as providers changed their APIs and retired models; automatic Gemini fallbacks now use Gemini 3.5 Flash-Lite rather than retired Gemini 2.5 models.
- Added fallback handling for provider quotas and temporary unavailability. Provider outages or exhausted quotas can still prevent AI work.

### New capabilities added in this fork

- Upload, translate, regenerate, select, and retain versions of transcripts.
- Generate transcript-language chapters with topic sections, summaries, and optional slide titles.
- Detect slides independently of transcript generation; run OCR, download slide images, and export OCR text.
- Identify speakers without retranscribing, with controls and progress for speaker detection.
- Create grouped HTML, Word, PDF, SCORM, transcript, and media exports, and retain completed exports for later viewing or download.

**In short:** this fork aims to be a more practical end-to-end lecture workflow: it addresses early environment compatibility, gives users more control over analysis, helps preserve work when processing fails, and connects transcripts, translations, chapters, slides, and exports in one interface.

### Key Features

- **Scene Detection**: Content-cut detection by default, plus adaptive slide detection with configurable detail, duration, duplicate filtering, hourly limits, and optional transcript chapter boundaries
- **Slide Content in Exports**: Optionally add each slide's extracted text (OCR) as a bullet list under its chapter in HTML, Word, PDF and outline exports
- **Visual Timeline**: Thumbnails offer chapter grouping with optional sections and slide changes. Playback navigation types are selected independently. Slide changes use a two-second noise floor by default; optional advanced build-duration filters can scale with video length.
- **Scene Names**: Slide Changes shows the existing timeline topic name next to
  each scene number when available, preferring the specific section title and
  falling back to the chapter title, without another AI request.
  **Add / refresh slide titles** applies the same titles after slides were detected
  earlier. If chapters are missing, it first generates them from the open transcript;
  it never reruns slide detection or OCR.
- **Optional Chapter Slides**: Chapter generation uses the transcript only.
  Enable **Add detected slides** afterwards to show existing slide images under
  chapters and sections; this does not detect slides, run OCR, or regenerate chapters.
- **Dual OCR Systems**: Run Start Slide OCR first to read detected text with Tesseract. Once that pass completes with readable results and no pending or failed text elements, Process with Surya OCR becomes available as an optional additional pass.
- **Multi-Source Transcription**: YouTube transcript extraction and Whisper-based speech recognition
- **Batch VTT Translation (no video required)**: Open **Extra options** in
  the top toolbar, then **Translate VTT files**. Choose multiple UTF-8 `.vtt` files and a target language
  (English, German, Arabic or Polish), then choose a translation model and click
  **Translate files**. Uses the same configured providers as transcript translation.
  Download a ZIP containing `<original-name>_<LANG>.vtt` files; duplicate names get
  a numeric suffix. Cue timestamps, identifiers, settings, inline tags, line breaks,
  STYLE/REGION blocks and notes are preserved. Only visible caption text is translated.
  Visible text fragments are translated in bounded batches without synthetic markers.
  Cue boundaries and formatting remain local, so the model cannot alter them.
  Short Groq fragments are packed up to 60 items within the existing character budget.
  Only missing translations are retried; successful translations are kept.
  Markdown JSON fences are accepted. Malformed JSON or invalid response shapes
  trigger smaller requests on the same model (at most two split levels), preserving
  successful sub-batches. Persistent malformed output remains an explicit error.
  Translation requests have a 30-second SDK timeout and no hidden SDK retries.
  Short Groq rate-limit waits (at most 15 seconds including a safety margin) get
  one retry of the same request on the same model, including with fallback disabled.
  Gemini minute-rate limits with an explicit retry delay get at most two retries
  of the same request, each capped at 65 seconds including a one-second safety
  margin. Gemini batches run sequentially to avoid concurrent request bursts.
  Daily, zero-capacity, unknown and longer quota waits do not trigger these retries.
  Exhausted retries, overload and timeout errors switch to the next configured
  model when fallback is enabled, or report an error when it is disabled.
  Provider limits can still prevent a translation.
  Both transcript and VTT translation save completed fragments locally after
  every successful response, including partial/malformed-response recovery.
  After a failure or server restart, retrying identical input and target language
  automatically resumes the remaining lines, even when selecting another model.
  Changed text, timestamps, speakers, chapter fields or target language use a new
  checkpoint. VTT retries also retain completed files until the entire ZIP is built.
  Successful jobs clear their checkpoints. Usage totals describe the current
  attempt, not requests made before resuming. Failed jobs from before this feature
  was installed cannot be recovered retroactively.
  Progress shows the current file, provider/model, completed batches, retry waits and elapsed time.
  The ZIP also includes `translation-report.json` with models, fragment/item counts and
  provider-reported input/output/total tokens. Missing usage is marked unavailable
  or partial, never treated as a complete zero-token result.
  Completed ZIPs remain in **Saved translations** after reload; opening the dialog
  reconnects to the last active batch. Invalid files are rejected before translation.
  A failed batch is reported explicitly, not presented as a complete partial ZIP.
  Up to 30 files, 5 MB per file and 20 MB total are supported.
  No video is uploaded and current lecture transcripts/detections are not modified.
  Translation model choices are shared with transcript translation, including
  Groq GPT-OSS 120B and GPT-OSS 20B, Gemini Flash/Flash-Lite
  and OpenAI GPT-4.1 mini, plus dedicated LibreTranslate and Microsoft Translator.
  Unconfigured or known-unavailable services/models are disabled.
  Auto tries configured LLMs first (Groq preferred), then open-source LibreTranslate,
  and Microsoft Translator last. If no LLM is configured, Auto starts with the
  first configured translation service. Chapter generation still uses only LLMs.
  Groq has request/token
  limits and uses small sequential batches; Gemini is also sequential, while
  OpenAI uses bounded concurrency.
  **Allow model fallback** is enabled for Auto. Choosing a specific model turns
  it off; explicitly enable it to permit switching providers/models.
  Batch file/language/model settings are locked while the batch runs.
  Fallbacks can incur charges under the configured provider's plan.
  Downloads use a single standard browser download flow for ZIPs, videos and
  chapter documents. Your browser's download settings determine the folder or
  whether to ask for a save location. The app does not create a separate
  save-picker placeholder before downloading.

- **Caption Video Copies**: Select a transcript or translation from the saved
  versions, then click **Add captions to video** in the Transcript toolbar.
  Its **Transcript for captions** dropdown uses the same saved versions; changing
  it also changes the transcript shown on screen.
  Choose the appearance, inspect the preview, then click **Create video copy**.
  The current transcript is captured when you create the copy, so later version
  changes do not alter the export. **Saved copies** at
  the top of **Add captions to video** is a dropdown listing copies of the current video only, newest first, with
  filenames and creation dates. Only completed copies appear in the dropdown;
  the newest is selected automatically. Click **Download MP4** to save the selected
  copy with the app's save-file workflow; the refresh button reloads the list.
  Below the creation controls, only the current export's progress is shown,
  followed by a **Download MP4** button when it finishes. Opening the dialog
  reconnects to an active export, without repeating old completed or failed jobs.
  Progress and elapsed time update in place, without rebuilding the popup or
  its saved-copy dropdown. Caption burning uses a faster software encoding
  preset at the same resolution and CRF quality setting; copies may be larger.
  Switching videos updates the dropdown to that video's copies. Copies also remain in **Saved exports**.
  Jobs and completed downloads remain available after a page
  reload; a server restart interrupts unfinished encoding. The original video is
  never overwritten. Choose white-on-black-box, white outlined, or yellow outlined
  captions. New video filenames include the caption language as an uppercase
  two-letter code, for example `Lecture_DE caption.mp4` or `Lecture_EN caption.mp4`.
  The language comes from the selected translation or the existing transcript-language
  detector, without a separate language selector. Older saved copies keep their original names.
  Choose **Selectable CC tracks (fast)** to include several saved transcripts or
  translations in one MP4. Select the versions in **Caption tracks**; the first
  selected version is the default track. This copies video and audio without
  re-encoding, so it is substantially faster than permanent captions and does not
  change picture quality. Each track has a title and language metadata. Players
  such as VLC can switch tracks or turn them off; embedded MP4 subtitle support
  varies between browsers and players, and appearance is controlled by the player.
  Filenames include the selected language codes, e.g. `Lecture_EN_DE caption.mp4`.
  **Use in editor** loads a completed copy without changing the lecture ID,
  transcripts, chapters, slide detections or OCR. Playback position is retained.
  The editor loads matching WebVTT tracks for selectable copies so browser playback
  does not depend on native MP4 subtitle support. Only one caption track displays
  at a time; switching sources disables old tracks before removing them, preventing
  stale caption layers from remaining on a paused video. Chapter and section clips use the
  selected video copy and retain all its embedded subtitle tracks. **Use original**
  switches back without deleting copies or analysis. Source selection lasts until
  you load another video or reload the page; saved copies remain available.
  Choose small/medium/large text and top/bottom positioning in that dialog.
  The default is medium white text in a black box, at the bottom.
  This dialog shows a real FFmpeg-rendered preview on the selected video's
  frame before exporting. **Enlarge preview** opens a
  large, viewport-fitted view; closing it returns to the side-panel options.
  Preview captions scale with the displayed video frame; a small thumbnail is
  not the final video's absolute text size. The enlarged preview uses the same
  caption style and wrapping as the exported MP4.
  Opening the dialog or changing options does not start a video export.
  Captions use safe margins and short, two-line blocks rather than large paragraphs.
  Long cues are split without dropping words; their sub-cue timing is estimated
  proportionally from the source cue duration, not newly recognized word timings.
  SRT/WebVTT files retain the original transcript cues. Preview and MP4 encoding
  use the same style and wrapping code;
  rendering requires FFmpeg with the subtitles/libass filter and installed fonts
  for the selected language. No transcription, translation, or AI call is made.
  The separate **Download transcript** toolbar action opens a format chooser for
  **SRT** and **WebVTT**,
  using UTF-8 text and the selected version's timestamps. These files are quick
  sidecar downloads, not burned-in video. Missing/zero cue durations extend to the
  next distinct cue start (or video end); cues beyond the video end are excluded.
- **Processing Controls**: Stop transcription/speaker detection and restore the
  previous transcript, or keep processing in the background while browsing saved
  transcripts. Selecting a saved version prevents progress polling from replacing it.
  A compact status strip above the workspace keeps the latest processing step visible
  across tabs, with **Show progress** and **Stop** controls. Background polling continues
  when browsing saved transcripts; completion offers **View result**, and failures
  remain visible until dismissed without replacing the transcript being viewed.
  While speakers are being labeled, the existing transcript (or newly completed
  speech-to-text result) stays available after reopening the video, so topic and
  timestamp chapters can be generated without waiting for speaker labels.
  The highlighted next-step prompt stays visible near the top while scrolling.
  Transcript completion status clears after five seconds; failures stay visible
  until dismissed.
  Reopening a saved video reconnects to the running job's steps and refreshes
  the saved-transcript selector even when the current transcript is still processing.
- **Saved Video Loading**: Saved transcripts and scenes are read as UTF-8,
  including Arabic text, without printing transcript content to the server console.
- **Speaker Identification**: Add speakers to the current transcript without
  re-transcribing. Interrupted speaker jobs restore the original transcript
  on server startup; worker initialization leaves active jobs untouched.
- **Gemini Transcript Formatting**: Structured JSON output with one visible retry
  per audio part if the response is unreadable; malformed output is not saved
  as a partial transcript.
- **Batched Translation**: Translate transcripts and chapter titles with an explicit Gemini/OpenAI model choice and visible provider/timing details
- **Semantic Embeddings**: Sentence-BERT based semantic similarity for transcript-OCR alignment
- **YOLO Object Detection**: Custom-trained YOLOv8 model for slide content detection (`slidevqa_best.pt`)
- **AI-Powered Summarization**: Google Gemini integration for chapter generation
- **Summary Provider Default**: Automatic summary generation prefers configured
  Groq, then Gemini and OpenAI; explicit model choices remain available.
- **Summary Language**: Chapter options default to the transcript's language,
  with English, German, French, Spanish and Arabic overrides.
- **Saved Transcript Versions**: Every generated or uploaded transcript and every
  translation is kept. Pick one from the list below the Transcript options button to use it again
  (summaries and exports follow the chosen version), or delete versions you no longer need.
- **Editable Content**: Use the pencil beside a chapter or section to correct its title,
  timestamps, summaries and section text; timestamps must remain ordered and sections
  inside their chapter. Manually changed timestamps are preserved exactly in new exports. When a chapter or section start is moved later, Save asks whether the vacated time should become a new chapter/section (OK) or extend the previous one (Cancel).
  **Edit slide titles** sets video-wide title overrides (leave blank to restore automatic
  titles). **Edit transcript** corrects segment text without changing timing or speakers
  and saves an edited version while keeping the original in the version list.
  Editing happens in the main panel, without a separate popup. All editors have
  explicit Save/Cancel controls and warn before discarding unsaved edits.
  Saved changes survive reloads and apply to navigation and new exports, not existing
  export files. Reload and reopen the editor if another operation changed the content.
- **Resizable Layout**: Drag the handles between the video, visual timeline and side
  panel to resize them; sizes are remembered and a double-click resets them.
- **Options State**: Options buttons show a downward indicator when closed and
  a highlighted **Open** state when expanded. Chapter options changes to
  **Hide chapter options** so it is clear how to collapse the inline panel.
- **Export Options**: Export Chapters opens the full options dialog immediately:
  grouping, fixed intervals, timestamps, sections, filenames and document formats.
  The default structure uses content chapters with key-point sections (slide changes
  are optional), numbered 1.1, 1.2 and timestamped at each part/section. Caption fragments are
  reassembled into sentences; internal part boundaries move to the next sentence
  start so no transcript words are lost or duplicated. These times are estimated
  from caption timing, while slide links retain the actual detected slide time.
  By default (option "Start each clip 1 second early"), each exported clip after the first starts up to one second early (clamped
  at the video's beginning), overlapping the previous clip to protect speech
  from small caption-timing errors. Logical chapter/transcript timestamps stay
  unchanged, and slide links account for the clip's earlier start.
  Unpunctuated Whisper phrases stay together until a timed speech pause or speaker
  turn; if neither timing nor punctuation identifies a safe break, the utterance
  remains intact rather than introducing arbitrary mid-sentence cuts.
  Each chapter also includes a concise summary in the transcript's language.
  **Chapter options → Chapter boundaries → My exact timestamps** uses your
  cut points instead of AI-selected chapter boundaries. Enter increasing
  MM:SS or HH:MM:SS timestamps (optional milliseconds), separated by new lines
  or commas. Do not include zero or video end. Titles, summaries and optional
  sections are generated from the transcript within each part; chapter starts
  are not snapped to slides, moved by cut verification, or merged when topics
  repeat. A cue crossing a cut can inform both neighbouring parts because
  word-level text cannot safely be inferred. Parts without transcript text
  are grey and explicitly labelled. The final range includes any silent tail
  through the actual video duration. Other boundary modes stay unchanged.
  **Chapter options → Split this video into MP4s (ZIP)** cuts the selected
  project video copy without changing its saved outline. **Extra options →
  Split uploaded video** is standalone: upload a video, enter cut points, and
  download the numbered MP4 parts as a ZIP. No transcript, AI or new project
  is needed. Both support 1–99 cuts (up to 100 parts), include video start and
  end automatically, and reject duplicate, unordered or out-of-range cuts.
  Uploads are limited to 2 GB. Splits re-encode for accurate seeking, preserve
  source resolution and audio, and retain compatible embedded subtitles through
  the existing MP4 export path. Actual video cuts follow frame precision.
  Progress, cancellation, saved-job recovery and ZIP download are available
  in the splitter; original media stays unchanged.
  The HTML export reads like a course outline: a linked outline at the top, AI-written
  part and section headings, direct summaries (no "the lecture explains that" filler),
  "Play from" links that seek the part's video, and collapsible transcripts.
  The app's **Navigate by** menu offers independent **Chapters**, **Sections**,
  and **Slide changes** checkboxes for Previous/Next buttons and keyboard navigation.
  Checked types are combined chronologically, with shared timestamps visited only
  once. Preferences are remembered; chapters are selected by default. With no types
  selected, navigation shows an explanatory notice. These choices do not change
  visual timeline visibility or export grouping.
  Titles appear directly beneath timeline screenshots (with the timestamp
  beside them) and beneath the playing video; HTML
  exports repeat chapter titles beneath each screenshot and chapter clip.
  New AI chapter titles are short noun phrases (at most five words); display
  titles are capped at 36 characters with full titles on hover. HTML
  screenshots are small clickable thumbnails beside chapter/section headings;
  clicking opens the image at full size. Slide labels use numbers only.
  Exports reuse saved full-size slide screenshots at matching timestamps, at
  their saved resolution. New captures are made only for missing images or
  chapter starts that do not match a detected slide; duplicate timestamps share
  one capture within an export.
  The slide-detection tab also offers **Export slides as one PDF**: one saved
  full-size screenshot per page in detection order, with no transcript, clips,
  or AI requests. Missing screenshots produce an explicit error.
  Video controls and the timeline sit directly below the player. Playback
  auto-scrolls within the transcript and timeline panels, without scrolling
  the surrounding page away from the video.
  The compact desktop layout keeps controls, video, timeline, and transcript
  together at normal browser zoom. Buttons and transcript rows use tighter
  spacing, video height adapts to the viewport, and panels remain scrollable.
  Narrow screens stack the panels and retain normal page scrolling.
  The interface uses a modern light theme that follows the operating system's
  dark-mode setting automatically, with visible keyboard focus and reduced
  motion when the system requests it. Options, processing, preview and download
  controls share visible accent-tinted buttons with an accent edge in both
  themes. Options retain their text labels and open/closed indicators rather
  than relying on color to identify their purpose. Main start/save actions use solid
  accent buttons; stop/delete actions use a red treatment. Navigation and
  Cancel/Close controls remain neutral.
  The right panel is narrow, and option dialogs and Settings open over it so
  the video stays visible. Caption previews, transcript downloads and saved
  caption settings use the same side-panel placement and width on desktop.
  Side-panel option dialogs are non-modal, so video controls and both resize
  handles remain usable while options are open. Escape closes the open options.
  Only the explicitly enlarged caption preview is modal.
  Interface labels are kept short. Caption, slide-detection and device
  explanations are available in expandable help sections; optional transcription
  prompts and speaker settings sit under **Advanced transcription options**.
  Progress, errors and required setup feedback remain available.
  Narrow-screen popups share one position near the content panel, keeping at
  least 248 pixels of usable height when the panel starts near the screen bottom.
  Detection
  buttons stay on the playback toolbar row, becoming icon-only on smaller screens.
  In HTML, the chapter's sections and transcript are grouped in indented,
  collapsible sections beneath the chapter. Word, PDF, HTML and outline exports
  share this hierarchy. Without configured AI,
  points are explicitly labelled extractive full sentences; configured AI failures
  are reported instead of silently substituting fabricated summaries. Select
  slide-only sections to omit AI key-point sections; chapter summaries are still
  generated when AI is configured. HTML downloads as a ZIP
  containing `index.html` and its required media: extract the complete package and
  open `index.html` locally. TXT/Markdown/JSON transcript files are packaged only
  when **Transcript files** is selected; other explicitly chosen formats remain
  included. Content-only, slide-only and fixed-interval grouping remain available.
  Webpage, SCORM and video-clip exports re-encode video with H.264's `veryfast`
  preset and CRF 23, retaining precise cuts, audio and selected caption streams.
  Long clips can still take time; progress updates roughly every ten seconds
  with the encoded percentage (or encoded minutes when duration is unknown).
  This speed/compression trade-off may change file sizes compared with the
  previous default `medium` preset. Document-only and screenshots/transcript-only
  exports do not encode clips.
- **Screenshots + transcript only (HTML)**: In **Export Chapters**, select this
  option after detecting slides and loading a transcript. Uncheck the other
  formats (including the default **HTML webpage**) to download a single,
  self-contained `.html` file. Each saved screenshot is embedded directly in the
  file, followed by the corresponding currently displayed transcript or
  translation. Fragments are assigned once by their start time to the visible
  screenshot; a fragment crossing a slide boundary remains with its starting
  screenshot, leading speech accompanies the first image, and final speech
  accompanies the last. Screenshots remain in chronological order even if a
  slide has no spoken text. There are no summaries, OCR text, video clips,
  external assets, AI calls or video encoding for this format alone. Open the
  download offline in a browser or revisit its **Saved exports** link.
  Selecting other formats too includes `screenshots_transcript.html` in the ZIP
  alongside those explicitly chosen outputs, whose existing processing is
  unchanged.
  Screenshot-only ZIP downloads use the original video's readable filename
  followed by `_screenshots.zip`, not its internal hash or a long button label.
- **Web Interface**: FastAPI-based web application with real-time updates

### Dedicated translation services

These translate text directly, without prompts or model-generated JSON. The app
batches caption fragments, validates returned text counts and uses the API's
input-order guarantee to keep cue formatting locally. They work for both VTT
batches and transcript translation.
Choose a service explicitly to use it immediately rather than waiting for LLM
fallbacks. Choosing it disables fallback by default; enable fallback explicitly
to permit another configured provider after temporary network/quota failures.
Authentication and malformed service responses remain explicit errors. With
fallback enabled, persistently malformed or missing LLM output can move to the
next provider after bounded recovery. Completed batches are retained within the
current job when switching providers.

- **LibreTranslate (open source)** uses Argos Translate. Use a hosted instance,
  or run it separately yourself. Set `LIBRETRANSLATE_URL` on the application server
  to the base URL (without `/translate`). For the hosted service, use
  `https://libretranslate.com` and configure `LIBRETRANSLATE_API_KEY` server-side.
  Hosted access requires an account/key and can incur charges despite the software
  being open source. For a local server, an example URL is
  `http://127.0.0.1:5000`. HTTP is accepted only on localhost; use HTTPS remotely.
  Follow the [official installation guide](https://docs.libretranslate.com/) to
  self-host it and install the language models you need. Language coverage, speed
  and translation quality depend on the instance; no endpoint or language support
  is assumed. The app sends up to 100 fragments / 10,000 UTF-16 units per request;
  choose/configure a server that accepts those limits.
- **Microsoft Translator** is the final optional proprietary fallback. Create an
  Azure Translator resource and configure `MICROSOFT_TRANSLATOR_KEY`, plus
  `MICROSOFT_TRANSLATOR_REGION` for regional or multi-service resources (not needed
  for a global resource). Uses the standard v3 neural text translation API with
  automatic source detection, not a generative LLM. Requests respect its
  1,000-item / 50,000-character limits. Azure quotas and charges still apply.

See [.env.example](.env.example) for empty configuration placeholders. Keys stay
on the backend; do not paste them into browser controls or commit them. Restart
the application after updating server configuration.

Reports show submitted and successfully translated character counts for these
services, not token counts. These are locally counted UTF-16 units (a conservative
request-size measure), not a billing statement. Failed attempts are included in
submitted counts; only successful ones in translated counts. Mixed LLM/service
jobs report character counts alongside available LLM token usage. No additional
Python dependency is needed to call the APIs. This integration does not install
or start LibreTranslate automatically.

## Interactive User Interface

The Video Lecture Analysis Toolkit features a web interface designed as an interactive dashboard for exploring analyzed lecture content with multiple synchronized views:

### Main Interface Components

**Video Player and Timelines**: The central video player includes an event timeline that marks slide transitions, chapter breaks, and search results. A visual timeline with clickable scene thumbnails provides navigation through the presentation's visual flow.

**Content Navigation Panel**: A vertical tabbed interface provides access to:
- **Transcript Tab**: Timestamped transcript with click-to-seek functionality
- **Chapters Tab**: AI-generated chapter markers for navigation  
- **Scene Changes Tab**: Log of detected scene transitions
- **Slide Content Tab**: OCR-extracted text from slides with temporal alignment

**Slide downloads**: Download an individual full-size JPG with the download icon
beside a slide in **Slide changes** or beneath a chapter/section in the sidebar.
The same download icon appears on detected-slide timeline cards, OCR slide
headers, and the enlarged slide view; downloading never seeks or selects a slide.
The default chapter-only timeline remains unchanged.
Chapter and section rows have a separate download icon for **all their slides**
(ZIP, including a `screenshots.json` timestamp manifest). These downloads follow
the nested slide list and never include slides from the next chapter/section.

**All slide text in Word**: In **Slide changes**, choose **Export all slide text
as Word**. One `.docx` contains chapter → section → slide headings and numbered
OCR text lists in chronological slide order. Existing OCR is reused; slides
without saved text are read automatically from their full-size screenshots using
Tesseract (install Tesseract or configure `TESSERACT_CMD`). This can take a few
minutes. Slides with no readable text remain in the document with an explicit
notice; failures are reported instead of silently omitting slides. With no
generated chapters, the document contains a single ordered Slides group.

**Integrated Search**: Search functionality that queries both spoken transcript and OCR-extracted visual text

### The Interactive Layer

**Content Customization**: Users can directly manipulate visual components overlaid on the video player:
- **Element Control**: Move, resize, or completely hide detected elements (presenter video, text boxes, images)
- **Accessibility Features**: Reduce visual clutter and cognitive load for neurodivergent users
- **Focus Enhancement**: Hide presenter to focus on slides, or enlarge specific diagrams

**Semantic Link Visualization**: 
- **Bidirectional Highlighting**: When transcript segments play, related slide text is highlighted
- **Interactive Exploration**: Clicking slide text boxes highlights corresponding transcript sentences
- **Cross-Modal Links**: Shows connections between spoken and visual information

### User Experience Features

- **Visual Clutter Reduction**: Customizable interface elements for cognitive load management
- **Multimodal Navigation**: Switch between audio, visual, and textual content modes


## Installation and Setup

### Prerequisites

- **Python>=3.10+** with pip package manager
- **CUDA-capable GPU** (recommended for optimal performance)
- **FFmpeg** for video processing
- **Tesseract OCR** for text extraction
- **Google API Key** for Gemini AI integration

### System Dependencies

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install tesseract-ocr tesseract-ocr-eng ffmpeg
```

**macOS:**
```bash
brew install tesseract ffmpeg
```

**Windows:**
- Install Tesseract from: https://github.com/UB-Mannheim/tesseract/wiki
- Install FFmpeg from: https://ffmpeg.org/download.html

### Python Environment Setup

1. **Clone the repository:**
   ```bash
   git clone <repository_url>
   cd slideDec
   ```

2. **Create virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

   Local Whisper and speaker identification decode audio with system FFmpeg
   and pass 16 kHz mono samples directly to their models, bypassing PyAV's file
   decoder. FFmpeg must be installed and available on `PATH`. Windows launches
   also check the installed user/system PATH and the Python environment's
   `Library/bin` directory, so an older VS Code process PATH does not hide a
   newly installed FFmpeg. Set `FFMPEG_BINARY` and `FFPROBE_BINARY` to executable
   paths if using a custom installation. These tools are shared by speaker
   detection, online transcription and chapter clip exports.

   Dependencies also constrain `av>=11,<19`: PyAV 19 removed the
   `metadata_errors` argument used by faster-whisper 1.2.1. If an existing
   environment reports `open() got an unexpected keyword argument
   'metadata_errors'`, reinstall the requirements and restart the application
   before retrying transcription. Updating the repository alone does not update
   installed packages.

### Windows local launch

Chapter exports default to the HTML webpage package. Word, PDF and other
formats are optional selections in the export dialog.
The export dialog immediately uses the current chapter topic or uploaded
filename while a whole-video title suggestion loads. Suggestions from an
older video or language cannot overwrite the current fields, and names you
type yourself are preserved.

Chapter sections follow topic changes rather than a fixed count. Balanced
groups related examples, steps and continuations of the same subject.
Main chapters follow distinct subjects or learning objectives. Brief groups
broad subjects; Balanced distinguishes topics; Detailed distinguishes finer
independent learning objectives. There are no minute targets, duration-derived
chapter quotas, short-chapter duration merges, or forced section limits.
Unequal chapter lengths are valid; percentages of lecture duration are only
informational, never a reason to force a split or merge. Long transcript request
parts carry the current chapter's context so technical boundaries do not imply
new topics. They also carry the current subsection and its takeaways: continued
explanations extend that subsection rather than appending another timestamp.
One demonstration or workflow (including its steps, examples and clarifying
Q&A) stays together unless the instructional subject genuinely changes.
Adjacent duplicate chapter/subsection headings are coalesced, retaining their
takeaways and the first timestamp.
Topic grouping is requested in the initial generation, with no fixed cap on
genuinely distinct topics. **Chapter options > Generation passes** offers
**One pass** (the faster default) or **Two passes** (generation followed by
an AI review of topic boundaries). **Enhance chapters** reviews an already
saved outline without regenerating the transcript or dropping its takeaways.
These actions are explicit; existing outlines are not silently rewritten.

**Editing:** Chapters and sections have a pencil on each row, rather than a
separate editing mode. Edit titles, starts, chapter summaries or section text,
then **Save** or **Cancel**. Both **Start** and **End** are editable, with millisecond
precision. Shortening an end creates a separate grey **Unassigned** part up to the
next boundary (or video end). New separated parts get an AI-generated topic title
and concise summary using only transcript cues starting inside that range, in the
content/transcript language. Existing part descriptions are not regenerated on
unrelated saves. Missing transcript text is labeled explicitly; if AI description
fails, the edit is still saved with an editable grey placeholder and a visible
warning. Shortening a chapter carries later child sections
into that grey part without deleting their text. The grey part stays included in
playback and exports unless explicitly skipped/left out of an export selection.
You can extend an end into an adjacent Unassigned part, but not across another
assigned chapter or section. Rename and clear the review flag to assign a grey
part. Small **+** controls between rows insert a chapter
or section. A new chapter partitions the previous chapter's sections at the new
boundary. Starts must be strictly ordered and inside the video duration (or the
saved transcript duration when no local video exists). **Delete** removes the outline label (and a chapter's child labels),
not source footage. Revision checks prevent stale overwrites.
**Edit slide titles** and **Edit transcript** remain under their panel's
**Actions** menu. Transcript corrections create an edited version while retaining
the original.
Saving a chapter or section preserves expanded chapter lists and summaries.
The **Chapters ready** success status hides after five seconds; active progress
and error messages remain visible.

**Review and skip:** A row's pencil also exposes **Flag for review** and a
separate **Skip during playback** checkbox. Flags are grey in the outline and
transcript, but remain included. Skipping is opt-in and affects playback only;
original files and exports are unchanged. New chapter generation is prompted to
isolate clear transcript-supported interruptions as grey review sections.
**Flag disruptions** in Chapter options reviews an existing outline, splitting
sections at transcript evidence without moving existing chapter starts.
Suggestions may be wrong: review them before choosing Skip. This is not an
audio/visual glitch detector; it only uses the transcript.
Changing a chapter's start also updates its first section. The previous chapter
and its last section end at this new boundary, so their durations adjust
automatically; changes that cross another section boundary are rejected.
Chapter summaries and section lists start collapsed. Expand a summary to read
it; use the pencil to edit it.
The Chapters tab has one **Chapter options** panel, with scrolling, a sticky
**Close options** button and Escape support. Generation settings, translation,
saved outlines and outline tools live there, not in a second Actions menu.
**Use detected slide changes for boundary detection** supplies visual evidence
to chapter boundaries, section boundaries, or both, including final cut verification.
Slide changes are not mandatory cuts and never delay a spoken topic introduction
just to match a later slide. **Also group topics on the same slide** is a separate
optional chapter-grouping rule. Slide options require detected slides, not OCR.
Exact timestamp chapters cannot move; slide evidence can still inform their sections.
Speaker modes are disabled unless every line of the open transcript is labeled.
Existing saved chapters are unchanged until regenerated.

During chapter generation, the same button shows a **stop icon**, the task and elapsed
time. Stop cancels the server coroutine and prevents it from saving a partial or
late outline. An already submitted provider request may finish remotely; stopping
does not undo provider usage. No made-up percentage or pause/resume promise is shown.
**Open saved chapters** lists existing outlines with their saved transcripts.
Generation options do not delete them; regenerating replaces the current outline
for that language. Previously overwritten, unarchived generation runs are not recoverable.
Inline row buttons provide the same actions without dragging. **Make a new chapter**
splits at a part's timestamp, carrying the following parts into the new chapter.
To move a section between chapters, open **Chapter options -> Reorganize chapters**.
Sections expand in this mode. Drag the `:::` handle into the neighboring chapter's
labelled drop zone, then **Save changes** (or **Cancel** to discard).
Only the first section can move to the previous chapter, or the last section to
the next chapter, preserving video order. The row buttons **Move to previous
chapter** / **Move to next chapter** offer the same actions without dragging.
Dropping directly onto an adjacent row merges items instead of moving them.
For a nonchronological edit, enable **Reorder clips** in this editor. Drag a
section's handle onto the upper/lower half of another section to place it
before/after that section, or use **Move earlier / Move later**. Either the
dragged section or its target (including their parent chapter) must have
**Review** or **Skip** set. Save changes persists the arranged section order
and chapter placement in the original project's outline, without changing source
timestamps or original video playback/navigation.

Selected clip downloads (ZIP or **One MP4**) and **Open selected clips as a new
project** follow this saved order. Chapter video/project buttons use the clips
currently arranged under that chapter. **Skip sections are included** in these
exports, as are Review sections; the flag is not an export-deletion command.
New projects copy and retime the selected transcript, words/speakers, outline,
slides/screenshots and OCR; they do not rerun AI. Embedded caption tracks are
carried with the clips. The new copy's timestamps describe its output sequence.
Whole-original-video exports still refer to the original video: select the
arranged clips and choose One MP4 to export the edit.

Use **Reset clip order**, then Save changes, to restore source order without
losing text or flags. Reset before merging, adding/deleting outline rows or
changing source boundaries. Stale arrangements are rejected rather than exporting
the wrong intervals. Ordinary title, summary and Review/Skip edits remain available.
A chapter with just one section is displayed/exported as the chapter alone,
without a redundant section heading; its text remains included.

**Languages:** Generate chapters always uses the open transcript, not an older
transcript or the interface language. **Chapter options -> Translate chapters...** and
**Translate slide titles** translate saved text directly, retaining timestamps,
structure, slide images and OCR. Choose **Open transcript's language** or an
saved transcript translation, or another English/German/Arabic/Polish target.
The destination defaults to the open transcript; translating chapters does not
silently change the transcript. Slide titles derived from chapter
topics can be translated too. Chapter and slide-title display language choices
are remembered separately for each video in this browser; translations themselves
are saved on the server. Editing and new exports use the selected content languages.
Generating new chapters returns chapter display to the open transcript's language.
Older mismatched outlines need an explicit translation or regeneration.

**Export defaults:** Content chapters remain the top-level parts, with saved
topic sections and slide-change sections included by default. Saved sections
are reused even when slide changes are included, avoiding unnecessary AI calls.
**Include videos for** offers each chapter, each part/section, or both, for
MP4, HTML and SCORM exports. Part videos are separate playable files, not just
links into a chapter video. A singleton section reuses the chapter video.
Files use hierarchy-first names such as `01-Chapter_title.mp4`,
`01_01-First_section.mp4`, `01_02-Second_section.mp4`, then `02-Next_chapter.mp4`,
so sorting by name keeps each chapter before its sections.
Existing exports are unchanged. **Saved exports** is collapsible and starts closed.

**Project Back/Next:** The arrow buttons at the top-left reopen the previous or
next project you worked with, like browser history. Hover to see the project
name. Opening a different project drops any Next entries. History is kept
in this browser across reloads (up to 50 projects). Switching is blocked
until open edits are saved or cancelled. Projects that no longer exist are
removed from history, and you stay on the current project.

**Dropdown menus:** Extra options, Actions menus, Navigate by and `?` help
popups close automatically when you click outside them or press Escape.

**Saved-file management:** **Extra options > Manage saved files** lists saved
uploads/projects and exports with confirmed deletion. When deleting an upload,
the checkbox offers either keeping completed exports or deleting them too.
Its transcript versions, chapters, slides and captions are removed; independent
chapter projects are kept. Running jobs cannot be deleted. Individual saved
exports also have a **Delete** action beside their download.

**Screenshots:** The camera icon (**Take screenshot** on hover) beside the video controls saves the current
frame as a PNG without pausing playback. It works at any loaded time, independently
of detected slides. Browser-rendered selectable subtitles and controls are not
part of the video pixels; burned captions are captured. Slide downloads use short,
descriptive names such as `Slide_002_Short_descriptive_example.jpg`, without video IDs.

**Stopping captioned videos:** While a burned-caption or selectable-track video
is being created, click the **stop icon on the running Create video button**.
This stops encoding and removes the unfinished output. The control also appears
when returning to an active job after reloading. Completed copies are kept.

**Whole-video summary:** In the Chapters tab, **Generate summary** creates one
short paragraph covering the entire open transcript, in its language. Long
transcripts are summarized in bounded parts and then synthesized, not truncated.
The paragraph is saved separately from the chapter outline and restored when
the same transcript is reopened, including after a server restart. Edited text
and translations have separate summaries; changing speaker names does not
invalidate a paragraph. The outline action is now called **Generate chapters**.
Both actions use the model selected under Chapter options.
The summary area is hidden until a summary exists. Saved summaries start as a
single collapsed **Video summary** disclosure; an explicitly generated summary
opens for reading. Generation progress and errors remain visible separately,
without a permanent empty summary card above the chapters.

**Compact actions:** Primary generation/confirmation actions use the accent
color; secondary actions and options share a neutral, compact button style.
Dropdown actions use consistent full-width, left-aligned text rows rather than
nested button cards. Toolbar dropdowns overlay the content rather than expanding
the toolbar, and long menus scroll. Dropdowns retain keyboard focus indicators, disabled states
and labeled destructive actions. Icon-only quick actions retain accessible names.

**Speaker timeline:** Once the open transcript has detected speaker labels,
use **Label speakers** directly in the Transcript toolbar to run speaker
identification without retranscribing. Saved speaker names are editable directly
below that toolbar and follow the displayed transcript. Model setup instructions
remain in Transcript options. During identification the overall progress bar is
indeterminate: percentages in the step log describe individual pyannote stages,
not the entire task. Elapsed time and the existing Stop processing action remain.
CPU identification caps speaker-embedding batches at four to reduce memory and
improve CPU throughput; GPU runs retain the model's configured batch size.
The batch size is shown in the step log. This still analyzes the complete audio
with the same model and detection settings. Long recordings can remain slow on
CPU-only machines; this change applies to newly started workers, not active jobs.
**Speakers: Fast** is the default beside **Label speakers**, using fewer overlapping
analysis windows (50% overlap instead of the standard model's 90%). This keeps
full audio coverage and the original transcript text/timestamps, but short
speaker changes may be less precise. The same choice applies when regenerating
a transcript with **Identify speakers** enabled. **Speakers: Standard** preserves
the model's original overlap. Fast mode is recorded in transcript metadata and
shown in transcript source information. It reduces analysis work, not necessarily
total runtime by the same factor. If an older server is still running, the UI
requires a restart instead of silently ignoring Fast mode; active jobs are not
changed.

Once the open transcript has detected speaker labels,
colored lanes appear beneath the video. Each lane shows a speaker's speaking
intervals on the same full-video time scale, including silence and overlaps.
Saved speaker names are shown. Click any lane to seek, or focus it and use
Left/Right (five seconds), Home or End. A playback cursor follows the video.
The timeline updates when switching transcript versions or renaming speakers
and is hidden for transcripts without speaker labels.

**Chapter projects:** Sections start collapsed even when a chapter has only one section; its text is
not automatically printed beneath the chapter title. Chapter summaries also
start collapsed, with the same compact arrow size as the section toggle.
In Edit chapters, sections also start collapsed. The main arrow opens the
sections; compact summary and merge icons have descriptive hover labels and
accessible names instead of full-width text buttons.

Each chapter and section row shows an external-link icon
(**Open as a project** on hover) beside its video
download icon, without a row Actions menu. Editing controls appear directly in
the rows only during inline editing. This creates an
independent project. Every section also has **Open as project** and a video
download icon; its clip ends at the next section, the next chapter, or the end
of the video. These use the same selected video source and captions as chapters.
The **Saved uploads** dropdown lists only original uploads and independent projects.
The separate **Video version** dropdown before **Examples** on the main screen lists **Original video**
and completed captioned copies for the selected upload, including after a reload.
Opening a copy also selects it there. Selecting a version restores its video and
caption tracks while retaining the original upload's transcript, chapters and slides.

**Chapter cut verification:** Every new AI topic-chapter generation, with either one or
two outline passes and either flat or sectioned output, checks its proposed
chapter starts against nearby transcript utterances before saving. The review
selects actual transcript IDs and exact supporting quotes rather than inventing
timestamps. It looks for the start of a topic, including introductions such as
"now demo two", not just the later detailed explanation. Correct starts are kept;
there is no fixed offset. Verification adds AI requests in bounded excerpt batches.
Invalid evidence, overlapping cuts or failed verification fail generation explicitly
and leave the existing outline untouched. First-section starts follow corrected
chapter starts; sections crossed by a correction move to the adjacent chapter.
Slide-aware grouping still applies afterward, merging same-slide topics and
aligning new chapters to the active slide's start. This is not a guarantee of
frame-perfect semantic cuts: transcript timing and AI interpretation still need
human review. Existing outlines and previously exported videos are not rewritten.

**Speaker-based chapters:** In **Chapter options**, choose
**Chapter boundaries**: **Topics** (the default), **Every speaker change (no AI)**,
or **Topics and speaker changes**. Both speaker modes require labels on every line
of the open transcript and use speaker IDs for changes and saved names for titles.
Consecutive lines from the same speaker stay together; a returning speaker gets
a new chapter. Speaker-only mode preserves the exact saved turn timestamps
without AI calls. The combined mode first generates and verifies topic chapters,
then splits them at every speaker turn, with titles such as `Alice - Demonstration`.
It does not invent per-speaker summaries from a whole-topic summary.
Speaker turns take priority over slide grouping; the slide option applies only
in Topics mode. Missing labels or ambiguous simultaneous speaker changes produce
an explicit error instead of guessing. Existing saved outlines are not modified
until generation succeeds.

**Custom and selected clips:** Use **Select all** / **Unselect all** above the outline to toggle every
chapter and section checkbox, including rows inside collapsed chapters.
Selection-only merge/delete rules still apply; select section rows only for
those outline actions.

**Project search:** A directly visible keyword/phrase field sits before **Search**.
The dropdown inside the split Search control chooses **Open project only**
(default) or **All projects**. Enter or Search opens results. Open-project scope
requires a loaded project and filters on the server before pagination; it never
silently falls back to all projects. It searches video names, saved transcripts (including
YouTube captions, translations and earlier saved versions), chapter titles,
section titles/text, and chapter/video summaries. Choose a content type to
narrow results; matches are case-insensitive literal text, not regular expressions.
Results include highlighted excerpts, the video name, content type, source
version and a timestamp where available. Click to load the saved video and seek
to the match without autoplay.
Section matches also open Chapters, expand the matching chapter, and focus/scroll
to the section row. If a historical match has no row in the current outline,
a notice explains that only its saved timestamp was opened.
Untimed video-name and overview-summary matches open without seeking.
Opening a result does not activate another transcript
version or overwrite saved content; the source label identifies historical or
translated matches. Save or cancel open edits before opening results.
Search uses on-disk text only (not unsaved edits, speech recognition, video
frames or a new AI request), requires the media to remain in the saved library,
deduplicates identical content across saved versions and pages results 30 at a
time. File changes are reflected on the next search, with explicit warnings
for unreadable saved files. No separate search index or external service is used.

**Sound in embedded browsers:** Audio mode is **Automatic** by default. Native
audio is used when supported. If a source has audio but its codec is unsupported,
or Chromium reports no decoded audio after three seconds of unmuted playback,
the app prepares a separate mono 16 kHz PCM WAV for the selected
original/captioned copy (about 115 MB per hour), reusing a completed copy while
the source remains unchanged. Preparation pauses the video and resumes it only
if it was playing beforehand. Video play/pause, seeking, speed and volume control synchronized WAV
playback, with drift correction and video pausing during audio buffering.
Automatic resume waits while the page is hidden and retries when it becomes
visible. Browser power-saving interruptions show a short notice rather than a
raw browser exception; successful playback clears that notice.
The extra **Audio volume** slider appears only in this mode: native video sound
stays muted to avoid double audio. Native playback uses the native volume control.
Successfully needed compatible audio is remembered per source. Switching sources
removes the old track before checking the new source. Normal success does not
leave instructions on screen. The **?** audio disclosure contains help and
**Automatic / Native / Compatible** overrides for browsers whose capability
reports cannot reliably identify an audio problem.
Original files, captions and MP4 downloads remain unchanged. Missing audio
streams, conversion and playback errors are shown explicitly.
The circular backward/forward controls each have an integrated dropdown for
**1 second** or **1 minute**. The two selectors share one interval: changing
either updates both directions. Click the arrow to seek once; hold it to repeat
after a short delay, stopping on release, cancellation, lost focus or hidden
page. Seeks are clamped to the video start/end without changing playback state.
Each repeat waits for the destination seek to finish and its decoded frame to
paint before the next jump; slow decoding does not skip over unseen frames.
Rapid clicks are queued in order. Paused video remains paused and displays each
destination frame; playing video continues playing. Loading another source or a
media error clears queued jumps.
Compatible audio follows these seeks automatically. **Speed** precedes
**Navigate by**.
Playback Speed applies to the current and default media rate, stays selected
when changing video versions, and follows native player speed changes.
Changing speed while a source loads keeps the latest choice, not a stale rate
captured before loading. Compatible audio follows the same rate.

**Change video speed:** The speedometer action beside playback Speed opens export
options for the currently selected original or captioned video version. Choose
0.25x–4x, the entire video or a custom Start/End range (with playhead pin buttons),
and **Download MP4** or **New project + MP4**. Audio uses pitch-preserving tempo
filters; video is re-encoded at the source resolution. Embedded text subtitles
are clipped and retimed; bitmap subtitles are explicitly rejected rather than
silently dropped. Burned-in captions remain in the image.

A new project clips and scales the selected saved transcript, word timings,
speaker labels, chapter/section timestamps, and slide times/durations. Existing
slide screenshots and OCR are copied; titles and summaries are reused. For a
range starting at `start` and speed `rate`, each timestamp becomes
`(original_time - start) / rate`, clipped to the selected range. No transcription,
chapter generation, or OCR is rerun. The parent remains unchanged. Only the
selected transcript/translation and chapter outline are copied, not their entire
version history. Encoding uses the common progress/stop button; saved jobs can be
reopened after reload. The completed result can be downloaded, and project output
can be opened independently after saving/cancelling any open edits.

**Running controls:** Processing buttons use the same elapsed-time display,
spinner for non-cancellable work, and a stop icon on the same button when server
cancellation is available. Progress percentages appear only when measured.
Merge/trim, split, transcript/speaker processing, chapter generation, video-copy
creation and clip encoding follow this pattern; stopping disables repeated
requests until the server confirms completion. File-save cancellation does not
claim to undo an already completed encoding or remote provider usage.

**Saved-content Undo/Redo:** Toolbar buttons and Ctrl+Z / Ctrl+Shift+Z (outside
text fields) restore saved transcript, outline and slide-title changes recorded
from this version onward. History persists locally in `.lectureseek-history`.
It is conflict-checked and blocked during content processing. New edits clear
Redo. File deletion, downloads, external changes and provider usage are not
undoable. History stores compressed changed file contents and grows with edits.

Loading a saved outline preserves its chapter/section boundaries and indices,
including neighbouring chapters with identical titles. Normalization is applied
during generation, not silently when loading; section downloads and project
creation therefore refer to the same outline shown on screen.

Use **Download custom clip** in the Chapters tab,
under **Downloads**, and enter start/end timestamps (MM:SS or HH:MM:SS, optional milliseconds)
to download an MP4 of any range, including without generated chapters. The
currently selected video source and captions are preserved.
While the custom clip panel is open, drag the **IN** and **OUT** markers on
the timeline below the video. The highlighted range and timestamp fields stay
synchronized. Pin buttons beside the fields use the current playback position.
**Video start** sets the start to 00:00:00; **Video end** sets the end to the
full video duration, including milliseconds. These shortcuts update the markers
without moving playback.
The temporary **Start here** button beside the timeline captures the current
playback position and becomes **End here**. Click again at the desired ending;
playback is not paused or moved. An end at or before the start shows an error
instead of silently changing your cut. After capturing the end, **Start here**
can begin a new range. Both marker labels use identical centered positioning,
so nearby IN/OUT labels keep their correct left-to-right order.
Dragging previews the selected frame without starting playback. Markers support
arrow keys (one second), Shift+arrow (ten seconds), Alt+arrow (0.1 seconds), and
Home/End. The boundaries cannot cross or exceed the video duration. Closing the
panel or switching videos removes the markers; invalid typed ranges cannot be
downloaded. No permanent toolbar controls are added.

**Privacy masking:** Expand **Privacy / hide regions** in the custom clip panel.
The preview highlights the selected detection with a cyan dashed box before you add it;
orange outlines mark active masks. Selecting a detection alone does not hide it in the download.
Draw a rectangle on the video preview, add an adjustable box, or choose an
existing detected region in the current slide. Each region supports **Blur**
(default), **Pixelate**, or **Solid black cover**, editable percentage coordinates,
and timing for just that slide/scene or the entire exported clip.
**Find people in clip** scans the selected video source locally every 0.5 seconds,
with progress and a Stop button. First use downloads YOLO11n model weights into
`~/.cache/lectureseek`; video frames are not uploaded. Automatic candidates
start with detected-interval timing; they can be adjusted, extended to the
entire export, or removed before downloading. Up to 64 regions are supported.
Masks apply only to the exported MP4; the original video is never changed.
Use **Video start** and **Video end** to mask a whole-video download.

Automatic detection can miss moving people, small webcam tiles, and faces.
Review the entire exported video before sharing. Blur and pixelation are not
guaranteed anonymization; use a solid cover for stronger visual concealment.
Audio, soft subtitles, and already burned-in captions outside the masked regions
are retained and can still identify people. The preview is illustrative; the
export uses FFmpeg masking, rounds region boundaries outward to video pixel
boundaries, and requires regions at least two source pixels wide and high.

**Webpage section screenshots:** Sections with detected slides retain those slide
images. Sections without slides capture a frame at their own exported start
timestamp instead of repeating the parent chapter screenshot. The enlarged
image uses that same frame, and the image is packaged in both webpage and SCORM
exports. Existing downloads remain unchanged; export again to use the correction.
Chapter and section thumbnails use a 160 by 90 pixel preview, with the full-size
image available by clicking it.

Chapter and section rows have selection checkboxes. Checking any row reveals a
compact selection bar above the chapters with the count, **Download selected clips**
icon, **One MP4 (selected parts only)** checkbox and **Clear selection** icon.
The bar disappears when the last selection is cleared or a different outline is loaded.
Editing a chapter or section preserves existing selections, even when new gap
sections shift row positions. A retimed section stays selected; newly created
sections start unselected. Selections also follow sections moved by a chapter split.
It also offers **Merge selected sections** and **Delete selected sections**.
Merge combines each consecutive group of selected sections, including across
chapter boundaries, placing the merged section in the earliest chapter.
Following chapters start at their first remaining section; fully consumed chapters
are removed. A chapter without section rows still separates groups.
Selections can include multiple groups across chapters; isolated selections and
unselected sections stay unchanged. Each group retains its first start and gets
a generated title and summary based on every selected section's title and summary,
without changing video order. If description generation fails, nothing is saved;
retry the merge. Review flags are kept; a merged
group is skipped only if every section in that group was marked Skip. The button
is available when at least one adjacent pair is selected. To merge whole
chapters, select two or more adjacent chapter rows instead and use the same
Merge button: every section of the merged chapters is kept in order inside the
earliest chapter, which gets a generated title and summary covering all of them.
Chapter and section rows cannot be mixed in one merge. Delete
removes selected section labels after confirmation, not original footage.
Both actions preserve expanded chapters and summaries and reject stale edits.
Routine editing disclaimers and instructions are collapsed behind contextual
question marks beside the corresponding option label or control, not on separate
lines. Expanded help appears in a dismissible panel. Help uses a quiet 13 px
glyph in a 24 px target, descriptive
accessible labels, keyboard focus styling, and click/tap disclosure rather than
hover-only tooltips. Escape closes help and returns focus to its trigger.
Chapter and section boundary verification checks transcript cue IDs and exact
supporting quotes. An invalid verification response gets one targeted correction
attempt for that batch, without regenerating the outline or accepting guessed
timestamps. If evidence still fails validation, existing saved chapters are kept.
Completed or failed background transcription clears its running controls.
Label speakers checks server status before blocking on a previous job, so a
stale browser processing flag cannot prevent labeling a completed transcript.
Topic-based and timestamp-based chapter generation can use the currently open
transcript while speaker labeling or transcription runs in the background.
Speaker-based chapters still wait for processing to finish. Saves are serialized
and recorded separately so Undo does not combine chapters with background transcript changes.
When a newly generated or uploaded transcript becomes ready, a compact prompt
offers **Chapters**, **Slides**, **Both**, or **Not now**. Nothing starts by
default; **Both** starts the independent jobs together using their current options.
**Always do this when I upload new videos** remembers the choice for future new transcripts in this browser.
Change or reset it under **Settings > When a new transcript is ready**.
Reopening saved projects, changing transcript versions or labeling speakers does
not trigger the prompt. Remembered automatic choices do not replace existing
chapters or detected slides.
Row editing keeps the optional Review reason field collapsed by default;
existing saved review notes remain available and are preserved when saving.
Start and End fields include pin buttons (**Start here** / **End here**) to
copy the current video position with millisecond precision, without pausing or
seeking. Manual entry and normal boundary validation remain available; Save
applies the edit.
Chapter/section displays and timestamp editors use `MM:SS` below one hour and
`H:MM:SS` afterward, matching playback time. Existing minute-only timestamps such
as `62:03` display as `1:02:03`; fractional seconds and saved cut positions are
preserved.
Successful clip-download status clears after four seconds. Starting another
download cancels the old dismissal timer; active progress and errors stay visible.

Chapter options include **Separate long transcript gaps**, off by default, with
an adjustable 5–300 second minimum (10 seconds initially). This fast timestamp-only
pass adds separate grey **No transcript text** review sections and restores the
surrounding topic at the end of each gap. It never marks them Skip or moves chapter
boundaries, including exact user cuts and speaker boundaries. Even Chapters only
outlines gain sections where needed to represent gaps. A transcript gap is not
confirmed acoustic silence: demos, music, or missed speech may be present.
Overlapping cues are combined before checking gaps. Without a known video duration,
the final transcript cue limits the checked range. No audio scan or extra AI call
is performed.
The download icon saves
all checked items in one ZIP with chronologically numbered MP4s.
Enable **One MP4 (selected parts only)** to concatenate all selected ranges,
including nonconsecutive sections, in original chronological order. Overlaps
are included only once and unselected gaps are omitted. Without merging,
each selected chapter/section is a separate clip, even if their ranges overlap.

**Merge uploaded clips:** Open **Extra options > Merge video clips**, choose
2-30 videos, reorder them with the arrow controls and give the compilation a
video title. Choose **Video only (download MP4)** (the default) to avoid the extra
library copy and project metadata work, or **Video + saved project** to also save
an independent project in the video library. Both produce a downloadable MP4
using the same encoding; originals are not altered. The dialog
supports stopping encoding and restoring job status across reloads. Limits are
2 GB per clip and 5 GB total. Mixed clips are re-encoded to letterboxed 720p,
30 fps H.264/AAC with silence added for clips without audio. Burned-in captions
remain visible; embedded subtitle tracks are not copied into uploaded merges.
**Extra options > Trim uploaded clips** opens the same dialog for a single local
file, without first creating a project. Set each clip's Start and optional End
(MM:SS or HH:MM:SS, milliseconds supported). Blank End uses that clip's actual
duration. With one file, **Trim clip** exports only that range; with multiple
files, **Merge clips** trims each range first and joins them in the displayed
order. Limits and output choices are the same. Invalid or out-of-bounds ranges
are rejected before encoding; source files are never modified.
These downloads do not alter the outline or create saved-upload projects.
The chapter toolbar keeps **Generate chapters**, **Downloads** and **Chapter actions**
visible. **Downloads** groups chapter exports and custom clips; selection-specific
downloads and merging appear only in the selection bar. **Chapter actions** includes video-summary generation, editing,
enhancement, options and translation. Menus start collapsed; individual chapter
and section rows retain their direct project and video-download icons.
Downloaded exports append a date before the extension, for example
`Lecture_2026-10-20.zip` or `01_02-Section_2026-10-20.mp4`.
New saved export jobs retain their creation date when downloaded again;
direct downloads use the current local date. Existing saved files are not renamed.
Opening a chapter or section creates an
independent MP4 of that excerpt, copies overlapping transcript cues with timing
reset to zero, and copies available slide images, custom titles and saved OCR.
The slide already visible at the chapter start becomes the first slide at zero.
When a translation is selected, both it and the original transcript are copied
when available. Speakers are retained; parent chapters are not copied so you can
generate a fresh outline for the excerpt. The parent project is never changed.
Creation reports encoding progress and reconnects after a page reload. The new
project opens automatically only if you are still viewing its parent; otherwise
use **Open chapter project**. It remains available in **Saved uploads** after a
server restart, and all normal transcription, chapter, slide and export tools work
on it. Creating a long chapter project still requires video encoding.
**Cancel encoding** stops the active project encoder and cleans incomplete output,
without modifying the parent. Cancellation is no longer available once encoding
has finished and the project is being saved. The selected captioned video copy is
used as the source, not silently replaced by the original. Embedded subtitle tracks
survive chapter extraction, including nested chapter projects; matching rebased
WebVTT tracks keep them visible in the browser. The visible caption track is kept
as the default. The download-arrow button on each chapter saves just that chapter
as an MP4 without creating a new project or exporting the whole lecture.

Automatic chapter and translation fallbacks include Gemini 3.5 Flash-Lite
instead of the retired Gemini 2.5 models. Provider quota limits and temporary
overload can still prevent generation; failures retain existing chapters and
include each provider's reason and retry timing when supplied.
Chapter responses are parsed before a transcript part is accepted. Malformed
JSON gets one corrective retry, then smaller transcript parts when possible
and a provider fallback if parsing still fails. Valid responses need no extra
AI calls; ambiguous extra JSON is never silently discarded.

For downloaded HTML exports, use **Extract All** on the ZIP and open
`index.html` from the extracted folder, not from inside the ZIP. Keep the
HTML, images and videos together. The page shows guidance if its media cannot
load. Alternatively, use **Saved exports > Open** in the app.
Opening speech before the first named section is included in that section's
transcript rather than turned into an extra section with a verbatim greeting
as its title.

Exports run in a background job with timestamped steps (clip encoding, images,
AI summaries, document building, packaging and saving). Export and
transcription progress show only the current step, replacing the previous one.
Active operation progress includes elapsed time, including uploads, caption
previews and video copies, slide downloads, summary generation and exports.
Persisted export and transcription jobs use server timing when reconnecting.
Saved exports include
an **Open HTML in browser** link and a download link in the Chapters tab.
HTML pages and their media remain under `static/exports/jobs` across app
restarts. Keep this directory to retain previous exports. In Colab, files
under `/content` still disappear when the runtime resets; download the package
or persist the exports directory in Drive. Older downloads created before
saved-export support are not automatically imported.

Intermediate export files use the operating system's temporary directory,
outside the saved exports folder, to avoid cloud-sync locks on Windows.
Temporary-file cleanup retries briefly; persistent cleanup failures are logged
and shown as warnings in export progress without invalidating saved output.
Progress-file updates retry when Windows briefly locks them (for example during
OneDrive sync), so a locked progress file no longer aborts an export.

Create an isolated Conda environment, install the Python requirements, and
install FFmpeg (including ffprobe) and Tesseract:

```powershell
conda create -n lecture-toolkit python=3.11 pip -y
conda run -n lecture-toolkit python -m pip install -r requirements.txt
conda install -n lecture-toolkit -c conda-forge tesseract -y
winget install --id Gyan.FFmpeg --exact
```

Copy `.env.example` to `.env` if it does not already exist, and enter
`GROQ_API_KEY` locally. Colab Secrets are not automatically available on your PC.
The `.env` file is ignored by Git but may still be synced by cloud storage if
your checkout is in OneDrive.

```powershell
conda run --no-capture-output -n lecture-toolkit python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. This binds only to your PC, uses the
`lecture-toolkit` environment, and loads `.env` through the application.
Open a new terminal after installing FFmpeg so its updated `PATH` is available.
Groq does not require local CUDA; audio is sent to Groq when selected.

### Configuration

Local Whisper GPU inference needs CUDA 12 cuBLAS and cuDNN 9 in addition to
a GPU recognized by PyTorch. On Linux, install `nvidia-cublas-cu12` and
`nvidia-cudnn-cu12==9.*`, and include their `lib` directories in
`LD_LIBRARY_PATH` **before starting the server**. Whisper checks that these
libraries can load; otherwise it reports the reason in its progress log and
uses CPU int8. The runtime status distinguishes GPU availability from the
device usable by Whisper. Groq transcription does not require local CUDA.

Regenerating a transcript replaces an active transcription worker. An
intentionally stopped worker cannot overwrite the replacement job's progress
or report its termination as a new failure. An unexpected exit code `-15`
means SIGTERM: check for a server/runtime restart or an external termination
request, rather than treating it as proof of a CUDA or memory error.

1. **Set up Google Gemini API:**
   ```bash
   # Create .env file
   echo "GOOGLE_API_KEY=your_api_key_here" > .env
   ```
   Get your API key from: https://makersuite.google.com/app/apikey

2. **Configure Tesseract (if not in PATH):**
   The app finds Tesseract on PATH, inside the active Conda/Python environment
   (even when the environment is not activated), or in the standard Windows install
   folders. To use another copy, set `TESSERACT_CMD` to the full path of the
   `tesseract` executable before starting the server.

### Model Files

The system requires pre-trained models (included in repository):
- `slidecraft_best.pt` - Alternative slide detection model

## Usage

### Quick Start

1. **Launch the application:**
   ```bash
   # For modular version (recommended)
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

2. **Access the web interface:**
   Open your browser to: http://localhost:8000

3. **Process content:**
   - **YouTube Videos**: Paste a URL, choose 480p (faster) or 720p, and click "Load Video"
   - **Local Files**: Upload video files directly
   - **Real-time Processing**: Monitor progress via live updates
   - **Slide Detection**: Choose content cuts (default), adaptive slides, or existing transcript chapters; higher video and screenshot quality uses more time and storage

### Advanced Usage Examples

#### 1. YouTube Video Analysis
```bash
# Navigate to http://localhost:8000
# Enter YouTube URL: https://www.youtube.com/watch?v=example_id
# System automatically:
# - Downloads video 
# - Extracts YouTube transcript
# - Detects scenes and extracts keyframes
# - Perform slide visual analysis
# - Performs OCR on slides
# - Generates semantic embeddings
# - Creates chapter summaries
```

#### 2. Local Video Processing
```bash
# Upload local video file via web interface
# System processes identically to YouTube content
# Supports: MP4, AVI, MOV, MKV formats
```

#### 3. API Integration
```python
import requests

# Process YouTube video
response = requests.post("http://localhost:8000/process_youtube", 
                        json={"url": "https://youtube.com/watch?v=example"})

# Get transcript-OCR relationships
relationships = requests.get("http://localhost:8000/get_transcript_ocr_relationships/video_id")

# Find OCR text for specific transcript segment
ocr_text = requests.get("http://localhost:8000/find_ocr_for_transcript/video_id/5")
```

### Core Functionality

#### Scene Detection and Analysis
- **Adaptive Detection**: Finds settled slide states and ignores cursor movement,
  compression noise and constantly moving regions such as webcam insets. Full
  slide replacements are preserved at every detail level. Choose **Slide changes only**,
  **Slide changes and major variations**, or **Slide changes and every addition**.
  Additions are bullet points, text or diagrams appearing step by step on the
  same slide (also called slide builds). Defaults use a
  two-second floor and no slide-count cap, with one-second sampling. Advanced
  build-duration filters no longer erase short full replacements or move earlier
  starts forward. An explicit count cap can still hide slides. Brief flashes,
  transitions that never settle, and changes outside the analyzed static area
  may still be missed; no setting guarantees detection of every visual event.
- **Hard cuts (Advanced)**: Detects abrupt visual edits, not semantic chapter
  boundaries. It can help with edited recordings; it does not classify intervals
  into slides, demos or speaker-only video.
- **Slide history**: View immutable saved images/timestamps while new detection
  runs, without replacing active analysis. Existing sets are archived before
  replacement and new completed runs are archived too. Sets overwritten before
  history was introduced cannot be recovered. Timeline film buttons download
  each slide's video until the next slide, or video end, using the selected media
  copy and captions.
- **Contextual help**: Explanatory text is collapsed behind keyboard-accessible
  question-mark disclosures. Errors, progress and source indicators stay visible.
- **Keyframe Extraction**: Saves representative frames for each scene
- **Thumbnail Generation**: Creates navigation thumbnails

#### OCR Text Extraction
- **Dual OCR Support**: Tesseract and Surya OCR engines
- **Preference System**: User-configurable OCR method selection
- **Live Progress**: Tesseract reads up to four text elements at once; one status line shows the slides being read now, elements done, elapsed time, and a warning if no update arrives for 20 seconds. **Stop OCR** cancels the remaining work, and starting again only reads elements that are still missing.
- **Bounding Box Detection**: Precise text location mapping
- **Quality Filtering**: Confidence-based text filtering

#### Transcript Processing
- **Multi-source Support**: YouTube transcripts and Whisper STT
- **Temporal Alignment**: Precise timestamp synchronization

#### Semantic Embeddings
- **Sentence-BERT**: Advanced semantic similarity computation
- **FAISS Indexing**: Efficient similarity search
- **Temporal Mapping**: Scene-transcript synchronization

## Technical Specifications

### Performance Characteristics
- **Processing Speed**: ~1-2x real-time for typical lecture videos
- **Memory Usage**: 2-4GB RAM for standard processing
- **GPU Acceleration**: CUDA support for Whisper and YOLO models
- **Concurrent Processing**: Multi-threaded scene and OCR analysis

## Slide Visual Analysis


### Supported Formats
- **Input Videos**: MP4, AVI, MOV, MKV, WebM

### API Endpoints

#### Core Processing
- `POST /process_youtube` - Process YouTube video
- `POST /upload_video` - Upload and process local video
- `GET /scenes/{video_id}` - Retrieve scene information
- `GET /video/{video_id}` - Stream processed video

#### OCR and Text Analysis
- `GET /ocr_text/{video_id}` - Get extracted OCR text
- `POST /process_surya_ocr/{video_id}` - Run Surya OCR
- `POST /set_ocr_preference` - Configure OCR engine

#### Transcript Processing
- `GET /get_transcript/{video_id}/{source}` - Retrieve transcripts
- `POST /generate_whisper_transcript/{video_id}` - Generate Whisper transcript
- `GET /whisper_transcript_status/{video_id}` - Check processing status

#### Embeddings and Relationships
- `POST /compute_embeddings/{video_id}` - Generate semantic embeddings
- `GET /get_transcript_ocr_relationships/{video_id}` - Get alignment data
- `GET /find_ocr_for_transcript/{video_id}/{index}` - Find related OCR text
- `GET /find_scene_for_transcript/{video_id}/{index}` - Find corresponding scenes


## Dependencies and Licenses

### Core Dependencies
```
fastapi              # Web framework (MIT License)
uvicorn             # ASGI server (BSD License)
yt-dlp              # YouTube downloader (Unlicense)
youtube-transcript-api # Transcript extraction (MIT License)
ultralytics         # YOLOv8 models (AGPL-3.0)
sentence-transformers # Semantic embeddings (Apache 2.0)
faster-whisper      # Speech recognition (MIT License)
google-generativeai # Gemini AI (Apache 2.0)
surya-ocr           # Advanced OCR (GPL-3.0)
opencv-python       # Computer vision (BSD License)
scenedetect         # Scene detection (BSD License)
faiss-cpu           # Similarity search (MIT License)
```

## Performance Optimization

### Docker Deployment
```dockerfile
FROM python:3.9-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr tesseract-ocr-eng ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy and install requirements
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application
COPY . /app
WORKDIR /app

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Production Configuration
```bash
# High-performance deployment
uvicorn main:app --workers 4 --host 0.0.0.0 --port 8000 --access-log

# With SSL (recommended for production)
uvicorn main:app --host 0.0.0.0 --port 443 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

## Troubleshooting

### Common Issues

**CUDA/GPU Issues:**
```bash
# Verify CUDA installation
nvidia-smi
python -c "import torch; print(torch.cuda.is_available())"
```

**Tesseract Not Found:**
```bash
# Ubuntu/Debian
sudo apt-get install tesseract-ocr tesseract-ocr-eng

# Or point the app at a specific binary
export TESSERACT_CMD=/usr/bin/tesseract
```

**YouTube Download Errors:**
```bash
# Update yt-dlp
pip install --upgrade yt-dlp

# Check video availability and region restrictions
```

**Memory Issues:**
- Reduce batch sizes in configuration
- Process shorter video segments
- Use CPU-only inference if GPU memory limited


### Acknowledgments
- **YOLOv8**: Ultralytics team for object detection framework
- **Whisper**: OpenAI for speech recognition technology
- **Sentence-BERT**: UKP Lab for semantic embeddings
- **Surya OCR**: VikParuchuri for advanced OCR capabilities
- **FastAPI**: Sebastian Ramirez for the excellent web framework
- **[Dubby fork](https://github.com/lailashoukryaiu/Dubby)** and its
  **[upstream project](https://github.com/MohammedAly22/Dubby)**: Mohammed Aly
  and contributors for the context-aware translation, isolated model-worker,
  and timestamp-aware dubbing architecture that inspired parts of this toolkit's
  translation roadmap.
  The implementation in this repository is original and does not vendor Dubby
  source code.
