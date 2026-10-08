const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'main.js'), 'utf8').replace(/\r\n/g, '\n');
const start = source.indexOf("    [\n        ['summaryOptionsBtn'");
const end = source.indexOf('\n    // Wire the core video', start);
const attributes = {};
const button = {
    firstChild: {textContent: 'Chapter options'},
    appendChild(child) { this.child = child; },
    contains(child) { return this.child === child; },
    setAttribute(key, value) { attributes[key] = value; },
    classList: {toggle(name, value) { attributes[name] = value; }},
};
const panel = {hidden: true, tagName: 'DIV', addEventListener() {}};
let update;
vm.runInNewContext(source.slice(start, end), {
    document: {
        getElementById(id) { return id === 'summaryOptionsBtn' ? button : id === 'summaryOptionsPanel' ? panel : null; },
        createElement() { return {setAttribute() {}}; },
    },
    MutationObserver: class { constructor(callback) { update = callback; } observe() {} },
});
assert.equal(attributes['aria-expanded'], 'false');
panel.hidden = false;
update();
assert.equal(attributes['aria-expanded'], 'true');
assert.equal(attributes['options-open'], true);
assert.equal(button.firstChild.textContent, 'Hide chapter options');
panel.hidden = true;
update();
assert.equal(attributes['options-open'], false);
assert.equal(button.firstChild.textContent, 'Chapter options');
console.log('PASS: options open/closed indicators and accessible state');
