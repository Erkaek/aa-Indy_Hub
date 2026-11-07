(function () {
    'use strict';

    var root = document.getElementById('adminUsersPage');
    if (!root) {
        return;
    }

    var fragmentTimeoutMs = 12000;

    function text(key, fallback) {
        return root.dataset[key] || fallback || '';
    }

    function appendQuery(url, query) {
        if (!query) {
            return url;
        }
        return url + (url.indexOf('?') >= 0 ? '&' : '?') + query;
    }

    function fetchFragment(url, controller, options) {
        var didTimeout = false;
        var timeoutId = window.setTimeout(function () {
            didTimeout = true;
            controller.abort();
        }, fragmentTimeoutMs);

        return window.fetch(url, Object.assign({
            credentials: 'same-origin',
            signal: controller.signal,
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        }, options || {})).then(function (response) {
            if (response.redirected || !response.ok) {
                throw new Error('http_' + response.status);
            }
            return response.json();
        }).catch(function (error) {
            if (didTimeout) {
                error.indyHubTimedOut = true;
            }
            throw error;
        }).finally(function () {
            window.clearTimeout(timeoutId);
        });
    }

    function renderStatus(target, message) {
        var status = document.createElement('div');
        var spinner = document.createElement('span');
        var label = document.createElement('span');

        status.className = 'd-flex align-items-center gap-2 text-body-secondary small';
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        spinner.className = 'spinner-border spinner-border-sm';
        spinner.setAttribute('aria-hidden', 'true');
        label.textContent = message;
        status.append(spinner, label);
        target.replaceChildren(status);
    }

    function renderEmpty(target, message) {
        var empty = document.createElement('div');
        empty.className = 'text-body-secondary small';
        empty.textContent = message;
        target.replaceChildren(empty);
    }

    function renderFragmentError(target, message, retryHandler) {
        var alert = document.createElement('div');
        var label = document.createElement('span');
        var retry = document.createElement('button');

        alert.className = 'alert alert-warning mb-0 d-flex flex-wrap align-items-center gap-2';
        alert.setAttribute('role', 'alert');
        label.textContent = message;
        retry.type = 'button';
        retry.className = 'btn btn-sm btn-outline-warning js-analytics-retry';
        retry.textContent = text('retryText', 'Retry');
        retry.addEventListener('click', retryHandler, {once: true});
        alert.append(label, retry);
        target.replaceChildren(alert);
    }

    function loadGlobalUsageFragment() {
        var container = document.getElementById('adminUsersGlobalUsageContainer');
        if (!container) {
            return;
        }
        if (container.indyHubFragmentController) {
            container.indyHubFragmentController.abort();
        }

        var controller = new AbortController();
        var url = appendQuery(
            container.dataset.url || '',
            window.location.search.replace(/^\?/, '')
        );
        container.indyHubFragmentController = controller;
        renderStatus(container, text('loadingGlobalText'));

        if (!url) {
            renderFragmentError(
                container,
                text('globalErrorText'),
                loadGlobalUsageFragment
            );
            return;
        }

        fetchFragment(url, controller).then(function (payload) {
            if (container.indyHubFragmentController !== controller) {
                return;
            }
            if (payload && payload.html) {
                container.innerHTML = payload.html;
            } else {
                renderEmpty(container, text('noGlobalText'));
            }
        }).catch(function (error) {
            if (container.indyHubFragmentController !== controller) {
                return;
            }
            renderFragmentError(
                container,
                error.indyHubTimedOut ? text('timeoutText') : text('globalErrorText'),
                loadGlobalUsageFragment
            );
        });
    }

    function prepareUsageModal(event) {
        var modal = event.target;
        var trigger = event.relatedTarget;
        if (!modal || modal.id !== 'adminUsersUsageModal' || !trigger) {
            return;
        }

        var target = modal.querySelector('.js-usage-detail-target');
        var username = trigger.dataset.usageUser || '';
        var title = modal.querySelector('.js-usage-modal-title');
        modal.dataset.usageUrl = trigger.dataset.usageUrl || '';
        if (title) {
            title.textContent = text('usageDetailText') + (username ? ' · ' + username : '');
        }
        if (target) {
            target.dataset.loaded = '0';
        }
        loadUsageModal(modal);
    }

    function loadUsageModal(modal) {
        var target = modal.querySelector('.js-usage-detail-target');
        var url = modal.dataset.usageUrl || '';
        if (!target || target.dataset.loaded === '1' || !url) {
            return;
        }
        if (target.indyHubFragmentController) {
            target.indyHubFragmentController.abort();
        }

        var controller = new AbortController();
        target.indyHubFragmentController = controller;
        renderStatus(target, text('loadingUserText'));

        fetchFragment(url, controller).then(function (payload) {
            if (target.indyHubFragmentController !== controller) {
                return;
            }
            if (payload && payload.html) {
                target.innerHTML = payload.html;
            } else {
                renderEmpty(target, text('noUserText'));
            }
            target.dataset.loaded = '1';
        }).catch(function (error) {
            if (target.indyHubFragmentController !== controller) {
                return;
            }
            renderFragmentError(
                target,
                error.indyHubTimedOut ? text('timeoutText') : text('userErrorText'),
                function () {
                    loadUsageModal(modal);
                }
            );
        });
    }

    function cancelUsageModalRequest(event) {
        var modal = event.target;
        if (!modal || modal.id !== 'adminUsersUsageModal') {
            return;
        }
        var target = modal.querySelector('.js-usage-detail-target');
        if (target && target.indyHubFragmentController) {
            target.indyHubFragmentController.abort();
            target.indyHubFragmentController = null;
        }
    }

    var form = document.getElementById('adminUsersFilters');
    var results = document.getElementById('adminUsersResults');
    var summary = document.getElementById('adminUsersSummary');
    var notice = document.getElementById('adminUsersNotice');
    var selected = new Set();
    var listController = null;
    var summaryController = null;
    var debounceTimer = null;
    var monitoring = null;
    var monitorTimer = null;
    var actionPending = false;
    var pendingQuery = window.location.search.replace(/^\?/, '');
    var committedQuery = pendingQuery;

    function announce(message, warning) {
        notice.hidden = !message;
        notice.className = 'alert py-2 mb-0 ' + (warning ? 'alert-warning' : 'alert-info');
        notice.textContent = message;
    }

    function updateExport(query) {
        document.getElementById('adminUsersExport').href = appendQuery(root.dataset.exportUrl, query);
    }

    function syncForm(filters) {
        var mapping = {q: 'search_query', corporation: 'corporation_query', activity: 'activity_filter', scopes: 'scope_filter', health_level: 'health_filter', usage: 'usage_filter', account: 'account_filter', notifications: 'notification_filter', status: 'status_filter', role: 'role_filter', sort: 'sort_key', direction: 'sort_direction', per_page: 'page_size', max_health_score: 'max_health_score'};
        Object.keys(mapping).forEach(function (name) {
            var field = form.elements.namedItem(name);
            if (field) {
                var value = filters[mapping[name]];
                if (name === 'corporation' && !value) { value = filters.selected_corporation_id; }
                field.value = value === null || value === undefined ? '' : String(value);
            }
        });
        document.querySelectorAll('.admin-users-preset').forEach(function (button) {
            var preset = button.dataset.preset;
            var active = preset === 'critical' ? filters.health_filter === 'critical' : preset === 'incomplete' ? filters.scope_filter === 'incomplete' : preset === 'inactive' ? filters.activity_filter === 'inactive' : preset === 'pending' ? filters.status_filter === 'pending' : !filters.health_filter && !filters.scope_filter && !filters.activity_filter && !filters.status_filter;
            button.classList.toggle('is-current', active);
            button.setAttribute('aria-pressed', String(active));
        });
    }

    function formQuery() {
        var params = new URLSearchParams(new FormData(form));
        Array.from(params.keys()).forEach(function (key) {
            if (!params.get(key)) { params.delete(key); }
        });
        return params.toString();
    }

    function updateSelection() {
        document.getElementById('adminUsersBulkBar').hidden = selected.size === 0;
        document.getElementById('adminUsersSelectionCount').textContent = selected.size;
        var checkboxes = Array.from(results.querySelectorAll('.js-user-select'));
        checkboxes.forEach(function (box) { box.checked = selected.has(Number(box.value)); });
        var page = document.getElementById('adminUsersSelectPage');
        if (page) {
            var count = checkboxes.filter(function (box) { return box.checked; }).length;
            page.checked = checkboxes.length > 0 && count === checkboxes.length;
            page.indeterminate = count > 0 && count < checkboxes.length;
        }
    }

    function clearSelection() {
        selected.clear();
        updateSelection();
    }

    function loadSummary(query) {
        if (summaryController) { summaryController.abort(); }
        var controller = new AbortController();
        summaryController = controller;
        summary.setAttribute('aria-busy', 'true');
        fetchFragment(appendQuery(root.dataset.summaryUrl, query), controller).then(function (payload) {
            if (summaryController !== controller) { return; }
            summary.innerHTML = payload.html;
        }).catch(function (error) {
            if (summaryController !== controller || error.name === 'AbortError' && !error.indyHubTimedOut) { return; }
            renderFragmentError(summary, text('detailErrorText'), function () { loadSummary(committedQuery); });
        }).finally(function () {
            if (summaryController === controller) { summary.setAttribute('aria-busy', 'false'); }
        });
    }

    function loadList(query, historyMode, preserveNotice) {
        if (listController) { listController.abort(); }
        var controller = new AbortController();
        listController = controller;
        pendingQuery = query;
        results.setAttribute('aria-busy', 'true');
        var params = new URLSearchParams(query);
        params.set('fragment', '1');
        fetchFragment(appendQuery(root.dataset.listUrl, params.toString()), controller).then(function (payload) {
            if (listController !== controller) { return; }
            results.innerHTML = payload.html;
            var canonical = new URLSearchParams(payload.query);
            if (payload.page > 1) { canonical.set('page', payload.page); }
            committedQuery = canonical.toString();
            pendingQuery = committedQuery;
            syncForm(payload.filters);
            updateSelection();
            updateExport(committedQuery);
            if (historyMode) {
                window.history[historyMode === 'replace' ? 'replaceState' : 'pushState']({}, '', '?' + committedQuery);
            }
            if (!preserveNotice) { announce(''); }
            loadSummary(committedQuery);
            var globalContainer = document.getElementById('adminUsersGlobalUsageContainer');
            if (globalContainer.indyHubFragmentController) { globalContainer.indyHubFragmentController.abort(); globalContainer.indyHubFragmentController = null; }
            renderEmpty(globalContainer, '');
            var reloadAnalytics = document.createElement('button');
            reloadAnalytics.type = 'button';
            reloadAnalytics.className = 'btn btn-sm btn-outline-primary';
            reloadAnalytics.textContent = text('loadingGlobalText').replace('…', '');
            reloadAnalytics.addEventListener('click', loadGlobalUsageFragment);
            globalContainer.append(reloadAnalytics);
        }).catch(function (error) {
            if (listController !== controller || error.name === 'AbortError' && !error.indyHubTimedOut) { return; }
            announce(text('listErrorText'), true);
            var retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'btn btn-sm btn-outline-warning ms-2';
            retry.textContent = text('retryText');
            retry.addEventListener('click', function () { loadList(query, historyMode); }, {once: true});
            notice.append(retry);
        }).finally(function () {
            if (listController === controller) { results.setAttribute('aria-busy', 'false'); }
        });
    }

    function filterChanged() {
        if (listController) { listController.abort(); listController = null; }
        results.setAttribute('aria-busy', 'false');
        window.clearTimeout(debounceTimer);
        pendingQuery = formQuery();
        debounceTimer = window.setTimeout(function () { debounceTimer = null; clearSelection(); loadList(formQuery(), 'push'); }, 350);
    }

    function applyPreset(preset) {
        window.clearTimeout(debounceTimer);
        ['activity', 'scopes', 'health_level', 'status', 'notifications'].forEach(function (name) { form.elements.namedItem(name).value = ''; });
        var presets = {active: ['activity', 'active'], inactive: ['activity', 'inactive'], incomplete: ['scopes', 'incomplete'], critical: ['health_level', 'critical'], pending: ['status', 'pending'], notifications: ['notifications', 'disabled']};
        if (presets[preset]) { form.elements.namedItem(presets[preset][0]).value = presets[preset][1]; }
        clearSelection();
        loadList(formQuery(), 'push');
    }

    function loadAccountDetail(modal) {
        var target = modal.querySelector('.js-account-detail-target');
        if (target.indyHubFragmentController) { target.indyHubFragmentController.abort(); }
        var controller = new AbortController();
        target.indyHubFragmentController = controller;
        renderStatus(target, text('loadingUserText'));
        fetchFragment(modal.dataset.detailUrl, controller).then(function (payload) {
            if (target.indyHubFragmentController !== controller) { return; }
            target.innerHTML = payload.html;
            modal.querySelector('.modal-title').textContent = payload.username;
        }).catch(function (error) {
            if (target.indyHubFragmentController !== controller || error.name === 'AbortError' && !error.indyHubTimedOut) { return; }
            renderFragmentError(target, text('detailErrorText'), function () { loadAccountDetail(modal); });
        });
    }

    function actionMessage(target, payload) {
        target.replaceChildren();
        var heading = document.createElement('strong');
        heading.textContent = payload.message || text('actionCompleteText');
        target.append(heading);
        (payload.results || []).forEach(function (result) {
            var line = document.createElement('div');
            line.className = 'small mt-1';
            line.textContent = result.username + ' · ' + result.message;
            target.append(line);
        });
        target.hidden = false;
        target.className = 'alert alert-info py-2 mb-0';
    }

    function pollAction() {
        if (!monitoring || document.hidden) { return; }
        var monitor = monitoring;
        var controller = new AbortController();
        monitor.controller = controller;
        fetchFragment(appendQuery(root.dataset.statusUrl, monitor.query), controller).then(function (payload) {
            if (monitoring !== monitor) { return; }
            if (payload.running && monitor.attempts++ < 40) {
                monitorTimer = window.setTimeout(pollAction, 3000);
            } else {
                monitoring = null;
                if (payload.failed) { announce(text('actionFailedText'), true); }
                else if (payload.running) { announce(text('actionStillRunningText'), false); }
                if (!debounceTimer) { loadList(pendingQuery, 'replace', true); }
                var modal = document.getElementById('adminUsersDetailModal');
                if (modal.classList.contains('show')) { loadAccountDetail(modal); }
            }
        }).catch(function () {
            if (monitoring !== monitor) { return; }
            monitoring = null;
            // Do not retry a POST whose result is uncertain. A fresh read is safe.
            announce(text('actionStatusErrorText'), true);
            if (!debounceTimer) { loadList(pendingQuery, 'replace', true); }
        });
    }

    function submitAction(action, scope, ids, button, target) {
        if (actionPending || !ids.length || ids.length > 25) { announce(text('selectionLimitText'), true); return; }
        actionPending = true;
        button.disabled = true;
        var controller = new AbortController();
        var token = document.querySelector('#adminUsersCsrf [name=csrfmiddlewaretoken]').value;
        fetchFragment(root.dataset.actionsUrl, controller, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': token, 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify({action: action, scope: scope, user_ids: ids})
        }).then(function (payload) {
            actionMessage(target, payload);
            if (monitoring && monitoring.controller) { monitoring.controller.abort(); }
            window.clearTimeout(monitorTimer);
            if (payload.task_id || payload.scheduled) {
                var query = payload.task_id ? new URLSearchParams({task_id: payload.task_id}) : new URLSearchParams({kind: action, scope: scope, user_ids: payload.results.filter(function (result) { return result.scheduled; }).map(function (result) { return result.user_id; }).join(',')});
                monitoring = {query: query.toString(), attempts: 0};
                monitorTimer = window.setTimeout(pollAction, 2000);
                if (button.classList.contains('js-admin-user-action')) { button.dataset.actionScheduled = '1'; }
            }
        }).catch(function () {
            announce(text('actionErrorText'), true);
        }).finally(function () {
            actionPending = false;
            button.disabled = button.dataset.actionScheduled === '1';
        });
    }

    if (form && results) {
        var main = root.closest('main');
        if (main) { main.classList.add('admin-users-main'); }
        var mobileQuery = window.matchMedia('(max-width: 767.98px)');
        function adjustMobileSidebar() {
            var sidebar = document.getElementById('sidebar');
            if (mobileQuery.matches && sidebar && window.bootstrap) {
                window.bootstrap.Collapse.getOrCreateInstance(sidebar, {toggle: false}).hide();
            }
        }
        adjustMobileSidebar();
        mobileQuery.addEventListener('change', adjustMobileSidebar);
        form.addEventListener('submit', function (event) { event.preventDefault(); window.clearTimeout(debounceTimer); clearSelection(); loadList(formQuery(), 'push'); });
        form.addEventListener('input', function (event) { if (event.target.type === 'search') { filterChanged(); } });
        form.addEventListener('change', function () { filterChanged(); });
        document.getElementById('adminUsersPageSize').addEventListener('change', filterChanged);
        document.getElementById('adminUsersReload').addEventListener('click', function () { loadList(pendingQuery, 'replace'); });
        document.getElementById('adminUsersClearSelection').addEventListener('click', clearSelection);
        document.getElementById('adminUsersRunBulk').addEventListener('click', function (event) { submitAction(document.getElementById('adminUsersBulkAction').value, document.getElementById('adminUsersBulkScope').value, Array.from(selected), event.currentTarget, notice); });
        results.addEventListener('change', function (event) {
            if (event.target.id === 'adminUsersSelectPage') {
                var boxes = Array.from(results.querySelectorAll('.js-user-select'));
                boxes.forEach(function (box) {
                    var id = Number(box.value);
                    if (event.target.checked && selected.size < 25) { selected.add(id); }
                    else if (!event.target.checked) { selected.delete(id); }
                });
                if (event.target.checked && boxes.length > 25) { announce(text('selectionLimitText'), true); }
            } else if (event.target.classList.contains('js-user-select')) {
                var id = Number(event.target.value);
                if (event.target.checked && selected.size < 25) { selected.add(id); }
                else if (!event.target.checked) { selected.delete(id); }
                else { announce(text('selectionLimitText'), true); }
            }
            updateSelection();
        });
        root.addEventListener('click', function (event) {
            var preset = event.target.closest('[data-preset]');
            if (preset) { applyPreset(preset.dataset.preset); return; }
            var action = event.target.closest('.js-admin-user-action');
            if (action) { submitAction(action.dataset.action, action.dataset.scope || 'character', [Number(action.dataset.userId)], action, action.closest('.admin-users-detail').querySelector('.js-admin-detail-notice')); return; }
            var link = event.target.closest('a');
            if (link && (results.contains(link) || form.contains(link))) {
                if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) { return; }
                event.preventDefault();
                window.clearTimeout(debounceTimer);
                if (form.contains(link)) { clearSelection(); }
                loadList(new URL(link.href, window.location.href).search.replace(/^\?/, ''), 'push');
            }
        });
        window.addEventListener('popstate', function () { clearSelection(); loadList(window.location.search.replace(/^\?/, ''), null); });
        document.addEventListener('show.bs.modal', function (event) {
            if (event.target.id === 'adminUsersDetailModal' && event.relatedTarget) { event.target.dataset.detailUrl = event.relatedTarget.dataset.detailUrl; loadAccountDetail(event.target); }
        });
        document.addEventListener('hide.bs.modal', function (event) {
            if (event.target.id !== 'adminUsersDetailModal') { return; }
            var target = event.target.querySelector('.js-account-detail-target');
            if (target.indyHubFragmentController) { target.indyHubFragmentController.abort(); target.indyHubFragmentController = null; }
        });
        document.addEventListener('visibilitychange', function () {
            window.clearTimeout(monitorTimer);
            if (!document.hidden && monitoring) { pollAction(); }
        });
        updateSelection();
        updateExport(committedQuery);
        loadSummary(committedQuery);
    }

    var globalTrigger = document.getElementById('adminUsersLoadGlobalUsageBtn');
    if (globalTrigger) {
        globalTrigger.addEventListener('click', loadGlobalUsageFragment);
    }
    document.addEventListener('show.bs.modal', prepareUsageModal);
    document.addEventListener('hide.bs.modal', cancelUsageModalRequest);
}());
