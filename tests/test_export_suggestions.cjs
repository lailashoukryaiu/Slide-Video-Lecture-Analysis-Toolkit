const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'main.js'), 'utf8');
const start = source.indexOf('let exportSuggestionGeneration =');
const end = source.indexOf('// Initialize the application', start);
const input = () => ({value: '', dataset: {}});
const elements = {exportTitle: input(), exportFilename: input()};
const hints = {exportTitleSuggestion: {}, exportFilenameSuggestion: {}};
const requests = [];
const errors = [];
const state = {
    currentVideoId: 'first-video-hash',
    currentVideoFilename: 'Canvas Studio Demo.mp4',
    currentTranslationLanguage: null,
    videoChapters: [{title: 'Creating Canvas courses', summary: 'Course setup'}],
};
const context = vm.createContext({
    state, elements,
    document: {getElementById: id => hints[id]},
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject})),
    showError: message => errors.push(message),
});
vm.runInContext(source.slice(start, end), context);
const reply = (request, title) => request.resolve({
    ok: true, json: async () => ({title_suggestion: title, filename_suggestion: title.replaceAll(' ', '_')}),
});

(async () => {
    const first = context.loadExportSuggestions();
    assert.equal(elements.exportTitle.value, 'Creating Canvas courses');
    assert.equal(elements.exportFilename.value, 'Canvas_Studio_Demo');
    assert(!elements.exportTitle.value.includes('hash'));
    state.currentVideoId = 'second-video';
    state.currentVideoFilename = 'Copilot Meeting.mp4';
    state.videoChapters = [{title: 'Copilot agents'}];
    const second = context.loadExportSuggestions();
    reply(requests[1], 'Enterprise Copilot');
    await second;
    reply(requests[0], 'Old Canvas title');
    await first;
    assert.equal(elements.exportTitle.value, 'Enterprise Copilot', 'Old video response must not overwrite current fields');
    assert.equal(hints.exportTitleSuggestion.textContent, 'Suggestion: Enterprise Copilot');

    const originalLanguage = context.loadExportSuggestions();
    state.currentTranslationLanguage = 'de';
    state.videoChapters = [{title: 'Copilot Agenten'}];
    const translated = context.loadExportSuggestions();
    assert.equal(elements.exportTitle.value, 'Copilot Agenten', 'Do not keep an old automatic language suggestion');
    reply(requests[3], 'Copilot Bereitstellung');
    await translated;
    reply(requests[2], 'English title');
    await originalLanguage;
    assert.equal(elements.exportTitle.value, 'Copilot Bereitstellung');

    const editing = context.loadExportSuggestions();
    elements.exportTitle.value = 'My custom title';
    elements.exportFilename.value = 'my_custom_file';
    reply(requests[4], 'Automatic title');
    await editing;
    assert.equal(elements.exportTitle.value, 'My custom title');
    assert.equal(elements.exportFilename.value, 'my_custom_file');

    const obsoleteError = context.loadExportSuggestions();
    state.currentVideoId = 'third-video';
    state.currentVideoFilename = 'Training Demo.mp4';
    state.videoChapters = [];
    const current = context.loadExportSuggestions();
    assert.equal(elements.exportTitle.value, 'Training Demo');
    requests[5].reject(new Error('Old request failed'));
    await obsoleteError;
    assert.equal(errors.length, 0, 'Obsolete failures must not show on another video');
    requests[6].reject(new Error('Current request failed'));
    await current;
    assert.equal(errors.length, 1);
    assert.equal(elements.exportTitle.value, 'Training Demo', 'Keep meaningful fallback on suggestion failure');
    console.log('PASS: meaningful export names, video/language races, manual edits, and request errors');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
