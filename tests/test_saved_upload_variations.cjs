const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = name => fs.readFileSync(path.join(__dirname, '..', 'static', 'js', name), 'utf8')
    .replace(/^import .*;$/gm, '').replace(/^export /gm, '');

(async () => {
    const template = fs.readFileSync(path.join(__dirname, '..', 'templates', 'index.html'), 'utf8');
    const versionContainer = {};
    const brand = {};
    const projectHistory = {};
    const extraOptions = {};
    const children = [projectHistory, versionContainer, brand, extraOptions];
    Object.defineProperty(versionContainer, 'nextSibling', {get: () => children[children.indexOf(versionContainer) + 1]});
    Object.defineProperty(projectHistory, 'nextSibling', {get: () => children[children.indexOf(projectHistory) + 1]});
    const inputSection = {
        get firstChild() { return children[0]; },
        querySelector: selector => selector === '#extraOptionsMenu' ? extraOptions : null,
        insertBefore(child, reference) { children.splice(children.indexOf(reference), 0, child); },
    };
    const layout = vm.createContext({
        console,
        document: {
            addEventListener() {},
            querySelector: () => inputSection,
            getElementById: () => ({parentElement: versionContainer}),
            createElement: () => ({querySelector: () => ({addEventListener() {}})}),
        },
        fetch: async () => ({json: async () => ({success: true, videos: []})}),
    });
    vm.runInContext(read('index.js'), layout);
    layout.initExampleVideos();
    assert.equal(children[0], projectHistory, 'Project Back/Next stays first');
    assert.match(children[1].innerHTML, /id="uploadedVideos"/, 'Saved uploads follows project history');
    assert.equal(children[2], versionContainer, 'Video version immediately follows Saved uploads');
    assert.match(children[3].innerHTML, /id="exampleVideos"/, 'Examples follows Video version');
    const screenshotButton = template.match(/<button id="takeScreenshotBtn"[\s\S]*?<\/button>/)[0];
    assert.match(screenshotButton, /aria-label="Take screenshot"/);
    assert.match(screenshotButton, /fa-camera/);
    assert.equal(screenshotButton.replace(/<[^>]*>/g, '').trim(), '', 'Screenshot button is icon-only');
    assert.equal(template.match(/id="customClipBtn"/g).length, 1);
    const downloadsMenu = template.match(/<details class="action-menu download-menu">[\s\S]*?<\/details>/)[0];
    assert.match(downloadsMenu, /aria-label="Chapter downloads"/);
    for (const id of ['exportChaptersBtn', 'customClipBtn']) {
        assert(downloadsMenu.includes(`id="${id}"`), `${id} belongs inside Downloads`);
    }
    assert(!downloadsMenu.includes('selected-video-controls'),
        'Download commands share direct menu rows, not differently sized nested controls');
    assert(!downloadsMenu.includes('selectedClipsStatus'), 'Encoding progress stays visible outside collapsed menus');
    const selectionBar = template.match(/<div id="clipSelectionActions"[\s\S]*?<\/div>/)[0];
    assert.match(selectionBar, /\bhidden>/);
    assert.match(selectionBar, /aria-label="Download selected clips"/);
    assert.match(selectionBar, /id="mergeSelectedClips"/);
    assert(!downloadsMenu.includes('downloadSelectedClipsBtn'), 'Selection actions are not buried in Downloads');
    assert(!read('action-menus.js').includes("'Chapter actions'"), 'Chapter tools stay in the unified options panel');
    assert.match(template, /id="summaryOptionsPanel"[\s\S]*id="edit-chapters-btn"[\s\S]*id="generateVideoSummaryBtn"/);

    const select = {
        options: [], value: '', listeners: {},
        appendChild(option) { this.options.push(option); },
        addEventListener(name, callback) { this.listeners[name] = callback; },
        get selectedOptions() { return this.options.filter(option => option.value === this.value); },
    };
    const opened = [];
    const versions = {options: [], dataset: {},
        replaceChildren() { this.options = []; },
        appendChild(option) { this.options.push(option); }};
    const versionState = {currentVideoId: null};
    const document = {
        addEventListener() {},
        getElementById: id => id === 'uploadedVideos' ? select : id === 'videoVersions' ? versions : null,
        querySelector: () => null,
        createElement: tag => tag === 'div' ? {querySelector: () => select} : {dataset: {}},
    };
    const context = vm.createContext({
        document, console, state: versionState,
        loadUploadedVideo: (...args) => opened.push(args),
        fetch: async () => ({json: async () => ({success: true, videos: [{
            video_id: 'parent', filename: 'Original.mp4', size_bytes: 1024,
            caption_variations: [{id: 'copy', filename: 'English.mp4'}],
        }]})}),
    });
    vm.runInContext(read('caption-export.js') + '\n' + read('index.js'), context);
    await context.initUploadedVideos({insertBefore() {}, querySelector: () => null});
    assert.equal(select.options.length, 1, 'Only the original upload appears in the main selector');
    select.value = 'parent';
    select.listeners.change();
    assert.deepEqual(opened[0], ['parent']);
    versionState.currentVideoId = 'parent';
    context.refreshVideoVersions();
    assert.equal(versions.options.length, 2);
    assert.equal(versions.options[0].textContent, 'Original video');
    assert.equal(versions.options[1].value, 'copy');
    versionState.currentVideoId = 'other';
    context.refreshVideoVersions();
    assert.equal(versions.options.length, 1, 'Copies of another upload never appear');
    versionState.currentVideoId = 'parent';
    context.removeSavedCaptionVariation('copy');
    assert.equal(versions.options.length, 1, 'Deleted exports disappear from Video version');

    const api = read('api-module.js');
    const load = api.slice(api.indexOf('async function loadUploadedVideo('),
        api.indexOf('// Mirrored by PRESET_LIMITS'));
    const state = {};
    const player = {
        set src(value) { this.source = value; queueMicrotask(() => this.onloadedmetadata()); },
    };
    const copies = [];
    const errors = [];
    let copyWorks = true;
    const loader = vm.createContext({
        state, document, setTimeout, clearTimeout, Array,
        elements: {
            loadingIndicator: {querySelector: () => ({})}, youtubeUrl: {},
            videoPlayer: player, detectScenesBtn: {}, exportChaptersBtn: {},
            scenesContainer: {}, transcriptContainer: {},
        },
        showError: error => { if (error) errors.push(error); }, showLoading() {},
        resetVideoStates() { state.currentVideoId = null; },
        fetch: async () => ({ok: true, json: async () => ({
            success: true, video_id: 'parent', video_url: '/video/parent',
        })}),
        readJsonResponse: response => response.json(),
        setupVideoPlayer() {},
        loadProjectCaptionTracks: async () => {},
        refreshTranscriptVersions: async () => {},
        refreshVideoVersions() {},
        useVideoCopy: async job => { copies.push(job.id); return copyWorks; },
        announceProjectOpened() {},
    });
    vm.runInContext(load, loader);
    await loader.loadUploadedVideo('parent', 'copy');
    assert.equal(state.currentVideoId, 'parent', 'The original analysis remains the active project');
    assert.deepEqual(copies, ['copy'], 'Reopening a variation explicitly restores the saved media copy');
    assert.equal(errors.length, 0);
    copyWorks = false;
    await loader.loadUploadedVideo('parent', 'copy');
    assert.match(errors[0], /selected captioned variation/, 'Unavailable copies must not silently fall back');
    console.log('PASS: upload-specific Video version selector keeps Saved uploads clean and reopens caption copies');
})().catch(error => { console.error(error); process.exitCode = 1; });
