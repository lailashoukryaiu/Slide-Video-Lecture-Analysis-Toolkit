const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'chapters.js'), 'utf8')
    .replace(/^import .*;\r?$/gm, '').replace(/^export /gm, '');
const element = () => ({
    children: [], textContent: '',
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
});
const progress = element();
const history = element();
const errors = [];
const context = vm.createContext({
    state: {currentVideoId: 'video'}, elements: {}, console,
    document: {
        createElement: element,
        getElementById: (id) => id === 'exportProgress' ? progress : history,
    },
    showError: (message) => errors.push(message),
    fetch: async () => ({
        ok: true, text: async () => JSON.stringify({exports: [{
            status: 'complete', title: '<unsafe title>', created_at: 100,
            open_url: '/export_jobs/test/webpage/index.html',
            download_url: '/export_jobs/test/download',
        }]}),
    }),
});
vm.runInContext(source, context);
context.renderExportSteps({
    status: 'running', elapsed: 31,
    steps: [{elapsed: 0, message: 'Starting'}, {elapsed: 10, message: 'Encoding clip'}],
});
assert(progress.children[1].children[1].textContent.includes('running for 21s'));
(async () => {
    await context.refreshSavedExports();
    assert.equal(errors.length, 0);
    const row = history.children[1];
    assert(row.children[0].textContent.includes('<unsafe title>'));
    assert.equal(row.children[1].textContent, 'Open HTML in browser');
    assert.equal(row.children[1].target, '_blank');
    assert.equal(row.children[1].rel, 'noopener');
    assert.equal(row.children[2].textContent, 'Download');
    console.log('PASS: export step timing, safe saved titles and browser/download links');
})().catch((error) => {console.error(error); process.exitCode = 1;});
