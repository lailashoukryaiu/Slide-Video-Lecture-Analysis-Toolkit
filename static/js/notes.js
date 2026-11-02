// notes.js - Notes tab: personal notes and an AI summary for the open lecture.
// Talks to the /api/notes endpoints in notes_routes.py.

import { state } from './main.js';

const API = '/api/notes';

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else node.setAttribute(key, value);
    }
    for (const child of children) node.append(child);
    return node;
}

function setStatus(text, kind = '') {
    const node = $('notesStatus');
    if (!node) return;
    node.textContent = text;
    node.className = `notes-status ${kind}`.trim();
}

async function request(url, options = {}) {
    const res = await fetch(url, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`);
    }
    return data;
}

function renderSummary(text) {
    const box = $('notesSummary');
    box.replaceChildren();
    if (!text) {
        box.append(el('p', { class: 'notes-muted', text: 'No summary yet. Click "Make summary".' }));
        return;
    }
    let list = null;
    text.split('\n').map((line) => line.trim()).filter(Boolean).forEach((line) => {
        if (line.startsWith('- ')) {
            if (!list) {
                list = el('ul');
                box.append(list);
            }
            list.append(el('li', { text: line.slice(2) }));
        } else {
            list = null;
            box.append(el('p', { text: line }));
        }
    });
}

async function loadNotes() {
    const videoId = state.currentVideoId;
    if (!videoId) {
        setStatus('Open a video first.', 'error');
        return;
    }
    try {
        const data = await request(`${API}/${encodeURIComponent(videoId)}`);
        $('notesText').value = data.notes || '';
        renderSummary(data.summary || '');
        setStatus(data.updated_at ? `Last saved ${new Date(data.updated_at).toLocaleString()}` : '');
    } catch (err) {
        setStatus(err.message, 'error');
    }
}

async function saveNotes() {
    const videoId = state.currentVideoId;
    if (!videoId) {
        setStatus('Open a video first.', 'error');
        return;
    }
    try {
        const data = await request(`${API}/${encodeURIComponent(videoId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes: $('notesText').value }),
        });
        setStatus(`Saved ${new Date(data.updated_at).toLocaleTimeString()}`);
    } catch (err) {
        setStatus(err.message, 'error');
    }
}

async function makeSummary() {
    const videoId = state.currentVideoId;
    if (!videoId) {
        setStatus('Open a video first.', 'error');
        return;
    }
    const btn = $('makeSummaryBtn');
    btn.disabled = true;
    setStatus('Making summary... this can take a little while.');
    try {
        const data = await request(`${API}/${encodeURIComponent(videoId)}/summary`, { method: 'POST' });
        renderSummary(data.summary);
        setStatus('Summary ready.');
    } catch (err) {
        setStatus(err.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

function addStyles() {
    const style = document.createElement('style');
    style.textContent = `
.notes-controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
#notesText { width: 100%; box-sizing: border-box; min-height: 160px; padding: 8px; font-family: inherit; }
#notesSummary { margin-top: 6px; line-height: 1.6; }
#notesSummary ul { padding-left: 20px; }
.notes-muted { opacity: 0.7; }
.notes-status { font-size: 13px; opacity: 0.85; }
.notes-status.error { color: #ff6b6b; }
`;
    document.head.append(style);
}

function init() {
    if (!$('notesText')) return;
    addStyles();
    $('saveNotesBtn').addEventListener('click', saveNotes);
    $('makeSummaryBtn').addEventListener('click', makeSummary);
    document.querySelector('.tab-btn[data-tab="notes"]')?.addEventListener('click', loadNotes);
}

init();
