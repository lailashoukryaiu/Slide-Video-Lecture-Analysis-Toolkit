const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'main.js'), 'utf8');
const start = source.indexOf('function navigateChapter(direction)');
const end = source.indexOf('/**', start);
assert(start >= 0 && end > start);
assert(start < source.indexOf('function setupKeyboardControls()'));
const elements = {videoPlayer: {currentTime: 0}};
const state = {videoChapters: [
    {timestamp: '00:00'}, {timestamp: '02:00'}, {timestamp: '01:02:03'},
], videoScenes: []};
let slideDirection;
let notification;
const navigateToNextSlide = () => {slideDirection = 1;};
const navigateToPreviousSlide = () => {slideDirection = -1;};
const showNotification = (message) => {notification = message;};
eval(source.slice(start, end));
navigateChapter(1);
assert.equal(elements.videoPlayer.currentTime, 120);
navigateChapter(1);
assert.equal(elements.videoPlayer.currentTime, 3723);
navigateChapter(-1);
assert.equal(elements.videoPlayer.currentTime, 120);
navigateChapter(-1);
assert.equal(elements.videoPlayer.currentTime, 0);
navigateChapter(-1);
assert.equal(elements.videoPlayer.currentTime, 0);
state.videoChapters = [];
state.videoScenes = [{time_seconds: 0}, {time_seconds: 20}];
navigateChapter(1);
assert.equal(slideDirection, 1);
navigateChapter(-1);
assert.equal(slideDirection, -1);
state.videoScenes = [];
navigateChapter(1);
assert(notification.includes('Generate chapters'));
console.log('PASS: navigation scope, chapter timestamps, slide fallback and empty-state feedback');
