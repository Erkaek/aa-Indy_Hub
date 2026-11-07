/* Display local blueprints immediately and update the results in the background. */

class PersonalBlueprintList {
    constructor(options = {}) {
        this.options = options;
        this.results = document.getElementById(options.resultsId || 'blueprintResults');
        this.form = document.getElementById(options.formId || 'blueprintFilters');
        this.status = document.getElementById(options.statusId || 'blueprintLiveStatus');
        this.controller = null;
        this.timer = null;
        this.filterTimer = null;
        this.refreshRunning = false;
        this.schedulingRefresh = false;
        this.stopped = false;
        this.form.addEventListener('submit', event => {
            event.preventDefault();
            clearTimeout(this.filterTimer);
            this.load(this.filterURL(), true);
        });
        this.form.addEventListener('input', event => {
            if (event.target.id !== (this.options.searchId || 'blueprintSearch')) return;
            clearTimeout(this.filterTimer);
            this.filterTimer = setTimeout(() => this.load(this.filterURL(), 'replace'), 300);
        });
        this.form.addEventListener('change', event => {
            if (event.target.tagName !== 'SELECT') return;
            clearTimeout(this.filterTimer);
            this.load(this.filterURL(), 'replace');
        });
        this.results.addEventListener('click', event => {
            const link = event.target.closest('.pagination a');
            if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            this.load(new URL(link.href), true);
        });
        this.results.addEventListener('change', event => {
            if (event.target.id !== 'perPage') return;
            const url = this.dataURL();
            url.searchParams.set('per_page', event.target.value);
            url.searchParams.delete('page');
            this.load(url, true);
        });
        document.getElementById('exportBtn')?.addEventListener('click', () => this.exportCSV());
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                clearTimeout(this.timer);
                clearTimeout(this.filterTimer);
                this.controller?.abort();
            } else {
                this.load(this.dataURL());
            }
        });
        window.addEventListener('pagehide', () => {
            this.stopped = true;
            clearTimeout(this.timer);
            clearTimeout(this.filterTimer);
            this.controller?.abort();
        });
        window.addEventListener('pageshow', event => {
            if (event.persisted) {
                this.stopped = false;
                this.load(this.dataURL());
            }
        });
        window.addEventListener('popstate', () => {
            this.restoreFilters(this.dataURL());
            this.load(this.dataURL());
        });
        this.load(this.dataURL());
    }

    dataURL() {
        const url = new URL(window.location.href);
        url.searchParams.delete('refresh');
        url.searchParams.delete('force_refresh');
        url.searchParams.delete('fragment');
        return url;
    }

    filterURL() {
        const url = this.dataURL();
        for (const [key, value] of new FormData(this.form)) {
            url.searchParams.set(key, value);
        }
        url.searchParams.set('applied', '1');
        url.searchParams.delete('page');
        return url;
    }

    showStatus(message, failed = false) {
        this.status.textContent = message;
        this.status.classList.toggle('text-danger', failed);
        this.status.classList.toggle('text-muted', !failed);
    }

    schedule() {
        clearTimeout(this.timer);
        if (!document.hidden && !this.stopped) {
            this.timer = setTimeout(() => this.load(this.dataURL()), this.refreshRunning ? 5000 : 30000);
        }
    }

    restoreFilters(url) {
        for (const field of this.form.elements) {
            if (field.name && field.name !== 'scope') {
                field.value = url.searchParams.get(field.name) || '';
            }
        }
    }

    async load(url, navigate = false) {
        clearTimeout(this.timer);
        this.controller?.abort();
        const controller = new AbortController();
        this.controller = controller;
        const timeout = setTimeout(() => controller.abort(), 12000);
        this.results.setAttribute('aria-busy', 'true');
        if (navigate) this.showStatus(this.status.dataset.updatingMessage);
        const requestURL = new URL(url);
        requestURL.searchParams.delete('refresh');
        requestURL.searchParams.set('fragment', '1');
        try {
            const response = await fetch(requestURL, {
                signal: controller.signal,
                credentials: 'same-origin',
                cache: 'no-store',
                headers: {Accept: 'application/json'}
            });
            if (!response.ok || response.redirected) throw new Error('Blueprint request failed');
            const payload = await response.json();
            if (this.controller !== controller || this.stopped) return;
            if (typeof payload.html !== 'string') throw new Error('Invalid blueprint response');
            if (navigate) {
                url.searchParams.delete('fragment');
                url.searchParams.delete('refresh');
                url.searchParams.delete('force_refresh');
                if (navigate === 'replace') {
                    window.history.replaceState({}, '', url);
                } else {
                    window.history.pushState({}, '', url);
                }
            }
            // Avoid disturbing scroll and focused pagination controls when unchanged.
            if (this.lastHTML !== payload.html) {
                this.results.innerHTML = payload.html;
                this.lastHTML = payload.html;
            }
            for (const [id, html] of Object.entries(payload.extra_html || {})) {
                const region = document.getElementById(id);
                if (!region || region.dataset.lastHTML === html) continue;
                const expanded = region.querySelector('.collapse.show')?.id;
                region.innerHTML = html;
                region.dataset.lastHTML = html;
                if (expanded) {
                    region.querySelector(`#${expanded}`)?.classList.add('show');
                    region.querySelector(`[aria-controls="${expanded}"], [data-bs-target="#${expanded}"]`)?.setAttribute('aria-expanded', 'true');
                }
            }
            this.options.onUpdate?.();
            const ownerSelect = document.getElementById('ownerFilter');
            const selectedOwner = ownerSelect.value;
            const firstOption = ownerSelect.options[0].cloneNode(true);
            const options = payload.owner_options.map(([id, name]) => new Option(name, String(id)));
            // Preserve an owner filter even when that owner's final blueprint was removed.
            if (selectedOwner && !options.some(option => option.value === selectedOwner)) {
                options.push(new Option(ownerSelect.selectedOptions[0].textContent, selectedOwner));
            }
            ownerSelect.replaceChildren(firstOption, ...options);
            ownerSelect.value = selectedOwner;
            const wasRunning = this.refreshRunning;
            this.refreshRunning = payload.refresh_running;
            if (this.refreshRunning) {
                this.showStatus(this.status.dataset.syncMessage);
            } else if (navigate || wasRunning || this.status.classList.contains('text-danger')) {
                this.showStatus(this.status.dataset.updatedMessage);
            }
        } catch (error) {
            if (this.controller !== controller || this.stopped || document.hidden) return;
            this.showStatus(this.status.dataset.errorMessage, true);
        } finally {
            clearTimeout(timeout);
            if (this.controller === controller) {
                this.controller = null;
                this.results.setAttribute('aria-busy', 'false');
                this.schedule();
            }
        }
    }

    async refresh() {
        if (this.schedulingRefresh) return;
        this.schedulingRefresh = true;
        const buttons = document.querySelectorAll(this.options.refreshButtons || 'button[onclick="refreshBlueprints()"]');
        buttons.forEach(button => { button.disabled = true; });
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 12000);
        try {
            const csrf = document.querySelector('[name="csrfmiddlewaretoken"]').value;
            const response = await fetch(this.dataURL(), {
                method: 'POST',
                signal: controller.signal,
                credentials: 'same-origin',
                headers: {'X-CSRFToken': csrf, Accept: 'application/json'},
                body: new URLSearchParams({action: 'refresh'})
            });
            if (response.redirected) throw new Error('Login required');
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || 'Refresh failed');
            this.refreshRunning = payload.refresh_running;
            this.showStatus(payload.message);
            this.schedule();
        } catch (error) {
            this.showStatus(this.status.dataset.errorMessage, true);
        } finally {
            clearTimeout(timeout);
            buttons.forEach(button => { button.disabled = false; });
            this.schedulingRefresh = false;
        }
    }

    exportCSV() {
        const rows = Array.from(this.results.querySelectorAll('.blueprint-item'), item => [
            item.dataset.typeId, item.dataset.typeName, item.dataset.me,
            item.dataset.te, item.dataset.runs === '-1' ? 'Original' : item.dataset.runs,
            item.dataset.quantity, item.dataset.characterId
        ]);
        if (!rows.length) return;
        const headers = ['Type ID', 'Type Name', 'Material Efficiency', 'Time Efficiency', 'Runs', 'Quantity', 'Character ID'];
        const csv = [headers, ...rows].map(row => row.map(value => `"${String(value).replace(/"/g, '""')}"`).join(',')).join('\n');
        const objectURL = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
        const link = document.createElement('a');
        link.href = objectURL;
        link.download = 'blueprints.csv';
        link.click();
        URL.revokeObjectURL(objectURL);
    }
}

function refreshBlueprints() {
    window.personalBlueprintList?.refresh();
}

document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('blueprintResults')) {
        window.personalBlueprintList = new PersonalBlueprintList();
    }
});
