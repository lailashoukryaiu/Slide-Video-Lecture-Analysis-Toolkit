const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const outlines = [];
const blurFilters = [];
function node() {
    return {
        children: [], listeners: {}, value: '', checked: false, disabled: false, textContent: '',
        classList: {toggle() {}}, attributes: {},
        addEventListener(type, callback) { this.listeners[type] = callback; },
        append(...items) { this.children.push(...items); },
        replaceChildren(...items) { this.children = items; },
        setAttribute(name, value) { this.attributes[name] = value; },
        getContext: () => ({drawImage() { if (this.filter) blurFilters.push(this.filter); },
            save() {}, restore() {}, beginPath() {}, rect() {},
            clip() {}, fillRect() {}, setLineDash() {},
            strokeRect(...box) { outlines.push({box, color: this.strokeStyle}); }}),
        getBoundingClientRect: () => ({left: 10, top: 20, width: 800, height: 400}),
        setPointerCapture(id) { this.pointer = id; }, hasPointerCapture(id) { return this.pointer === id; },
        releasePointerCapture() { this.pointer = null; },
    };
}
const ids = Object.fromEntries(['clipPrivacyPreview', 'clipPrivacyEnabled', 'clipPrivacyStatus',
    'clipPrivacyScan', 'clipPrivacyRegions', 'clipPrivacyEffect', 'clipPrivacyDetection',
    'clipPrivacyAddDetection', 'clipPrivacyAddManual', 'clipPrivacyOptions', 'customClipStart',
    'customClipEnd', 'clipPrivacyClear', 'clipPrivacyUndo', 'clipPrivacyBlurStrength',
    'clipPrivacyBlurValue'].map(id => [id, node()]));
ids.clipPrivacyEffect.value = 'blur';
ids.clipPrivacyBlurStrength.value = ids.clipPrivacyBlurValue.value = '24';
ids.customClipStart.value = '00:02';
ids.customClipEnd.value = '00:08';
const player = {...node(), currentTime: 3, duration: 10, videoWidth: 1600, videoHeight: 800};
const state = {currentVideoId: 'video', videoScenes: [
    {time_seconds: 0, yolo_detections: {image_size: [800, 400], detections: [
        {class: 'Webcam', bbox: [600, 0, 800, 100]},
    ]}}, {time_seconds: 5},
]};
let media = null, reply, posts = [];
const context = vm.createContext({
    document: {getElementById: id => ids[id], createElement: node},
    clipSeconds: value => value.split(':').reduce((n, part) => n * 60 + Number(part), 0),
    setTimeout, clearTimeout, DOMException, structuredClone,
    fetch: async (url, options) => {
        posts.push({url, options});
        return {ok: true, json: async () => options ? {job_id: 'scan'} : reply};
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'clip-privacy.js'), 'utf8')
    .replace(/^import .*;$/gm, '').replace(/^export /gm, ''), context);
const controls = context.initClipPrivacy({state, player, mediaExportId: () => media});
const change = (element, value) => { element.value = value; element.listeners.change(); };
const normalized = value => JSON.parse(JSON.stringify(value));

(async () => {
    assert.throws(() => context.detectionPrivacyBox({bbox: [0, 0, 3, 3]}, null), /source image size/);
    controls.open();
    assert.equal(controls.settings().length, 0);
    outlines.length = 0;
    change(ids.clipPrivacyDetection, '0');
    assert.deepEqual(outlines.at(-1), {box: [720, 0, 240, 120], color: '#22d3ee'},
        'The selected detection is highlighted at its normalized position before adding it');
    assert.equal(controls.settings().length, 0, 'Previewing a candidate does not add an export mask');
    ids.clipPrivacyDetection.value = '0';
    ids.clipPrivacyAddDetection.onclick();
    assert(outlines.some(outline => outline.color === '#f59e0b'), 'Added masks have a distinct outline');
    await ids.clipPrivacyClear.onclick();
    assert.equal(controls.settings().length, 0);
    assert.equal(ids.clipPrivacyEnabled.checked, false);
    assert.equal(ids.clipPrivacyClear.disabled, true);
    await ids.clipPrivacyUndo.onclick();
    assert.equal(controls.settings().length, 1, 'Undo restores all cleared regions and masking');
    change(ids.clipPrivacyRegions.children[0].children[1], 'pixelate');
    assert.equal(controls.settings()[0].effect, 'pixelate');
    await ids.clipPrivacyUndo.onclick();
    assert.equal(controls.settings()[0].effect, 'blur', 'Undo also restores individual appearance edits');
    let region = normalized(controls.settings())[0];
    assert.deepEqual(region, {x: .75, y: 0, width: .25, height: .25, start: 0, end: 5, effect: 'blur', blur_strength: 24});
    ids.clipPrivacyBlurStrength.value = '6';
    ids.clipPrivacyBlurStrength.listeners.input();
    ids.clipPrivacyBlurStrength.listeners.change();
    assert.equal(controls.settings()[0].blur_strength, 6, 'The downloaded video uses the selected strength');
    assert(blurFilters.includes('blur(3.6px)'), 'Preview strength scales with the source-to-preview resolution');
    await ids.clipPrivacyUndo.onclick();
    assert.equal(ids.clipPrivacyBlurStrength.value, '24', 'Undo restores the previous blur strength');
    const row = ids.clipPrivacyRegions.children[0];
    change(row.children[1], 'cover');
    change(row.children[2], 'range');
    region = normalized(controls.settings())[0];
    assert.equal(region.end, 10);
    assert.equal(region.effect, 'cover');
    change(row.children[2], 'scene');
    ids.customClipStart.value = '00:06';
    assert.throws(() => controls.settings(), /No privacy regions overlap/);
    ids.customClipStart.value = '00:02';
    change(row.children[2], 'range');
    assert.equal(player.currentTime, 3, 'Adding or editing regions does not move playback');
    const x = row.children[3].children[0];
    change(x, '99');
    assert.equal(controls.settings()[0].x, .75);
    assert.match(ids.clipPrivacyStatus.textContent, /inside the video/);
    change(x, '50');
    assert.equal(controls.settings()[0].x, .5);
    row.children.at(-2).onclick();
    assert.equal(player.currentTime, 0);
    row.children.at(-1).onclick();
    assert.throws(() => controls.settings(), /at least one/);
    ids.clipPrivacyPreview.listeners.pointerdown({button: 0, pointerId: 1, clientX: 90, clientY: 60, preventDefault() {}});
    ids.clipPrivacyPreview.listeners.pointerup({pointerId: 1, clientX: 410, clientY: 220});
    region = normalized(controls.settings())[0];
    assert.equal(region.x, .1);
    assert.equal(region.y, .1);
    assert.equal(region.width, .4);
    assert.equal(region.height, .4);
    assert.equal(ids.clipPrivacyPreview.pointer, null);
    ids.clipPrivacyRegions.children[0].children.at(-1).onclick();
    reply = {status: 'complete', regions: [{x: .2, y: .1, width: .2, height: .3, start: 2, end: 4, effect: 'blur'}]};
    await ids.clipPrivacyScan.onclick();
    assert.equal(controls.settings()[0].start, 2);
    assert.equal(controls.settings()[0].end, 4);
    assert.equal(ids.clipPrivacyScan.disabled, false);
    assert.match(ids.clipPrivacyStatus.textContent, /can miss people/);
    const submitted = JSON.parse(posts[0].options.body);
    assert.equal(submitted.start, '00:02');
    assert.equal(submitted.end, '00:08');
    change(ids.clipPrivacyRegions.children[0].children[2], 'range');
    assert.equal(controls.settings()[0].end, 10, 'Detected intervals can instead cover the full exported clip');
    change(ids.clipPrivacyRegions.children[0].children[2], 'detected');
    assert.equal(controls.settings()[0].end, 4);
    media = 'another-source';
    assert.throws(() => controls.settings(), /source changed/);
    controls.close(); controls.open();
    assert.equal(controls.settings().length, 0);
    ids.clipPrivacyEnabled.checked = true;
    assert.throws(() => controls.settings(), /at least one/);
    reply = {status: 'error', error: 'Model is unavailable'};
    await ids.clipPrivacyScan.onclick();
    assert.match(ids.clipPrivacyStatus.textContent, /Model is unavailable/);
    assert.equal(ids.clipPrivacyScan.disabled, false);

    let pendingStatus;
    context.fetch = async (url, options) => {
        posts.push({url, options});
        if (options) return {ok: true, json: async () => ({job_id: 'pending-scan'})};
        return new Promise(resolve => { pendingStatus = resolve; });
    };
    const scanning = ids.clipPrivacyScan.onclick();
    await new Promise(resolve => setImmediate(resolve));
    assert.throws(() => controls.settings(), /Finish or stop/);
    await ids.clipPrivacyScan.onclick();
    pendingStatus({ok: true, json: async () => ({status: 'complete', regions: reply.regions || []})});
    await scanning;
    assert(posts.some(post => post.url.endsWith('/pending-scan/cancel')));
    assert.equal(ids.clipPrivacyRegions.children.length, 0, 'Cancelled scans cannot install stale masks');
    assert.equal(ids.clipPrivacyScan.disabled, false);

    const changedSourceScan = ids.clipPrivacyScan.onclick();
    await new Promise(resolve => setImmediate(resolve));
    media = 'changed-during-scan';
    pendingStatus({ok: true, json: async () => ({status: 'complete', regions: [
        {x: 0, y: 0, width: .3, height: .3, start: 0, end: 5, effect: 'blur'},
    ]})});
    await changedSourceScan;
    assert.equal(ids.clipPrivacyRegions.children.length, 0, 'Responses for an old media source are ignored');
    assert.equal(ids.clipPrivacyScan.disabled, false);
    controls.close(); controls.open();
    context.fetch = async (url, options) => {
        return {ok: true, json: async () => options ? {job_id: 'empty-scan'}
            : {status: 'complete', regions: []}};
    };
    await ids.clipPrivacyScan.onclick();
    assert.equal(ids.clipPrivacyEnabled.checked, false, 'Empty scans do not enable a maskless privacy export');
    assert.equal(controls.settings().length, 0);
    context.fetch = async (url, options) => {
        if (url.endsWith('/cancel')) throw new Error('Cancel request failed');
        if (options) return {ok: true, json: async () => ({job_id: 'stop-failure'})};
        return new Promise(resolve => { pendingStatus = resolve; });
    };
    const failedStopScan = ids.clipPrivacyScan.onclick();
    await new Promise(resolve => setImmediate(resolve));
    await ids.clipPrivacyScan.onclick();
    assert.match(ids.clipPrivacyStatus.textContent, /Could not stop privacy scan: Cancel request failed/);
    pendingStatus({ok: true, json: async () => ({status: 'complete', regions: []})});
    await failedStopScan;
    controls.close();
    console.log('PASS: detected/manual privacy regions, source geometry, effects, timing, preview, scan, cancellation and source guards');
})().catch(error => {console.error(error); process.exitCode = 1;});
