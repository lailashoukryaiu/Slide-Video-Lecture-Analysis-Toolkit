// main.js - Main application entry point

import { setupVideoPlayer, updateTimeMarker } from './video.js';
import { loadTranscript, updateTranscriptDisplay, updateActiveTranscript } from './transcript.js';
import { checkSceneDetection, updateScenes, toggleSceneMarkers, findSceneAtTime, downloadSceneScreenshots } from './scenes.js';
import { generateChapters, updateChapters, exportChapters } from './chapters.js';
import { setupSearch, setupSlideSearch, toggleTimestamps, toggleFuzzySearch } from './search.js';
import { fetchOcrResults, updateSlideContentDisplay } from './ocr.js';
import { setupTabs, showError, showLoading, showNotification, openSettingsModal, closeSettingsModal, saveSettings, generateWhisperTranscript } from './ui.js';
import { processVideo, checkYoloStatus, processVideoUpload, loadUploadedVideo, detectScenes, regenerateTranscript, uploadTranscriptFile, translateTranscript } from './api-module.js?v=translated-export-fix-20260922';
import { elements } from './elements.js';
import { initInteractiveLayer } from './interactive-layer.js';

// Global state
export const state = {
    currentTranscript: [],
    videoScenes: [],
    sceneDetectionInterval: null,
    sceneDetectionStartedAt: null,
    currentVideoId: null,
    youtubeTranscriptInterval: null,
    youtubeRetryTimer: null,
    summaryRetryTimer: null,
    summaryGenerationInProgress: false,
    currentDebugScene: null,
    showSceneMarkers: true,
    showTimestamps: true,
    fuzzySearchEnabled: true,
    searchResults: [],
    currentSearchIndex: -1,
    ocrResults: [],
    ocrProcessing: false,
    slideSearchResults: [],
    currentSlideSearchIndex: -1,
    currentTranscriptSource: 'youtube',  // Default transcript source
    currentTranslationLanguage: null,
    interactiveLayerActive: false,
    showTranscriptHighlighting: true,  // Default to showing transcript highlighting
    // Transcript-OCR relationship data
    transcriptOcrRelationships: null,
    ocr_to_transcript: {}, // Map for quick lookup: scene_index_ocrText -> transcript matches
    transcript_to_ocr: {}, // Map for quick lookup: transcript_index -> OCR matches
    sceneDetectionThreshold: Number(localStorage.getItem('sceneDetectionThreshold')) || 0.5
};

window.addEventListener('video-file-selected', (event) => {
    const file = event.detail;
    if (file) {
        processVideoUpload(file).catch((error) => {
            showError(`Error uploading video: ${error.message}`);
        });
    }
});

/**
 * Navigates to the next detected slide/scene
 */
function navigateToNextSlide() {
    const videoPlayer = elements.videoPlayer;
    if (!videoPlayer || !state.videoScenes || state.videoScenes.length === 0) {
        return;
    }

    const currentTime = videoPlayer.currentTime;
    let currentIndex = -1;
    
    console.log('Next slide navigation - Current time:', currentTime);
    console.log('Available scenes:', state.videoScenes.map((s, i) => `${i}: ${s.time_seconds}s`));
    
    // Find the current scene index - use the scene whose start time is closest to but not greater than current time
    // Add a small tolerance (0.1 seconds) to handle floating-point precision issues
    const tolerance = 0.1;
    
    for (let i = state.videoScenes.length - 1; i >= 0; i--) {
        const scene = state.videoScenes[i];
        
        // If current time is at or after this scene's start time (within tolerance)
        if (currentTime >= (scene.time_seconds - tolerance)) {
            currentIndex = i;
            console.log(`Found current scene at index ${i} (${scene.time_seconds}s)`);
            break;
        }
    }
    
    console.log('Current index:', currentIndex);
    
    // Navigate to next scene
    if (currentIndex >= 0 && currentIndex < state.videoScenes.length - 1) {
        const nextScene = state.videoScenes[currentIndex + 1];
        videoPlayer.currentTime = nextScene.time_seconds+0.1;
        console.log(`Navigating to next scene at index ${currentIndex + 1} (${nextScene.time_seconds}s)`);
        showNotification(`Navigated to slide ${currentIndex + 2}`, 'info');
    } else if (currentIndex === -1 && state.videoScenes.length > 0) {
        // If no current scene found, go to first scene
        videoPlayer.currentTime = state.videoScenes[0].time_seconds;
        console.log('No current scene found, going to first scene');
        showNotification('Navigated to first slide', 'info');
    } else {
        console.log('Already at last slide');
        showNotification('Already at last slide', 'warning');
    }
}

/**
 * Navigates to the previous detected slide/scene
 */
function navigateToPreviousSlide() {
    const videoPlayer = elements.videoPlayer;
    if (!videoPlayer || !state.videoScenes || state.videoScenes.length === 0) {
        return;
    }

    const currentTime = videoPlayer.currentTime;
    let currentIndex = -1;
    
    console.log('Previous slide navigation - Current time:', currentTime);
    console.log('Available scenes:', state.videoScenes.map((s, i) => `${i}: ${s.time_seconds}s`));
    
    // Find the current scene index - use the scene whose start time is closest to but not greater than current time
    // Add a small tolerance (0.1 seconds) to handle floating-point precision issues
    const tolerance = 0.1;
    
    for (let i = state.videoScenes.length - 1; i >= 0; i--) {
        const scene = state.videoScenes[i];
        
        // If current time is at or after this scene's start time (within tolerance)
        if (currentTime >= (scene.time_seconds - tolerance)) {
            currentIndex = i;
            console.log(`Found current scene at index ${i} (${scene.time_seconds}s)`);
            break;
        }
    }
    
    console.log('Current index:', currentIndex);
    
    // Navigate to previous scene
    if (currentIndex > 0) {
        const previousScene = state.videoScenes[currentIndex - 1];
        videoPlayer.currentTime = previousScene.time_seconds;
        console.log(`Navigating to previous scene at index ${currentIndex - 1} (${previousScene.time_seconds}s)`);
        showNotification(`Navigated to slide ${currentIndex}`, 'info');
    } else if (currentIndex === -1 && state.videoScenes.length > 0) {
        // If no current scene found, go to first scene
        videoPlayer.currentTime = state.videoScenes[0].time_seconds;
        console.log('No current scene found, going to first scene');
        showNotification('Navigated to first slide', 'info');
    } else {
        console.log('Already at first slide');
        showNotification('Already at first slide', 'warning');
    }
}

// Debounce variables to prevent double keypress
let lastKeyTime = 0;
let lastKey = null;

/**
 * Sets up keyboard controls for slide navigation
 */
function setupKeyboardControls() {
    document.addEventListener('keydown', (event) => {
        // Only handle keyboard shortcuts when not typing in input fields
        if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA' || event.target.isContentEditable) {
            return;
        }

        function navigateChapter(direction) {
            const player = elements.videoPlayer;
            const chapters = state.videoChapters || [];
            if (!player || !chapters.length) return;
            const times = chapters.map((chapter) => {
                const [minutes, seconds] = String(chapter.timestamp).split(':').map(Number);
                return minutes * 60 + seconds;
            });
            const currentIndex = times.reduce((index, time, candidate) => (
                time <= player.currentTime + 0.5 ? candidate : index
            ), -1);
            const targetIndex = direction > 0
                ? Math.min(times.length - 1, currentIndex + 1)
                : Math.max(0, currentIndex <= 0 ? 0 : currentIndex - 1);
            player.currentTime = times[targetIndex];
            player.play().catch(() => {});
        }

        const currentTime = Date.now();
        
        // Debounce: ignore if same key pressed within 200ms
        if (event.key === lastKey && (currentTime - lastKeyTime) < 200) {
            console.log(`Ignoring duplicate ${event.key} keypress (${currentTime - lastKeyTime}ms ago)`);
            event.preventDefault();
            return;
        }
        
        lastKey = event.key;
        lastKeyTime = currentTime;

        switch (event.key) {
            case 'l':
            case 'L':
                console.log('L pressed - navigating to next slide');
                event.preventDefault();
                navigateToNextSlide();
                break;
            case 'k':
            case 'K':
                console.log('K pressed - navigating to previous slide');
                event.preventDefault();
                navigateToPreviousSlide();
                break;
        }
    });
}

async function loadExportSuggestions() {
    if (!state.currentVideoId) return;
    try {
        const response = await fetch(
            `/export_suggestions/${encodeURIComponent(state.currentVideoId)}`
        );
        if (!response.ok) {
            throw new Error(`Could not load export suggestions (${response.status})`);
        }
        const suggestions = await response.json();
        if (!elements.exportTitle.value.trim()) {
            elements.exportTitle.value = suggestions.title_suggestion || '';
        }
        if (!elements.exportFilename.value.trim()) {
            elements.exportFilename.value = suggestions.filename_suggestion || '';
        }
        const titleSuggestion = document.getElementById('exportTitleSuggestion');
        const filenameSuggestion = document.getElementById('exportFilenameSuggestion');
        if (titleSuggestion && suggestions.title_suggestion) {
            titleSuggestion.textContent = `Suggestion: ${suggestions.title_suggestion}`;
        }
        if (filenameSuggestion && suggestions.filename_suggestion) {
            filenameSuggestion.textContent = `Suggestion: ${suggestions.filename_suggestion}`;
        }
    } catch (error) {
        showError(`Could not load export suggestions: ${error.message}`);
    }
}

// Initialize the application
function initApp() {
    if (elements.videoPlayer) {
        elements.videoPlayer.controls = true;
        elements.videoPlayer.setAttribute('controls', '');
    }
    const bind = (element, event, handler) => {
        if (element) element.addEventListener(event, handler);
    };

    // Wire the core video controls before optional analysis features initialize.
    bind(elements.loadVideoBtn, 'click', processVideo);
    bind(elements.detectScenesBtn, 'click', detectScenes);
    if (elements.downloadScreenshotsBtn) {
        bind(elements.downloadScreenshotsBtn, 'click', downloadSceneScreenshots);
    }

    // Non-essential startup checks must not block the rest of the interface.
    try {
        checkYoloStatus();
    } catch (error) {
        console.error('YOLO status check failed:', error);
    }
    
    // Set up tabs
    try {
        setupTabs();
    } catch (error) {
        console.error('Tab initialization failed:', error);
    }
    
    // Optional analysis features must not prevent the core controls or
    // dynamically-created video selectors from initializing.
    try {
        setupSearch();
        setupSlideSearch();
        setupKeyboardControls();
    } catch (error) {
        console.error('Search/navigation initialization failed:', error);
    }

    try {
        initInteractiveLayer();
        console.log('Interactive layer initialized');
    } catch (error) {
        console.error('Interactive layer initialization failed:', error);
    }
    
    // Add event listeners
    bind(elements.generateSummaryBtn, 'click', generateChapters);
    bind(elements.summaryOptionsBtn, 'click', () => {
        elements.summaryOptionsPanel.hidden = !elements.summaryOptionsPanel.hidden;
    });
    bind(elements.exportOptionsBtn, 'click', () => {
        elements.exportOptionsPanel.hidden = !elements.exportOptionsPanel.hidden;
        if (!elements.exportOptionsPanel.hidden) {
            void loadExportSuggestions();
            const suggestedTitle = (state.videoChapters || [])
                .map((chapter) => String(chapter.title || '').trim())
                .find((title) => title && !/^(part|chapter)\b/i.test(title));
            if (suggestedTitle && !elements.exportTitle.value.trim()) {
                elements.exportTitle.value = suggestedTitle;
            }
            if (suggestedTitle && !elements.exportFilename.value.trim()) {
                elements.exportFilename.value = suggestedTitle.replace(/[^A-Za-z0-9._-]+/g, '_');
            }
        }
    });
    bind(elements.exportChaptersBtn, 'click', () => {
        if (elements.exportOptionsPanel.hidden) {
            elements.exportOptionsPanel.hidden = false;
            return;
        }
        void exportChapters();
    });
    bind(elements.regenerateTranscriptBtn, 'click', regenerateTranscript);
    bind(elements.uploadTranscriptBtn, 'click', () => void uploadTranscriptFile().catch((error) => showError(error.message)));
    bind(elements.translateTranscriptBtn, 'click', () => void translateTranscript().catch((error) => showError(error.message)));
    bind(elements.previousChapterBtn, 'click', () => navigateChapter(-1));
    bind(elements.nextChapterBtn, 'click', () => navigateChapter(1));
    bind(elements.playbackSpeed, 'change', (event) => {
        elements.videoPlayer.playbackRate = Number(event.target.value);
    });
    bind(elements.transcriptOptionsBtn, 'click', () => {
        elements.transcriptGenerationControls.hidden = !elements.transcriptGenerationControls.hidden;
    });
    bind(elements.intervalExportToggle, 'change', (event) => {
        elements.intervalDuration.disabled = !event.target.checked;
    });
    bind(elements.sceneDetectionThreshold, 'input', (event) => {
        elements.sceneDetectionThresholdValue.textContent = event.target.value;
        state.sceneDetectionThreshold = Number(event.target.value);
        localStorage.setItem('sceneDetectionThreshold', event.target.value);
    });
    if (elements.sceneDetectionThreshold) {
        elements.sceneDetectionThreshold.value = state.sceneDetectionThreshold;
    }
    if (elements.sceneDetectionThresholdValue) {
        elements.sceneDetectionThresholdValue.textContent = state.sceneDetectionThreshold;
    }
    bind(elements.closeDetectionBtn, 'click', () => {
        if (elements.detectionOverlay) elements.detectionOverlay.style.display = 'none';
    });
    bind(elements.debugDetectionsBtn, 'click', () => {
        debugDetections(state.currentDebugScene, state.currentVideoId);
    });
    bind(elements.closeDebugBtn, 'click', () => {
        if (elements.debugOverlay) elements.debugOverlay.style.display = 'none';
    });
    bind(elements.sceneToggle, 'change', toggleSceneMarkers);
    bind(elements.timestampToggle, 'change', toggleTimestamps);
    bind(elements.fuzzySearchToggle, 'change', toggleFuzzySearch);
    
    // Settings modal
    bind(elements.settingsBtn, 'click', openSettingsModal);
    bind(elements.closeSettingsBtn, 'click', closeSettingsModal);
    bind(elements.saveSettingsBtn, 'click', saveSettings);
    
    // Whisper transcript generation
    bind(elements.generateWhisperBtn, 'click', () => {
        if (state.currentVideoId) {
            generateWhisperTranscript(state.currentVideoId);
        } else {
            showNotification('Please load a video first', 'error');
        }
    });
    
    // Listen for transcript loaded events
    document.addEventListener('transcriptLoaded', (event) => {
        loadTranscript(event.detail);
    });
    
    // Check for last URL in localStorage
    const lastUrl = localStorage.getItem('lastYoutubeUrl');
    if (lastUrl) {
        if (elements.youtubeUrl) elements.youtubeUrl.value = lastUrl;
    }
    
    // Fetch current transcript preference
    fetch('/get_transcript_preference')
        .then(response => response.json())
        .then(data => {
            state.currentTranscriptSource = data.preference || 'youtube';
        })
        .catch(error => {
            console.error('Error fetching transcript preference:', error);
        });
}

// Debug detections function
function debugDetections(scene, videoId) {
    if (!scene) return;
    
    const sceneIndex = scene.index || 0;
    
    fetch(`/scene_detections/${videoId}/${sceneIndex}`)
        .then(response => response.json())
        .then(data => {
            console.log("Debug detection data:", data);
            
            // Show debug info in the debug overlay
            const debugContent = elements.debugContent;
            debugContent.innerHTML = `
                <h3>Detection Debug Info</h3>
                <p>Video ID: ${videoId}</p>
                <p>Scene Index: ${sceneIndex}</p>
                <p>Has detections: ${data.has_detections}</p>
                <p>Detection count: ${data.detection_count}</p>
                <pre>${JSON.stringify(data, null, 2)}</pre>
            `;
            
            elements.debugOverlay.style.display = 'block';
        })
        .catch(error => {
            console.error("Error debugging detections:", error);
            showError("Error debugging detections. See console for details.");
        });
}

// Export functions and state for use in other modules
export {
    initApp,
    debugDetections,
    navigateToNextSlide,
    navigateToPreviousSlide
}; 