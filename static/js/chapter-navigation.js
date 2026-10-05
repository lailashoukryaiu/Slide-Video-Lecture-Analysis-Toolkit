export function chapterStart(chapter) {
    const parts = String(chapter.timestamp).split(':').map(Number);
    if (parts.length < 2 || parts.length > 3 || parts.some((part) => !Number.isFinite(part) || part < 0)) {
        return NaN;
    }
    return parts.reduce((seconds, part) => seconds * 60 + part, 0);
}

export function navigationParts(chapters = [], scenes = [], mode = 'combined') {
    const topics = chapters.map((chapter) => ({start: chapterStart(chapter), title: chapter.title}))
        .filter((part) => Number.isFinite(part.start)).sort((a, b) => a.start - b.start);
    const slides = scenes.map((scene, index) => {
        const topic = topics.filter((part) => part.start <= scene.time_seconds).at(-1);
        return {
            start: Number(scene.time_seconds),
            title: `Slide ${index + 1}${topic?.title ? `: ${topic.title}` : ''}`,
        };
    }).filter((part) => Number.isFinite(part.start));
    const selected = mode === 'topic' ? topics : mode === 'slides' ? slides : [...slides, ...topics];
    return [...new Map(selected.map((part) => [part.start, part])).values()].sort((a, b) => a.start - b.start);
}

export function activePart(parts, time) {
    return parts.filter((part) => part.start <= time + 0.01).at(-1);
}
