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

    function fetchFragment(url, controller) {
        var didTimeout = false;
        var timeoutId = window.setTimeout(function () {
            didTimeout = true;
            controller.abort();
        }, fragmentTimeoutMs);

        return window.fetch(url, {
            credentials: 'same-origin',
            signal: controller.signal,
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        }).then(function (response) {
            if (!response.ok) {
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

    var globalTrigger = document.getElementById('adminUsersLoadGlobalUsageBtn');
    if (globalTrigger) {
        globalTrigger.addEventListener('click', loadGlobalUsageFragment);
    }
    document.addEventListener('show.bs.modal', prepareUsageModal);
    document.addEventListener('hide.bs.modal', cancelUsageModalRequest);
}());
