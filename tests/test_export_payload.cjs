const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'chapters.js'), 'utf8')
    .replace(/^import .*;\r?$/gm, '')
    .replace(/^export /gm, '');
const groupingOptions = ['topic', 'slides', 'combined'].map(value => ({ value, disabled: false }));
const grouping = {
    value: 'combined',
    options: groupingOptions,
    get selectedOptions() { return this.options.filter(option => option.value === this.value); },
};
const elements = {
    exportChaptersBtn: {},
    intervalExportToggle: { checked: false },
    intervalDuration: { value: 3 },
    chapterGrouping: grouping,
    exportImages: { checked: false },
    exportTranscripts: { checked: false },
    exportClips: { checked: false },
    exportWord: { checked: false },
    exportPdf: { checked: false },
    exportWebpage: { checked: true },
    exportOutline: { checked: false },
    exportScorm: { checked: false },
    exportAvailabilityNote: {},
};
const state = {
    currentVideoId: 'lecture',
    currentTranscript: [{ text: 'A full sentence.', start: 0 }],
    videoChapters: [],
    videoScenes: [],
};
let payload;
const context = vm.createContext({
    elements, state, console,
    showError: () => {}, showErrorWithActions: () => {}, showNotification: () => {},
    saveBlobToUserLocation: async () => {},
    fetch: async (url, options) => {
        assert.equal(url, '/export_chapters/lecture');
        payload = JSON.parse(options.body);
        return {
            ok: true, blob: async () => ({}),
            headers: { get: () => 'attachment; filename="lecture.zip"' },
        };
    },
});
vm.runInContext(source, context);
context.updateExportAvailability();
assert.equal(elements.intervalExportToggle.checked, false, 'Do not force fixed intervals');
assert.equal(grouping.value, 'combined');
assert.equal(groupingOptions[2].disabled, false, 'Combined remains available without both inputs');
assert.equal(elements.exportWebpage.disabled, false);

(async () => {
    await context.exportChapters();
    assert.equal(payload.chapter_grouping, 'combined');
    assert.equal(payload.subpart_mode, 'both');
    assert.equal(payload.timestamp_mode, 'subpart');
    assert.equal(payload.include_webpage, true);
    for (const field of ['include_images', 'include_transcripts', 'include_clips', 'include_word', 'include_pdf']) {
        assert.equal(payload[field], false, `${field} must not be implicitly enabled by HTML`);
    }
    elements.intervalExportToggle.checked = true;
    elements.intervalDuration.value = 0.5;
    grouping.value = 'slides';
    elements.timestampMode = { value: 'part' };
    elements.subpartMode = { value: 'slides' };
    await context.exportChapters();
    assert.equal(payload.interval_minutes, 0.5);
    assert.equal(payload.chapter_grouping, 'slides');
    assert.equal(payload.timestamp_mode, 'part');
    assert.equal(payload.subpart_mode, 'slides');
    console.log('PASS: combined sentence/slide defaults, HTML-only flags, no forced intervals, explicit modes preserved');
})().catch(error => { console.error(error); process.exitCode = 1; });
