// chapters.js - Chapter generation functionality

import { elements } from './elements.js';
import { state } from './main.js';
import { showError, showErrorWithActions, showNotification } from './ui.js';
import { saveBlobToUserLocation, formatTime, formatTimestamp } from './utils.js';
import { conciseTitle, slideTitle, sceneTopicTitle } from './chapter-navigation.js';
import { scrollWithinContainer } from './scroll-utils.js';
import { createSlideDownloadButton } from './slide-downloads.js';
import { startElapsedTimer, startProcessButton } from './progress-time.js';
import { selectedMediaExportId } from './caption-export.js';
import { createChapterProjectButton, createChapterDownloadButton, createClipSelection } from './chapter-projects.js';
import { deleteSavedExport } from './library.js';
import { contentLanguage, setContentLanguage } from './content-languages.js';
import { splitTimestamps } from './custom-clip-range.js';
import { arrangeSectionRows } from './clip-arrangement.js';

let chapterMarkerGeneration = 0;
let summaryStatusTimer = null;
let summaryJobVideoId = null;
let chapterProcessButton = null;

export function updateChapterGenerationOptions() {
    const basis = document.getElementById('chapterBasis');
    if (!basis) return;
    const labeled = Boolean(state.currentTranscript?.length) && state.currentTranscript.every(cue => {
        const speaker = cue.speaker || cue.speaker_name;
        return typeof speaker === 'string' && Boolean(speaker.trim());
    });
    for (const option of basis.options) {
        if (['speakers', 'topics_speakers'].includes(option.value)) {
            option.disabled = !labeled;
            option.title = labeled ? '' : 'Label every transcript line first in the Transcript tab.';
        }
    }
    if (!labeled && ['speakers', 'topics_speakers'].includes(basis.value)) {
        basis.value = 'topics';
        basis.dispatchEvent(new Event('change'));
    }
    document.getElementById('chapterSpeakerHelp').textContent = labeled
        ? 'Speaker boundaries are available for the fully labeled open transcript. Saved speaker names are used.'
        : "Speaker modes are disabled: label every line's speaker in the Transcript tab first.";
    const hasSlides = Boolean(state.videoScenes?.length);
    const scope = document.getElementById('chapterSlideBoundaries');
    const sectioned = elements.chapterStructure?.value !== 'flat';
    for (const option of scope.options) {
        option.disabled = (['chapters', 'both'].includes(option.value) && basis.value === 'timestamps')
            || (['sections', 'both'].includes(option.value) && !sectioned);
    }
    if (scope.selectedOptions[0]?.disabled) scope.value = basis.value === 'timestamps' && sectioned ? 'sections' : 'none';
    scope.disabled = !hasSlides || basis.value === 'speakers';
    document.getElementById('chapterConsiderSlides').disabled = !hasSlides || basis.value !== 'topics';
    document.getElementById('chapterSlideHelp').textContent = !hasSlides
        ? 'Detect slides first to enable slide-aware boundaries. No OCR is required.'
        : basis.value === 'speakers' ? 'Every speaker change uses exact saved speaker turns, not slide boundaries.'
        : 'Detected slide changes inform generation and final cut verification. They are not mandatory cuts; spoken introductions are preserved. Exact timestamp chapters are never moved.';
}

export function initChapterGenerationOptions() {
    initChapterPlaybackTracking();
    const panel = document.getElementById('summaryOptionsPanel');
    const close = () => {
        panel.hidden = true;
        document.getElementById('summaryOptionsBtn').focus();
    };
    document.getElementById('closeChapterOptionsBtn').addEventListener('click', close);
    panel.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    });
    for (const event of ['transcriptUpdated', 'slidesUpdated', 'videoSelectionChanged']) {
        document.addEventListener(event, updateChapterGenerationOptions);
    }
    for (const id of ['chapterBasis', 'chapterStructure', 'summaryOptionsBtn']) {
        document.getElementById(id).addEventListener(id === 'summaryOptionsBtn' ? 'click' : 'change', updateChapterGenerationOptions);
    }
    updateChapterGenerationOptions();
}

function outlineControl(label, detail, insertion = false) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = insertion ? 'outline-insert' : 'btn btn-secondary btn-icon outline-pencil';
    button.title = label;
    button.setAttribute('aria-label', label);
    button.innerHTML = insertion ? '+' : '<i class="fas fa-pen" aria-hidden="true"></i>';
    button.addEventListener('click', event => {
        event.stopPropagation();
        document.dispatchEvent(new CustomEvent('outlineEditRequested', {detail: {...detail, anchor: button}}));
    });
    return button;
}

export function clearChapterMarkers() {
    chapterMarkerGeneration += 1;
    const progressContainer = elements.videoProgress;
    if (!progressContainer) return;
    progressContainer.querySelectorAll('.chapter-marker, .chapter-segment').forEach((marker) => marker.remove());
}

export function updateExportAvailability() {
    const hasVideo = Boolean(state.currentVideoId);
    const hasTranscript = Array.isArray(state.currentTranscript) && state.currentTranscript.length > 0;
    const hasTopics = Array.isArray(state.videoChapters) && state.videoChapters.length > 0;
    const hasSlides = Array.isArray(state.videoScenes) && state.videoScenes.length > 0;
    const hasParts = hasTopics || hasSlides;
    const intervalToggle = elements.intervalExportToggle;

    if (intervalToggle) {
        intervalToggle.disabled = false;
    }

    const useIntervals = Boolean(intervalToggle?.checked);
    const canBuildParts = hasVideo;
    const chapterGrouping = elements.chapterGrouping;
    if (chapterGrouping) {
        [...chapterGrouping.options].forEach((option) => {
            const requiresTopics = option.value === 'topic';
            const requiresSlides = option.value === 'slides';
            option.disabled = !useIntervals && (
                (requiresTopics && !hasTopics) || (requiresSlides && !hasSlides)
            );
        });
        if (chapterGrouping.selectedOptions[0]?.disabled) {
            const availableOption = [...chapterGrouping.options].find((option) => !option.disabled);
            if (availableOption) chapterGrouping.value = availableOption.value;
        }
        chapterGrouping.disabled = !canBuildParts;
    }

    [
        elements.timestampMode,
        elements.subpartMode,
    ].forEach((control) => {
        if (control) control.disabled = !canBuildParts;
    });

    [elements.exportImages, elements.exportClips].forEach((control) => {
        if (control) control.disabled = !hasVideo || !canBuildParts;
    });
    if (elements.exportTranscripts) {
        elements.exportTranscripts.disabled = !hasTranscript || !canBuildParts;
    }
    [
        elements.exportWord,
        elements.exportPdf,
        elements.exportWebpage,
        elements.exportScorm,
    ].forEach((control) => {
        if (control) control.disabled = !canBuildParts || !hasTranscript;
    });
    if (elements.exportOutline) {
        elements.exportOutline.disabled = !canBuildParts || !hasTranscript;
    }
    if (elements.exportScreenshotTranscript) {
        elements.exportScreenshotTranscript.disabled = !hasVideo || !hasSlides || !hasTranscript;
    }
    const sectionClips = document.getElementById('exportSectionClips');
    if (sectionClips) sectionClips.disabled = !canBuildParts || !hasTranscript;
    const clipScope = document.getElementById('exportVideoScope');
    if (clipScope) clipScope.disabled = !hasVideo;

    if (elements.exportAvailabilityNote) {
        if (!hasVideo) {
            elements.exportAvailabilityNote.textContent = 'Load a video before exporting.';
        } else if (!hasParts && !useIntervals) {
            elements.exportAvailabilityNote.textContent =
                'Without chapters or slides, the full video is one part; transcript-based sections are still available.';
        } else if (!hasTranscript) {
            elements.exportAvailabilityNote.textContent =
                'Transcript-dependent exports are disabled until a transcript is available.';
        } else {
            elements.exportAvailabilityNote.textContent = '';
        }
    }
}

function renderExportSteps(status) {
    const container = document.getElementById('exportProgress');
    if (!container) return;
    container.replaceChildren();
    const heading = document.createElement('strong');
    heading.textContent = status.status === 'complete' ? 'Export ready' : status.status === 'error' ? 'Export stopped' : 'Exporting…';
    container.appendChild(heading);
    const list = document.createElement('ol');
    const steps = status.steps || [];
    // Show only the latest step; each new step replaces the previous one.
    const index = steps.length - 1;
    if (index >= 0) {
        const step = steps[index];
        const item = document.createElement('li');
        const duration = Math.max(0, (status.elapsed || step.elapsed) - step.elapsed);
        const running = status.status === 'running';
        item.textContent = `${Math.floor(step.elapsed / 60)}:${String(Math.floor(step.elapsed % 60)).padStart(2, '0')} — ${step.message} (${running ? 'running for' : 'took'} ${Math.round(duration)}s)`;
        list.appendChild(item);
    }
    container.appendChild(list);
    if (status.error) {
        const message = document.createElement('p');
        message.textContent = status.error;
        container.appendChild(message);
    }
    startElapsedTimer(container, status.elapsed || 0, status.status === 'running' || status.status === 'queued');
}

async function downloadSavedExport(item, link) {
    link.classList.add('is-loading');
    link.setAttribute('aria-busy', 'true');
    const stopElapsed = startElapsedTimer(link);
    try {
        const response = await fetch(item.download_url);
        if (!response.ok) throw new Error(`server answered ${response.status}`);
        const blob = await response.blob();
        const disposition = response.headers.get('content-disposition') || '';
        const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        const plain = disposition.match(/filename="?([^";]+)"?/i);
        const filename = encoded ? decodeURIComponent(encoded[1]) : plain ? plain[1] : (item.filename || 'export.zip');
        await saveBlobToUserLocation(blob, filename);
    } catch (error) {
        if (error?.name === 'AbortError') return;
        showError(`Could not download saved export: ${error.message}`);
    } finally {
        stopElapsed();
        link.classList.remove('is-loading');
        link.removeAttribute('aria-busy');
    }
}

export async function refreshSavedExports() {
    const container = document.getElementById('savedExports');
    if (!container) return;
    if (!state.currentVideoId) {
        container.replaceChildren();
        document.getElementById('exportProgress')?.replaceChildren();
        return;
    }
    const videoId = state.currentVideoId;
    try {
        const response = await fetch(`/saved_exports/${encodeURIComponent(videoId)}`);
        const data = await readJsonResponse(response, 'Saved exports');
        if (state.currentVideoId !== videoId) return;
        container.replaceChildren();
        const completed = data.exports.filter((item) => item.status === 'complete');
        if (!completed.length) {
            const message = document.createElement('p');
            message.className = 'saved-exports-empty';
            message.textContent = 'Completed exports will appear here and remain available after server restarts.';
            container.appendChild(message);
        }
        const list = document.createElement('ul');
        list.className = 'saved-exports-list';
        completed.forEach((item) => {
            const row = document.createElement('li');
            row.className = 'saved-export-row';
            const info = document.createElement('div');
            info.className = 'saved-export-info';
            const title = document.createElement('span');
            title.className = 'saved-export-title';
            title.textContent = item.title;
            title.title = item.title;
            const date = document.createElement('time');
            date.className = 'saved-export-date';
            const created = new Date(item.created_at * 1000);
            date.dateTime = created.toISOString();
            date.textContent = created.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
            info.appendChild(title);
            info.appendChild(date);
            row.appendChild(info);
            const actions = document.createElement('div');
            actions.className = 'saved-export-actions';
            if (item.open_url) {
                const open = document.createElement('a');
                open.innerHTML = '<i class="fas fa-external-link-alt" aria-hidden="true"></i> Open';
                open.title = 'Open HTML in browser';
                open.setAttribute('aria-label', `Open ${item.title} in browser`);
                open.href = item.open_url;
                open.target = '_blank';
                open.rel = 'noopener';
                open.className = 'btn btn-secondary btn-action';
                actions.appendChild(open);
            }
            const download = document.createElement('a');
            download.innerHTML = '<i class="fas fa-download" aria-hidden="true"></i>';
            download.title = 'Download';
            download.setAttribute('aria-label', `Download ${item.title}`);
            download.href = item.download_url;
            download.className = 'btn btn-secondary btn-action btn-icon';
            download.addEventListener('click', (event) => {
                event.preventDefault();
                void downloadSavedExport(item, download);
            });
            actions.appendChild(download);
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'btn btn-secondary btn-action';
            remove.textContent = 'Delete';
            remove.setAttribute('aria-label', `Delete export ${item.title}`);
            remove.addEventListener('click', async () => {
                try { if (await deleteSavedExport(item)) await refreshSavedExports(); }
                catch (error) { showError(error.message); }
            });
            actions.appendChild(remove);
            row.appendChild(actions);
            list.appendChild(row);
        });
        if (completed.length) container.appendChild(list);
        if (completed.some((item) => item.open_url)) {
            const help = document.createElement('p');
            help.className = 'saved-exports-empty';
            help.textContent = 'For downloaded HTML: extract the entire ZIP, then open index.html from the extracted folder. Keep its images and videos alongside it, or use Open above.';
            container.appendChild(help);
        }
    } catch (error) {
        if (state.currentVideoId !== videoId) return;
        container.replaceChildren();
        const message = document.createElement('p');
        message.setAttribute('role', 'alert');
        message.textContent = error instanceof TypeError
            ? 'Could not reach the app server to load saved exports. A failed export does not remove saved history. Wait for the server to finish restarting, then click Refresh saved exports.'
            : `Could not load saved exports: ${error.message}. Click Refresh saved exports to retry.`;
        container.appendChild(message);
    }
}

export async function exportChapters() {
    if (state.exportInProgress) {
        showNotification('An export is already running.', 'info');
        return;
    }
    state.exportInProgress = true;
    const videoId = state.currentVideoId;
    const button = elements.exportChaptersBtn;
    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Exporting...';
    let stopDownloadElapsed;
    showError('');
    try {
        const useIntervals = elements.intervalExportToggle?.checked;
        const intervalMinutes = Number(elements.intervalDuration?.value);
        if (useIntervals && (!Number.isFinite(intervalMinutes) || intervalMinutes <= 0)) {
            throw new Error('Enter an interval duration greater than zero minutes.');
        }
        const options = {
            ...(useIntervals ? { interval_minutes: intervalMinutes } : {}),
            include_images: !elements.exportImages?.disabled && (elements.exportImages?.checked ?? false),
            include_transcripts: !elements.exportTranscripts?.disabled && (elements.exportTranscripts?.checked ?? false),
            include_clips: !elements.exportClips?.disabled && (elements.exportClips?.checked ?? false),
            include_word: !elements.exportWord?.disabled && (elements.exportWord?.checked ?? false),
            include_pdf: !elements.exportPdf?.disabled && (elements.exportPdf?.checked ?? false),
            include_webpage: !elements.exportWebpage?.disabled && (elements.exportWebpage?.checked ?? true),
            include_screenshot_transcript: !elements.exportScreenshotTranscript?.disabled
                && (elements.exportScreenshotTranscript?.checked ?? false),
            include_outline: !elements.exportOutline?.disabled && (elements.exportOutline?.checked ?? false),
            include_scorm: !elements.exportScorm?.disabled && (elements.exportScorm?.checked ?? false),
            chapter_grouping: elements.chapterGrouping?.value || 'topic',
            timestamp_mode: elements.timestampMode?.value || 'subpart',
            subpart_mode: elements.subpartMode?.value || 'both',
            clip_overlap: document.getElementById('exportClipOverlap')?.checked ?? true,
            include_section_clips: !document.getElementById('exportSectionClips')?.disabled
                && (document.getElementById('exportSectionClips')?.checked ?? false),
            video_scope: document.getElementById('exportVideoScope')?.value || 'chapters',
            include_slide_text: document.getElementById('exportSlideText')?.checked ?? false,
            document_title: elements.exportTitle?.value.trim() || '',
            export_filename: elements.exportFilename?.value.trim() || '',
            transcript_language: state.currentTranslationLanguage || '',
            chapter_language: contentLanguage(videoId, 'chapters', state.currentTranslationLanguage),
            slide_title_language: contentLanguage(videoId, 'slides', state.currentTranslationLanguage),
            transcript_source: state.currentTranscriptSource || '',
            media_export_id: selectedMediaExportId(videoId),
        };
        if (options.include_screenshot_transcript) {
            options.screenshot_transcript = state.currentTranscript.map(item => ({...item}));
        }
        if (elements.exportOptionsDialog?.open) elements.exportOptionsDialog.close();
        renderExportSteps({status: 'running', steps: [{elapsed: 0, message: 'Starting export job'}]});
        const response = await fetch(`/export_jobs/${encodeURIComponent(videoId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(options)
        });
        if (!response.ok) {
            const contentType = response.headers.get('content-type') || '';
            let message = `Chapter export failed (${response.status})`;
            if (contentType.includes('application/json')) {
                const error = await response.json();
                message = error.detail || error.error || message;
            } else {
                const text = (await response.text()).trim();
                if (text) {
                    message = text;
                }
            }
            throw new Error(message);
        }
        const job = await readJsonResponse(response, 'Start export');
        let status;
        do {
            const statusResponse = await fetch(job.status_url);
            status = await readJsonResponse(statusResponse, 'Export progress');
            if (state.currentVideoId === videoId) renderExportSteps(status);
            if (status.status === 'error') throw new Error(status.error || 'Export failed');
            if (status.status !== 'complete') {
                await new Promise((resolve) => setTimeout(resolve, 1000));
            }
        } while (status.status !== 'complete');
        await refreshSavedExports();
        button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Downloading...';
        stopDownloadElapsed = startElapsedTimer(button);
        const downloadResponse = await fetch(status.download_url);
        if (!downloadResponse.ok) throw new Error(`Could not download export (${downloadResponse.status})`);
        const blob = await downloadResponse.blob();
        const directWord = options.include_word && !options.include_pdf
            && !options.include_webpage && !options.include_outline && !options.include_scorm
            && !options.include_screenshot_transcript
            && !options.include_images && !options.include_transcripts && !options.include_clips;
        const directPdf = options.include_pdf && !options.include_word
            && !options.include_webpage && !options.include_outline && !options.include_scorm
            && !options.include_screenshot_transcript
            && !options.include_images && !options.include_transcripts && !options.include_clips;
        const directScreenshotHtml = options.include_screenshot_transcript
            && !options.include_images && !options.include_transcripts && !options.include_clips
            && !options.include_word && !options.include_pdf && !options.include_webpage
            && !options.include_outline && !options.include_scorm && !options.include_section_clips;
        const fallbackFilename = directScreenshotHtml
            ? `${videoId}_screenshots_transcript.html`
            : directWord
            ? `${videoId}_chapter_document.docx`
            : directPdf
                ? `${videoId}_chapter_document.pdf`
                : `${videoId}_${useIntervals ? 'intervals' : 'chapters'}.zip`;
        const disposition = downloadResponse.headers.get('content-disposition') || '';
        const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
        const filename = filenameMatch ? filenameMatch[1] : fallbackFilename;
        await saveBlobToUserLocation(blob, filename);
    } catch (error) {
        showError(`Error exporting chapters: ${error.message}`);
    } finally {
        stopDownloadElapsed?.();
        state.exportInProgress = false;
        button.disabled = false;
        button.innerHTML = '<i class="fas fa-download" aria-hidden="true"></i> Export chapters';
    }
}

/**
 * Generates chapter markers from the transcript
 */
export async function generateChapters() {
    if (state.outlineRowEditing || state.resourceLinkEditing) { showError('Save or cancel the open row or link edit first.'); return; }
    if (state.summaryGenerationInProgress) {
        if (!summaryJobVideoId) {
            showNotification('Another outline operation is running.', 'info');
            return;
        }
        const button = elements.generateSummaryBtn;
        chapterProcessButton?.update({stopping: true});
        try {
            const response = await fetch(`/cancel_summary/${encodeURIComponent(summaryJobVideoId)}`, {method: 'POST'});
            const result = await readJsonResponse(response, 'Stop chapter generation');
            if (!result.success) throw new Error(result.error);
        } catch (error) {
            showError(`Could not stop chapters: ${error.message}`);
            chapterProcessButton?.update({stopping: false});
        }
        return;
    }
    if (!state.currentTranscript?.length) {
        showError('Generate or load a transcript before generating chapters.');
        return;
    }
    state.summaryGenerationInProgress = true;
    document.dispatchEvent(new CustomEvent('summaryGenerationChanged'));
    const generateSummaryBtn = elements.generateSummaryBtn;
    let stopElapsed;
    let stopButtonElapsed;
    
    try {
        chapterProcessButton = startProcessButton(generateSummaryBtn, 'Generating chapters', {cancellable: true});
        stopButtonElapsed = chapterProcessButton;
        const passes = document.getElementById('chapterPasses')?.value || 'one';
        const basis = document.getElementById('chapterBasis')?.value || 'topics';
        if (['speakers', 'topics_speakers'].includes(basis)) {
            const labeled = state.currentTranscript.every(cue => {
                const speaker = cue.speaker || cue.speaker_name;
                return typeof speaker === 'string' && Boolean(speaker.trim());
            });
            if (!labeled) throw new Error("Label every transcript line's speaker in the Transcript tab first.");
        }
        const cutPoints = basis === 'timestamps'
            ? splitTimestamps(document.getElementById('chapterCutPoints').value) : undefined;
        const status = setSummaryStatus('generating', '<i class="fas fa-spinner fa-spin"></i> '
            + (basis === 'timestamps' ? 'Generating chapter content within your exact timestamp boundaries...'
                : basis === 'speakers' ? 'Creating chapters from saved speaker turns (no AI)...'
                : passes === 'two' ? 'Generating and reviewing the outline, then verifying cut boundaries against the transcript...'
                : 'Generating chapters and verifying cut boundaries against the transcript...'));
        stopElapsed = startElapsedTimer(status);
        showError('');
        const requestVideoId = state.currentVideoId;
        summaryJobVideoId = requestVideoId;
        const requestLanguage = state.currentTranslationLanguage || '';
        const response = await fetch('/generate_summary', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                transcript: state.currentTranscript,
                video_id: state.currentVideoId,
                model: elements.summaryModel?.value || null,
                transcript_language: requestLanguage,
                structure: elements.chapterStructure?.value || 'sections',
                detail: elements.chapterDetail?.value || 'balanced',
                instructions: (elements.chapterInstructions?.value || '').trim(),
                passes,
                basis,
                flag_transcript_gaps: Boolean(document.getElementById('chapterFlagTranscriptGaps')?.checked),
                transcript_gap_seconds: Number(document.getElementById('chapterTranscriptGapSeconds')?.value || 10),
                ...(cutPoints ? {cut_points: cutPoints} : {}),
                consider_slides: basis === 'topics'
                    && !document.getElementById('chapterConsiderSlides')?.disabled
                    && (document.getElementById('chapterConsiderSlides')?.checked ?? false),
                slide_boundary_scope: document.getElementById('chapterSlideBoundaries')?.disabled
                    ? 'none' : document.getElementById('chapterSlideBoundaries')?.value || 'none',
            })
        });

        const data = await readJsonResponse(response, 'Chapter generation');
        if (data.cancelled) {
            if (state.summaryRetryTimer) { clearInterval(state.summaryRetryTimer); state.summaryRetryTimer = null; }
            setSummaryStatus('complete', 'Chapter generation stopped. Your existing chapters were kept.');
            return;
        }

        if (!data.success) {
            throw new Error(data.error);
        }
        if (requestVideoId !== state.currentVideoId || requestLanguage !== (state.currentTranslationLanguage || '')) {
            // The user switched video or transcript meanwhile; the chapters stay saved with their transcript.
            setSummaryStatus('complete', '<i class="fas fa-info-circle"></i> Chapters were saved for the transcript that was open when you started.');
            return;
        }

        // Store the chapters in state
        setContentLanguage(requestVideoId, 'chapters', null);
        state.videoChapters = data.chapters;
        if (state.summaryRetryTimer) {
            clearInterval(state.summaryRetryTimer);
            state.summaryRetryTimer = null;
        }
        elements.exportChaptersBtn.disabled = false;
        
        updateChapters(data.chapters);
        if (data.notice) {
            showNotification(data.notice, 'info');
        }
        setSummaryStatus('complete', '<i class="fas fa-check-circle"></i> Chapters ready.');
    } catch (error) {
        showError(`Error generating chapters: ${error.message}`);
        const isModelError = /gemini|openai|model|429|404|quota|overload|unavailable|high demand/i.test(error.message);
        const detail = document.createElement('span');
        detail.textContent = error.message;
        const openAiHint = /OPENAI_API_KEY/.test(error.message)
            ? ' Add OPENAI_API_KEY in Colab to enable the OpenAI fallback.'
            : '';
        const statusMessage = isModelError
            ? `<i class="fas fa-exclamation-circle"></i> The AI provider could not generate chapters: ${detail.innerHTML}. Your existing chapters were kept.${openAiHint}`
            : error instanceof TypeError && /fetch|network/i.test(error.message)
            ? '<i class="fas fa-exclamation-circle"></i> Lost connection to the app server. It may have stopped or restarted. Your existing chapters were kept. Restore the server connection before retrying.'
            : `<i class="fas fa-exclamation-circle"></i> Chapter generation failed: ${detail.innerHTML}. Your existing chapters were kept. You can retry without reloading the video.`;
        const status = setSummaryStatus(
            'error',
            statusMessage
        );
        const retryButton = document.createElement('button');
        retryButton.type = 'button';
        retryButton.className = 'btn btn-secondary btn-action summary-retry-btn';
        retryButton.innerHTML = '<i class="fas fa-redo"></i> Retry';
        retryButton.addEventListener('click', generateChapters);
        status.appendChild(retryButton);
        if (isModelError) {
            const autoRetryButton = document.createElement('button');
            autoRetryButton.type = 'button';
            autoRetryButton.className = 'btn btn-secondary btn-action summary-retry-btn';
            autoRetryButton.innerHTML = '<i class="fas fa-sync"></i> Retry automatically';
            autoRetryButton.addEventListener('click', () => {
                if (state.summaryRetryTimer) return;
                autoRetryButton.disabled = true;
                state.summaryRetryTimer = setInterval(() => {
                    if (!state.summaryGenerationInProgress) {
                        void generateChapters();
                    }
                }, 15000);
                showNotification('Retrying chapter generation now; automatic retries will continue every 15 seconds.', 'info');
                void generateChapters();
            });
            status.appendChild(autoRetryButton);
            const stopRetryButton = document.createElement('button');
            stopRetryButton.type = 'button';
            stopRetryButton.className = 'btn btn-danger summary-retry-btn';
            stopRetryButton.innerHTML = '<i class="fas fa-stop"></i> Stop automatic retry';
            stopRetryButton.addEventListener('click', () => {
                if (state.summaryRetryTimer) {
                    clearInterval(state.summaryRetryTimer);
                    state.summaryRetryTimer = null;
                }
                showNotification('Automatic chapter retry stopped.', 'info');
            });
            status.appendChild(stopRetryButton);
        }
    } finally {
        stopElapsed?.();
        stopButtonElapsed?.();
        chapterProcessButton = null;
        summaryJobVideoId = null;
        state.summaryGenerationInProgress = false;
        document.dispatchEvent(new CustomEvent('summaryGenerationChanged'));
        generateSummaryBtn.disabled = false;
        generateSummaryBtn.innerHTML = '<i class="fas fa-magic"></i> Generate chapters';
        generateSummaryBtn.removeAttribute('aria-busy');
        generateSummaryBtn.removeAttribute('aria-label');
    }
}

function setSummaryStatus(state, message) {
    clearTimeout(summaryStatusTimer);
    summaryStatusTimer = null;
    let status = elements.summaryStatus;
    if (!status) {
        status = document.createElement('div');
        status.id = 'summaryStatus';
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        elements.chaptersContainer.prepend(status);
    }

    status.className = `summary-status summary-status-${state.split(' ')[0]}`;
    status.innerHTML = message;
    status.hidden = false;
    if (state.split(' ')[0] === 'complete') {
        summaryStatusTimer = setTimeout(() => {
            status.hidden = true;
            summaryStatusTimer = null;
        }, 5000);
    }
    return status;
}

async function readJsonResponse(response, operation) {
    const responseText = await response.text();
    let data;
    try {
        data = JSON.parse(responseText);
    } catch (error) {
        const detail = responseText.replace(/\s+/g, ' ').trim().slice(0, 240);
        throw new Error(
            `${operation} failed (HTTP ${response.status}). The server returned invalid JSON${detail ? `: ${detail}` : '.'}`
        );
    }
    if (!response.ok) {
        throw new Error(data.error || data.detail || `${operation} failed (HTTP ${response.status}).`);
    }
    return data;
}

function bindChapterSeek(row, time, label) {
    row.tabIndex = 0;
    row.setAttribute('aria-label', label);
    const controls = 'button, input, textarea, select, summary, a, [contenteditable="true"]';
    const seek = (event) => {
        if (row.closest('.is-inline-editing') || event.target.closest(controls)) return;
        elements.videoPlayer.currentTime = time;
        elements.videoPlayer.play().catch((error) => {
            if (error.name !== 'AbortError') {
                console.warn('Unable to play chapter or section:', error);
                showError(`Could not play the selected chapter or section: ${error.message}`);
            }
        });
    };
    row.addEventListener('click', seek);
    row.addEventListener('keydown', (event) => {
        if (event.target !== row || !['Enter', ' '].includes(event.key)) return;
        event.preventDefault();
        seek(event);
    });
}

/**
 * Updates the chapters display with new chapter data
 * @param {Array} chapters - The chapter data
 */
function selectionKey(chapters, chapterIndex, sectionIndex) {
    const chapter = chapters?.[chapterIndex];
    const item = sectionIndex == null ? chapter : chapter?.sections?.[sectionIndex];
    return item ? `${sectionIndex == null ? 'chapter' : 'section'}:${timestampSeconds(item.timestamp)}` : null;
}

export function captureChapterSelection(container, chapters, edit) {
    const selected = new Set();
    container.querySelectorAll('.clip-selection:checked').forEach(input => {
        const {chapter_index: chapter, section_index: section} = JSON.parse(input.dataset.clipSelection);
        if (edit?.action === 'delete' && chapter === edit.chapter && edit.section == null) return;
        const affected = edit && chapter === edit.chapter &&
            (edit.section == null ? section == null || section === 0 : section === edit.section);
        if (affected && edit.action === 'delete') return;
        const key = affected && edit.action === 'update'
            ? `${section == null ? 'chapter' : 'section'}:${timestampSeconds(edit.timestamp)}`
            : selectionKey(chapters, chapter, section);
        if (key) selected.add(key);
    });
    return selected;
}

export function restoreChapterSelection(container, chapters, selected) {
    container.querySelectorAll('.clip-selection').forEach(input => {
        const {chapter_index: chapter, section_index: section} = JSON.parse(input.dataset.clipSelection);
        input.checked = selected.has(selectionKey(chapters, chapter, section));
    });
}

export function updateChapters(chapters, {editing = false, preserveExpansion = false, retimedChapter = null,
    selectionEdit = null} = {}) {
    const chaptersContainer = elements.chaptersContainer;
    const status = elements.summaryStatus;
    const selected = preserveExpansion
        ? captureChapterSelection(chaptersContainer, state.videoChapters, selectionEdit) : new Set();
    const expanded = new Map();
    if (preserveExpansion) {
        chaptersContainer.querySelectorAll(':scope > .chapter-item').forEach(row => {
            const start = Number(row.dataset.start);
            const key = retimedChapter && start === timestampSeconds(retimedChapter.previous)
                ? timestampSeconds(retimedChapter.current) : start;
            expanded.set(key, {
                sections: row.querySelector(':scope > .chapter-expand')?.getAttribute('aria-expanded') === 'true',
                summary: Boolean(row.querySelector('.chapter-summary-toggle')?.open),
            });
        });
    }
    chaptersContainer.querySelectorAll(':scope > .chapter-item, :scope > .chapter-sections, :scope > .outline-move-zone, :scope > .outline-insert, :scope > p').forEach(element => element.remove());
    if (status) {
        status.hidden = false;
    }
    
    if (!chapters || chapters.length === 0) {
        chaptersContainer.innerHTML = '<p>No chapters available.</p>';
        if (!editing) chaptersContainer.appendChild(outlineControl('Add chapter', {action: 'add_chapter', chapter: 0}, true));
        setSummaryStatus('complete', '<i class="fas fa-info-circle"></i> No chapters are available.');
        // Still clear/refresh any stale timeline and scene captions from a
        // previously loaded video/language instead of leaving them stale.
        state.videoChapters = [];
        activeChapterKey = '';
        updateActiveChapter();
        document.dispatchEvent(new CustomEvent('chaptersUpdated', { detail: [] }));
        return;
    }

    // Update the list of chapters in the sidebar
    if (!editing && timestampSeconds(chapters[0].timestamp) > 0) {
        chaptersContainer.appendChild(outlineControl('Add chapter at the beginning', {action: 'add_chapter', chapter: 0}, true));
    }
    chapters.forEach((chapter, index) => {
        const div = document.createElement('div');
        div.className = 'chapter-item';
        div.dataset.start = timestampSeconds(chapter.timestamp);
        if (chapter.flagged || chapter.skipped) {
            div.classList.add('outline-muted');
            div.title = chapter.skipped ? 'Skipped during playback' : `Review suggested: ${chapter.flag_reason || 'Check this part'}`;
        }
        const timestamp = document.createElement('span');
        timestamp.className = 'chapter-timestamp';
        timestamp.textContent = formatTimestamp(chapter.timestamp);
        const title = document.createElement('span');
        title.className = 'chapter-title';
        title.textContent = conciseTitle(chapter.title);
        title.title = chapter.title;
        const number = document.createElement('span');
        number.className = 'chapter-number';
        number.textContent = `${index + 1}.`;
        div.appendChild(timestamp);
        div.appendChild(number);
        div.appendChild(title);
        div.appendChild(createChapterProjectButton(chapter, index, chapters));
        div.appendChild(createChapterDownloadButton(chapter, index, chapters));
        if (!editing) div.appendChild(createClipSelection(chapter, index, chapters));
        if (!editing) div.appendChild(outlineControl('Edit chapter', {action: 'update', chapter: index}));
        if (chapter.summary && !editing) {
            const disclosure = document.createElement('details');
            disclosure.className = 'chapter-summary-toggle';
            const toggle = document.createElement('summary');
            toggle.textContent = 'Chapter summary';
            const summary = document.createElement('p');
            summary.className = 'chapter-summary';
            summary.textContent = chapter.summary;
            disclosure.append(toggle, summary);
            div.appendChild(disclosure);
        }
        bindChapterSeek(div, timestampSeconds(chapter.timestamp), `Play chapter ${index + 1}: ${chapter.title}`);
        chaptersContainer.appendChild(div);
        if (Array.isArray(chapter.sections) && chapter.sections.length) {
            const sections = renderChapterSections(chapter.sections, index, chapters, editing);
            sections.hidden = true;
            div.classList.add('has-sections');
            div.prepend(expandToggle(div, sections, 'sections'));
            chaptersContainer.appendChild(sections);
        } else if (!editing) {
            div.appendChild(outlineControl('Add section', {action: 'add_section', chapter: index, section: 0,
                timestamp: chapter.timestamp}, true));
        }
        if (!editing) chaptersContainer.appendChild(outlineControl('Add chapter here',
            {action: 'add_chapter', chapter: index + 1, timestamp: formatClock(elements.videoPlayer?.currentTime || 0)}, true));
    });
    // Store the chapters in state
    state.videoChapters = chapters;
    if (!editing && chapters[0]?.clip_arrangement) {
        try { arrangeSectionRows(chaptersContainer, chapters); }
        catch (error) { showError(error.message); }
    }
    attachChapterSlides();
    if (preserveExpansion) {
        chaptersContainer.querySelectorAll(':scope > .chapter-item').forEach(row => {
            const previous = expanded.get(Number(row.dataset.start));
            if (!previous) return;
            const toggle = row.querySelector(':scope > .chapter-expand');
            if (previous.sections && toggle?.getAttribute('aria-expanded') === 'false') toggle.click();
            const summary = row.querySelector('.chapter-summary-toggle');
            if (summary) summary.open = previous.summary;
        });
    }
    restoreChapterSelection(chaptersContainer, chapters, selected);
    document.dispatchEvent(new CustomEvent('chaptersUpdated', { detail: chapters }));
    elements.exportChaptersBtn.disabled = false;
    setSummaryStatus('complete', '<i class="fas fa-check-circle"></i> Chapters ready.');
    
    // Add chapter markers to the timeline
    addChapterMarkersToTimeline(chapters);
    activeChapterKey = '';
    updateActiveChapter();
}

function expandToggle(row, list, noun) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'chapter-expand';
    const setExpanded = (expanded) => {
        list.hidden = !expanded;
        row.classList.toggle('expanded', expanded);
        button.setAttribute('aria-expanded', String(expanded));
        const count = list.querySelectorAll('.chapter-section, .chapter-slide').length;
        button.title = expanded ? `Hide ${noun}` : `Show ${count} ${count === 1 ? noun.replace(/s$/, '') : noun}`;
        button.setAttribute('aria-label', button.title);
    };
    setExpanded(false);
    button.addEventListener('click', (event) => {
        event.stopPropagation();
        setExpanded(list.hidden);
        activeChapterKey = '';
        updateActiveChapter();
    });
    button.addEventListener('keydown', (event) => event.stopPropagation());
    return button;
}

/** Lists the detected slide changes beneath each section (or each chapter without sections). */
export function attachChapterSlides() {
    const container = elements.chaptersContainer;
    if (!container) return;
    container.querySelectorAll('.slide-download').forEach((element) => element.remove());
    container.querySelectorAll('.chapter-slides, .chapter-slides-wrap').forEach((element) => element.remove());
    container.querySelectorAll('.has-slides').forEach((row) => {
        row.classList.remove('has-slides', 'expanded');
        row.querySelector(':scope > .chapter-expand')?.remove();
    });
    if (!document.getElementById('chapterSlidesToggle')?.checked) return;
    const scenes = (state.videoScenes || [])
        .map((scene, index) => ({ scene, index, time: Number(scene.time_seconds) }))
        .filter((slide) => Number.isFinite(slide.time));
    const rows = [];
    container.querySelectorAll(':scope > .chapter-item').forEach((chapter) => {
        const list = chapter.nextElementSibling?.classList.contains('chapter-sections') ? chapter.nextElementSibling : null;
        const sections = list ? [...list.querySelectorAll(':scope > .chapter-section')] : [];
        if (!sections.length) {
            rows.push({ row: chapter, start: Number(chapter.dataset.start) });
            return;
        }
        sections.forEach((section, index) => rows.push({
            row: section,
            start: index === 0 ? Math.min(Number(chapter.dataset.start), Number(section.dataset.start)) : Number(section.dataset.start),
        }));
    });
    if (!scenes.length || !rows.length) return;
    rows.sort((a, b) => a.start - b.start);
    rows.forEach(({ row, start }, rowIndex) => {
        const end = rowIndex + 1 < rows.length ? rows[rowIndex + 1].start : Infinity;
        // Slides shown before the first chapter belong to it; a slide counts from half a second early.
        const inside = scenes.filter(({ time }) => (rowIndex === 0 || time >= start - 0.5) && time < end - 0.5);
        if (!inside.length) return;
        const list = document.createElement('ol');
        list.className = 'chapter-slides';
        inside.forEach(({ scene, index, time }) => list.appendChild(slideRow(scene, index, time)));
        row.appendChild(createSlideDownloadButton(inside.map(({ index }) => index),
            `Download all slides in ${row.tagName === 'LI' ? 'section' : 'chapter'} (${inside.length})`, {archive: true}));
        row.classList.add('has-slides');
        row.prepend(expandToggle(row, list, 'slides'));
        if (row.tagName === 'LI') {
            const wrap = document.createElement('li');
            wrap.className = 'chapter-slides-wrap';
            wrap.appendChild(list);
            row.after(wrap);
        } else {
            row.after(list);
        }
    });
    container.querySelectorAll(':scope > .chapter-item.has-sections').forEach((chapter) => {
        const sections = chapter.nextElementSibling;
        const indices = [...sections.querySelectorAll('.chapter-slide')].map((slide) => Number(slide.dataset.index));
        if (indices.length) chapter.appendChild(createSlideDownloadButton(indices,
            `Download all slides in chapter (${indices.length})`, {archive: true}));
    });
    activeChapterKey = '';
    updateActiveChapter();
}

function slideRow(scene, index, time) {
    const item = document.createElement('li');
    item.className = 'chapter-slide';
    item.tabIndex = 0;
    item.dataset.start = time;
    item.dataset.index = index;
    if (scene.thumbnail) {
        const image = document.createElement('img');
        image.src = scene.thumbnail;
        image.alt = '';
        image.loading = 'lazy';
        item.appendChild(image);
    }
    const timestamp = document.createElement('span');
    timestamp.className = 'chapter-timestamp';
    timestamp.textContent = scene.timestamp ? formatTimestamp(scene.timestamp) : formatClock(time);
    const title = document.createElement('span');
    title.textContent = slideTitle(scene, contentLanguage(state.currentVideoId, 'slides', state.currentTranslationLanguage))
        || sceneTopicTitle(state.videoChapters || [], Number(scene.time_seconds)) || `Slide ${index + 1}`;
    item.append(timestamp, title);
    item.appendChild(createSlideDownloadButton([index], `Download slide ${index + 1}`));
    const seek = (event) => {
        event.stopPropagation();
        elements.videoPlayer.currentTime = time;
    };
    item.addEventListener('click', seek);
    item.addEventListener('keydown', (event) => {
        if (event.target.closest('button, input, textarea, details')) return;
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            seek(event);
        }
    });
    return item;
}

function formatClock(seconds) {
    return formatTime(Math.max(0, seconds));
}

let activeChapterKey = '';
let chapterPlaybackTracking = true;

export function initChapterPlaybackTracking() {
    const toggle = document.getElementById('chapterPlaybackTracking');
    if (!toggle) return;
    try {
        chapterPlaybackTracking = localStorage.getItem('chapterPlaybackTracking') !== 'false';
    } catch (error) {
        showError(`Could not read the chapter playback preference: ${error.message}`);
    }
    toggle.checked = chapterPlaybackTracking;
    toggle.addEventListener('change', () => {
        chapterPlaybackTracking = toggle.checked;
        try {
            localStorage.setItem('chapterPlaybackTracking', String(chapterPlaybackTracking));
        } catch (error) {
            showError(`Could not save the chapter playback preference: ${error.message}`);
        }
        activeChapterKey = '';
        updateActiveChapter();
    });
    activeChapterKey = '';
    updateActiveChapter();
}

/** Highlights the chapter and section being played, like the active transcript line. */
export function updateActiveChapter() {
    if (elements.chaptersContainer?.classList.contains('is-inline-editing')) return;
    const container = elements.chaptersContainer;
    const time = elements.videoPlayer?.currentTime;
    if (!container || !Number.isFinite(time)) return;
    const nowPlaying = document.getElementById('chapterNowPlaying');
    if (!chapterPlaybackTracking) {
        container.querySelectorAll('.chapter-item.active, .chapter-section.active, .chapter-slide.active')
            .forEach(item => {
                item.classList.remove('active');
                item.removeAttribute('aria-current');
            });
        if (nowPlaying) { nowPlaying.hidden = true; nowPlaying.textContent = ''; }
        return;
    }
    const latest = (items) => items.reduce((found, item) => (
        Number(item.dataset.start) <= time + 0.01 &&
            (!found || Number(item.dataset.start) >= Number(found.dataset.start)) ? item : found
    ), null);
    const chapters = [...container.querySelectorAll('.chapter-item')];
    let chapter = latest(chapters);
    let sectionList = chapter?.nextElementSibling?.classList.contains('chapter-sections') ? chapter.nextElementSibling : null;
    let section = sectionList ? latest([...sectionList.querySelectorAll('.chapter-section')]) : null;
    if (state.videoChapters?.[0]?.clip_arrangement) {
        const sourceChapterIndex = chapters.indexOf(chapter);
        section = latest([...container.querySelectorAll('.chapter-section')]
            .filter(row => Number(row.dataset.sourceChapterIndex) === sourceChapterIndex));
        if (section) {
            sectionList = section.closest('.chapter-sections');
            chapter = sectionList.previousElementSibling;
        }
    }
    const slide = latest([...container.querySelectorAll('.chapter-slide')]);
    const slideList = slide?.closest('.chapter-slides');
    const slideVisible = Boolean(slide && slideList && !slideList.hidden && !slideList.closest('[hidden]'));
    const key = `${chapter?.dataset.start ?? ''}|${section?.dataset.start ?? ''}|${sectionList?.hidden ?? ''}|${slide?.dataset.start ?? ''}|${slideVisible}`;
    if (key === activeChapterKey) return;
    activeChapterKey = key;
    container.querySelectorAll('.chapter-item.active, .chapter-section.active, .chapter-slide.active')
        .forEach((item) => {
            item.classList.remove('active');
            item.removeAttribute('aria-current');
        });
    for (const item of [chapter, section, slide]) {
        if (!item) continue;
        item.classList.add('active');
        item.setAttribute('aria-current', 'location');
    }
    if (nowPlaying) {
        const chapterIndex = chapters.indexOf(chapter);
        const chapterData = state.videoChapters?.[chapterIndex];
        const sections = sectionList ? [...sectionList.querySelectorAll('.chapter-section')] : [];
        const sectionIndex = sections.indexOf(section);
        const sectionData = section?.dataset.sourceChapterIndex != null
            ? state.videoChapters?.[Number(section.dataset.sourceChapterIndex)]?.sections?.[Number(section.dataset.sourceSectionIndex)]
            : chapterData?.sections?.[sectionIndex];
        const text = chapterData ? [
            `Now playing: Chapter ${chapterIndex + 1}: ${chapterData.title}`,
            sectionData ? `Section ${chapterIndex + 1}.${sectionIndex + 1}: ${sectionData.title}` : '',
        ].filter(Boolean).join(' / ') : '';
        if (nowPlaying.textContent !== text) nowPlaying.textContent = text;
        nowPlaying.hidden = !text;
    }
    const visible = slideVisible ? slide : section && !sectionList.hidden ? section : chapter;
    // Do not move a row away from the pointer or keyboard focus while it is being used.
    if (visible && !container.matches(':hover, :focus-within')) {
        scrollWithinContainer(scrollParent(container), visible);
    }
}

function scrollParent(element) {
    for (let node = element; node; node = node.parentElement) {
        const overflow = getComputedStyle(node).overflowY;
        if ((overflow === 'auto' || overflow === 'scroll') && node.scrollHeight > node.clientHeight) return node;
    }
    return element;
}

export function revealOutlineSearchResult(result) {
    if (!['section', 'chapter', 'summary'].includes(result.kind) || !Number.isFinite(result.timestamp)) return null;
    const container = elements.chaptersContainer;
    const selector = result.kind === 'section' ? '.chapter-section' : '.chapter-item';
    const matches = [...container.querySelectorAll(selector)]
        .filter(row => Math.abs(Number(row.dataset.start) - result.timestamp) < 0.001);
    const row = matches.find(item =>
        item.querySelector(result.kind === 'section' ? '.section-title' : '.chapter-title')?.textContent === result.title)
        || matches[0];
    if (!row) return null;
    document.querySelector('.tab-btn[data-tab="chapters"]')?.click();
    if (result.kind === 'section') {
        const list = row.closest('.chapter-sections');
        if (list?.hidden) list.previousElementSibling?.querySelector('.chapter-expand')?.click();
    }
    row.focus({preventScroll: true});
    scrollWithinContainer(scrollParent(container), row);
    return row;
}

function timestampSeconds(timestamp) {
    return String(timestamp).split(':').map(Number).reduce((total, part) => total * 60 + part, 0);
}

function renderChapterSections(sections, chapterIndex, chapters, editing = false) {
    const list = document.createElement('ol');
    list.className = 'chapter-sections';
    list.dataset.chapterIndex = String(chapterIndex);
    if (!editing && timestampSeconds(sections[0].timestamp) > timestampSeconds(chapters[chapterIndex].timestamp)) list.appendChild(outlineControl('Add section at the beginning',
        {action: 'add_section', chapter: chapterIndex, section: 0, timestamp: chapters[chapterIndex].timestamp}, true));
    sections.forEach((section, sectionIndex) => {
        const item = document.createElement('li');
        item.className = 'chapter-section';
        item.tabIndex = 0;
        item.dataset.start = timestampSeconds(section.timestamp);
        item.dataset.sourceTimestamp = section.timestamp;
        item.dataset.sourceChapterIndex = String(chapterIndex);
        item.dataset.sourceSectionIndex = String(sectionIndex);
        if (section.flagged || section.skipped || chapters[chapterIndex].flagged || chapters[chapterIndex].skipped) {
            item.classList.add('outline-muted');
        }
        const timestamp = document.createElement('span');
        timestamp.className = 'chapter-timestamp';
        timestamp.textContent = formatTimestamp(section.timestamp);
        const title = document.createElement('span');
        title.className = 'section-title';
        title.textContent = section.title;
        item.appendChild(timestamp);
        item.appendChild(title);
        item.appendChild(createChapterProjectButton(section, chapterIndex, chapters, sectionIndex));
        item.appendChild(createChapterDownloadButton(section, chapterIndex, chapters, sectionIndex));
        if (!editing) item.appendChild(createClipSelection(section, chapterIndex, chapters, sectionIndex));
        if (!editing) item.appendChild(outlineControl('Edit section',
            {action: 'update', chapter: chapterIndex, section: sectionIndex}));
        item.title = [section.skipped ? 'Skipped during playback' : '', section.flagged
            ? `Review suggested: ${section.flag_reason || 'Check this part'}` : '', section.point || ''].filter(Boolean).join('\n');
        bindChapterSeek(item, timestampSeconds(section.timestamp), `Play section: ${section.title}`);
        list.appendChild(item);
        if (!editing) list.appendChild(outlineControl('Add section or start new chapter here', {action: 'insert_boundary',
            chapter: chapterIndex, section: sectionIndex + 1,
            timestamp: formatClock(elements.videoPlayer?.currentTime || 0)}, true));
    });
    return list;
}

/**
 * Adds chapter markers to the timeline
 * @param {Array} chapters - The chapter data
 */
function addChapterMarkersToTimeline(chapters) {
    if (!chapters || chapters.length === 0) {
        return;
    }
    
    // Get the progress container and video player
    const progressContainer = elements.videoProgress;
    const videoPlayer = elements.videoPlayer;
    const generation = chapterMarkerGeneration;
    
    if (!progressContainer || !videoPlayer) {
        console.error('Progress container or video player not found');
        return;
    }
    
    // Remove any existing chapter markers first
    const existingMarkers = progressContainer.querySelectorAll('.chapter-marker');
    existingMarkers.forEach(marker => marker.remove());
    
    // If the video hasn't loaded yet or duration is unavailable, set up a one-time event listener
    if (!videoPlayer.duration || isNaN(videoPlayer.duration)) {
        console.log('Video duration not available yet, waiting for loadedmetadata event');
        
        // Set up a one-time event listener for when metadata is loaded
        const onMetadataLoaded = () => {
            if (generation !== chapterMarkerGeneration) return;
            addChapterMarkersWithDuration(chapters, videoPlayer, progressContainer);
            videoPlayer.removeEventListener('loadedmetadata', onMetadataLoaded);
        };
        
        videoPlayer.addEventListener('loadedmetadata', onMetadataLoaded);
        return;
    }
    
    // If duration is available, add markers immediately
    addChapterMarkersWithDuration(chapters, videoPlayer, progressContainer);
}

/**
 * Helper function to add chapter markers once video duration is available
 * @param {Array} chapters - The chapter data
 * @param {HTMLVideoElement} videoPlayer - The video player
 * @param {HTMLElement} progressContainer - The progress container
 */
function addChapterMarkersWithDuration(chapters, videoPlayer, progressContainer) {
    const duration = videoPlayer.duration;
    
    // First, remove any existing chapter segments
    const existingSegments = progressContainer.querySelectorAll('.chapter-segment');
    existingSegments.forEach(segment => segment.remove());
    
    // Add segments representing chapter regions
    for (let i = 0; i < chapters.length; i++) {
        // Convert timestamp to seconds for current chapter
        console.log(chapters)
        console.log(chapters[0])
        const currTimeInSeconds = timestampSeconds(chapters[i].timestamp);
        
        // Determine the end time of this chapter (start of next chapter or end of video)
        let endTimeInSeconds;
        if (i < chapters.length - 1) {
            endTimeInSeconds = timestampSeconds(chapters[i + 1].timestamp);
        } else {
            endTimeInSeconds = duration;
        }
        
        // Create segment element
        const segment = document.createElement('div');
        segment.className = 'chapter-segment';
        
        // Calculate position and width
        const startPosition = (currTimeInSeconds / duration) * 100;
        const endPosition = (endTimeInSeconds / duration) * 100;
        const width = endPosition - startPosition;
        
        // Set position and width
        segment.style.left = `${startPosition}%`;
        segment.style.width = `${width}%`;
        
        // Add data attributes for reference
        segment.dataset.chapterIndex = i;
        segment.dataset.startTime = currTimeInSeconds;
        segment.dataset.endTime = endTimeInSeconds;
        
        // Add tooltip to the segment
        const tooltip = document.createElement('div');
        tooltip.className = 'chapter-tooltip';
        tooltip.textContent = `Chapter ${i + 1}: ${chapters[i].title}`;
        segment.appendChild(tooltip);
        
        // Add mouseenter event to check and adjust tooltip position if needed
        segment.addEventListener('mouseenter', () => adjustTooltipPosition(segment, tooltip));
        
        // Add click behavior to the segment
        segment.addEventListener('click', (e) => {
            // Only seek if clicked directly on segment (not on a marker)
            if (e.target === segment || e.target === tooltip) {
                // Seek to the middle of the chapter unless it's very short
                const chapterDuration = endTimeInSeconds - currTimeInSeconds;
                const seekTime = currTimeInSeconds + (chapterDuration > 10 ? 1 : chapterDuration / 10);
                videoPlayer.currentTime = seekTime;
                videoPlayer.play().catch((error) => {
                    if (error.name !== 'AbortError') {
                        console.warn('Unable to play chapter segment:', error);
                    }
                });
            }
        });
        
        // Add to container
        progressContainer.appendChild(segment);
    }
    
    // Add each chapter marker to the timeline
    chapters.forEach((chapter, index) => {
        // Convert timestamp to seconds
        const timeInSeconds = timestampSeconds(chapter.timestamp);
        
        // Calculate position
        const position = (timeInSeconds / duration) * 100;
        
        // Create the marker
        const marker = document.createElement('div');
        marker.className = 'chapter-marker';
        marker.style.left = `${position}%`;
        
        // Add title attribute for accessibility
        marker.title = `Chapter ${index + 1}: ${chapter.title}`;
        
        // Add click behavior
        marker.addEventListener('click', (e) => {
            videoPlayer.currentTime = timeInSeconds;
            videoPlayer.play().catch((error) => {
                if (error.name !== 'AbortError') {
                    console.warn('Unable to play chapter marker:', error);
                }
            });
            e.stopPropagation(); // Prevent the progress bar click from firing too
        });
        
        // Add to the container
        progressContainer.appendChild(marker);
    });
    
    console.log(`Added ${chapters.length} chapter markers and segments to timeline`);
}

/**
 * Adjusts tooltip position dynamically to prevent it from going off-screen
 * @param {HTMLElement} segment - The chapter segment element
 * @param {HTMLElement} tooltip - The tooltip element
 */
function adjustTooltipPosition(segment, tooltip) {
    // Wait for tooltip to be visible to get its dimensions
    setTimeout(() => {
        // Reset any previous adjustments
        tooltip.style.transform = 'translateX(-50%)';
        
        // Get the tooltip's position and dimensions
        const tooltipRect = tooltip.getBoundingClientRect();
        const viewportWidth = window.innerWidth;
        
        // Check if tooltip overflows left edge
        if (tooltipRect.left < 0) {
            const leftOverflow = Math.abs(tooltipRect.left);
            // Adjust to the right to keep it within viewport
            tooltip.style.transform = `translateX(calc(-50% + ${leftOverflow + 10}px))`;
        }
        // Check if tooltip overflows right edge
        else if (tooltipRect.right > viewportWidth) {
            const rightOverflow = tooltipRect.right - viewportWidth;
            // Adjust to the left to keep it within viewport
            tooltip.style.transform = `translateX(calc(-50% - ${rightOverflow + 10}px))`;
        }
    }, 10); // Small delay to ensure tooltip is visible
} 