// chapters.js - Chapter generation functionality

import { elements } from './elements.js';
import { state } from './main.js';
import { showError, showErrorWithActions, showNotification } from './ui.js';

let chapterMarkerGeneration = 0;

export function clearChapterMarkers() {
    chapterMarkerGeneration += 1;
    const progressContainer = elements.videoProgress;
    if (!progressContainer) return;
    progressContainer.querySelectorAll('.chapter-marker, .chapter-segment').forEach((marker) => marker.remove());
}

export async function exportChapters() {
    const button = elements.exportChaptersBtn;
    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Exporting...';
    showError('');
    try {
        const useIntervals = elements.intervalExportToggle?.checked;
        const intervalMinutes = Number(elements.intervalDuration?.value);
        if (useIntervals && (!Number.isFinite(intervalMinutes) || intervalMinutes <= 0)) {
            throw new Error('Enter an interval duration greater than zero minutes.');
        }
        const response = await fetch(`/export_chapters/${state.currentVideoId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(useIntervals ? { interval_minutes: intervalMinutes } : {})
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Chapter export failed');
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${state.currentVideoId}_${useIntervals ? 'intervals' : 'chapters'}.zip`;
        link.click();
        URL.revokeObjectURL(url);
    } catch (error) {
        showError(`Error exporting chapters: ${error.message}`);
    } finally {
        button.disabled = false;
        button.innerHTML = '<i class="fas fa-download"></i> Export Chapters';
    }
}

/**
 * Generates chapter markers from the transcript
 */
export async function generateChapters() {
    const generateSummaryBtn = elements.generateSummaryBtn;
    
    generateSummaryBtn.disabled = true;
    generateSummaryBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating...';
    setSummaryStatus('generating', '<i class="fas fa-spinner fa-spin"></i> Generating summary and chapters. This may take a minute...');
    showError('');

    try {
        const response = await fetch('/generate_summary', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                transcript: state.currentTranscript,
                video_id: state.currentVideoId
            })
        });

        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error);
        }

        // Store the chapters in state
        state.videoChapters = data.chapters;
        if (state.summaryRetryTimer) {
            clearInterval(state.summaryRetryTimer);
            state.summaryRetryTimer = null;
        }
        elements.exportChaptersBtn.disabled = false;
        
        updateChapters(data.chapters);
        setSummaryStatus('complete', '<i class="fas fa-check-circle"></i> Summary and chapters ready.');
    } catch (error) {
        showError(`Error generating summary: ${error.message}`);
        const isModelError = /gemini|model|429|404|quota|overload|unavailable|high demand/i.test(error.message);
        const statusMessage = isModelError
            ? '<i class="fas fa-exclamation-circle"></i> Gemini is currently unavailable or busy. Your existing chapters were kept. Please retry later.'
            : '<i class="fas fa-exclamation-circle"></i> Summary generation failed. Your existing chapters were kept. You can retry without reloading the video.';
        const status = setSummaryStatus(
            'error',
            statusMessage
        );
        const retryButton = document.createElement('button');
        retryButton.type = 'button';
        retryButton.className = 'btn btn-secondary summary-retry-btn';
        retryButton.innerHTML = '<i class="fas fa-redo"></i> Retry';
        retryButton.addEventListener('click', generateChapters);
        status.appendChild(retryButton);
        if (isModelError) {
            const autoRetryButton = document.createElement('button');
            autoRetryButton.type = 'button';
            autoRetryButton.className = 'btn btn-secondary summary-retry-btn';
            autoRetryButton.innerHTML = '<i class="fas fa-sync"></i> Retry automatically';
            autoRetryButton.addEventListener('click', () => {
                if (state.summaryRetryTimer) return;
                state.summaryRetryTimer = setInterval(generateChapters, 15000);
                showNotification('Automatic Gemini retry enabled. Use Stop automatic retry to stop it.', 'info');
            });
            status.appendChild(autoRetryButton);
            const stopRetryButton = document.createElement('button');
            stopRetryButton.type = 'button';
            stopRetryButton.className = 'btn btn-secondary summary-retry-btn';
            stopRetryButton.innerHTML = '<i class="fas fa-stop"></i> Stop automatic retry';
            stopRetryButton.addEventListener('click', () => {
                if (state.summaryRetryTimer) {
                    clearInterval(state.summaryRetryTimer);
                    state.summaryRetryTimer = null;
                }
                showNotification('Automatic Gemini retry stopped.', 'info');
            });
            status.appendChild(stopRetryButton);
    } finally {
        generateSummaryBtn.disabled = false;
        generateSummaryBtn.innerHTML = '<i class="fas fa-magic"></i> Generate Summary';
    }
}

function setSummaryStatus(state, message) {
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
    return status;
}

/**
 * Updates the chapters display with new chapter data
 * @param {Array} chapters - The chapter data
 */
export function updateChapters(chapters) {
    const chaptersContainer = elements.chaptersContainer;
    const status = elements.summaryStatus;
    chaptersContainer.querySelectorAll(':scope > .chapter-item, :scope > p').forEach(element => element.remove());
    if (status) {
        status.hidden = false;
    }
    
    if (!chapters || chapters.length === 0) {
        chaptersContainer.innerHTML = '<p>No chapters available.</p>';
        setSummaryStatus('complete', '<i class="fas fa-info-circle"></i> No chapters are available.');
        return;
    }

    // Update the list of chapters in the sidebar
    chapters.forEach(chapter => {
        const div = document.createElement('div');
        div.className = 'chapter-item';
        div.innerHTML = `
            <span class="chapter-timestamp">${chapter.timestamp}</span>
            <span>${chapter.title}</span>
        `;
        div.onclick = () => {
            const [minutes, seconds] = chapter.timestamp.split(':').map(Number);
            const time = minutes * 60 + seconds;
            elements.videoPlayer.currentTime = time;
            elements.videoPlayer.play();
        };
        chaptersContainer.appendChild(div);
    });
    
    // Store the chapters in state
    state.videoChapters = chapters;
    elements.exportChaptersBtn.disabled = false;
    setSummaryStatus('complete', '<i class="fas fa-check-circle"></i> Summary and chapters ready.');
    
    // Add chapter markers to the timeline
    addChapterMarkersToTimeline(chapters);
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
        const [currMinutes, currSeconds] = chapters[i].timestamp.split(':').map(Number);
        const currTimeInSeconds = currMinutes * 60 + currSeconds;
        
        // Determine the end time of this chapter (start of next chapter or end of video)
        let endTimeInSeconds;
        if (i < chapters.length - 1) {
            const [nextMinutes, nextSeconds] = chapters[i + 1].timestamp.split(':').map(Number);
            endTimeInSeconds = nextMinutes * 60 + nextSeconds;
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
                videoPlayer.play();
            }
        });
        
        // Add to container
        progressContainer.appendChild(segment);
    }
    
    // Add each chapter marker to the timeline
    chapters.forEach((chapter, index) => {
        // Convert timestamp to seconds
        const [minutes, seconds] = chapter.timestamp.split(':').map(Number);
        const timeInSeconds = minutes * 60 + seconds;
        
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
            videoPlayer.play();
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