const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'ocr.js'), 'utf8');
const start = source.indexOf('export async function fetchOcrResults(');
const end = source.indexOf('\n/**', start);
const buttons = new Map();
let response;
const state = {currentVideoId: 'video', ocrResults: []};
const container = {
    prepend(button) { buttons.set(button.id, button); },
};
const context = {
    state,
    elements: {slideContentContainer: container, slideSearch: {}},
    window: {sseConnection: {}},
    document: {
        getElementById(id) { return buttons.get(id); },
        createElement() {
            return {
                style: {},
                addEventListener() {},
                remove() { buttons.delete(this.id); },
            };
        },
    },
    fetch: async () => ({json: async () => response}),
    updateSlideContentDisplay() { buttons.delete('startOcrBtn'); },
    renderOcrStatus() {},
    console,
};
vm.createContext(context);
vm.runInContext(source.slice(start, end).replace('export ', ''), context);

async function check(overrides, primary, surya) {
    response = {
        success: true,
        detections_complete: true,
        ocr_count: 1,
        ocr_results: [{ocr_class: 'title', text: 'Title'}],
        pending_ocr_count: 0,
        failed_ocr_count: 0,
        pending_slide_count: 1,
        ocr_running: false,
        ...overrides,
    };
    await context.fetchOcrResults('video');
    assert.equal(Boolean(buttons.get('startOcrBtn')), primary);
    assert.equal(Boolean(buttons.get('processSuryaBtn')), surya);
}

(async () => {
    await check({pending_ocr_count: 222, ocr_count: 0, ocr_results: []}, true, false);
    await check({pending_ocr_count: 1}, true, false);
    await check({}, false, true);
    await check({pending_ocr_count: 222}, true, false); // Detection rerun removes stale Surya.
    await check({failed_ocr_count: 1}, true, false);
    await check({ocr_running: true}, false, false);
    await check({detections_complete: false}, false, false);
    await check({ocr_count: 0, ocr_results: []}, false, false);
    await check({ocr_results: [{ocr_class: 'unmatched'}]}, false, false);
    await check({}, false, true); // Reloading completed primary OCR offers Surya.

    let refreshed = 0;
    context.fetchOcrResults = () => { refreshed++; };
    context.renderOcrStatus = () => {};
    context.updatePartialOcrResults = () => {};
    const eventStart = source.indexOf('function handleSSEEvent(');
    const eventEnd = source.indexOf('\nfunction formatElapsed', eventStart);
    vm.runInContext(source.slice(eventStart, eventEnd), context);
    for (const event of ['ocr_complete', 'ocr_cancelled']) {
        context.handleSSEEvent({event, data: {final_results: [{text: 'Title'}]}}, 'video');
    }
    assert.equal(refreshed, 2, 'Completion and cancellation must reconcile controls even with final results');
    console.log('PASS: OCR button ordering, retries, reloads, and completion refresh');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
