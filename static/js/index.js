// index.js - Entry point for non-module environments
// This file is used to import all modules and expose them to the global scope

import { initApp } from './main.js?v=f08fe2a';
import { elements } from './elements.js';
import { loadUploadedVideo } from './api.js';

// Example videos that have been pre-processed
const exampleVideos = [
    {
        title: "Intro to Large Language Models",
        url: "https://www.youtube.com/watch?v=zjkBMFhNj_g",
        description: "Andrej Karpathy's lecture on Large Language Models"
    },
    // {
    //     title: "Stanford CS224N: NLP with Deep Learning",
    //     url: "https://www.youtube.com/watch?v=8rXD5-xhemo",
    //     description: "Natural Language Processing lecture by Professor Christopher Manning"
    // },
    // Add more example videos here
];

// Initialize example videos dropdown
function initExampleVideos() {
    const inputSection = document.querySelector('.input-section');
    if (!inputSection) {
        console.error('Cannot initialize video selectors: .input-section is missing');
        return;
    }

    const container = document.createElement('div');
    container.className = 'example-videos-container';
    container.innerHTML = `
        <select id="exampleVideos" class="example-videos-select" title="Select an example video">
            <option value="">Examples ▾</option>
            ${exampleVideos.map(video => `
                <option value="${video.url}">${video.title}</option>
            `).join('')}
        </select>
    `;

    // Insert the container at the start of the input section
    inputSection.insertBefore(container, inputSection.firstChild);
    initUploadedVideos(inputSection);

    // Add event listener for selection change
    const select = container.querySelector('#exampleVideos');
    
    select.addEventListener('change', (e) => {
        const selectedVideo = exampleVideos.find(v => v.url === e.target.value);
        if (selectedVideo) {
            elements.youtubeUrl.value = selectedVideo.url;
            // Show a notification with the video description
            const notification = document.createElement('div');
            notification.className = 'notification info';
            notification.textContent = selectedVideo.description;
            document.body.appendChild(notification);
            notification.style.opacity = '1';
            setTimeout(() => {
                notification.style.opacity = '0';
                setTimeout(() => notification.remove(), 300);
            }, 3000);
        }

    });
}

async function initUploadedVideos(inputSection) {
    const container = document.createElement('div');
    container.className = 'example-videos-container';
    container.innerHTML = '<select id="uploadedVideos" class="example-videos-select" title="Select a previously uploaded video"><option value="">Saved uploads ▾</option></select>';
    inputSection.insertBefore(container, inputSection.firstChild);
    const select = container.querySelector('#uploadedVideos');
    try {
        const response = await fetch('/uploaded_videos');
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'Could not list saved uploads');
        data.videos.forEach(video => {
            const option = document.createElement('option');
            option.value = video.video_id;
            option.textContent = `${video.filename} (${(video.size_bytes / 1048576).toFixed(1)} MB)`;
            select.appendChild(option);
        });
        select.addEventListener('change', () => {
            if (select.value) loadUploadedVideo(select.value);
        });
    } catch (error) {
        console.error('Error loading saved uploads:', error);
    }
}

// Initialize the application when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    try {
        initApp();
    } catch (error) {
        console.error('Application initialization failed:', error);
    }
    try {
        initExampleVideos();
    } catch (error) {
        console.error('Video selector initialization failed:', error);
    }
});