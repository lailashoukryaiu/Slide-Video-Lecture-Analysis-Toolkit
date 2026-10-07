const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.join(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'templates', 'index.html'), 'utf8');
const input = template.match(/<input\b[^>]*id="chapterSlidesToggle"[^>]*>/);
assert(input);
assert(!/\bchecked\b/.test(input[0]), 'Chapter slides are off by default');
const source = fs.readFileSync(path.join(root, 'static', 'js', 'chapters.js'), 'utf8')
    .replace(/^import .*;\r?$/gm, '').replace(/^export /gm, '');
let removed = 0;
let sceneReads = 0;
const state = {
    get videoScenes() { sceneReads++; return []; },
};
const toggle = { checked: false };
const container = {
    querySelectorAll(selector) {
        return selector === '.slide-download' ? [{ remove() { removed++; } }] : [];
    },
};
const context = vm.createContext({
    state, elements: { chaptersContainer: container },
    document: { getElementById() { return toggle; } },
});
vm.runInContext(source, context);
context.attachChapterSlides();
assert.equal(sceneReads, 0, 'Default chapter rendering does not process slides');
assert.equal(removed, 1, 'Turning off removes previously attached download controls');
toggle.checked = true;
context.attachChapterSlides();
assert.equal(sceneReads, 1, 'Slides are processed only after opting in');
console.log('PASS: chapter slides default off, cleanup and explicit opt-in');
