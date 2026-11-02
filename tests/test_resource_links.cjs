const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({URL, TypeError});
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'resource-links.js'), 'utf8')
    .replace(/^import .*;$/gm, '').replace(/^export /gm, ''), context);
const docs = {title: 'Docs', url: 'https://example.com'};
const chapters = [
    {timestamp: '00:00', title: 'Chapter A', resources: [docs], sections: [
        {timestamp: '00:00', title: 'Section A', resources: [docs]},
        {timestamp: '00:10', title: 'Section B', resources: [{...docs, start: 5.125, end: 8.5}]},
    ]},
    {timestamp: '00:20', title: 'Chapter B', resources: [docs]},
];
const entries = context.resourceEntries(chapters, 30);
assert.equal(entries.length, 4);
assert.equal(entries[0].start, 0);
assert.equal(entries[0].end, 20);
assert.equal(entries[1].end, 10);
assert.equal(entries[2].start, 5.125, 'Custom timing overrides inherited section timing');
assert.equal(entries[2].end, 8.5);
assert.equal(entries[3].end, 30);
assert.equal(entries.filter(link => 10 >= link.start && 10 < link.end).length, 1);
assert.equal(entries.filter(link => 30 >= link.start && 30 < link.end).length, 0);
assert.equal(context.safeResourceUrl('https://example.com/docs'), 'https://example.com/docs');
for (const unsafe of ['javascript:alert(1)', 'data:text/html,test', 'file:///test', '/relative', 'https://u:p@example.com']) {
    assert.equal(context.safeResourceUrl(unsafe), null);
}
assert.equal(context.resourceEntries([], 30).length, 0);
console.log('PASS: inherited/custom resource timing, boundaries and safe external links');
