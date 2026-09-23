// api.js - API interaction functionality

import { elements } from './elements.js';
import { state } from './main.js';
import { extractVideoId } from './utils.js';
import { showError, showErrorWithActions, showLoading, showNotification } from './ui.js';
import { setupVideoPlayer } from './video.js';
import { loadTranscript } from './transcript.js';
import { updateChapters, clearChapterMarkers } from './chapters.js';
import { checkSceneDetection, updateScenes, stopDetectionPolling } from './scenes.js';
import { fetchOcrResults } from './ocr.js';

export async function regenerateTranscript() {
    const videoId = getActiveVideoId();
    if (!videoId) {
        showError('Load a video before regenerating its transcript.');
        return;
    }
    state.currentVideoId = videoId;
    const originalTranscript = state.currentTranscript.map((item) => ({ ...item }));

    const diarization = elements.transcriptDiarization?.checked || false;
    const button = elements.regenerateTranscriptBtn;
    if (button) {
        button.disabled = true;
        button.textContent = 'Regenerating...';
    }
    const pollGeneration = ++state.whisperTranscriptPollGeneration;
    const startedAt = Date.now();
    renderWhisperProgress({ status: 'queued', progress: 0 }, startedAt);
    try {
        const response = await fetch(`/generate_whisper_transcript/${encodeURIComponent(videoId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                diarization,
                force: true
            })
        });
        const data = await readJsonResponse(response, 'Transcript regeneration');
        if (!data.success) throw new Error(data.error || 'Transcript regeneration failed');
        elements.transcriptOptionsDialog?.close();
        showNotification(
            data.phase === 'identifying_speakers'
                ? 'Using the existing transcript. Speaker identification started.'
                : data.status === 'in_progress'
                ? 'Transcript generation is already running. Showing its progress.'
                : 'Transcript regeneration started.',
            'info'
        );

        while (
            state.currentVideoId === videoId
            && state.whisperTranscriptPollGeneration === pollGeneration
        ) {
            const statusResponse = await fetch(`/whisper_transcript_status/${encodeURIComponent(videoId)}`);
            const status = await readJsonResponse(statusResponse, 'Transcript status');
            if (status.status === 'complete') {
                state.currentTranscriptSource = 'whisper';
                loadTranscript(applySpeakerNames(status.transcript, status.speaker_names || {}));
                renderSpeakerNames(status.speaker_names || {});
                showNotification(`Transcript generated with ${status.model || model}.`, 'success');
                return;
            }
            if (status.status === 'error') throw new Error(status.error || 'Transcript regeneration failed');
            renderWhisperProgress(status, startedAt);
            await delay(3000);
        }
    } catch (error) {
        showError(`Error regenerating transcript: ${error.message}`);
        if (originalTranscript.length) {
            loadTranscript(originalTranscript);
        } else {
            elements.transcriptContainer.innerHTML = `
                <div class="transcript-processing transcript-error">
                    <i class="fas fa-info-circle"></i>
                    <p>Transcript regeneration stopped. ${escapeHtml(error.message)}</p>
                </div>
            `;
        }
    } finally {
        if (button && state.whisperTranscriptPollGeneration === pollGeneration) {
            button.disabled = false;
            button.textContent = 'Regenerate transcript';
        }
    }
}

function renderWhisperProgress(status, startedAt) {
    const progress = Number.isFinite(Number(status.progress))
        ? Math.max(0, Math.min(100, Number(status.progress)))
        : 0;
    const elapsedSeconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
    const phase = status.phase === 'identifying_speakers'
        ? 'Identifying speakers with pyannote'
        : status.phase === 'transcribing' || progress > 0
            ? 'Transcribing audio'
            : 'Starting Whisper model';
    const lastUpdate = Number.isFinite(Number(status.last_updated_seconds_ago))
        ? ` Server heartbeat: ${Math.round(Number(status.last_updated_seconds_ago))}s ago.`
        : '';
    elements.transcriptContainer.innerHTML = `
        <div class="transcript-processing">
            <i class="fas fa-spinner fa-spin"></i>
            <div class="progress-container">
                <div class="progress-bar">
                    <div class="progress-fill" style="width: ${progress}%"></div>
                </div>
                <div class="progress-text">${Math.round(progress)}%</div>
            </div>
            <p>${phase} (${elapsedSeconds}s elapsed).${lastUpdate}</p>
            <p>You can continue using slide detection and video controls while this runs.</p>
        </div>
    `;
}

function delay(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function escapeHtml(value) {
    const element = document.createElement('div');
    element.textContent = String(value);
    return element.innerHTML;
}

export async function uploadTranscriptFile() {
        const file = elements.transcriptFile?.files?.[0];
        if (!state.currentVideoId || !file) {
            showError('Select a transcript file after loading a video.');
            return;
        }
        const form = new FormData();
        form.append('transcript', file);
        const response = await fetch(`/upload_transcript/${encodeURIComponent(state.currentVideoId)}`, { method: 'POST', body: form });
        const data = await readJsonResponse(response, 'Transcript upload');
        loadTranscript(data.transcript);
        state.currentTranscriptSource = 'uploaded';
        updateTranslationOptions(data.source_language);
        showNotification(`Uploaded transcript loaded (${data.source_language}).`, 'success');
}

export async function translateTranscript() {
    const button = elements.translateTranscriptBtn;
    const originalText = button?.textContent || 'Translate transcript';
    if (!state.currentVideoId || !state.currentTranscript.length) {
        showError('Load a transcript before translating it.');
        return;
    }
    if (button) {
        button.disabled = true;
    }
    const originalTranscript = state.currentTranscript.map((item) => ({ ...item }));
    const selectedModel = elements.translationModel?.value || '';
    const selectedModelLabel = elements.translationModel?.selectedOptions[0]?.textContent || 'Auto';
    const startedAt = Date.now();
    const updateElapsed = () => {
        const elapsed = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
        if (button) button.textContent = `Translating with ${selectedModelLabel}… ${elapsed}s`;
        if (elements.translationModelStatus) {
            elements.translationModelStatus.textContent =
                `Translation is running with ${selectedModelLabel} (${elapsed}s elapsed). Long transcripts are processed in parallel batches.`;
        }
    };
    updateElapsed();
    const elapsedTimer = setInterval(updateElapsed, 1000);
    let translationProgressTimer = null;
    try {
        const target = elements.translationTarget.value;
        if (!target) {
            state.currentTranslationLanguage = null;
            showNotification('No translation selected. The transcript remains in its original language.', 'info');
            return;
        }
        elements.transcriptOptionsDialog?.close();
        elements.transcriptContainer.innerHTML = `
            <div class="transcript-processing">
                <i class="fas fa-spinner fa-spin"></i>
                <p id="translationProgressText">Preparing translation with ${escapeHtml(selectedModelLabel)}...</p>
                <p>The transcript and chapter titles are translated in parallel batches.</p>
            </div>
        `;
        const progressText = document.getElementById('translationProgressText');
        translationProgressTimer = setInterval(() => {
            if (!progressText) return;
            const elapsed = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
            progressText.textContent =
                `Translating with ${selectedModelLabel} (${elapsed}s elapsed)...`;
        }, 1000);
        const response = await fetch(`/translate_transcript/${encodeURIComponent(state.currentVideoId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                transcript: state.currentTranscript,
                target_language: target,
                model: selectedModel
            })
        });
        const data = await readJsonResponse(response, 'Transcript translation');
        clearInterval(translationProgressTimer);
        translationProgressTimer = null;
        if (
            !Array.isArray(data.transcript)
            || data.transcript.length !== originalTranscript.length
        ) {
            throw new Error('The translation model returned an incomplete transcript.');
        }
        const sourceText = originalTranscript.map((item) => String(item.text || '').trim()).join('\n');
        const translatedText = data.transcript.map((item) => String(item.text || '').trim()).join('\n');
        if (sourceText && translatedText === sourceText) {
            throw new Error(
                'The translation model returned the original text unchanged. Try another translation model.'
            );
        }
        loadTranscript(data.transcript);
        state.currentTranscriptSource = `translation:${target}`;
        state.currentTranslationLanguage = target;
        if (Array.isArray(data.chapters)) {
            state.videoChapters = data.chapters;
            updateChapters(data.chapters);
        }
        const translationSummary =
            `${data.provider} ${data.model}; ${data.batch_count} batch${data.batch_count === 1 ? '' : 'es'} in ${data.elapsed_seconds}s`;
        if (elements.translationModelStatus) {
            elements.translationModelStatus.textContent = `Last translation: ${translationSummary}.`;
        }
        showNotification(`Transcript translated to ${data.language} using ${translationSummary}.`, 'success');
    } catch (error) {
        loadTranscript(originalTranscript);
        if (elements.translationModelStatus) {
            elements.translationModelStatus.textContent = `Translation failed: ${error.message}`;
        }
        throw error;
    } finally {
        clearInterval(elapsedTimer);
        if (translationProgressTimer) clearInterval(translationProgressTimer);
        if (button) {
            button.disabled =
                !elements.translationTarget.value
                || elements.translationModel?.dataset.available === 'false';
            button.textContent = originalText;
        }
    }
}

export async function updateTranslationModelStatus() {
    if (!elements.translationModelStatus) return;
    try {
        const response = await fetch('/runtime_status');
        const data = await readJsonResponse(response, 'Runtime status');
        if (elements.speakerDiarizationTokenStatus) {
            elements.speakerDiarizationTokenStatus.textContent = data.huggingface_token_configured
                ? 'Hugging Face token detected. '
                : 'No Hugging Face token detected. ';
        }
        const hasTranslationModel = Boolean(
            data.translation_default_provider && data.translation_default_model
        );
        elements.translationModel.dataset.available = String(hasTranslationModel);
        if (!hasTranslationModel) {
            elements.translationModelStatus.textContent =
                'No translation model is configured. Set GOOGLE_API_KEY or OPENAI_API_KEY in Colab and restart the server.';
            elements.translateTranscriptBtn.disabled = true;
            return;
        }
        const defaultDescription = `${data.translation_default_provider} ${data.translation_default_model}`;
        elements.translationModelStatus.textContent =
            `Auto currently uses ${defaultDescription}. Long transcripts are translated in parallel batches.`;
        [...elements.translationModel.options].forEach((option) => {
            if (option.value.startsWith('gemini-')) option.disabled = !data.gemini_configured;
            if (option.value.startsWith('gpt-')) option.disabled = !data.openai_configured;
        });
        if (elements.translationModel.selectedOptions[0]?.disabled) {
            elements.translationModel.value = '';
        }
    } catch (error) {
        elements.translationModel.dataset.available = 'false';
        elements.translateTranscriptBtn.disabled = true;
        elements.translationModelStatus.textContent =
            `Could not check translation model configuration: ${error.message}`;
    }
}

function updateTranslationOptions(sourceLanguage) {
        [...elements.translationTarget.options].forEach((option) => {
            option.disabled = Boolean(option.value) && option.value === sourceLanguage;
        });
        if (elements.translationTarget.value === sourceLanguage) {
            elements.translationTarget.value = '';
        }
        elements.translateTranscriptBtn.disabled = !elements.translationTarget.value;
    }

export function renderSpeakerNames(names = {}) {
        const panel = elements.speakerNamesPanel;
        if (!panel) return;
        panel.innerHTML = '';
        const speakers = new Set(state.currentTranscript.map((item) => item.speaker).filter(Boolean));
        if (!speakers.size) {
            panel.hidden = true;
            return;
        }

        panel.hidden = false;
        const title = document.createElement('strong');
        title.textContent = 'Speaker names';
        panel.appendChild(title);
        const updatedNames = { ...names };
        speakers.forEach((speaker) => {
            const row = document.createElement('label');
            row.className = 'speaker-name-row';
            row.innerHTML = `<span>${speaker}</span>`;
            const input = document.createElement('input');
            input.value = names[speaker] || speaker.replace(/_/g, ' ');
            input.setAttribute('aria-label', `Name for ${speaker}`);
            input.addEventListener('change', async () => {
                updatedNames[speaker] = input.value.trim() || speaker;
                const response = await fetch(`/speaker_names/${encodeURIComponent(state.currentVideoId)}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ names: updatedNames })
                });
                const data = await readJsonResponse(response, 'Saving speaker names');
                showNotification('Speaker name saved.', 'success');
                loadTranscript(state.currentTranscript.map((item) => ({ ...item, speaker_name: data.speaker_names[item.speaker] || item.speaker })));
            });
            row.appendChild(input);
            panel.appendChild(row);
        });
    }

function applySpeakerNames(transcript, names) {
    return transcript.map((item) => ({
        ...item,
        speaker_name: item.speaker ? (names[item.speaker] || item.speaker) : undefined
    }));
}

async function readJsonResponse(response, operation) {
    const responseText = await response.text();
    if (!responseText.trim()) {
        throw new Error(`${operation} failed (HTTP ${response.status}). The server returned an empty response.`);
    }

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

async function startYoutubeWhisperPolling(videoId) {
    const updateTranscript = (transcript) => {
        state.currentTranscript = transcript;
        state.currentTranscriptSource = 'whisper';
        loadTranscript(transcript);
        elements.generateSummaryBtn.disabled = false;
        elements.exportChaptersBtn.disabled = false;
        showNotification('Whisper transcript generation complete. Generate Summary is ready.', 'success');
    };

    const checkStatus = async () => {
        const response = await fetch(`/whisper_transcript_status/${videoId}`);
        const data = await response.json();
        if (data.status === 'complete' && Array.isArray(data.transcript)) {
            updateTranscript(data.transcript);
            if (state.youtubeTranscriptInterval) {
                clearInterval(state.youtubeTranscriptInterval);
                state.youtubeTranscriptInterval = null;
            }
            return true;
        }
        if (data.status === 'error') {
            if (state.youtubeTranscriptInterval) {
                clearInterval(state.youtubeTranscriptInterval);
                state.youtubeTranscriptInterval = null;
            }
            elements.transcriptContainer.innerHTML = `
                <div class="transcript-processing transcript-error">
                    <i class="fas fa-info-circle"></i>
                    <p>Transcript generation stopped: ${data.error || 'No recognizable speech was found in this video.'}</p>
                    <p>You can still detect slides, but transcript-based summaries are unavailable.</p>
                </div>
            `;
            elements.generateSummaryBtn.disabled = true;
            elements.exportChaptersBtn.disabled = false;
            throw new Error(data.error || 'Whisper transcript generation failed.');
        }
        return false;
    };

    let status = await fetch(`/whisper_transcript_status/${videoId}`).then((response) => response.json());
    if (status.status === 'queued') {
        const response = await fetch(`/generate_whisper_transcript/${videoId}`, { method: 'POST' });
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Could not start Whisper transcript generation.');
        }
    } else if (status.status === 'complete' && Array.isArray(status.transcript)) {
        updateTranscript(status.transcript);
        return;
    } else if (status.status === 'error') {
        throw new Error(status.error || 'Whisper transcript generation failed.');
    }

    elements.transcriptContainer.innerHTML = `
        <div class="transcript-processing">
            <i class="fas fa-spinner fa-spin"></i>
            <p>Generating transcript with Whisper AI. You can generate the summary when it is ready.</p>
        </div>
    `;
    elements.loadingIndicator.querySelector('p').textContent =
        'Video ready. Generating transcript in the background...';
    await checkStatus();
    if (!state.currentTranscript.length) {
        state.youtubeTranscriptInterval = setInterval(() => {
            checkStatus().catch((error) => showError(`Error generating transcript: ${error.message}`));
        }, 5000);
    }
}

function getActiveVideoId() {
    if (state.currentVideoId) {
        return state.currentVideoId;
    }

    const source = elements.videoPlayer?.currentSrc || elements.videoPlayer?.src || '';
    const sourceMatch = source.match(/\/video\/([^/?#]+)/);
    if (sourceMatch) {
        return decodeURIComponent(sourceMatch[1]);
    }

    return extractVideoId(elements.youtubeUrl?.value || '');
}

/**
 * Resets all video-related states when loading a new video
 */
function resetVideoStates() {
    if (state.youtubeTranscriptInterval) {
        clearInterval(state.youtubeTranscriptInterval);
        state.youtubeTranscriptInterval = null;
    }
    if (state.summaryRetryTimer) {
        clearInterval(state.summaryRetryTimer);
        state.summaryRetryTimer = null;
    }
    state.summaryGenerationInProgress = false;

    // Reset SSE connection if it exists
    if (window.sseConnection) {
        console.log('Closing existing SSE connection');
        window.sseConnection.close();
        window.sseConnection = null;
    }
    stopDetectionPolling();
    if (state.transcriptOcrStatusInterval) {
        clearInterval(state.transcriptOcrStatusInterval);
        state.transcriptOcrStatusInterval = null;
    }
    
    // Reset OCR state
    state.ocrResults = [];
    state.ocrProcessing = false;
    
    // Remove any existing progress containers
    const progressContainer = document.getElementById('ocrProgressContainer');
    if (progressContainer) {
        progressContainer.remove();
    }

    // Reset video player
    const videoPlayer = elements.videoPlayer;
    state.currentVideoId = null;
    state.currentTranslationLanguage = null;
    state.whisperTranscriptPollGeneration += 1;
    if (elements.translationTarget) {
        elements.translationTarget.value = '';
    }
    if (elements.translateTranscriptBtn) {
        elements.translateTranscriptBtn.disabled = true;
    }
    if (elements.regenerateTranscriptBtn) {
        elements.regenerateTranscriptBtn.disabled = false;
        elements.regenerateTranscriptBtn.textContent = 'Regenerate transcript';
    }
    clearChapterMarkers();
    videoPlayer.pause();
    videoPlayer.removeAttribute('src');
    videoPlayer.load();
    elements.exportChaptersBtn.disabled = true;

    // Clear existing transcript and chapters
    elements.transcriptContainer.innerHTML = '';
    elements.chaptersContainer.innerHTML = '<p>Click "Generate Summary" to create chapter markers from the transcript.</p>';
    
    // Reset chapter state
    state.videoChapters = [];
    document.dispatchEvent(new CustomEvent('chaptersUpdated', { detail: [] }));
    
    // Reset scene detection state
    if (state.sceneDetectionInterval) {
        clearInterval(state.sceneDetectionInterval);
        state.sceneDetectionInterval = null;
    }
    state.videoScenes = [];
    state.sceneDetectionStartedAt = null;
    state.currentDebugScene = null;
    elements.detectScenesBtn.disabled = true;
    if (elements.downloadScreenshotsBtn) {
        elements.downloadScreenshotsBtn.disabled = true;
    }
    
    // Reset slide content and disable search
    elements.slideContentContainer.innerHTML = `
        <div class="coming-soon">
            <i class="fas fa-cogs"></i>
            <h3>Processing Text Elements</h3>
            <p>Text detection and OCR will begin after scene detection is complete.</p>
        </div>
    `;
    elements.slideSearch.disabled = true;
    elements.slideSearch.value = '';
    elements.clearSlideSearchBtn.classList.remove('visible');
    elements.slideSearchResultsCount.textContent = '0/0';
    elements.prevSlideSearchBtn.disabled = true;
    elements.nextSlideSearchBtn.disabled = true;

    // Reset search states
    state.searchResults = [];
    state.currentSearchIndex = -1;
    state.slideSearchResults = [];
    state.currentSlideSearchIndex = -1;

    // Reset transcript-OCR relationships
    state.transcriptOcrRelationships = null;
    state.ocr_to_transcript = {};
    state.transcript_to_ocr = {};

    // Reset current transcript
    state.currentTranscript = [];
}

/**
 * Processes a YouTube video
 */
export async function processVideo() {
    const url = elements.youtubeUrl.value;
    const videoId = extractVideoId(url);
    
    if (!videoId) {
        showError('Invalid YouTube URL');
        return;
    }

    // Save the URL to localStorage
    localStorage.setItem('lastYoutubeUrl', url);

    showError('');
    const savedUploads = document.getElementById('uploadedVideos');
    if (savedUploads) savedUploads.value = '';
    showLoading(true);
    elements.loadingIndicator.querySelector('p').textContent = 'Uploading video...';
    elements.generateSummaryBtn.disabled = true;
    elements.exportChaptersBtn.disabled = true;

    try {
        // Reset all video-related states
        resetVideoStates();

        const videoQuality = Number(elements.youtubeVideoQuality?.value || 480);
        const response = await fetch(`/download/${videoId}?quality=${encodeURIComponent(videoQuality)}`);
        const data = await readJsonResponse(response, 'YouTube video download');

        if (!data.success) {
            throw new Error(data.error || 'The server could not download this YouTube video.');
        }
        state.currentVideoId = data.video_id || videoId;
        elements.exportChaptersBtn.disabled = false;

        // Set new source and wait for metadata to load
        await new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                reject(new Error('Upload completed, but the browser could not load the video preview.'));
            }, 30000);
            elements.videoPlayer.src = data.video_url;
            
            elements.videoPlayer.onloadedmetadata = () => {
                clearTimeout(timeout);
                setupVideoPlayer(elements.videoPlayer);
                resolve();
            };
            
            elements.videoPlayer.onerror = () => {
                clearTimeout(timeout);
                reject(new Error('Failed to load video'));
            };
        });

        // Store the ID returned by the server so downloaded files and
        // subsequent scene requests always refer to the same video.
        if (state.youtubeRetryTimer) {
            clearInterval(state.youtubeRetryTimer);
            state.youtubeRetryTimer = null;
        }
        elements.detectScenesBtn.disabled = false;
        
        // Check transcript availability
        const hasYoutubeTranscript = data.has_youtube_transcript;
        const hasWhisperTranscript = data.has_whisper_transcript;
        
        // Determine which transcript to use based on preference and availability
        let transcriptToUse = data.transcript;
        let transcriptSource = state.currentTranscriptSource;
        
        if (transcriptSource === 'whisper' && !hasWhisperTranscript) {
            // If whisper is preferred but not available, show notification
            if (hasYoutubeTranscript) {
                showNotification('Whisper transcript not available. Using YouTube transcript instead.', 'info');
                transcriptSource = 'youtube';
            } else {
                showNotification('No transcripts available. Please generate a Whisper transcript in Settings.', 'info');
                transcriptSource = null;
            }
        } else if (transcriptSource === 'youtube' && !hasYoutubeTranscript) {
            // If youtube is preferred but not available, try whisper
            if (hasWhisperTranscript) {
                showNotification('YouTube transcript not available. Using Whisper transcript instead.', 'info');
                transcriptSource = 'whisper';
                
                // Fetch Whisper transcript
                const whisperResponse = await fetch(`/get_transcript/${videoId}/whisper`);
                const whisperData = await whisperResponse.json();
                if (whisperData.success) {
                    transcriptToUse = whisperData.transcript;
                }
            } else {
                showNotification('No transcripts available. Please generate a Whisper transcript in Settings.', 'info');
                transcriptSource = null;
            }
        }
        
        // Update current transcript source
        state.currentTranscriptSource = transcriptSource;

        // Transcript generation is independent from slide detection. Make
        // scene controls available even when Whisper has no usable speech.
        elements.detectScenesBtn.disabled = false;

        if (transcriptToUse) {
            state.currentTranscript = transcriptToUse;
            
            // Check for existing summary
            const summaryResponse = await fetch(`/summary/${videoId}`);
            const summaryData = await summaryResponse.json();
            
            if (summaryData.success && summaryData.exists) {
                // Load existing summary
                updateChapters(summaryData.chapters);
            } else {
                elements.chaptersContainer.innerHTML = '<p>Click "Generate Summary" to create chapter markers from the transcript.</p>';
            }

            // Update transcript display
            loadTranscript(transcriptToUse);

            // Enable generate summary button if transcript is available
            elements.generateSummaryBtn.disabled = false;
            elements.exportChaptersBtn.disabled = false;
        } else if (data.transcript_in_progress) {
            try {
                await startYoutubeWhisperPolling(state.currentVideoId);
            } catch (error) {
                showError(`Transcript unavailable: ${error.message}`);
            }
        } else {
            try {
                await startYoutubeWhisperPolling(state.currentVideoId);
            } catch (error) {
                showError(`Transcript unavailable: ${error.message}`);
            }
        }

        elements.scenesContainer.innerHTML = '<p>Click "Detect Slides" to analyze this video.</p>';

    } catch (error) {
        const message = `Error: ${error.message}`;
        const restrictionError = /youtube|sign in|authentication|not a bot|bot|private|age-restricted|unavailable|forbidden|403|429/i.test(error.message);
        if (restrictionError) {
            const startAutoRetry = () => {
                if (state.youtubeRetryTimer) return;
                state.youtubeRetryTimer = setInterval(() => processVideo(), 15000);
                showNotification('Automatic YouTube retry enabled. Use Stop automatic retry to stop it.', 'info');
            };
            const stopAutoRetry = () => {
                if (state.youtubeRetryTimer) {
                    clearInterval(state.youtubeRetryTimer);
                    state.youtubeRetryTimer = null;
                }
                showError('');
            };
            showErrorWithActions(
                `${message} YouTube may be restricting this download. You can retry.`,
                [
                    { label: 'Retry YouTube download', action: () => processVideo() },
                    { label: 'Retry automatically', action: startAutoRetry },
                    { label: 'Stop automatic retry', action: stopAutoRetry }
                ]
            );
        } else {
            showError(message);
        }
    } finally {
        showLoading(false);
    }
}

/**
 * Processes a video file upload
 */
export async function processVideoUpload(file) {
    showError('');
    showLoading(true);
    elements.loadingIndicator.querySelector('p').textContent = 'Uploading video...';
    elements.generateSummaryBtn.disabled = true;

    try {
        // Reset all video-related states
        resetVideoStates();

        // Create FormData and append file
        const formData = new FormData();
        formData.append('video', file);

        const response = await uploadVideoWithProgress(formData);

        elements.loadingIndicator.querySelector('p').textContent =
            'Upload complete. Preparing video preview...';
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error);
        }
        state.currentVideoId = data.video_id;
        elements.exportChaptersBtn.disabled = false;

        // Set new source and wait for metadata to load
        await new Promise((resolve, reject) => {
            elements.videoPlayer.src = data.video_url;
            
            elements.videoPlayer.onloadedmetadata = () => {
                setupVideoPlayer(elements.videoPlayer);
                resolve();
            };
            
            elements.videoPlayer.onerror = () => {
                reject(new Error('Failed to load video'));
            };
        });

        elements.detectScenesBtn.disabled = false;
        elements.exportChaptersBtn.disabled = false;
        elements.loadingIndicator.querySelector('p').textContent =
            'Video ready. Scene detection is separate and must be started manually.';
        
        // Check if we have an existing transcript in the response
        console.log("check transcript", data.transcript)
        if (data.transcript) {
            console.log('Using existing transcript');
            state.currentTranscript = data.transcript;
            state.currentTranscriptSource = data.has_whisper_transcript ? 'whisper' : 'youtube';
            
            // Update transcript display
            loadTranscript(data.transcript);
            
            // Enable generate summary button
            elements.generateSummaryBtn.disabled = false;
            elements.exportChaptersBtn.disabled = false;
            
            // Check for existing summary
            const summaryResponse = await fetch(`/summary/${data.video_id}`);
            const summaryData = await summaryResponse.json();
            
            if (summaryData.success && summaryData.exists) {
                // Load existing summary
                updateChapters(summaryData.chapters);
            } else {
                elements.chaptersContainer.innerHTML = '<p>Click "Generate Summary" to create chapter markers from the transcript.</p>';
            }
        }
        // If transcript is being generated, show progress
        else if (data.transcript_in_progress) {
            const transcriptStartedAt = Date.now();
            // Show a message in the transcript container
            renderWhisperProgress({ status: 'queued', progress: 0 }, transcriptStartedAt);
            elements.loadingIndicator.querySelector('p').textContent =
                'Video ready. Generating transcript in the background...';
            
            // Start polling for transcript completion
            let whisperCheckInterval = null;
            const checkWhisperTranscript = async () => {
                if (state.currentVideoId !== data.video_id) {
                    if (whisperCheckInterval) clearInterval(whisperCheckInterval);
                    return;
                }
                try {
                    const whisperResponse = await fetch(`/whisper_transcript_status/${data.video_id}`);
                    const whisperData = await readJsonResponse(whisperResponse, 'Transcript status');
                    
                    if (whisperData.status === 'complete') {
                        if (whisperCheckInterval) clearInterval(whisperCheckInterval);
                        
                        // Transcript is ready, update UI
                        showNotification('Whisper transcript generation complete!', 'success');
                        
                        // Update current transcript
                        state.currentTranscript = whisperData.transcript;
                        state.currentTranscriptSource = 'whisper';
                        
                        // Update transcript display
                        loadTranscript(whisperData.transcript);
                        
                        // Enable generate summary button
                        elements.generateSummaryBtn.disabled = false;
                    } else if (whisperData.status === 'error') {
                        if (whisperCheckInterval) clearInterval(whisperCheckInterval);
                        elements.transcriptContainer.innerHTML = `
                            <div class="transcript-processing transcript-error">
                                <i class="fas fa-info-circle"></i>
                                <p>Transcript generation stopped: ${escapeHtml(whisperData.error || 'No recognizable speech was found in this video.')}</p>
                                <p>You can still detect slides, but transcript-based summaries are unavailable.</p>
                            </div>
                        `;
                        elements.generateSummaryBtn.disabled = true;
                        elements.exportChaptersBtn.disabled = false;
                        showError(`Error generating transcript: ${whisperData.error}`);
                    } else {
                        renderWhisperProgress(whisperData, transcriptStartedAt);
                    }
                } catch (error) {
                    console.error('Error checking Whisper transcript status:', error);
                    elements.transcriptContainer
                        .querySelectorAll('.transcript-status-warning')
                        .forEach((warning) => warning.remove());
                    elements.transcriptContainer.insertAdjacentHTML(
                        'beforeend',
                        `<p class="transcript-status-warning">Could not refresh transcript status: ${escapeHtml(error.message)}</p>`
                    );
                }
            };
            
            // Check immediately and then every 5 seconds
            whisperCheckInterval = setInterval(checkWhisperTranscript, 5000);
            void checkWhisperTranscript();
        }
        // No transcript available and not being generated
        else {
            elements.transcriptContainer.innerHTML = '<p>No transcript available for this video.</p>';
            elements.generateSummaryBtn.disabled = true;
            elements.loadingIndicator.querySelector('p').textContent =
                'Video ready. No transcript is available.';
        }
        
        elements.scenesContainer.innerHTML = '<p>Click "Detect Slides" to analyze this video.</p>';

    } catch (error) {
        showError(`Error: ${error.message}`);
    } finally {
        showLoading(false);
    }

}

function uploadVideoWithProgress(formData) {
    return new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open('POST', '/upload_video');
        request.upload.addEventListener('progress', (event) => {
            if (!event.lengthComputable) return;
            const percent = Math.round((event.loaded / event.total) * 100);
            elements.loadingIndicator.querySelector('p').textContent =
                percent >= 100
                    ? 'Upload sent. Finalizing video...'
                    : `Uploading video... ${percent}%`;
        });
        request.addEventListener('load', () => {
            const responseText = request.responseText || '';
            try {
                JSON.parse(responseText);
            } catch (error) {
                reject(new Error(
                    `Video upload failed (HTTP ${request.status}). The server returned invalid JSON.`
                ));
                return;
            }
            resolve(new Response(responseText, {
                status: request.status,
                headers: { 'Content-Type': 'application/json' }
            }));
        });
        request.addEventListener('error', () => reject(new Error('Network error while uploading the video.')));
        request.addEventListener('abort', () => reject(new Error('Video upload was cancelled.')));
        request.send(formData);
    });
}

export async function loadUploadedVideo(videoId) {
    showError('');
    showLoading(true);
    elements.loadingIndicator.querySelector('p').textContent = 'Loading saved video...';
    elements.youtubeUrl.value = '';
    const exampleVideos = document.getElementById('exampleVideos');
    if (exampleVideos) exampleVideos.value = '';
    try {
        resetVideoStates();
        const response = await fetch(`/uploaded_videos/${encodeURIComponent(videoId)}`);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.detail || data.error || 'Could not load saved video');
        }

        await new Promise((resolve, reject) => {
            const timeout = setTimeout(() => reject(new Error('Saved video preview could not be loaded.')), 30000);
            elements.videoPlayer.src = data.video_url;
            elements.videoPlayer.onloadedmetadata = () => {
                clearTimeout(timeout);
                setupVideoPlayer(elements.videoPlayer);
                resolve();
            };
            elements.videoPlayer.onerror = () => {
                clearTimeout(timeout);
                reject(new Error('Failed to load saved video'));
            };
        });

        state.currentVideoId = data.video_id;
        elements.detectScenesBtn.disabled = false;
        elements.exportChaptersBtn.disabled = false;
        if (Array.isArray(data.scenes) && data.scenes.length > 0) {
            updateScenes(data.scenes, elements.videoPlayer);
            elements.scenesContainer.insertAdjacentHTML(
                'afterbegin',
                '<p class="scene-help">Loaded previously detected slides.</p>'
            );
            if (elements.downloadScreenshotsBtn) {
                elements.downloadScreenshotsBtn.disabled = false;
            }
            fetchOcrResults(data.video_id);
        } else {
            elements.scenesContainer.innerHTML = '<p>Click "Detect Slides" to analyze this video.</p>';
        }

        if (data.transcript) {
            state.currentTranscript = data.transcript;
            state.currentTranscriptSource = 'whisper';
            loadTranscript(data.transcript);
            elements.generateSummaryBtn.disabled = false;
            elements.exportChaptersBtn.disabled = false;
            const summaryResponse = await fetch(`/summary/${data.video_id}`);
            const summaryData = await summaryResponse.json();
            if (summaryData.success && summaryData.exists) {
                updateChapters(summaryData.chapters);
            }
        } else if (data.transcript_in_progress) {
            elements.transcriptContainer.innerHTML = '<p>Whisper transcript is still processing. You can detect slides now.</p>';
        } else {
            elements.transcriptContainer.innerHTML = '<p>No transcript available for this video.</p>';
        }
    } catch (error) {
        showError(`Error loading saved video: ${error.message}`);
    } finally {
        showLoading(false);
    }
}

export async function detectScenes() {
    const videoId = state.currentVideoId;
    if (!videoId) return;
    const button = elements.detectScenesBtn;
    const mode = elements.sceneDetectionMode.value;
    const hasChapters = Array.isArray(state.videoChapters) && state.videoChapters.length > 0;
    if (mode === 'chapters' && !hasChapters) {
        showError('Generate transcript chapters before using chapter-only slide detection.');
        return;
    }
    const contentThreshold = Number(elements.sceneDetectionThreshold.value);
    const minimumDuration = Number(elements.minimumSlideDuration.value);
    const maximumSlidesPerHour = Number(elements.maximumSlidesPerHour.value);
    const screenshotHeight = Number(elements.slideImageQuality.value);
    if (
        !Number.isFinite(contentThreshold)
        || !Number.isFinite(minimumDuration)
        || !Number.isFinite(maximumSlidesPerHour)
        || !Number.isFinite(screenshotHeight)
    ) {
        showError('Please select valid slide-detection options.');
        return;
    }
    state.sceneDetectionThreshold = contentThreshold;
    localStorage.setItem('contentCutThresholdV1', String(contentThreshold));
    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Detecting Slides...';
    state.sceneDetectionStartedAt = Date.now();
    elements.scenesContainer.innerHTML = '<p>Detecting scene changes...</p><p class="scene-progress-status"><i class="fas fa-spinner fa-spin"></i> Analysis is running...</p>';
    try {
        const response = await fetch(`/detect_scenes/${videoId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mode,
                adaptive_detail: elements.adaptiveDetail.value,
                content_threshold: contentThreshold,
                minimum_slide_duration: minimumDuration,
                maximum_slides_per_hour: maximumSlidesPerHour,
                merge_similar_slides: elements.mergeSimilarSlides.checked,
                include_chapter_boundaries: hasChapters && elements.includeChapterBoundaries.checked,
                screenshot_height: screenshotHeight
            })
        });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.detail || data.error || 'Could not start scene detection');
        elements.scenesContainer.insertAdjacentHTML(
            'afterbegin',
            `<p class="scene-progress-status">Method: ${mode === 'adaptive' ? `Adaptive (${elements.adaptiveDetail.value})` : mode === 'content' ? `Content cuts (${contentThreshold})` : 'Transcript chapters'}.</p>`
        );
        await checkSceneDetection(videoId);
        state.sceneDetectionInterval = setInterval(() => checkSceneDetection(videoId), 2000);
        fetchOcrResults(videoId);
        loadTranscriptOcrRelationships(videoId);
    } catch (error) {
        showError(`Error detecting scenes: ${error.message}`);
        button.disabled = false;
        button.innerHTML = '<i class="fas fa-film"></i> Detect Slides';
    }
}

/**
 * Checks the status of the YOLO model
 */
export async function checkYoloStatus() {
    try {
        const response = await fetch('/yolo_status');
        const data = await response.json();
        
        if (!data.loaded) {
            console.warn("YOLO model not loaded:", data.error);
            showError('Object detection (YOLO) is not available: ' + data.error);
        }
    } catch (error) {
        console.error("Error checking YOLO status:", error);
    }
}

/**
 * Loads the transcript-OCR relationships for a video
 * @param {string} videoId - The YouTube video ID
 * @returns {Promise<boolean>} - Whether the relationships were loaded successfully
 */
export async function loadTranscriptOcrRelationships(videoId) {
    try {
        console.log('Loading transcript-OCR relationships...');
        const response = await fetch(`/get_transcript_ocr_relationships/${videoId}`);
        const data = await readJsonResponse(response, 'Transcript-OCR relationships');
        
        if (data.success) {
            // Store relationships in state for later use
            state.transcriptOcrRelationships = data;
            
            // Store for quick lookups
            state.ocr_to_transcript = {};
            if (data.ocr_to_transcript_relationships) {
                data.ocr_to_transcript_relationships.forEach(rel => {
                    // Create key using scene_index and ocr_text
                    const key = `${rel.scene_index}_${rel.ocr_text}`;
                    state.ocr_to_transcript[key] = rel.matches;
                });
            }
            
            // Store transcript to OCR relationships for quick lookups
            state.transcript_to_ocr = {};
            if (data.transcript_to_ocr_relationships) {
                data.transcript_to_ocr_relationships.forEach(rel => {
                    state.transcript_to_ocr[rel.transcript_index] = rel.matches;
                });
            }
            console.log('Transcript-OCR relationships loaded successfully');
            return true;
        } else if (data.error === "Embeddings not computed yet") {
            // Trigger computation of embeddings
            console.log('Triggering computation of transcript-OCR relationships...');
            const computeResponse = await fetch(`/compute_embeddings/${videoId}`, {
                method: 'POST'
            });
            
            if (computeResponse.ok) {
                // Set up polling to check for completion
                const checkEmbeddings = async () => {
                    const statusResponse = await fetch(`/embeddings_status/${videoId}`);
                    const statusData = await readJsonResponse(statusResponse, 'Embedding status');
                    
                    if (statusData.success) {
                        if (statusData.status === 'completed') {
                            clearInterval(state.transcriptOcrStatusInterval);
                            state.transcriptOcrStatusInterval = null;
                            // Load the completed relationships
                            return loadTranscriptOcrRelationships(videoId);
                        } else if (statusData.status === 'failed') {
                            clearInterval(state.transcriptOcrStatusInterval);
                            state.transcriptOcrStatusInterval = null;
                            console.error('Failed to compute transcript-OCR relationships:', statusData.error);
                            return false;
                        }
                        // Still in progress, continue polling
                    } else {
                        clearInterval(state.transcriptOcrStatusInterval);
                        state.transcriptOcrStatusInterval = null;
                        console.error('Failed to check embedding status:', statusData.error);
                        return false;
                    }
                };
                
                // Check every 3 seconds
                if (state.transcriptOcrStatusInterval) {
                    clearInterval(state.transcriptOcrStatusInterval);
                }
                state.transcriptOcrStatusInterval = setInterval(
                    () => checkEmbeddings().catch((error) => {
                        if (state.transcriptOcrStatusInterval) {
                            clearInterval(state.transcriptOcrStatusInterval);
                            state.transcriptOcrStatusInterval = null;
                        }
                        console.error('Error checking embedding status:', error);
                    }),
                    3000
                );
                
                // Also check immediately
                await checkEmbeddings().catch((error) => {
                    if (state.transcriptOcrStatusInterval) {
                        clearInterval(state.transcriptOcrStatusInterval);
                        state.transcriptOcrStatusInterval = null;
                    }
                    console.error('Error checking embedding status:', error);
                });
                return true;
            } else {
                const errorData = await computeResponse.text();
                console.error(
                    'Failed to trigger transcript-OCR relationship computation:',
                    errorData.slice(0, 240)
                );
                return false;
            }
        } else {
            console.error('Failed to load transcript-OCR relationships:', data.error);
            return false;
        }
    } catch (error) {
        console.error('Error loading transcript-OCR relationships:', error);
        return false;
    }
} 