import { state } from './main.js';
import { elements } from './elements.js';
import { updateChapters, attachChapterSlides } from './chapters.js';
import { updateScenes } from './scenes.js';
import { showError, showNotification } from './ui.js';
import { startProcessButton } from './progress-time.js';
import { contentLanguage, contentPreference, setContentLanguage } from './content-languages.js';
import { openOptionsDialog } from './utils.js';

export function initChapterTools() {
    const actions = [
        ['enhanceChaptersBtn', 'chapters', 'enhance'],
        ['reviewDisruptionsBtn', 'chapters', 'review'],
        ['confirmChapterTranslationBtn', 'chapters', 'translate'],
        ['translateSlideTitlesBtn', 'slides', 'translate'],
    ];
    let busy = false;
    let translationContext = '';
    const translationDialog = document.getElementById('chapterTranslationDialog');
    const translationKey = () => JSON.stringify([state.currentVideoId, state.currentTranscriptVersionId,
        state.currentTranslationLanguage, state.videoChapters]);
    const languages = {en: 'English', de: 'German', ar: 'Arabic', pl: 'Polish'};
    document.getElementById('translateChaptersBtn').addEventListener('click', async () => {
        if (busy || !state.videoChapters?.length) return;
        translationContext = translationKey();
        const videoId = state.currentVideoId;
        const select = document.getElementById('contentTranslationTarget');
        const status = document.getElementById('chapterTranslationContext');
        const option = document.createElement('option');
        option.value = 'transcript';
        option.textContent = `Open transcript (${languages[state.currentTranslationLanguage] || 'original language'})`;
        select.replaceChildren(option);
        openOptionsDialog(translationDialog);
        status.textContent = 'Loading saved transcript translations...';
        try {
            const response = await fetch(`/transcript_versions/${encodeURIComponent(videoId)}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Could not load saved transcripts.');
            if (translationContext !== translationKey()) return;
            const saved = new Map((data.versions || []).filter(v => v.kind === 'translation')
                .map(v => [v.language, v.label]));
            for (const [language, label] of saved) {
                const item = document.createElement('option');
                item.value = language;
                item.textContent = `Saved transcript: ${languages[language] || language} (${label})`;
                select.append(item);
            }
            const others = document.createElement('optgroup');
            others.label = 'Other languages (no saved transcript translation)';
            for (const [language, label] of Object.entries(languages)) {
                if (saved.has(language) || language === state.currentTranslationLanguage) continue;
                const item = document.createElement('option');
                item.value = language;
                item.textContent = label;
                others.append(item);
            }
            select.append(others);
            status.textContent = `${state.videoChapters.length} open chapters. Choose the transcript language to match.`;
        } catch (error) {
            status.textContent = error.message;
        }
    });
    let displayedContext = '';
    const refresh = () => {
        const context = `${state.currentVideoId || ''}:${state.currentTranslationLanguage || ''}`;
        if (!busy && context !== displayedContext) {
            document.getElementById('contentTranslationTarget').value =
                contentPreference(state.currentVideoId, 'chapters') || 'transcript';
            document.getElementById('slideTranslationTarget').value =
                contentPreference(state.currentVideoId, 'slides') || 'transcript';
            displayedContext = context;
        }
        for (const [id, kind] of actions) {
            if (!document.getElementById(id)) continue;
            document.getElementById(id).disabled = busy || state.summaryGenerationInProgress
                || !state.currentVideoId || !(kind === 'slides' ? state.videoScenes : state.videoChapters)?.length;
        }
        document.getElementById('translateChaptersBtn').disabled = busy || state.summaryGenerationInProgress
            || !state.currentVideoId || !state.videoChapters?.length;
    };
    for (const event of ['chaptersUpdated', 'slidesUpdated', 'transcriptUpdated', 'summaryGenerationChanged']) {
        document.addEventListener(event, refresh);
    }
    for (const [id, kind, operation] of actions) {
        const button = document.getElementById(id);
        if (!button) continue;
        button.addEventListener('click', async () => {
            if (busy || state.summaryGenerationInProgress) return;
            if (state.outlineRowEditing || state.resourceLinkEditing) { showError('Save or cancel the open row or link edit first.'); return; }
            if (id === 'confirmChapterTranslationBtn') {
                if (translationContext !== translationKey()) {
                    showError('The video, transcript or chapters changed. Reopen Translate chapters first.');
                    return;
                }
            }
            const videoId = state.currentVideoId;
            const transcriptLanguage = state.currentTranslationLanguage || '';
            const versionId = state.currentTranscriptVersionId || '';
            const source = state.currentTranscriptSource === 'uploaded' ? 'uploaded'
                : state.currentTranscriptSource === 'youtube' ? 'youtube' : 'whisper';
            const language = contentLanguage(videoId, kind, transcriptLanguage);
            const original = JSON.stringify(kind === 'slides' ? state.videoScenes : state.videoChapters);
            const target = document.getElementById(kind === 'slides'
                ? 'slideTranslationTarget' : 'contentTranslationTarget').value;
            const label = button.textContent;
            busy = true;
            state.summaryGenerationInProgress = true;
            elements.generateSummaryBtn.disabled = true;
            refresh();
            const runningLabel = operation === 'review' ? 'Reviewing possible disruptions'
                : operation === 'enhance' ? 'Reviewing topic boundaries...' : 'Translating saved text...';
            const stopElapsed = startProcessButton(button, runningLabel);
            try {
                const response = await fetch(`/chapter_tools/${encodeURIComponent(videoId)}`, {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        kind, operation, language, source, version_id: versionId,
                        transcript_language: transcriptLanguage,
                        chapter_language: contentLanguage(videoId, 'chapters', transcriptLanguage),
                        target_language: target === 'transcript' ? transcriptLanguage || 'transcript' : target,
                        model: elements.summaryModel?.value || null,
                        detail: elements.chapterDetail?.value || 'balanced',
                        instructions: (elements.chapterInstructions?.value || '').trim(),
                    }),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || `${operation} failed (${response.status})`);
                const sameContext = videoId === state.currentVideoId
                    && transcriptLanguage === (state.currentTranslationLanguage || '')
                    && versionId === (state.currentTranscriptVersionId || '')
                    && original === JSON.stringify(kind === 'slides' ? state.videoScenes : state.videoChapters);
                if (sameContext) {
                    setContentLanguage(videoId, kind, data.language || null);
                    if (kind === 'chapters') updateChapters(data.content);
                    else {
                        updateScenes(data.content, elements.videoPlayer);
                        attachChapterSlides();
                    }
                }
                showNotification(sameContext
                    ? operation === 'review' ? 'Review complete. Grey suggestions stay included until you choose to skip them.'
                        : operation === 'enhance' ? 'Chapters enhanced and saved.' : 'Translation saved. Timing and structure are unchanged.'
                    : 'Result saved for the video and content that were open when you started. Reload it to view the result.',
                'success');
                if (id === 'confirmChapterTranslationBtn') translationDialog.close();
            } catch (error) {
                showError(`Could not ${operation} ${kind}: ${error.message}. Existing content was kept.`);
            } finally {
                stopElapsed();
                button.textContent = label;
                busy = false;
                state.summaryGenerationInProgress = false;
                elements.generateSummaryBtn.disabled = !state.currentTranscript?.length;
                refresh();
            }
        });
    }
    refresh();
}
