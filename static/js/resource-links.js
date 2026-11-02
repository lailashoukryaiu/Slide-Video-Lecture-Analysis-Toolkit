import { state } from './main.js';
import { updateChapters } from './chapters.js';
import { contentLanguage } from './content-languages.js';
import { showError, showNotification } from './ui.js';
import { refreshTranscriptVersions } from './transcript-versions.js';
import { clipTimestamp } from './custom-clip-range.js';

const seconds = stamp => String(stamp).split(':').reduce((sum, part) => sum * 60 + Number(part), 0);

export function resourceEntries(chapters, duration = Infinity) {
    return (chapters || []).flatMap((chapter, ci) => {
        const end = chapters[ci + 1] ? seconds(chapters[ci + 1].timestamp) : duration;
        const rows = [{item: chapter, section: null, start: seconds(chapter.timestamp), end},
            ...(chapter.sections || []).map((item, si, sections) => ({
                item, section: si, start: si === 0 ? seconds(chapter.timestamp) : seconds(item.timestamp),
                end: sections[si + 1] ? seconds(sections[si + 1].timestamp) : end,
            }))];
        return rows.flatMap(row => (row.item.resources || []).map((link, index) => ({
            ...link, chapter: ci, section: row.section, index, owner: row.item.title,
            start: link.start ?? row.start, end: link.end ?? row.end,
        })));
    });
}

export function safeResourceUrl(value) {
    try {
        const url = new URL(value);
        return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (error) {
        if (error instanceof TypeError) return null;
        throw error;
    }
}

export function initResourceLinks() {
    const container = document.getElementById('chaptersContainer');
    const player = document.getElementById('videoPlayer');
    const panel = document.getElementById('currentResources');
    if (!container || !player || !panel) return;
    let editor = null, saving = false, panelKey = '';
    const selection = () => ({
        videoId: state.currentVideoId, kind: 'chapters',
        language: contentLanguage(state.currentVideoId, 'chapters', state.currentTranslationLanguage),
        transcript_language: state.currentTranslationLanguage || '',
        source: ['uploaded', 'youtube'].includes(state.currentTranscriptSource) ? state.currentTranscriptSource : 'whisper',
        version_id: state.currentTranscriptVersionId || '',
    });
    const button = (text, action) => {
        const control = document.createElement('button');
        control.type = 'button'; control.className = 'btn btn-secondary';
        control.textContent = text; control.addEventListener('click', action);
        return control;
    };
    const anchor = link => {
        const url = safeResourceUrl(link.url);
        const node = document.createElement(url ? 'a' : 'span');
        node.textContent = link.title;
        if (url) { node.href = url; node.target = '_blank'; node.rel = 'noopener noreferrer'; }
        else node.textContent += ' (invalid link)';
        return node;
    };
    const close = () => {
        editor?.remove(); editor = null; state.resourceLinkEditing = false;
    };
    const edit = async (ci, si, index, host) => {
        if (saving || state.summaryGenerationInProgress || state.outlineRowEditing
                || document.querySelector('.is-inline-editing:not(.resource-link-editor)')) {
            showError('Finish the current edit or chapter generation before editing links.'); return;
        }
        if (editor && !window.confirm('Discard the unsaved link edit?')) return;
        close();
        const selected = selection();
        const rendered = JSON.stringify(state.videoChapters);
        const form = document.createElement('form');
        form.className = 'resource-link-editor is-inline-editing';
        form.setAttribute('aria-label', index == null ? 'Add further-information link' : 'Edit further-information link');
        form.addEventListener('click', event => event.stopPropagation());
        form.addEventListener('keydown', event => {
            event.stopPropagation();
            if (event.key === 'Escape' && !saving) { event.preventDefault(); close(); }
        });
        const status = document.createElement('p'); status.setAttribute('role', 'status');
        status.textContent = 'Loading saved links...'; form.append(status, button('Cancel', close));
        host.append(form); editor = form; state.resourceLinkEditing = true;
        try {
            const response = await fetch(`/editable_content/${encodeURIComponent(selected.videoId)}?` + new URLSearchParams(selected));
            const snapshot = await response.json();
            if (!response.ok) throw new Error(snapshot.detail || 'Could not load links.');
            if (editor !== form || JSON.stringify(selection()) !== JSON.stringify(selected)) return;
            if (JSON.stringify(snapshot.content) !== rendered) throw new Error('The saved chapters changed. Reload chapters before editing links.');
            const owner = si == null ? snapshot.content[ci] : snapshot.content[ci].sections[si];
            const previous = index == null ? {} : owner.resources[index];
            const field = (label, value, type = 'text') => {
                const wrapper = document.createElement('label'); wrapper.textContent = label;
                const input = document.createElement('input'); input.type = type; input.value = value;
                input.setAttribute('aria-label', label); wrapper.append(input); form.append(wrapper);
                return input;
            };
            form.replaceChildren();
            const title = field('Link title', previous.title || ''); title.required = true; title.maxLength = 500;
            const url = field('Link URL', previous.url || '', 'url'); url.required = true; url.maxLength = 2048;
            const timing = document.createElement('label');
            const custom = document.createElement('input'); custom.type = 'checkbox'; custom.checked = previous.start != null;
            timing.append(custom, document.createTextNode(' Use custom video time range'));
            form.append(timing);
            const start = field('Link start (MM:SS or HH:MM:SS)', clipTimestamp(previous.start ?? seconds(owner.timestamp)));
            const nextChapter = snapshot.content[ci + 1];
            const nextSection = si == null ? null : snapshot.content[ci].sections[si + 1];
            const defaultEnd = nextSection ? seconds(nextSection.timestamp) : nextChapter ? seconds(nextChapter.timestamp) : snapshot.duration;
            const end = field('Link end (MM:SS or HH:MM:SS)', clipTimestamp(previous.end ?? defaultEnd));
            const updateTiming = () => { start.parentElement.hidden = end.parentElement.hidden = !custom.checked; };
            custom.addEventListener('change', updateTiming); updateTiming();
            const hint = document.createElement('small');
            hint.textContent = 'Without custom times, the link follows its chapter or section. Custom times are measured from the beginning of the video.';
            form.append(hint);
            const actions = document.createElement('div'); actions.className = 'row-actions';
            const save = button('Save link', () => form.requestSubmit()); save.type = 'button';
            const cancel = button('Cancel', close);
            actions.append(save, cancel); form.append(actions, status); status.textContent = '';
            form.addEventListener('submit', async event => {
                event.preventDefault();
                if (saving || editor !== form) return;
                try {
                    if (JSON.stringify(selection()) !== JSON.stringify(selected)) throw new Error('The selected video or transcript changed. Reopen the link editor.');
                    if (!safeResourceUrl(url.value.trim())) throw new Error('Enter an HTTP or HTTPS URL without embedded credentials.');
                    const link = {title: title.value.trim(), url: url.value.trim()};
                    if (custom.checked) {
                        const parse = input => {
                            if (!/^(?:\d+:)?\d+:[0-5]\d(?:\.\d{1,3})?$/.test(input.value.trim())
                                    || (input.value.trim().split(':').length === 3 && Number(input.value.trim().split(':')[1]) >= 60)) {
                                throw new Error('Use MM:SS or HH:MM:SS for link times.');
                            }
                            return seconds(input.value.trim());
                        };
                        link.start = parse(start); link.end = parse(end);
                        if (!(link.start < link.end && link.end <= snapshot.duration)) throw new Error('End must be after Start and within the video.');
                    }
                    saving = true; save.disabled = cancel.disabled = true;
                    status.textContent = 'Saving link...';
                    const edits = structuredClone(snapshot.content);
                    const target = si == null ? edits[ci] : edits[ci].sections[si];
                    target.resources = [...(target.resources || [])];
                    if (index == null) target.resources.push(link); else target.resources[index] = link;
                    const result = await fetch(`/editable_content/${encodeURIComponent(selected.videoId)}`, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({...selected, revision: snapshot.revision, edits}),
                    });
                    const data = await result.json();
                    if (!result.ok) throw new Error(data.detail || 'Could not save link.');
                    if (JSON.stringify(selection()) === JSON.stringify(selected)) {
                        close(); updateChapters(data.content, {preserveExpansion: true});
                        await refreshTranscriptVersions();
                        showNotification('Further-information link saved.', 'success');
                    }
                } catch (error) { status.textContent = error.message; }
                finally { saving = false; save.disabled = cancel.disabled = false; }
            });
            title.focus();
        } catch (error) { status.textContent = error.message; }
    };
    const remove = async (ci, si, index) => {
        if (saving || editor || state.outlineRowEditing || state.summaryGenerationInProgress || document.querySelector('.is-inline-editing')) {
            showError('Finish the current edit or chapter generation before removing links.'); return;
        }
        if (!window.confirm('Remove this further-information link?')) return;
        const selected = selection();
        const rendered = JSON.stringify(state.videoChapters);
        saving = true;
        try {
            const response = await fetch(`/editable_content/${encodeURIComponent(selected.videoId)}?` + new URLSearchParams(selected));
            const snapshot = await response.json();
            if (!response.ok) throw new Error(snapshot.detail || 'Could not load links.');
            if (JSON.stringify(selection()) !== JSON.stringify(selected)) throw new Error('The selected video changed.');
            if (JSON.stringify(snapshot.content) !== rendered) throw new Error('The saved chapters changed. Reload them before removing a link.');
            const owner = si == null ? snapshot.content[ci] : snapshot.content[ci].sections[si];
            owner.resources.splice(index, 1);
            const result = await fetch(`/editable_content/${encodeURIComponent(selected.videoId)}`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({...selected, revision: snapshot.revision, edits: snapshot.content}),
            });
            const data = await result.json();
            if (!result.ok) throw new Error(data.detail || 'Could not remove link.');
            if (JSON.stringify(selection()) === JSON.stringify(selected)) {
                updateChapters(data.content, {preserveExpansion: true}); await refreshTranscriptVersions();
            }
        } catch (error) { showError(error.message); }
        finally { saving = false; }
    };
    const decorate = () => {
        container.querySelectorAll('.resource-links').forEach(node => node.remove());
        if (container.classList.contains('is-inline-editing')) return;
        const chapters = state.videoChapters || [];
        const attach = (row, ci, si) => {
            const owner = si == null ? chapters[ci] : chapters[ci]?.sections?.[si];
            if (!owner) return;
            const details = document.createElement('details'); details.className = 'resource-links';
            details.addEventListener('click', event => event.stopPropagation());
            details.addEventListener('keydown', event => event.stopPropagation());
            const summary = document.createElement('summary');
            summary.textContent = `Further information (${owner.resources?.length || 0})`;
            details.append(summary);
            for (const [index, link] of (owner.resources || []).entries()) {
                const item = document.createElement('div'); item.className = 'resource-link-row';
                item.append(anchor(link), button('Edit link', () => edit(ci, si, index, details)),
                    button('Remove link', () => void remove(ci, si, index)));
                if (link.start != null) {
                    const timing = document.createElement('span'); timing.textContent = `${clipTimestamp(link.start)} - ${clipTimestamp(link.end)}`;
                    item.append(timing);
                }
                details.append(item);
            }
            details.append(button('Add link', () => edit(ci, si, null, details)));
            row.append(details);
        };
        container.querySelectorAll(':scope > .chapter-item').forEach((row, ci) => attach(row, ci, null));
        container.querySelectorAll('.chapter-section').forEach(row =>
            attach(row, Number(row.dataset.sourceChapterIndex), Number(row.dataset.sourceSectionIndex)));
    };
    const updatePanel = () => {
        const entries = resourceEntries(state.videoChapters, player.duration)
            .filter(link => player.currentTime >= link.start && player.currentTime < link.end);
        const key = JSON.stringify(entries);
        if (key === panelKey) return;
        panelKey = key; panel.hidden = !entries.length; panel.replaceChildren();
        if (!entries.length) return;
        const heading = document.createElement('strong'); heading.textContent = 'Further information - now playing';
        const list = document.createElement('ul');
        for (const link of entries) {
            const item = document.createElement('li');
            const owner = document.createElement('span'); owner.textContent = ` - ${link.owner}`;
            item.append(anchor(link), owner); list.append(item);
        }
        panel.append(heading, list);
    };
    document.addEventListener('chaptersUpdated', () => { close(); decorate(); updatePanel(); });
    document.addEventListener('videoSelectionChanged', () => { close(); panelKey = ''; panel.hidden = true; panel.replaceChildren(); });
    document.addEventListener('transcriptUpdated', () => { if (!saving) close(); });
    window.addEventListener('beforeunload', event => {
        if (editor) { event.preventDefault(); event.returnValue = ''; }
    });
    player.addEventListener('timeupdate', updatePanel);
    player.addEventListener('seeked', updatePanel);
    player.addEventListener('loadedmetadata', updatePanel);
    decorate(); updatePanel();
}
