// --- State ---
let presetsCache = [];
let eventsCache = [];
let selectedPresetId = null;
let clickerEnabled = false;
const presetModes = {}; // id -> "only_tp" | "autobuy"
let modeEditPresetId = null;
let deletePresetId = null;
let eventSource = null;

// --- Helpers ---
function mapBotStatusToText(status) {
    switch (status) {
        case 'busy':
            return 'Попытка выкупить предмет';
        case 'in_teleporting':
            return 'Teleporting';
        case 'idle':
        default:
            return 'Ожидание';
    }
}
function mapPresetStatusToDotColor(status) {
    switch (status) {
        case 'searching':
            return 'bg-emerald-400';
        case 'connecting':
            return 'bg-amber-400';
        case 'error':
            return 'bg-rose-500';
        case 'stopped':
        default:
            return 'bg-slate-500';
    }
}

function mapPresetStatusToText(status) {
    switch (status) {
        case 'searching':
            return 'Поиск';
        case 'connecting':
            return 'Подключение';
        case 'error':
            return 'Ошибка';
        case 'stopped':
        default:
            return 'Остановлен';
    }
}

function applyBotStatus(botStatus) {
    const statusText = document.getElementById('status-text');
    const statusDot = document.getElementById('status-dot');
    if (!statusText || !statusDot) return;

    const status = botStatus || 'idle';
    statusText.textContent = mapBotStatusToText(status);
    const statusColor =
        status === 'busy' ? 'bg-amber-400' :
        status === 'in_teleporting' ? 'bg-sky-400' : 'bg-emerald-400';
    statusDot.className = 'w-2.5 h-2.5 rounded-full status-dot ' + statusColor;
}

// --- Mode modal ---
function openModeModal(preset) {
    modeEditPresetId = preset.id;
    const modal = document.getElementById('mode-modal');
    const nameEl = document.getElementById('mode-modal-name');
    if (nameEl) nameEl.textContent = preset.name || 'Без названия';
    modal.classList.remove('hidden');
}

function closeModeModal() {
    const modal = document.getElementById('mode-modal');
    modeEditPresetId = null;
    modal.classList.add('hidden');
}

// --- API calls ---
async function loadStatus() {
    try {
        const res = await fetch('/api/status');
        if (!res.ok) return;
        const data = await res.json();
        applyBotStatus(data.bot_status);
    } catch (err) {
        console.error('Failed to load status', err);
    }
}

async function loadEvents() {
    try {
        const res = await fetch('/api/events');
        if (!res.ok) return;
        const data = await res.json();
        eventsCache = Array.isArray(data) ? data : [];
        renderEvents();
    } catch (err) {
        console.error('Failed to load events', err);
    }
}

async function loadPresets() {
    try {
        const res = await fetch('/api/live_search/presets');
        if (!res.ok) return;
        const data = await res.json();
        presetsCache = Array.isArray(data) ? data : [];
        renderPresets();
        renderStats();
    } catch (err) {
        console.error('Failed to load presets', err);
    }
}

async function loadCookies() {
    try {
        const res = await fetch('/api/auth/cookies');
        if (!res.ok) return;
        const data = await res.json();
        const poeInput = document.getElementById('poe-sessid');
        const cfInput = document.getElementById('cf-clearance');
        if (poeInput) poeInput.value = data.poe_sessid || '';
        if (cfInput) cfInput.value = data.cf_clearance || '';
    } catch (err) {
        console.error('Failed to load cookies', err);
    }
}

async function saveCookies() {
    const poeInput = document.getElementById('poe-sessid');
    const cfInput = document.getElementById('cf-clearance');
    const poe_sessid = poeInput.value.trim();
    const cf_clearance = cfInput.value.trim();
    try {
        await fetch('/api/auth/cookies', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ poe_sessid, cf_clearance }),
        });
    } catch (err) {
        console.error('Failed to save cookies', err);
    }
}

async function createPreset() {
    const nameInput = document.getElementById('preset-name');
    const urlInput = document.getElementById('preset-url');
    const modeSelect = document.getElementById('preset-mode');

    const name = nameInput.value.trim();
    const url = urlInput.value.trim();
    const mode = modeSelect.value || 'only_tp';

    if (!url) {
        alert('Введите URL live search.');
        return;
    }

    try {
        const res = await fetch('/api/live_search/presets', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url,
                name: name || null,
                mode,
            }),
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert('Не удалось создать пресет: ' + (err.detail || res.status));
            return;
        }

        // Не модифицируем локальный кэш вручную, ожидаем SSE или ручной reload.
        await loadPresets();

        nameInput.value = '';
        urlInput.value = '';
        modeSelect.value = 'only_tp';
    } catch (err) {
        console.error('Failed to create preset', err);
        alert('Не удалось создать пресет.');
    }
}

async function startPreset(id) {
    await fetch(`/api/live_search/presets/${id}/start`, { method: 'POST' });
    await loadPresets();
}

async function stopPreset(id) {
    await fetch(`/api/live_search/presets/${id}/stop`, { method: 'POST' });
    await loadPresets();
}

async function startAllPresets() {
    toggleBulkButtons(true);
    try {
        await fetch('/api/live_search/presets/start_all', { method: 'POST' });
        await loadPresets();
    } finally {
        toggleBulkButtons(false);
    }
}

async function stopAllPresets() {
    toggleBulkButtons(true);
    try {
        await fetch('/api/live_search/presets/stop_all', { method: 'POST' });
        await loadPresets();
    } finally {
        toggleBulkButtons(false);
    }
}

function toggleBulkButtons(disabled) {
    const btnStartAll = document.getElementById('btn-start-all');
    const btnStopAll = document.getElementById('btn-stop-all');
    [btnStartAll, btnStopAll].forEach(btn => {
        if (!btn) return;
        btn.disabled = disabled;
        btn.style.opacity = disabled ? '0.6' : '1';
    });
}

async function updatePresetModeOnBackend(presetId, uiMode) {
    const res = await fetch(`/api/live_search/presets/${presetId}/mode`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: uiMode }),
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert('Не удалось обновить режим: ' + (err.detail || res.status));
        throw new Error('Failed to update preset mode');
    }

    await loadPresets();
}

// --- Render ---
function renderEvents() {
    const list = document.getElementById('events-list');
    if (!list) return;
    list.innerHTML = '';

    [...eventsCache].slice(-200).reverse().forEach(event => {
        const row = document.createElement('div');
        row.className = 'flex items-start gap-2';

        const levelColor =
            event.level === 'error'
                ? 'text-rose-400'
                : event.level === 'warning'
                    ? 'text-amber-300'
                    : 'text-sky-300';

        row.innerHTML = `
            <span class="mt-0.5 text-[0.65rem] ${levelColor}">●</span>
            <p class="text-[0.72rem] text-slate-200">${event.message}</p>
        `;
        list.appendChild(row);
    });
}

function renderPresets() {
    const container = document.getElementById('presets-list');
    container.innerHTML = '';

    if (!presetsCache.length) {
        const empty = document.createElement('div');
        empty.className = 'text-[0.75rem] text-slate-500 mt-2';
        empty.textContent = 'Нет сохранённых пресетов. Добавьте первый.';
        container.appendChild(empty);
        selectedPresetId = null;
        return;
    }

    presetsCache.forEach((preset, index) => {
        const isSelected = selectedPresetId
            ? preset.id === selectedPresetId
            : index === 0;

        if (!selectedPresetId && index === 0) {
            selectedPresetId = preset.id;
        }

        const row = document.createElement('div');
        let baseClass =
            'grid grid-cols-12 items-center px-2 py-2 rounded-xl border transition cursor-pointer ';
        if (isSelected) {
            baseClass += 'bg-slate-900/80 border-accent/60 shadow-[0_0_15px_rgba(168,85,247,0.35)]';
        } else {
            baseClass += 'border-transparent hover:border-slate-700/80 hover:bg-slate-900/60';
        }
        row.className = baseClass;

        const mode = preset.mode || presetModes[preset.id] || 'only_tp';
        presetModes[preset.id] = mode;
        const modeLabel = mode === 'autobuy' ? 'Автопокупка' : 'Только ТП';

        const statusDotColor = mapPresetStatusToDotColor(preset.status);
        const statusText = mapPresetStatusToText(preset.status);
        const isActive = preset.status === 'searching' || preset.status === 'connecting';

        row.innerHTML = `
            <div class="col-span-4 flex flex-col">
                <span class="text-[0.8rem] text-slate-100">${preset.name || 'Live search preset'}</span>
                <span class="text-[0.65rem] text-slate-500 truncate">${preset.url}</span>
            </div>
            <div class="col-span-3 flex items-center gap-2 text-[0.7rem]">
                <button data-action="mode"
                        class="px-2 py-0.5 rounded-full border border-slate-600/70 text-slate-200 hover:border-accent hover:text-accent transition">
                    Режим: ${modeLabel}
                </button>
            </div>
            <div class="col-span-2 flex items-center gap-2 text-[0.75rem]">
                <span class="w-2 h-2 rounded-full status-dot ${statusDotColor}"></span>
                <span class="text-slate-200">${statusText}</span>
            </div>
            <div class="col-span-3 flex items-center justify-end gap-2">
                <button data-action="toggle"
                        class="w-9 h-9 rounded-full border border-slate-700 bg-slate-900/80 text-slate-200 hover:border-accent hover:text-accent transition flex items-center justify-center">
                    ${isActive
                        ? '<span class="text-lg leading-none">■</span>'
                        : '<span class="text-lg leading-none">▶</span>'}
                </button>
                <button data-action="delete"
                        class="w-9 h-9 rounded-full border border-slate-700 bg-slate-900/80 text-slate-300 hover:border-rose-400 hover:text-rose-200 transition flex items-center justify-center text-lg leading-none">
                    ✕
                </button>
            </div>
        `;

        row.addEventListener('click', (e) => {
            if (e.target.closest('button')) return;
            selectedPresetId = preset.id;
            renderPresets();
        });

        const btnMode = row.querySelector('button[data-action="mode"]');
        const btnToggle = row.querySelector('button[data-action="toggle"]');
        const btnDelete = row.querySelector('button[data-action="delete"]');

        btnMode.addEventListener('click', (e) => {
            e.stopPropagation();
            openModeModal(preset);
        });

        btnToggle.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (isActive) {
                await stopPreset(preset.id);
            } else {
                await startPreset(preset.id);
            }
        });

        btnDelete.addEventListener('click', async (e) => {
            e.stopPropagation();
            deletePresetId = preset.id;
            openDeleteModal();
        });

        container.appendChild(row);
    });
}

function renderStats() {
    const teleportsEl = document.getElementById('stat-teleports');
    const boughtEl = document.getElementById('stat-bought');
    const successEl = document.getElementById('stat-success');
    const tbody = document.getElementById('stats-presets-body');
    if (!teleportsEl || !tbody) return;

    teleportsEl.textContent = '0';
    boughtEl.textContent = '0';
    successEl.textContent = '0%';

    tbody.innerHTML = '';

    if (!presetsCache.length) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td colspan="5"
                class="py-2 text-[0.75rem] text-slate-500 text-center">
                Нет данных по пресетам.
            </td>`;
        tbody.appendChild(tr);
        return;
    }

    presetsCache.forEach(preset => {
        const tr = document.createElement('tr');
        const statusText = mapPresetStatusToText(preset.status);
        tr.className = 'border-t border-slate-800/80';
        tr.innerHTML = `
            <td class="py-1.5 pr-2 text-[0.78rem] text-slate-100">
                ${preset.name || 'Live search preset'}
            </td>
            <td class="py-1.5 px-2 text-[0.75rem] text-slate-400">
                ${statusText}
            </td>
            <td class="py-1.5 px-2 text-[0.75rem] text-center text-slate-300">—</td>
            <td class="py-1.5 px-2 text-[0.75rem] text-center text-slate-300">—</td>
            <td class="py-1.5 pl-2 text-[0.75rem] text-center text-slate-300">—</td>
        `;
        tbody.appendChild(tr);
    });
}

// --- SSE ---
function initSSE() {
    if (eventSource) {
        eventSource.close();
    }
    const es = new EventSource('/api/stream');
    es.addEventListener('status', (e) => {
        try {
            const data = JSON.parse(e.data);
            applyBotStatus(data.bot_status);
        } catch (err) {
            console.error('Failed to parse status event', err);
        }
    });
    es.addEventListener('event', (e) => {
        try {
            const ev = JSON.parse(e.data);
            eventsCache.push(ev);
            if (eventsCache.length > 200) {
                eventsCache = eventsCache.slice(-200);
            }
            renderEvents();
        } catch (err) {
            console.error('Failed to parse event', err);
        }
    });
    es.addEventListener('presets', (e) => {
        try {
            const payload = JSON.parse(e.data);
            if (payload && payload.presets) {
                presetsCache = payload.presets;
                renderPresets();
                renderStats();
            }
        } catch (err) {
            console.error('Failed to parse presets', err);
        }
    });
    es.onerror = (err) => {
        console.warn('SSE connection error, will retry...', err);
    };
    eventSource = es;
}

// --- Init UI ---
function initUI() {
    document.getElementById('btn-add-preset').addEventListener('click', createPreset);
    document.getElementById('btn-start-all').addEventListener('click', startAllPresets);
    document.getElementById('btn-stop-all').addEventListener('click', stopAllPresets);
    document.getElementById('btn-save-cookies').addEventListener('click', saveCookies);

    const clickerBtn = document.getElementById('clicker-button');
    const clickerState = document.getElementById('clicker-state');
    clickerBtn.addEventListener('click', () => {
        clickerEnabled = !clickerEnabled;
        clickerState.textContent = clickerEnabled ? 'ON' : 'OFF';
        clickerBtn.style.opacity = clickerEnabled ? '1' : '0.6';
    });

    const modal = document.getElementById('mode-modal');
    const closeBtn = document.getElementById('mode-modal-close');
    closeBtn.addEventListener('click', closeModeModal);
    modal.addEventListener('click', (e) => {
        if (e.target === e.currentTarget) {
            closeModeModal();
        }
    });

    document.querySelectorAll('#mode-modal button[data-mode]').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!modeEditPresetId) return;
            const uiMode = btn.dataset.mode; // "only_tp" | "autobuy"
            try {
                await updatePresetModeOnBackend(modeEditPresetId, uiMode);
            } catch (e) {
                console.error(e);
                return;
            }
            presetModes[modeEditPresetId] = uiMode;
            closeModeModal();
            renderPresets();
            renderStats();
        });
    });

    const deleteModal = document.getElementById('delete-modal');
    const deleteClose = document.getElementById('delete-modal-close');
    const deleteCancel = document.getElementById('delete-cancel');
    const deleteConfirm = document.getElementById('delete-confirm');

    deleteClose.addEventListener('click', closeDeleteModal);
    deleteCancel.addEventListener('click', closeDeleteModal);
    deleteConfirm.addEventListener('click', async () => {
        if (!deletePresetId) return;
        const res = await fetch(`/api/live_search/presets/${deletePresetId}`, {
            method: 'DELETE',
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert('Не удалось удалить пресет: ' + (err.detail || res.status));
            return;
        }
        delete presetModes[deletePresetId];
        if (selectedPresetId === deletePresetId) {
            selectedPresetId = null;
        }
        deletePresetId = null;
        closeDeleteModal();
        await loadPresets();
        renderStats();
    });
}

async function init() {
    initUI();
    await Promise.all([
        loadStatus(),
        loadPresets(),
        loadEvents(),
        loadCookies(),
    ]);
    initSSE();
}

window.addEventListener('DOMContentLoaded', init);

function openDeleteModal() {
    const modal = document.getElementById('delete-modal');
    modal.classList.remove('hidden');
}

function closeDeleteModal() {
    const modal = document.getElementById('delete-modal');
    modal.classList.add('hidden');
    deletePresetId = null;
}
