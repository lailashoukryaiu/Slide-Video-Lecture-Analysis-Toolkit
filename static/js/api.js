// api.js - API interaction functionality

import { elements } from './elements.js';
import { state } from './main.js';
import { extractVideoId } from './utils.js';
import { showError, showErrorWithActions, showLoading, showNotification } from './ui.js';
import { setupVideoPlayer } from './video.js';
import { loadTranscript } from './transcript.js';
import { updateChapters, clearChapterMarkers } from './chapters.js';
import { checkSceneDetection } from './scenes.js';
import { fetchOcrResults } from './ocr.js';

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
            elements.exportChaptersBtn.disabled = true;
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

/**
 * Resets all video-related states when loading a new video
 */
function resetVideoStates() {
    if (state.youtubeTranscriptInterval) {
        clearInterval(state.youtubeTranscriptInterval);
        state.youtubeTranscriptInterval = null;
    }

    // Reset SSE connection if it exists
    if (window.sseConnection) {
        console.log('Closing existing SSE connection');
        window.sseConnection.close();
        window.sseConnection = null;
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
    clearChapterMarkers();
    videoPlayer.pause();
    videoPlayer.removeAttribute('src');
    videoPlayer.load();

    // Clear existing transcript and chapters
    elements.transcriptContainer.innerHTML = '';
    elements.chaptersContainer.innerHTML = '<p>Click "Generate Summary" to create chapter markers from the transcript.</p>';
    
    // Reset chapter state
    state.videoChapters = [];
    
    // Reset scene detection state
    if (state.sceneDetectionInterval) {
        clearInterval(state.sceneDetectionInterval);
        state.sceneDetectionInterval = null;
    }
    state.videoScenes = [];
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

        const response = await fetch(`/download/${videoId}`);
        const data = await readJsonResponse(response, 'YouTube video download');

        if (!data.success) {
            throw new Error(data.error || 'The server could not download this YouTube video.');
        }

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
        state.currentVideoId = data.video_id || videoId;
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

        const response = await fetch('/upload_video', {
            method: 'POST',
            body: formData
        });

        elements.loadingIndicator.querySelector('p').textContent =
            'Upload complete. Preparing video preview...';
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error);
        }

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

        // Store video ID
        state.currentVideoId = data.video_id;
        elements.detectScenesBtn.disabled = false;
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
            // Show a message in the transcript container
            elements.transcriptContainer.innerHTML = `
                <div class="transcript-processing">
                    <i class="fas fa-spinner fa-spin"></i>
                    <p>Generating transcript with Whisper AI. This may take several minutes...</p>
                </div>
            `;
            elements.loadingIndicator.querySelector('p').textContent =
                'Video ready. Generating transcript in the background...';
            
            // Start polling for transcript completion
            const checkWhisperTranscript = async () => {
                try {
                    const whisperResponse = await fetch(`/whisper_transcript_status/${data.video_id}`);
                    const whisperData = await whisperResponse.json();
                    
                    if (whisperData.status === 'complete') {
                        clearInterval(whisperCheckInterval);
                        
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
                        clearInterval(whisperCheckInterval);
                        elements.transcriptContainer.innerHTML = `
                            <div class="transcript-processing transcript-error">
                                <i class="fas fa-info-circle"></i>
                                <p>Transcript generation stopped: ${whisperData.error || 'No recognizable speech was found in this video.'}</p>
                                <p>You can still detect slides, but transcript-based summaries are unavailable.</p>
                            </div>
                        `;
                        elements.generateSummaryBtn.disabled = true;
                        elements.exportChaptersBtn.disabled = true;
                        showError(`Error generating transcript: ${whisperData.error}`);
                    } else if (whisperData.status === 'in_progress' && whisperData.progress) {
                        // Update progress indicator
                        elements.transcriptContainer.innerHTML = `
                            <div class="transcript-processing">
                                <i class="fas fa-spinner fa-spin"></i>
                                <div class="progress-container">
                                    <div class="progress-bar">
                                        <div class="progress-fill" style="width: ${whisperData.progress}%"></div>
                                    </div>
                                    <div class="progress-text">${Math.round(whisperData.progress)}%</div>
                                </div>
                                <p>Generating transcript with Whisper AI...</p>
                            </div>
                        `;
                    }
                } catch (error) {
                    console.error('Error checking Whisper transcript status:', error);
                }
            };
            
            // Check immediately and then every 5 seconds
            checkWhisperTranscript();
            const whisperCheckInterval = setInterval(checkWhisperTranscript, 5000);
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
        elements.scenesContainer.innerHTML = '<p>Click "Detect Slides" to analyze this video.</p>';

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
    const selectedThreshold = Number(elements.sceneDetectionThreshold.value);
    if (!Number.isFinite(selectedThreshold)) {
        showError('Please select a valid slide-change sensitivity.');
        return;
    }
    state.sceneDetectionThreshold = selectedThreshold;
    localStorage.setItem('sceneDetectionThreshold', String(selectedThreshold));
    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Detecting Slides...';
    state.sceneDetectionStartedAt = Date.now();
    elements.scenesContainer.innerHTML = '<p>Detecting scene changes...</p><p class="scene-progress-status"><i class="fas fa-spinner fa-spin"></i> Analysis is running...</p>';
    try {
        const response = await fetch(`/detect_scenes/${videoId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                adaptive_threshold: selectedThreshold,
                mode: elements.sceneDetectionMode.value
            })
        });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.detail || data.error || 'Could not start scene detection');
        elements.scenesContainer.insertAdjacentHTML(
            'afterbegin',
            `<p class="scene-progress-status">Sensitivity used: ${selectedThreshold.toFixed(1)}</p>`
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
        const data = await response.json();
        
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
                    const statusData = await statusResponse.json();
                    
                    if (statusData.success) {
                        if (statusData.status === 'completed') {
                            clearInterval(checkInterval);
                            // Load the completed relationships
                            return loadTranscriptOcrRelationships(videoId);
                        } else if (statusData.status === 'failed') {
                            clearInterval(checkInterval);
                            console.error('Failed to compute transcript-OCR relationships:', statusData.error);
                            return false;
                        }
                        // Still in progress, continue polling
                    } else {
                        clearInterval(checkInterval);
                        console.error('Failed to check embedding status:', statusData.error);
                        return false;
                    }
                };
                
                // Check every 3 seconds
                const checkInterval = setInterval(checkEmbeddings, 3000);
                
                // Also check immediately
                await checkEmbeddings();
                return true;
            } else {
                console.error('Failed to trigger transcript-OCR relationship computation');
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