import { clipSeconds } from './custom-clip-range.js';

const effects = [['blur', 'Blur'], ['pixelate', 'Pixelate'], ['cover', 'Solid cover']];
const limit = 64;

export function currentPrivacyScene(scenes, time, duration) {
    const starts = scenes.map(scene => Number(scene.time_seconds ?? clipSeconds(scene.timestamp || '')));
    const index = starts.findIndex((start, i) => Number.isFinite(start) && start <= time
        && (i === starts.length - 1 || starts[i + 1] > time));
    if (index < 0 || starts[index] >= duration) return null;
    return {scene: scenes[index], start: starts[index], end: Math.min(starts[index + 1] ?? duration, duration)};
}

export function detectionPrivacyBox(detection, imageSize) {
    if (!Array.isArray(imageSize) || imageSize.length !== 2 || !imageSize.every(size => Number.isFinite(size) && size > 0)
        || !Array.isArray(detection.bbox) || detection.bbox.length !== 4
        || !detection.bbox.every(Number.isFinite)) throw new Error('Detection coordinates or source image size are missing.');
    const [left, top, right, bottom] = detection.bbox;
    const [width, height] = imageSize;
    const x = Math.max(0, left / width), y = Math.max(0, top / height);
    const w = Math.min(1, right / width) - x, h = Math.min(1, bottom / height) - y;
    if (w <= 0 || h <= 0) throw new Error('The detected region is outside the video.');
    return {x, y, width: w, height: h};
}

export function initClipPrivacy({state, player, mediaExportId, doc = document}) {
    const get = id => doc.getElementById(id);
    const canvas = get('clipPrivacyPreview');
    if (!canvas) return {open() {}, close() {}, settings: () => []};
    const enabled = get('clipPrivacyEnabled'), status = get('clipPrivacyStatus');
    const scanButton = get('clipPrivacyScan'), list = get('clipPrivacyRegions');
    const effect = get('clipPrivacyEffect'), detected = get('clipPrivacyDetection');
    const clearButton = get('clipPrivacyClear'), undoButton = get('clipPrivacyUndo');
    const blurStrength = get('clipPrivacyBlurStrength'), blurValue = get('clipPrivacyBlurValue');
    const context = canvas.getContext('2d');
    const snapshot = doc.createElement('canvas'), patch = doc.createElement('canvas');
    let regions = [], detections = [], scene = null, identity = null, drag = null;
    let active = false, scanId = null, generation = 0, timer = null, rejectWait = null;
    let previewFailed = false;
    let history = [];
    let adjustingStrength = false;
    const remember = () => {
        history.push({regions: structuredClone(regions), enabled: enabled.checked, strength: blurValue.value || '24'});
        if (history.length > 50) history.shift();
    };
    const message = (text, error = false) => {
        status.textContent = text;
        status.classList.toggle('outline-error', error);
    };
    const identityNow = () => `${state.currentVideoId}:${mediaExportId() || ''}`;
    const read = async (url, options) => {
        const response = await fetch(url, options);
        const data = await response.json();
        if (!response.ok || data.success === false) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
        return data;
    };
    const draw = () => {
        if (!active || !player.videoWidth || !player.videoHeight) return;
        try {
            canvas.width = Math.min(960, player.videoWidth);
            canvas.height = Math.round(canvas.width * player.videoHeight / player.videoWidth);
            snapshot.width = canvas.width;
            snapshot.height = canvas.height;
            snapshot.getContext('2d').drawImage(player, 0, 0, canvas.width, canvas.height);
            context.drawImage(snapshot, 0, 0);
            for (const region of regions) {
                if (!enabled.checked || player.currentTime < region.start || player.currentTime >= region.end) continue;
                const x = region.x * canvas.width, y = region.y * canvas.height;
                const w = region.width * canvas.width, h = region.height * canvas.height;
                context.save();
                context.beginPath();
                context.rect(x, y, w, h);
                context.clip();
                if (region.effect === 'cover') {
                    context.fillStyle = '#000';
                    context.fillRect(x, y, w, h);
                } else if (region.effect === 'blur') {
                    context.filter = `blur(${Number(blurStrength.value) * canvas.width / player.videoWidth}px)`;
                    context.drawImage(snapshot, 0, 0);
                } else {
                    patch.width = Math.max(1, Math.round(w / 16));
                    patch.height = Math.max(1, Math.round(h / 16));
                    patch.getContext('2d').drawImage(snapshot, x, y, w, h, 0, 0, patch.width, patch.height);
                    context.imageSmoothingEnabled = false;
                    context.drawImage(patch, x, y, w, h);
                }
                context.restore();
                context.strokeStyle = '#f59e0b';
                context.lineWidth = 2;
                context.strokeRect(x, y, w, h);
            }
            const candidate = detections[Number(detected.value)];
            if (candidate) {
                const box = detectionPrivacyBox(candidate, scene?.scene.yolo_detections?.image_size);
                const x = box.x * canvas.width, y = box.y * canvas.height;
                const w = box.width * canvas.width, h = box.height * canvas.height;
                context.save();
                context.fillStyle = 'rgba(34, 211, 238, 0.2)';
                context.fillRect(x, y, w, h);
                context.strokeStyle = '#22d3ee';
                context.lineWidth = 3;
                context.setLineDash([8, 4]);
                context.strokeRect(x, y, w, h);
                context.restore();
            }
        } catch (error) {
            if (!previewFailed) message(`Could not draw the privacy preview: ${error.message}`, true);
            previewFailed = true;
        }
    };
    const select = (options, value, change) => {
        const control = doc.createElement('select');
        for (const [id, label] of options) {
            const option = doc.createElement('option');
            option.value = id; option.textContent = label;
            control.append(option);
        }
        control.value = value;
        control.addEventListener('change', () => { remember(); change(control.value); render(); draw(); });
        return control;
    };
    const render = () => {
        clearButton.disabled = !regions.length;
        undoButton.disabled = !history.length;
        list.replaceChildren();
        regions.forEach((region, index) => {
            const row = doc.createElement('details'), summary = doc.createElement('summary');
            summary.textContent = `Region ${index + 1} (${region.start.toFixed(2)}-${region.end.toFixed(2)}s)`;
            row.append(summary);
            const appearance = select(effects, region.effect, value => { region.effect = value; });
            appearance.setAttribute('aria-label', `Region ${index + 1} appearance`);
            row.append(appearance);
            const options = [['range', 'Entire exported clip']];
            if (region.sceneStart !== undefined) options.unshift(['scene', 'Only this slide / scene']);
            if (region.detectedStart !== undefined) options.unshift(['detected', 'Only detected intervals']);
            const scope = select(options, region.scope, value => {
                region.scope = value;
                region.start = value === 'scene' ? region.sceneStart : value === 'detected' ? region.detectedStart : 0;
                region.end = value === 'scene' ? region.sceneEnd : value === 'detected' ? region.detectedEnd : player.duration;
                summary.textContent = `Region ${index + 1} (${region.start.toFixed(2)}-${region.end.toFixed(2)}s)`;
            });
            scope.setAttribute('aria-label', `Region ${index + 1} timing`);
            row.append(scope);
            for (const key of ['x', 'y', 'width', 'height']) {
                const label = doc.createElement('label'), input = doc.createElement('input');
                label.textContent = `${key} (%) `;
                input.type = 'number'; input.min = '0'; input.max = '100'; input.step = '0.1';
                input.value = (region[key] * 100).toFixed(1);
                input.addEventListener('change', () => {
                    const value = Number(input.value) / 100;
                    const old = region[key];
                    region[key] = value;
                    if (!input.value.trim() || !Number.isFinite(value) || region.x < 0 || region.y < 0
                        || region.width <= 0 || region.height <= 0 || region.x + region.width > 1
                        || region.y + region.height > 1) {
                        region[key] = old; input.value = (old * 100).toFixed(1);
                        message('Keep each region inside the video, with positive width and height.', true);
                    } else {
                        region[key] = old; remember(); region[key] = value;
                        undoButton.disabled = false;
                        message(''); draw();
                    }
                });
                label.append(input); row.append(label);
            }
            const preview = doc.createElement('button');
            preview.type = 'button'; preview.className = 'btn btn-secondary'; preview.textContent = 'Preview / seek';
            preview.onclick = () => { player.currentTime = region.start; draw(); };
            const remove = doc.createElement('button');
            remove.type = 'button'; remove.className = 'btn btn-secondary'; remove.textContent = 'Remove region';
            remove.onclick = () => { remember(); regions.splice(index, 1); render(); draw(); };
            row.append(preview, remove);
            list.append(row);
        });
    };
    const refreshDetections = () => {
        scene = currentPrivacyScene(state.videoScenes || [], player.currentTime, player.duration);
        detections = scene?.scene.yolo_detections?.detections || [];
        detected.replaceChildren();
        detections.forEach((item, index) => {
            const option = doc.createElement('option');
            option.value = String(index); option.textContent = `${index + 1}: ${item.class || 'Detected region'}`;
            detected.append(option);
        });
        detected.disabled = !detections.length;
        get('clipPrivacyAddDetection').disabled = !detections.length;
    };
    const add = box => {
        if (regions.length >= limit) { message(`Choose at most ${limit} regions.`, true); return; }
        remember();
        enabled.checked = true;
        regions.push({...box, effect: effect.value, scope: scene ? 'scene' : 'range',
            start: scene?.start ?? 0, end: scene?.end ?? player.duration,
            ...(scene ? {sceneStart: scene.start, sceneEnd: scene.end} : {})});
        render(); draw();
        message('Region added. Review its appearance and timing below.');
    };
    const stopScan = async () => {
        generation++;
        scanButton.disabled = false;
        clearTimeout(timer);
        rejectWait?.(new DOMException('Privacy scan stopped', 'AbortError'));
        rejectWait = null;
        if (!scanId) return true;
        const id = scanId; scanId = null;
        const version = generation;
        scanButton.disabled = true;
        scanButton.textContent = 'Find people in clip';
        try { await read(`/privacy_scan_jobs/${id}/cancel`, {method: 'POST'}); return true; }
        catch (error) {
            if (version === generation) {
                scanId = id;
                scanButton.textContent = 'Retry stopping privacy scan';
                message(`Could not stop privacy scan: ${error.message}`, true);
            }
            return false;
        } finally {
            if (version === generation) scanButton.disabled = false;
        }
    };
    scanButton.onclick = async () => {
        if (scanId) { if (await stopScan()) message('Privacy scan stop requested.'); return; }
        const start = get('customClipStart').value.trim(), end = get('customClipEnd').value.trim();
        if (!(clipSeconds(start) >= 0 && clipSeconds(end) > clipSeconds(start) && clipSeconds(end) <= player.duration)) {
            message('Choose a valid clip range before scanning.', true); return;
        }
        const version = ++generation, sourceIdentity = identityNow();
        let finished = false;
        scanButton.disabled = true;
        message('Starting local people detection...');
        try {
            const data = await read(`/privacy_scans/${encodeURIComponent(state.currentVideoId)}`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({start, end, effect: effect.value, media_export_id: mediaExportId()}),
            });
            if (version !== generation || sourceIdentity !== identityNow() || !active) {
                await read(`/privacy_scan_jobs/${data.job_id}/cancel`, {method: 'POST'}); return;
            }
            scanId = data.job_id;
            scanButton.disabled = false;
            scanButton.textContent = 'Stop privacy scan';
            while (version === generation && active && sourceIdentity === identityNow()) {
                const status = await read(`/privacy_scan_jobs/${scanId}`);
                if (version !== generation || sourceIdentity !== identityNow() || !active) return;
                message(status.steps?.at(-1)?.message || 'Scanning...');
                if (['error', 'cancelled', 'complete'].includes(status.status)) finished = true;
                if (status.status === 'error') throw new Error(status.error);
                if (status.status === 'cancelled') { message('Privacy scan stopped.'); return; }
                if (status.status === 'complete') {
                    if (regions.length + status.regions.length > limit) throw new Error('Too many regions. Remove existing regions or scan a shorter clip.');
                    if (status.regions.length) remember();
                    regions.push(...status.regions.map(region => ({...region, scope: 'detected',
                        detectedStart: region.start, detectedEnd: region.end})));
                    if (status.regions.length) enabled.checked = true;
                    render(); draw();
                    message(`Found ${status.regions.length} candidate regions. Review and correct them; detection can miss people.`);
                    return;
                }
                await new Promise((resolve, reject) => {
                    rejectWait = reject;
                    timer = setTimeout(() => { rejectWait = null; resolve(); }, 1000);
                });
            }
        } catch (error) {
            if (error.name !== 'AbortError') message(`Privacy scan failed: ${error.message}`, true);
        } finally {
            if (version === generation) {
                if (scanId && !finished) {
                    try { await read(`/privacy_scan_jobs/${scanId}/cancel`, {method: 'POST'}); }
                    catch (error) { message(`Could not stop privacy scan: ${error.message}`, true); }
                }
                scanId = null; scanButton.disabled = false; scanButton.textContent = 'Find people in clip';
            }
        }
    };
    get('clipPrivacyAddManual').onclick = () => add({x: 0.1, y: 0.1, width: 0.3, height: 0.3});
    blurStrength.addEventListener('input', () => {
        const strength = Number(blurStrength.value);
        if (!Number.isFinite(strength) || strength < 1 || strength > 48) {
            message('Blur strength must be from 1 to 48.', true); return;
        }
        if (!adjustingStrength) remember();
        adjustingStrength = true;
        blurValue.value = blurStrength.value;
        undoButton.disabled = false;
        draw();
    });
    blurStrength.addEventListener('change', () => { adjustingStrength = false; });
    clearButton.onclick = async () => {
        if (!regions.length || (scanId && !await stopScan())) return;
        remember(); regions = []; enabled.checked = false;
        render(); draw(); message('All privacy regions cleared. Undo restores them.');
    };
    undoButton.onclick = async () => {
        if (!history.length || (scanId && !await stopScan())) return;
        const previous = history.pop();
        regions = previous.regions; enabled.checked = previous.enabled;
        blurStrength.value = blurValue.value = previous.strength;
        adjustingStrength = false;
        render(); draw(); message('Last region change undone.');
    };
    get('clipPrivacyAddDetection').onclick = () => {
        try { add(detectionPrivacyBox(detections[Number(detected.value)], scene?.scene.yolo_detections?.image_size)); }
        catch (error) { message(error.message, true); }
    };
    const point = event => {
        const bounds = canvas.getBoundingClientRect();
        return {x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
            y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height))};
    };
    canvas.addEventListener('pointerdown', event => {
        if (event.button !== 0 || !active) return;
        event.preventDefault(); drag = {...point(event), pointerId: event.pointerId}; canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove', event => {
        if (!drag || drag.pointerId !== event.pointerId) return;
        const end = point(event);
        draw();
        context.strokeStyle = '#f59e0b';
        context.lineWidth = 2;
        context.strokeRect(Math.min(drag.x, end.x) * canvas.width, Math.min(drag.y, end.y) * canvas.height,
            Math.abs(drag.x - end.x) * canvas.width, Math.abs(drag.y - end.y) * canvas.height);
    });
    canvas.addEventListener('pointerup', event => {
        if (!drag || drag.pointerId !== event.pointerId) return;
        const end = point(event);
        const box = {x: Math.min(drag.x, end.x), y: Math.min(drag.y, end.y),
            width: Math.abs(drag.x - end.x), height: Math.abs(drag.y - end.y)};
        drag = null;
        if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
        if (box.width < 0.005 || box.height < 0.005) { message('Draw a larger privacy box.', true); return; }
        add(box);
    });
    canvas.addEventListener('pointercancel', () => { drag = null; });
    player.addEventListener('seeked', () => { if (active) { refreshDetections(); draw(); } });
    player.addEventListener('timeupdate', () => {
        if (active && currentPrivacyScene(state.videoScenes || [], player.currentTime, player.duration)?.scene !== scene?.scene) {
            refreshDetections();
        }
        draw();
    });
    detected.addEventListener('change', draw);
    enabled.addEventListener('change', draw);
    get('clipPrivacyOptions').addEventListener('toggle', draw);
    return {
        open() {
            if (identity !== identityNow()) { regions = []; history = []; enabled.checked = false; identity = identityNow(); }
            active = true; previewFailed = false;
            refreshDetections(); render(); draw(); message('');
        },
        close() {
            active = false;
            if (drag && canvas.hasPointerCapture(drag.pointerId)) canvas.releasePointerCapture(drag.pointerId);
            drag = null; void stopScan();
        },
        settings() {
            if (scanId || scanButton.disabled) throw new Error('Finish or stop the privacy scan and review its regions first.');
            if (!enabled.checked) return [];
            if (!active || identity !== identityNow()) throw new Error('The video source changed. Reopen the custom clip dialog.');
            if (!regions.length) throw new Error('Add and review at least one privacy region, or disable privacy masks.');
            const start = clipSeconds(get('customClipStart').value.trim()), end = clipSeconds(get('customClipEnd').value.trim());
            if (!regions.some(region => region.start < end && region.end > start)) {
                throw new Error('No privacy regions overlap this clip. Adjust their timing or turn masking off.');
            }
            return regions.map(({x, y, width, height, start, end, effect}) => ({
                x, y, width, height, start, end, effect,
                ...(effect === 'blur' ? {blur_strength: Number(blurStrength.value)} : {}),
            }));
        },
    };
}
