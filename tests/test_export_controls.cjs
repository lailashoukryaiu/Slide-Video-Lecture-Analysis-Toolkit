const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'main.js'), 'utf8');
const start = source.indexOf('bind(elements.exportChaptersBtn,');
const end = source.indexOf('bind(elements.startExportBtn', start);
assert(start >= 0 && end > start);

let handler;
let opened = false;
let suggestionsRequested = false;
const elements = {
    exportChaptersBtn: {},
    exportOptionsDialog: {
        open: false,
        showModal() {
            opened = true;
            this.open = true;
        },
    },
};
const bind = (element, event, callback) => {
    assert.equal(element, elements.exportChaptersBtn);
    assert.equal(event, 'click');
    handler = callback;
};
const updateExportAvailability = () => {};
const loadExportSuggestions = () => {
    suggestionsRequested = true;
    return new Promise(() => {});
};
const refreshSavedExports = async () => {};

eval(source.slice(start, end));
handler();
assert(opened);
assert(suggestionsRequested);
console.log('PASS: export click opens options without waiting for suggestions');
