import { state } from './main.js';
import { updateChapters } from './chapters.js';
import { contentLanguage } from './content-languages.js';
import { showError, showNotification } from './ui.js';
import { startProcessButton } from './progress-time.js';
import { formatTime, formatTimestamp } from './utils.js';

export function initOutlineEditor() {
    let editor = null;
    let saving = false;
    let editingRow = null;
    const context = () => ({
        videoId: state.currentVideoId,
        language: contentLanguage(state.currentVideoId, 'chapters', state.currentTranslationLanguage),
        transcript_language: state.currentTranslationLanguage || '',
        source: state.currentTranscriptSource === 'uploaded' ? 'uploaded'
            : state.currentTranscriptSource === 'youtube' ? 'youtube' : 'whisper',
        version_id: state.currentTranscriptVersionId || '',
    });
    const close = () => {
        editor?.remove();
        editingRow?.classList.remove('is-inline-editing');
        editingRow = null;
        editor = null;
        state.outlineRowEditing = false;
    };
    document.addEventListener('outlineEditRequested', async event => {
        if (saving || state.resourceLinkEditing || state.summaryGenerationInProgress) {
            showError('Wait for the current save or chapter generation to finish.');
            return;
        }
        if (editor && !window.confirm('Discard the unsaved row edit?')) return;
        close();
        const request = event.detail;
        if (request.action === 'insert_boundary') {
            const choices = document.createElement('div');
            choices.className = 'outline-row-editor outline-insert-choices';
            choices.setAttribute('role', 'group');
            choices.setAttribute('aria-label', 'Insert at this section boundary');
            choices.addEventListener('click', event => event.stopPropagation());
            const choose = (label, detail) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-secondary';
                button.textContent = label;
                button.addEventListener('click', () => {
                    close();
                    document.dispatchEvent(new CustomEvent('outlineEditRequested', {detail: {...request, ...detail}}));
                });
                choices.append(button);
                return button;
            };
            const first = choose('Add section', {action: 'add_section'});
            choose('Start new chapter here', {
                action: 'add_chapter', chapter: request.chapter + 1, section: null,
                splitFrom: {chapter: request.chapter, section: request.section},
            });
            const cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'btn btn-secondary';
            cancel.textContent = 'Cancel';
            cancel.addEventListener('click', close);
            choices.append(cancel);
            choices.addEventListener('keydown', event => {
                event.stopPropagation();
                if (event.key === 'Escape') { event.preventDefault(); close(); request.anchor.focus(); }
            });
            request.anchor.after(choices);
            editor = choices;
            state.outlineRowEditing = true;
            first.focus();
            return;
        }
        const selected = context();
        const rendered = JSON.stringify(state.videoChapters || []);
        if (!selected.videoId) { showError('Load a saved transcript first.'); return; }
        const form = document.createElement('form');
        form.className = 'outline-row-editor';
        for (const name of ['click', 'keydown']) form.addEventListener(name, event => event.stopPropagation());
        editingRow = request.anchor.closest('.chapter-item, .chapter-section');
        editingRow?.classList.add('is-inline-editing');
        form.setAttribute('aria-label', request.action.startsWith('add') ? 'Add outline item' : 'Edit outline item');
        const status = document.createElement('p');
        status.setAttribute('role', 'status');
        status.textContent = 'Loading saved row...';
        form.append(status);
        request.anchor.after(form);
        editor = form;
        state.outlineRowEditing = true;
        try {
            const response = await fetch(`/editable_content/${encodeURIComponent(selected.videoId)}?`
                + new URLSearchParams({...selected, kind: 'chapters'}));
            const snapshot = await response.json();
            if (!response.ok) throw new Error(snapshot.detail || 'Could not load the saved outline.');
            if (editor !== form || JSON.stringify(selected) !== JSON.stringify(context())) return;
            if (JSON.stringify(snapshot.content) !== rendered) {
                throw new Error('The saved outline changed. Reload chapters before editing this row.');
            }
            const isChapter = request.action === 'add_chapter' || request.section == null;
            const old = request.action.startsWith('add') ? null
                : isChapter ? snapshot.content[request.chapter]
                    : snapshot.content[request.chapter]?.sections?.[request.section];
            if (!old && !request.action.startsWith('add')) throw new Error('This item changed. Reload the chapters.');
            const field = (label, value, multiline = false, parent = form) => {
                const wrapper = document.createElement('label');
                wrapper.textContent = label;
                const input = document.createElement(multiline ? 'textarea' : 'input');
                input.value = value || '';
                input.maxLength = multiline ? 20000 : ['Start', 'End'].includes(label) ? 30 : 500;
                if (!multiline) input.required = true;
                input.setAttribute('aria-label', label);
                wrapper.append(input);
                parent.append(wrapper);
                return input;
            };
            form.replaceChildren();
            const parse = stamp => String(stamp).split(':').reduce((sum, part) => sum * 60 + Number(part), 0);
            const clock = value => {
                const millis = Math.round(value * 1000);
                return `${formatTime(Math.floor(millis / 1000))}.${String(millis % 1000).padStart(3, '0')}`;
            };
            const chapters = snapshot.content;
            const chapter = chapters[request.chapter];
            const sections = chapter?.sections || [];
            const splitSections = request.splitFrom ? chapters[request.splitFrom.chapter]?.sections || [] : [];
            const splitSection = splitSections[request.splitFrom?.section];
            const cues = state.currentTranscript || [];
            const duration = snapshot.duration
                ?? Math.max(0, ...cues.map(cue => Number(cue.start) + Number(cue.duration || 0)));
            let initial = old?.timestamp || request.timestamp || '00:00';
            if (!old) {
                const lower = request.splitFrom
                    ? parse(splitSections[request.splitFrom.section - 1].timestamp) + 0.001
                    : isChapter ? (request.chapter ? parse(chapters[request.chapter - 1].timestamp) + 0.001 : 0)
                    : request.section ? parse(sections[request.section - 1].timestamp) + 0.001 : parse(chapter.timestamp);
                const next = isChapter ? chapter?.timestamp : sections[request.section]?.timestamp || chapters[request.chapter + 1]?.timestamp;
                const upper = (next ? parse(next) : duration) - 0.001;
                if (lower > upper) throw new Error('There is no time gap here. Edit the existing boundary instead.');
                const playhead = document.getElementById('videoPlayer')?.currentTime || 0;
                const boundary = splitSection ? parse(splitSection.timestamp) : null;
                const value = boundary !== null && boundary >= lower && boundary <= upper ? boundary
                    : playhead >= lower && playhead <= upper ? playhead : (lower + upper) / 2;
                initial = clock(value);
            }
            const time = field('Start', formatTimestamp(initial));
            const next = isChapter ? chapters[request.chapter + (old ? 1 : 0)]?.timestamp
                : sections[request.section + (old ? 1 : 0)]?.timestamp || chapters[request.chapter + 1]?.timestamp;
            const end = field('End', next ? formatTimestamp(next) : clock(duration));
            for (const [input, label] of [[time, 'Start'], [end, 'End']]) {
                const wrapper = document.createElement('div');
                wrapper.className = 'custom-clip-time-field';
                const pin = document.createElement('button');
                pin.type = 'button';
                pin.className = 'btn btn-secondary btn-icon';
                pin.title = `${label} here`;
                pin.setAttribute('aria-label', `Set ${label.toLowerCase()} to current video time`);
                pin.innerHTML = '<i class="fas fa-map-pin" aria-hidden="true"></i>';
                pin.addEventListener('click', () => {
                    if (saving) return;
                    const player = document.getElementById('videoPlayer');
                    if (!player || player.readyState < 1 || !Number.isFinite(player.currentTime)) {
                        status.className = 'outline-error';
                        status.textContent = 'Load the video before setting a timestamp from playback.';
                        return;
                    }
                    input.value = clock(player.currentTime);
                    status.className = '';
                    status.textContent = '';
                });
                input.after(wrapper);
                wrapper.append(input, pin);
            }
            const help = document.createElement('details');
            help.className = 'contextual-help';
            const helpToggle = document.createElement('summary');
            helpToggle.textContent = '?';
            helpToggle.title = 'Section timing and playback help';
            helpToggle.setAttribute('aria-label', 'Section timing and playback help');
            const hint = document.createElement('p');
            hint.textContent = 'An earlier end keeps the remaining time as a grey part with a transcript-based title and summary. Nothing is skipped. Flag for review displays a grey row but still includes it. Skip affects playback only; the original video and exports are unchanged. Deleting removes an outline label, not footage.';
            help.append(helpToggle, hint);
            help.addEventListener('keydown', event => {
                if (event.key === 'Escape') { event.preventDefault(); help.open = false; helpToggle.focus(); }
            });
            form.append(help);
            const title = field('Title', old?.title || splitSection?.title || '');
            const summary = document.createElement('details');
            summary.className = 'chapter-summary-toggle chapter-summary-edit';
            const summaryToggle = document.createElement('summary');
            summaryToggle.textContent = isChapter ? 'Chapter summary' : 'Section summary';
            summary.append(summaryToggle);
            form.append(summary);
            const text = field(summaryToggle.textContent, old?.[isChapter ? 'summary' : 'point'], true, summary);
            const check = (label, value) => {
                const wrapper = document.createElement('label');
                const input = document.createElement('input');
                input.type = 'checkbox';
                input.checked = Boolean(value);
                wrapper.append(input, document.createTextNode(label));
                form.append(wrapper);
                return input;
            };
            const flagged = check('Flag for review', old?.flagged);
            const review = document.createElement('details');
            review.className = 'chapter-summary-toggle review-reason-edit';
            const reviewToggle = document.createElement('summary');
            reviewToggle.textContent = 'Review reason';
            review.append(reviewToggle);
            form.append(review);
            const reason = field('Review reason', old?.flag_reason, true, review);
            reason.maxLength = 1000;
            const skipped = check('Skip during playback', old?.skipped);
            const actions = document.createElement('div');
            actions.className = 'outline-editor-actions';
            const button = (label, type = 'button') => {
                const control = document.createElement('button');
                control.type = type;
                control.className = 'btn btn-secondary';
                control.textContent = label;
                actions.append(control);
                return control;
            };
            const save = button('Save', 'submit');
            const cancel = button('Cancel');
            cancel.addEventListener('click', close);
            const remove = old ? button('') : null;
            if (remove) {
                const label = isChapter ? 'Delete chapter' : 'Delete section';
                remove.className = 'btn btn-secondary btn-icon delete-action';
                remove.title = label;
                remove.setAttribute('aria-label', label);
                remove.innerHTML = '<i class="fas fa-trash" aria-hidden="true"></i>';
            }
            form.append(actions, status);
            status.textContent = '';
            async function persist(action) {
                if (saving) return;
                if (JSON.stringify(selected) !== JSON.stringify(context())) {
                    status.className = 'outline-error';
                    status.textContent = 'The selected video or transcript changed. Reopen this row.';
                    return;
                }
                saving = true;
                form.setAttribute('aria-busy', 'true');
                save.disabled = cancel.disabled = true;
                if (remove) remove.disabled = true;
                status.className = '';
                status.textContent = 'Saving and describing any new separated parts...';
                try {
                    const response = await fetch(`/outline_item/${encodeURIComponent(selected.videoId)}`, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({...selected, revision: snapshot.revision,
                            action, chapter: request.chapter, section: request.section,
                            item: {timestamp: time.value.trim(), end_timestamp: end.value.trim(), title: title.value.trim(),
                                [isChapter ? 'summary' : 'point']: text.value.trim(),
                                flagged: flagged.checked, flag_reason: reason.value, skipped: skipped.checked}}),
                    });
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || 'Could not save the outline.');
                    close();
                    if (JSON.stringify(selected) === JSON.stringify(context())) {
                        updateChapters(data.content, {
                            preserveExpansion: true,
                            selectionEdit: {action, chapter: request.chapter, section: request.section,
                                timestamp: time.value.trim()},
                            retimedChapter: action === 'update' && isChapter
                                ? {previous: old.timestamp, current: time.value.trim()} : null,
                        });
                    }
                    for (const warning of data.warnings || []) showNotification(warning, 'warning');
                } catch (error) {
                    status.className = 'outline-error';
                    status.textContent = `${error.message} Your changes are still here.`;
                } finally {
                    saving = false;
                    form.setAttribute('aria-busy', 'false');
                    save.disabled = cancel.disabled = false;
                    if (remove) remove.disabled = false;
                }
            }
            form.addEventListener('submit', event => { event.preventDefault(); void persist(request.action); });
            remove?.addEventListener('click', () => {
                if (window.confirm('Delete this outline item? This removes its label, not footage. To omit this time during playback, use Skip instead.')) {
                    void persist('delete');
                }
            });
            title.focus();
        } catch (error) {
            status.className = 'outline-error';
            status.textContent = error.message;
            const cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'btn btn-secondary';
            cancel.textContent = 'Close';
            cancel.addEventListener('click', close);
            form.append(cancel);
        }
    });
    for (const event of ['videoSelectionChanged', 'transcriptUpdated', 'chaptersUpdated']) {
        document.addEventListener(event, () => { if (!saving) close(); });
    }
    for (const [id, action] of [['mergeSelectedSectionsBtn', 'merge'], ['deleteSelectedSectionsBtn', 'delete']]) {
        document.getElementById(id)?.addEventListener('click', async () => {
            if (saving || editor || state.summaryGenerationInProgress) {
                showError('Save or cancel the current edit and wait for chapter generation to finish first.');
                return;
            }
            const selections = [...document.querySelectorAll('.clip-selection:checked')]
                .map(input => JSON.parse(input.dataset.clipSelection));
            const chapterRows = selections.filter(part => !Number.isInteger(part.section_index));
            const mergingChapters = action === 'merge' && selections.length > 1 && chapterRows.length === selections.length;
            if (!selections.length || (chapterRows.length && !mergingChapters)) {
                showError(action === 'merge'
                    ? 'Select either adjacent sections or adjacent chapters to merge, not both.'
                    : 'Select section rows only, not chapters.');
                return;
            }
            const message = action === 'delete'
                ? `Delete ${selections.length} selected section labels? Original footage is not deleted.`
                : mergingChapters
                ? 'Merge each adjacent group among the selected chapters? '
                    + 'All their sections are kept, in order, inside the earliest chapter. '
                    + 'Each group gets a new chapter title and summary covering all its chapters. '
                    + 'Isolated selected chapters stay unchanged.'
                : 'Merge each adjacent group among the selected sections, including across chapter boundaries? '
                    + 'Merged sections stay in the earliest chapter; following chapter starts move to their first remaining section, and empty chapters are removed. '
                    + 'Isolated selections and unselected sections stay unchanged. '
                    + 'Each group gets a new title and summary covering all its sections, and is skipped only if every section in that group was marked Skip.';
            if (!window.confirm(message)) return;
            const selected = context();
            const rendered = JSON.stringify(state.videoChapters || []);
            const buttons = ['mergeSelectedSectionsBtn', 'deleteSelectedSectionsBtn'].map(name => document.getElementById(name));
            saving = true;
            state.outlineRowEditing = true;
            buttons.forEach(button => { if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); } });
            const finish = startProcessButton(document.getElementById(id),
                action === 'merge' ? `Merging and summarizing ${mergingChapters ? 'chapters' : 'sections'}` : 'Deleting section labels');
            try {
                const response = await fetch(`/editable_content/${encodeURIComponent(selected.videoId)}?`
                    + new URLSearchParams({...selected, kind: 'chapters'}));
                const snapshot = await response.json();
                if (!response.ok) throw new Error(snapshot.detail || 'Could not load the outline.');
                if (JSON.stringify(selected) !== JSON.stringify(context()) || JSON.stringify(snapshot.content) !== rendered) {
                    throw new Error('The video, transcript or outline changed. Reload chapters before changing selected sections.');
                }
                const saved = await fetch(`/outline_selection/${encodeURIComponent(selected.videoId)}`, {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({...selected, revision: snapshot.revision, action, selections}),
                });
                const data = await saved.json();
                if (!saved.ok) throw new Error(data.detail || 'Could not save the selected section changes.');
                if (JSON.stringify(selected) === JSON.stringify(context())) {
                    updateChapters(data.content, {preserveExpansion: true});
                }
                showNotification(action === 'merge'
                    ? `Adjacent selected ${mergingChapters ? 'chapters' : 'sections'} merged.` : 'Section labels deleted.', 'success');
            } catch (error) {
                showError(error.message);
            } finally {
                finish();
                saving = false;
                state.outlineRowEditing = false;
                buttons.forEach(button => { if (button) button.setAttribute('aria-busy', 'false'); });
                document.dispatchEvent(new CustomEvent('sectionSelectionChanged'));
            }
        });
    }
}
