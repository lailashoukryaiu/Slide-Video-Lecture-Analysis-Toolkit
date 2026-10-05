// Drag handles that let the user resize the visual timeline and the side panel.
(function () {
    const STORAGE_KEY = 'slidedec-layout-sizes';
    const handles = {
        timelineSplitter: {
            variable: '--timeline-width', min: 140, max: 420,
            target: () => document.querySelector('.video-container'),
            // The timeline sits right of the handle, so it grows when dragging left.
            size: (target, x) => target.getBoundingClientRect().right - x - 12,
        },
        panelSplitter: {
            variable: '--side-panel-width', min: 240, max: 640,
            target: () => document.querySelector('.main-content'),
            size: (target, x) => target.getBoundingClientRect().right - x - 6,
        },
    };

    function load() {
        try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; } catch { return {}; }
    }

    function save(sizes) {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(sizes)); } catch { /* storage unavailable */ }
    }

    function apply(id, value) {
        const config = handles[id];
        const target = config.target();
        if (!target) return;
        if (value == null) {
            target.style.removeProperty(config.variable);
            return;
        }
        const clamped = Math.round(Math.min(config.max, Math.max(config.min, value)));
        target.style.setProperty(config.variable, `${clamped}px`);
        return clamped;
    }

    function start() {
        const sizes = load();
        Object.keys(handles).forEach((id) => {
            const handle = document.getElementById(id);
            const config = handles[id];
            if (!handle) return;
            if (sizes[id]) apply(id, sizes[id]);

            const current = () => {
                const value = parseFloat(getComputedStyle(config.target()).getPropertyValue(config.variable));
                return Number.isFinite(value) ? value : (id === 'panelSplitter'
                    ? document.querySelector('.content-tabs').getBoundingClientRect().width
                    : document.querySelector('.timeline-container').getBoundingClientRect().width);
            };
            const commit = (value) => {
                sizes[id] = apply(id, value);
                save(sizes);
                window.dispatchEvent(new Event('resize'));
            };

            handle.addEventListener('pointerdown', (event) => {
                if (event.button !== 0) return;
                event.preventDefault();
                handle.setPointerCapture(event.pointerId);
                handle.classList.add('dragging');
                document.body.classList.add('layout-resizing');
                const move = (moveEvent) => commit(config.size(config.target(), moveEvent.clientX));
                const stop = () => {
                    handle.classList.remove('dragging');
                    document.body.classList.remove('layout-resizing');
                    handle.removeEventListener('pointermove', move);
                    handle.removeEventListener('pointerup', stop);
                    handle.removeEventListener('pointercancel', stop);
                };
                handle.addEventListener('pointermove', move);
                handle.addEventListener('pointerup', stop);
                handle.addEventListener('pointercancel', stop);
            });

            handle.addEventListener('keydown', (event) => {
                const step = event.shiftKey ? 48 : 16;
                if (event.key === 'ArrowLeft') commit(current() + step);
                else if (event.key === 'ArrowRight') commit(current() - step);
                else return;
                event.preventDefault();
            });

            handle.addEventListener('dblclick', () => {
                apply(id, null);
                delete sizes[id];
                save(sizes);
                window.dispatchEvent(new Event('resize'));
            });
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
