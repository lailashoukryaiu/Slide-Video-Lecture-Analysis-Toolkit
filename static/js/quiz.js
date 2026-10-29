// quiz.js - Quiz tab: generate, answer, see results as visuals, and jump to chapters to review.
// Talks to the /api/quiz endpoints in quiz_routes.py.

import { state } from './main.js';

const API = '/api/quiz';
let currentQuiz = null;
let chaptersCache = { videoId: null, chapters: [] };
let role = 'instructor';  // the Quiz tab is the instructor tool; students use the Profiles tab

// Adds the role to a request. There is no login yet, so this is a simple switch, not security.
function withRole(url) {
    return `${url}${url.includes('?') ? '&' : '?'}role=${encodeURIComponent(role)}`;
}

function applyRole() {
    const isInstructor = role === 'instructor';
    const generate = $('generateQuizBtn');
    if (generate) generate.style.display = isInstructor ? '' : 'none';
    const del = $('deleteQuizBtn');
    if (del) {
        del.style.display = isInstructor ? '' : 'none';
        del.disabled = !currentQuiz;
    }
    const nameInput = $('studentName');
    if (nameInput) nameInput.style.display = isInstructor ? 'none' : '';
}

function studentName() {
    const input = $('studentName');
    const name = input ? input.value.trim() : '';
    return name || 'Student';
}

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else node.setAttribute(key, value);
    }
    for (const child of children) node.append(child);
    return node;
}

function setStatus(text, kind = '') {
    const node = $('quizStatus');
    if (!node) return;
    node.textContent = text;
    node.className = `quiz-status ${kind}`.trim();
}

async function request(url, options = {}) {
    const res = await fetch(url, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`);
    }
    return data;
}

function postJson(url, body) {
    return request(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
}

function clockToSeconds(clock) {
    return String(clock).split(':').reduce((total, part) => total * 60 + Number(part), 0);
}

function seekTo(clock) {
    const player = $('videoPlayer');
    if (!player) return;
    player.currentTime = clockToSeconds(clock);
    player.play().catch(() => {});
}

// ---------- scope list (chapters and sections) ----------

async function loadChapters(videoId) {
    if (chaptersCache.videoId === videoId) return chaptersCache.chapters;
    try {
        const res = await fetch(`/static/summaries/${encodeURIComponent(videoId)}.json`);
        chaptersCache = { videoId, chapters: res.ok ? await res.json() : [] };
    } catch {
        chaptersCache = { videoId, chapters: [] };
    }
    return chaptersCache.chapters;
}

async function refreshScopes() {
    const videoId = state.currentVideoId;
    const select = $('quizScope');
    if (!select) return;
    select.replaceChildren(el('option', { value: 'full', text: 'Full lecture' }));
    if (!videoId) return;
    const chapters = await loadChapters(videoId);
    chapters.forEach((chapter, i) => {
        select.append(el('option', { value: `chapter:${i}`, text: `${chapter.timestamp}  ${chapter.title}` }));
        (chapter.sections || []).forEach((section, j) => {
            select.append(el('option', { value: `section:${i}:${j}`, text: `    ${section.timestamp}  ${section.title}` }));
        });
    });
}

function scopeToBody(value) {
    const [kind, a, b] = value.split(':');
    if (kind === 'chapter') return { scope_type: 'chapter', chapter_index: Number(a) };
    if (kind === 'section') return { scope_type: 'section', chapter_index: Number(a), section_index: Number(b) };
    return { scope_type: 'full' };
}

// ---------- saved quizzes ----------

async function refreshSaved() {
    const videoId = state.currentVideoId;
    const select = $('quizSaved');
    if (!select) return;
    select.replaceChildren(el('option', { value: '', text: 'Saved quizzes' }));
    if (!videoId) return;
    try {
        const items = await request(withRole(`${API}/list?video_id=${encodeURIComponent(videoId)}`));
        items.forEach((item) => {
            const when = new Date(item.created_at).toLocaleString();
            const level = item.bloom ? ` · ${item.bloom}` : '';
            const draft = '';
            select.append(el('option', {
                value: item.quiz_id,
                text: `${draft}${item.title} · ${item.question_count} q${level} · ${when}`,
            }));
        });
    } catch {
        // No saved quizzes yet is fine.
    }
}

async function loadSaved(quizId) {
    const videoId = state.currentVideoId;
    if (!quizId || !videoId) return;
    setStatus('Loading...');
    try {
        const quiz = await request(withRole(`${API}/${encodeURIComponent(quizId)}?video_id=${encodeURIComponent(videoId)}`));
        renderQuiz(quiz, videoId);
        setStatus(`Loaded: ${quiz.questions.length} questions`);
    } catch (err) {
        setStatus(err.message, 'error');
    }
}

// ---------- generate ----------

async function generateQuiz() {
    const videoId = state.currentVideoId;
    const btn = $('generateQuizBtn');
    if (!videoId) {
        setStatus('Open a video first.', 'error');
        return;
    }
    btn.disabled = true;
    setStatus('Generating... this can take 30 seconds or more.');
    try {
        const body = {
            video_id: videoId,
            ...scopeToBody($('quizScope').value),
            difficulty: $('quizDifficulty').value,
            bloom: $('quizBloom') ? $('quizBloom').value : 'understand',
            count: Number($('quizCount').value) || 6,
        };
        const quiz = await postJson(`${API}/generate`, body);
        renderQuiz(quiz, videoId);
        setStatus(`Ready: ${quiz.questions.length} questions`);
        refreshSaved();
    } catch (err) {
        setStatus(err.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ---------- render the quiz ----------

function renderQuiz(quiz, videoId) {
    currentQuiz = { quiz, videoId };
    const container = $('quizContainer');
    container.replaceChildren();
    container.append(renderTypeBars(quiz));
    container.append(el('h3', { class: 'quiz-title', text: quiz.scope.title }));
    quiz.questions.forEach((q, index) => container.append(renderQuestion(q, index)));
    const submit = el('button', { class: 'btn btn-accent quiz-submit', type: 'button', text: 'Submit answers' });
    submit.addEventListener('click', submitQuiz);
    container.append(submit);
    container.append(el('div', { id: 'quizResults', class: 'quiz-summary' }));

    const del = $('deleteQuizBtn');
    if (del) del.disabled = role !== 'instructor';
}

// Instructor only: who answered this quiz, with their score.
async function attemptsPanel(quiz, videoId) {
    const box = el('div', { class: 'viz-box' });
    box.append(el('strong', { text: 'Students who answered' }));
    try {
        const url = `${API}/${encodeURIComponent(quiz.quiz_id)}/attempts?video_id=${encodeURIComponent(videoId)}&role=instructor`;
        const rows = await request(url);
        if (!rows.length) {
            box.append(el('div', { class: 'viz-muted', text: 'No attempts yet.' }));
            return box;
        }
        rows.forEach((a) => {
            const when = new Date(a.submitted_at).toLocaleDateString();
            const grade = a.grade ? ` · ${a.grade}` : '';
            const color = a.percentage >= 70 ? 'viz-bar-green' : (a.percentage >= 50 ? 'viz-bar-yellow' : 'viz-bar-red');
            box.append(barRow(`${a.student_name} · ${when}`, a.percentage / 100, `${a.percentage}%${grade}`, color));
        });
    } catch (err) {
        box.append(el('div', { class: 'viz-muted', text: err.message }));
    }
    return box;
}

// Instructor only: publish a draft quiz to students, or hide it again.
function publishControl(quiz) {
    const published = quiz.published !== false;
    const box = el('div', { class: 'viz-box' });
    box.append(el('div', { class: 'viz-muted', text: published
        ? 'Visible to students.'
        : 'Draft: students cannot see this quiz yet.' }));
    const btn = el('button', { class: 'btn btn-secondary', type: 'button',
        text: published ? 'Unpublish' : 'Publish to students' });
    btn.addEventListener('click', () => togglePublish(quiz, !published));
    box.append(btn);
    return box;
}

async function togglePublish(quiz, publish) {
    if (!currentQuiz) return;
    const videoId = currentQuiz.videoId;
    try {
        const url = `${API}/${encodeURIComponent(quiz.quiz_id)}/publish?video_id=${encodeURIComponent(videoId)}&publish=${publish}&role=instructor`;
        const data = await request(url, { method: 'POST' });
        quiz.published = data.published;
        setStatus(data.published ? 'Published to students.' : 'Unpublished (draft).');
        renderQuiz(quiz, videoId);
        refreshSaved();
    } catch (err) {
        setStatus(err.message, 'error');
    }
}

// A small bar chart of how many questions of each type the quiz has.
function renderTypeBars(quiz) {
    const labels = { mcq: 'Multiple choice', true_false: 'True / False', fill_in_blank: 'Fill in', short_answer: 'Short answer' };
    const counts = {};
    quiz.questions.forEach((q) => { counts[q.type] = (counts[q.type] || 0) + 1; });
    const total = quiz.questions.length || 1;
    const box = el('div', { class: 'viz-box' });
    box.append(el('strong', { text: 'Questions by type' }));
    Object.entries(counts).forEach(([type, count]) => {
        box.append(barRow(labels[type] || type, count / total, `${count}`, 'viz-bar-blue'));
    });
    return box;
}

function barRow(label, fraction, valueText, colorClass) {
    const row = el('div', { class: 'viz-row' });
    row.append(el('span', { class: 'viz-label', text: label }));
    const track = el('div', { class: 'viz-track' });
    track.append(el('div', { class: `viz-fill ${colorClass}`, style: `width:${Math.round(fraction * 100)}%` }));
    row.append(track);
    row.append(el('span', { class: 'viz-value', text: valueText }));
    return row;
}

function renderQuestion(q, index) {
    const name = `answer-${q.id}`;
    const wrap = el('div', { class: 'quiz-question', 'data-qid': q.id });
    wrap.append(el('div', { class: 'quiz-q-head' }, [
        el('strong', { text: `${index + 1}. ` }),
        el('span', { text: q.question }),
    ]));

    if (q.type === 'mcq') {
        q.options.forEach((opt) => {
            wrap.append(el('label', { class: 'quiz-option', 'data-key': opt.key }, [
                el('input', { type: 'radio', name, value: opt.key }),
                el('span', { text: ` ${opt.key}) ${opt.text}` }),
            ]));
        });
    } else if (q.type === 'true_false') {
        [['true', 'True'], ['false', 'False']].forEach(([value, label]) => {
            wrap.append(el('label', { class: 'quiz-option', 'data-key': value }, [
                el('input', { type: 'radio', name, value }),
                el('span', { text: ` ${label}` }),
            ]));
        });
    } else if (q.type === 'fill_in_blank') {
        wrap.append(el('input', { type: 'text', name, placeholder: 'Type the missing word' }));
    } else {
        wrap.append(el('textarea', { name, rows: '3', placeholder: 'Write your answer' }));
    }

    const source = el('button', { class: 'quiz-source', type: 'button', text: `Watch at ${q.source.timestamp}` });
    source.addEventListener('click', () => seekTo(q.source.timestamp));
    wrap.append(source);
    wrap.append(el('div', { class: 'quiz-result', 'data-result': q.id }));
    return wrap;
}

function collectAnswers() {
    const answers = {};
    document.querySelectorAll('#quizContainer .quiz-question').forEach((wrap) => {
        const qid = wrap.dataset.qid;
        const checked = wrap.querySelector('input[type=radio]:checked');
        const text = wrap.querySelector('input[type=text], textarea');
        answers[qid] = checked ? checked.value : (text ? text.value.trim() : '');
    });
    return answers;
}

// ---------- submit and visual results ----------

async function submitQuiz() {
    if (!currentQuiz) return;
    const { quiz, videoId } = currentQuiz;
    const btn = document.querySelector('#quizContainer .quiz-submit');
    btn.disabled = true;
    setStatus('Grading... short answers are checked by AI.');
    try {
        const url = withRole(`${API}/${encodeURIComponent(quiz.quiz_id)}/submit?video_id=${encodeURIComponent(videoId)}`);
        const attempt = await postJson(url, { answers: collectAnswers(), student_name: studentName() });
        await showResults(attempt);
        const grade = attempt.score.grade ? ` · Grade ${attempt.score.grade}` : '';
        setStatus(`Score: ${attempt.score.percentage}%${grade}`);
    } catch (err) {
        setStatus(err.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

async function showResults(attempt) {
    const { percentage, grade, graded, total } = attempt.score;
    const summary = $('quizResults');
    summary.replaceChildren();
    summary.append(scoreCard(percentage, grade, graded, total));

    attempt.results.forEach((r) => {
        markOptions(r);
        const box = document.querySelector(`#quizContainer [data-result="${r.question_id}"]`);
        if (!box) return;
        const mark = r.is_correct === null
            ? el('span', { class: 'badge badge-grey', text: 'Not graded' })
            : el('span', { class: `badge ${r.is_correct ? 'badge-green' : 'badge-red'}`,
                           text: r.is_correct ? 'Correct' : 'Wrong' });
        box.replaceChildren(mark, el('div', { class: 'quiz-feedback', text: r.feedback }));
        if (r.review) {
            const link = el('button', {
                class: 'quiz-source', type: 'button',
                text: `Review: ${r.review.title} (${r.review.timestamp})`,
            });
            link.addEventListener('click', () => seekTo(r.review.timestamp));
            box.append(link);
        }
    });

    summary.append(await chapterBars(attempt));
    summary.append(reviewBox(attempt));
    const retake = el('button', { class: 'btn btn-accent', type: 'button', text: 'Retake this quiz' });
    retake.addEventListener('click', retakeQuiz);
    summary.append(retake);
}

// Same questions again. Earlier attempts stay saved, so progress keeps every try.
function retakeQuiz() {
    if (!currentQuiz) return;
    renderQuiz(currentQuiz.quiz, currentQuiz.videoId);
    setStatus('Try again. Your earlier attempts are kept.');
}

// My progress: every attempt for this lecture, oldest first, with the average and the change.
async function showProgress() {
    const videoId = state.currentVideoId;
    if (!videoId) {
        setStatus('Open a video first.', 'error');
        return;
    }
    let panel = $('quizProgress');
    if (!panel) {
        panel = el('div', { id: 'quizProgress', class: 'viz-box' });
        $('quizContainer').before(panel);
    }
    try {
        const data = await request(`${API}/progress?video_id=${encodeURIComponent(videoId)}`);
        panel.replaceChildren(el('strong', { text: 'My progress' }));
        if (!data.count) {
            panel.append(el('div', { class: 'viz-muted', text: 'No attempts yet. Solve a quiz first.' }));
            return;
        }
        const change = data.change_since_first;
        const changeText = change === null ? '' : ` · change since first try: ${change > 0 ? '+' : ''}${change}%`;
        panel.append(el('div', { class: 'viz-muted', text: `Average ${data.average}% over ${data.count} tries${changeText}` }));
        data.attempts.forEach((a) => {
            const when = new Date(a.submitted_at).toLocaleDateString();
            const grade = a.grade ? ` · ${a.grade}` : '';
            const color = a.percentage >= 70 ? 'viz-bar-green' : (a.percentage >= 50 ? 'viz-bar-yellow' : 'viz-bar-red');
            panel.append(barRow(`${a.title} · ${when}`, a.percentage / 100, `${a.percentage}%${grade}`, color));
        });
    } catch (err) {
        setStatus('quizStatus', err.message, true);
    }
}

// Green for the correct option, red for the student's wrong choice.
function markOptions(r) {
    if (r.correct_answer === undefined) return;
    const wrap = document.querySelector(`#quizContainer [data-qid="${r.question_id}"]`);
    if (!wrap) return;
    const correctLabel = wrap.querySelector(`[data-key="${r.correct_answer}"]`);
    if (correctLabel) correctLabel.classList.add('opt-correct');
    if (!r.is_correct && r.student_answer) {
        const chosen = wrap.querySelector(`[data-key="${r.student_answer}"]`);
        if (chosen) chosen.classList.add('opt-wrong');
    }
}

function scoreCard(percentage, grade, graded, total) {
    const color = percentage >= 70 ? 'viz-bar-green' : (percentage >= 50 ? 'viz-bar-yellow' : 'viz-bar-red');
    const box = el('div', { class: 'viz-box' });
    box.append(el('strong', { text: `Score ${percentage}%${grade ? `  ·  Grade ${grade}` : ''}` }));
    box.append(barRow('Result', percentage / 100, `${graded}/${total}`, color));
    return box;
}

// One bar per chapter: the average of the graded questions that point into that chapter.
async function chapterBars(attempt) {
    const chapters = await loadChapters(state.currentVideoId);
    const buckets = {};
    attempt.results.forEach((r) => {
        if (!r.review || r.score === null) return;
        const bucket = buckets[r.review.chapter_index] || (buckets[r.review.chapter_index] = []);
        bucket.push(r.score);
    });
    const box = el('div', { class: 'viz-box' });
    box.append(el('strong', { text: 'Score by chapter' }));
    const indexes = Object.keys(buckets).map(Number).sort((a, b) => a - b);
    if (!indexes.length) {
        box.append(el('div', { class: 'viz-muted', text: 'No graded questions to show by chapter.' }));
        return box;
    }
    indexes.forEach((i) => {
        const scores = buckets[i];
        const average = scores.reduce((a, b) => a + b, 0) / scores.length;
        const color = average >= 0.7 ? 'viz-bar-green' : (average >= 0.6 ? 'viz-bar-yellow' : 'viz-bar-red');
        const title = chapters[i] ? chapters[i].title : `Chapter ${i + 1}`;
        box.append(barRow(title, average, `${Math.round(average * 100)}%`, color));
    });
    return box;
}

// Short overall line, plus the chapters to review with a link to each.
function reviewBox(attempt) {
    const box = el('div', { class: 'viz-box' });
    if (attempt.overall_feedback) {
        box.append(el('div', { class: 'quiz-feedback', text: attempt.overall_feedback }));
    }
    const weak = attempt.weak_chapters || [];
    if (weak.length) {
        box.append(el('strong', { text: 'Chapters to review' }));
        weak.forEach((w) => {
            const link = el('button', {
                class: 'quiz-source', type: 'button',
                text: `${w.title} (${w.timestamp}) · ${Math.round(w.average * 100)}%`,
            });
            link.addEventListener('click', () => seekTo(w.timestamp));
            box.append(el('div', {}, [link]));
        });
    }
    const recs = attempt.recommendations || [];
    if (recs.length) {
        box.append(el('strong', { text: 'Next steps' }));
        recs.slice(0, 3).forEach((rec) => {
            const jump = el('button', { class: 'quiz-source', type: 'button', text: `Watch ${rec.timestamp}` });
            jump.addEventListener('click', () => seekTo(rec.timestamp));
            box.append(el('div', { class: 'viz-muted' }, [el('span', { text: `${rec.topic}: ` }), jump]));
        });
    }
    return box;
}

// ---------- delete ----------

async function deleteQuiz() {
    if (!currentQuiz) return;
    const { quiz, videoId } = currentQuiz;
    if (!window.confirm('Delete this quiz and all of its attempts?')) return;
    try {
        await request(withRole(`${API}/${encodeURIComponent(quiz.quiz_id)}?video_id=${encodeURIComponent(videoId)}`), {
            method: 'DELETE',
        });
        currentQuiz = null;
        $('quizContainer').replaceChildren(el('p', { text: 'Quiz deleted.' }));
        $('deleteQuizBtn').disabled = true;
        setStatus('Deleted.');
        refreshSaved();
    } catch (err) {
        setStatus(err.message, 'error');
    }
}

// ---------- styles and setup ----------

function addStyles() {
    const style = document.createElement('style');
    style.textContent = `
.quiz-question { border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 8px; padding: 10px; margin-bottom: 10px; }
.quiz-q-head { margin-bottom: 6px; }
.quiz-option { display: block; margin: 4px 0; padding: 3px 6px; border-radius: 6px; cursor: pointer; }
.quiz-option.opt-correct { background: rgba(46, 160, 67, 0.25); outline: 1px solid #2ea043; }
.quiz-option.opt-wrong { background: rgba(218, 54, 51, 0.25); outline: 1px solid #da3633; }
.quiz-question input[type=text], .quiz-question textarea { width: 100%; box-sizing: border-box; }
.quiz-source { background: none; border: none; color: inherit; text-decoration: underline; cursor: pointer; padding: 0; font-size: 12px; margin-top: 4px; }
.quiz-result { margin-top: 8px; font-size: 14px; }
.quiz-feedback { margin-top: 4px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; color: white; }
.badge-green { background: #2ea043; }
.badge-red { background: #da3633; }
.badge-grey { background: #6e7681; }
.viz-box { border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 8px; padding: 10px; margin: 8px 0; }
.viz-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; font-size: 13px; }
.viz-label { width: 150px; flex-shrink: 0; }
.viz-track { flex: 1; height: 12px; background: rgba(128, 128, 128, 0.25); border-radius: 6px; overflow: hidden; }
.viz-fill { height: 100%; border-radius: 6px; }
.viz-value { width: 52px; text-align: right; flex-shrink: 0; }
.viz-bar-green { background: #2ea043; }
.viz-bar-yellow { background: #d29922; }
.viz-bar-red { background: #da3633; }
.viz-bar-blue { background: #388bfd; }
.viz-muted { opacity: 0.8; font-size: 13px; margin-top: 4px; }
.quiz-summary { margin-top: 10px; }
.quiz-status { font-size: 13px; opacity: 0.85; }
.quiz-status.error { color: #ff6b6b; }
`;
    document.head.append(style);
}

function init() {
    const generate = $('generateQuizBtn');
    if (!generate) return;
    addStyles();
    generate.addEventListener('click', generateQuiz);
    $('quizSaved').addEventListener('change', (e) => loadSaved(e.target.value));
    const del = $('deleteQuizBtn');
    if (del) del.addEventListener('click', deleteQuiz);
    document.querySelector('.tab-btn[data-tab="quiz"]')?.addEventListener('click', () => {
        refreshScopes();
        refreshSaved();
    });
    const progressBtn = el('button', { class: 'btn btn-secondary', type: 'button', text: 'My progress' });
    progressBtn.addEventListener('click', showProgress);
    if (del) del.parentElement.insertBefore(progressBtn, del);
    applyRole();
}

init();
