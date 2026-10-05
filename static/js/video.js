// video.js - Video player functionality

import { elements } from './elements.js';
import { formatTime } from './utils.js';
import { state } from './main.js';
import { updateActiveTranscript } from './transcript.js';
import { navigationParts, activePart, conciseTitle } from './chapter-navigation.js';
import { scrollWithinContainer } from './scroll-utils.js';

export function updateChapterCaptions() {
    const parts = navigationParts(state.videoChapters || [], state.videoScenes || [], state.chapterGrouping);
    const caption = document.getElementById('currentChapterTitle');
    if (caption) {
        const text = activePart(parts, elements.videoPlayer.currentTime)?.title
            || 'No chapter or slide title available at this time.';
        caption.title = text;
        if (caption.textContent !== conciseTitle(text)) caption.textContent = conciseTitle(text);
    }
    document.querySelectorAll('.timeline-item').forEach((item) => {
        const image = item.querySelector('.timeline-thumbnail');
        if (!image) return;
        let title = item.querySelector('.chapter-caption');
        if (!title) {
            title = document.createElement('div');
            title.className = 'chapter-caption';
            item.appendChild(title);
        }
        const text = activePart(parts, Number(image.dataset.time))?.title || 'No chapter title available.';
        // The numbered badge already identifies the slide, so show only the topic beneath it.
        const shown = conciseTitle(text.replace(/^\d+:\s*/, '').replace(/^\d+$/, ''));
        title.title = text;
        if (title.textContent !== shown) title.textContent = shown;
    });
}

/**
 * Sets up the video progress bar
 * @param {HTMLVideoElement} videoPlayer - The video player element
 */
export function setupVideoPlayer(videoPlayer) {
    const progressBar = elements.videoProgress;
    const hoverTime = elements.progressHoverTime;

    if (videoPlayer._navigationHandlers) {
        videoPlayer.removeEventListener('timeupdate', videoPlayer._navigationHandlers.timeupdate);
        videoPlayer.removeEventListener('timeupdate', videoPlayer._navigationHandlers.timeline);
        videoPlayer.removeEventListener('timeupdate', videoPlayer._navigationHandlers.transcript);
    }
    if (progressBar._navigationHandlers) {
        progressBar.removeEventListener('mousemove', progressBar._navigationHandlers.mousemove);
        progressBar.removeEventListener('mouseleave', progressBar._navigationHandlers.mouseleave);
        progressBar.removeEventListener('click', progressBar._navigationHandlers.click);
    }

    const navigationHandlers = {
        timeupdate: () => {
            updateTimeMarker(videoPlayer);
            updateChapterCaptions();
        },
        timeline: updateTimelineHighlight,
        transcript: updateActiveTranscript
    };
    videoPlayer._navigationHandlers = navigationHandlers;

    const progressHandlers = {
        mousemove: (e) => {
        const rect = progressBar.getBoundingClientRect();
        const pos = (e.clientX - rect.left) / rect.width;
        const time = pos * videoPlayer.duration;
        hoverTime.textContent = formatTime(time);
        hoverTime.style.left = `${pos * 100}%`;
        hoverTime.style.display = 'block';
        },

        mouseleave: () => {
            hoverTime.style.display = 'none';
        },

        click: (e) => {
            const rect = progressBar.getBoundingClientRect();
            const pos = (e.clientX - rect.left) / rect.width;
            videoPlayer.currentTime = pos * videoPlayer.duration;
        }
    };
    progressBar._navigationHandlers = progressHandlers;
    progressBar.addEventListener('mousemove', progressHandlers.mousemove);
    progressBar.addEventListener('mouseleave', progressHandlers.mouseleave);
    progressBar.addEventListener('click', progressHandlers.click);

    // Update time marker on timeupdate
    videoPlayer.addEventListener('timeupdate', navigationHandlers.timeupdate);
    
    // Add timeupdate listener to highlight current thumbnail
    videoPlayer.addEventListener('timeupdate', navigationHandlers.timeline);
    
    // Add listener for transcript updates
    videoPlayer.addEventListener('timeupdate', navigationHandlers.transcript);
    updateChapterCaptions();
}

/**
 * Updates the time marker position
 * @param {HTMLVideoElement} videoPlayer - The video player element
 */
export function updateTimeMarker(videoPlayer) {
    const timeMarker = elements.timeMarker;
    const progress = (videoPlayer.currentTime / videoPlayer.duration) * 100;
    timeMarker.style.left = `${progress}%`;
}

/**
 * Updates the highlighted thumbnail in the timeline
 */
export function updateTimelineHighlight() {
    const videoPlayer = elements.videoPlayer;
    const currentTime = videoPlayer.currentTime;
    const thumbnails = document.querySelectorAll('.timeline-thumbnail');
    
    // Find the current scene in all scenes
    let currentThumbnailIndex = -1;
    
    // Find which thumbnail corresponds to the current time
    for (let i = 0; i < state.videoScenes.length; i++) {
        const nextIndex = i + 1;
        if (nextIndex < state.videoScenes.length) {
            if (currentTime >= state.videoScenes[i].time_seconds && 
                currentTime < state.videoScenes[nextIndex].time_seconds) {
                currentThumbnailIndex = i;
                break;
            }
        } else if (currentTime >= state.videoScenes[i].time_seconds) {
            // Last thumbnail
            currentThumbnailIndex = i;
        }
    }
    
    // Update thumbnail highlighting
    thumbnails.forEach((thumbnail, index) => {
        if (index === currentThumbnailIndex) {
            thumbnail.classList.add('active');
            
            // Scroll the active thumbnail into view if it's not visible
            const timelineContainer = elements.thumbnailTimeline;
            const thumbnailItem = thumbnail.parentElement;
            scrollWithinContainer(timelineContainer, thumbnailItem, 'horizontal');
        } else {
            thumbnail.classList.remove('active');
        }
    });
} 