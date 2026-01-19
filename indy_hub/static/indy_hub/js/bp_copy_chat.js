(function () {
    var debugEnabled = (typeof window !== 'undefined' && window.INDY_HUB_DEBUG === true);
    function debugLog() {
        if (!debugEnabled || typeof console === 'undefined' || typeof console.debug !== 'function') {
            return;
        }
        console.debug.apply(console, arguments);
    }

    function __(message) {
        if (typeof window !== 'undefined' && typeof window.gettext === 'function') {
            return window.gettext(message);
        }
        return message;
    }

    debugLog('[IndyHub] bp_copy_chat.js loaded');
    function $(arg1, arg2) {
        if (typeof arg1 === 'string') {
            return (arg2 || document).querySelector(arg1);
        }
        if (!arg1) {
            return null;
        }
        return arg1.querySelector(arg2);
    }

    function ensureModalElement() {
        return document.querySelector('[data-chat-modal]');
    }

    function createEl(tag, className, text) {
        var el = document.createElement(tag);
        if (className) {
            el.className = className;
        }
        if (typeof text === 'string') {
            el.textContent = text;
        }
        return el;
    }

    function scrollToBottom(container) {
        container.scrollTop = container.scrollHeight;
    }

    function scrollMessagesToBottom(container) {
        if (!container) {
            return;
        }
        if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
            window.requestAnimationFrame(function () {
                window.requestAnimationFrame(function () {
                    scrollToBottom(container);
                });
            });
        } else {
            scrollToBottom(container);
        }
    }

    function scrollMessagesToBottomNow(container) {
        if (!container) {
            return;
        }
        scrollToBottom(container);
        if (typeof window !== 'undefined') {
            window.setTimeout(function () {
                scrollToBottom(container);
            }, 50);
        }
    }

    function withViewerRole(url, viewerRole) {
        if (!url || !viewerRole) {
            return url;
        }
        try {
            var origin = (typeof window !== 'undefined' && window.location && window.location.origin) ? window.location.origin : undefined;
            var resolved = new URL(url, origin);
            resolved.searchParams.set('viewer_role', viewerRole);
            if (typeof window !== 'undefined' && window.location && resolved.origin === window.location.origin) {
                return resolved.pathname + resolved.search + resolved.hash;
            }
            return resolved.toString();
        } catch (err) {
            var separator = url.indexOf('?') === -1 ? '?' : '&';
            return url + separator + 'viewer_role=' + encodeURIComponent(viewerRole);
        }
    }

    function labelFor(role, viewerRole, labels) {
        if (role === viewerRole) {
            return labels.you || 'You';
        }
        return labels[role] || role;
    }

    function init() {
        var modalEl = ensureModalElement();
        if (!modalEl) {
            debugLog('[IndyHub] No chat modal found on page');
        }
        if (!modalEl) {
            return;
        }

        function getCsrfToken() {
            if (formEl) {
                var input = $('input[name="csrfmiddlewaretoken"]', formEl);
                if (input && input.value) {
                    return input.value;
                }
            }
            if (typeof window !== 'undefined' && window.csrfToken) {
                return window.csrfToken;
            }
            var match = document.cookie ? document.cookie.match(/csrftoken=([^;]+)/) : null;
            if (match && match[1]) {
                try {
                    return decodeURIComponent(match[1]);
                } catch (err) {
                    return match[1];
                }
            }
            return '';
        }

        var bootstrapModalCtor = null;
        if (typeof window !== 'undefined') {
            if (window.bootstrap && window.bootstrap.Modal) {
                bootstrapModalCtor = window.bootstrap.Modal;
            } else if (window.bootstrap5 && window.bootstrap5.Modal) {
                bootstrapModalCtor = window.bootstrap5.Modal;
            }
        }

        var useBootstrap = Boolean(bootstrapModalCtor);
        var modal = useBootstrap ? bootstrapModalCtor.getOrCreateInstance(modalEl) : null;
        var backdropEl = null;
        var previousBodyOverflow = '';

        function ensureBackdrop() {
            if (backdropEl) {
                return;
            }
            backdropEl = document.createElement('div');
            backdropEl.className = 'modal-backdrop fade show';
            document.body.appendChild(backdropEl);
        }

        function removeBackdrop() {
            if (!backdropEl) {
                return;
            }
            if (backdropEl.parentNode) {
                backdropEl.parentNode.removeChild(backdropEl);
            }
            backdropEl = null;
        }


        function showModal() {
            if (useBootstrap) {
                modal.show();
                return;
            }
            if (modalEl.classList.contains('show')) {
                return;
            }
            ensureBackdrop();
            modalEl.style.display = 'block';
            modalEl.removeAttribute('aria-hidden');
            document.body.classList.add('modal-open');
            previousBodyOverflow = document.body.style.overflow || '';
            document.body.style.overflow = 'hidden';
            if (state.pendingInitialScroll) {
                scrollMessagesToBottom(messageContainer);
            }
        }

        function hideModal() {
            if (useBootstrap) {
                modal.hide();
                return;
            }
            modalEl.classList.remove('show');
            modalEl.style.display = 'none';
            modalEl.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('modal-open');
            document.body.style.overflow = previousBodyOverflow;
            previousBodyOverflow = '';
            removeBackdrop();
            onModalClosed();
        }

        var formEl = $('[data-chat-form]', modalEl);
        var messageContainer = $('[data-chat-messages]', modalEl);
        var statusEl = $('[data-chat-status]', modalEl);
        var summaryEl = $('[data-chat-summary]', modalEl);
        var inputEl = $('[data-chat-input]', modalEl);
        var actionsEl = $('[data-chat-actions]', modalEl);
        var actionStatusEl = actionsEl ? $('[data-chat-action-status]', actionsEl) : null;
        var acceptBtn = actionsEl ? $('[data-chat-accept]', actionsEl) : null;
        var rejectBtn = actionsEl ? $('[data-chat-reject]', actionsEl) : null;

        if (!messageContainer || !formEl || !inputEl) {
            return;
        }

        var state = {
            fetchUrl: null,
            sendUrl: null,
            viewerRole: 'buyer',
            labels: {
                buyer: __('Buyer'),
                seller: __('Builder'),
                system: __('System'),
                you: __('You')
            },
            typeName: '',
            typeId: null,
            polling: null,
            isOpen: false,
            decisionUrl: null,
            lastDecision: null,
            actionSubmitting: false
        };

        var defaults = window.indyChatDefaults || {};
        if (defaults.viewerRole) {
            state.viewerRole = defaults.viewerRole;
        }
        if (defaults.labels) {
            state.labels = Object.assign({}, state.labels, defaults.labels);
        }

        updateActions(null);

        function showStatus(message, tone) {
            if (!statusEl) {
                return;
            }
            if (!message) {
                statusEl.classList.add('d-none');
                statusEl.textContent = '';
                statusEl.classList.remove('alert-danger', 'alert-warning', 'alert-info', 'alert-success');
                return;
            }
            var toneClass = 'alert-info';
            if (tone === 'error') {
                toneClass = 'alert-danger';
            } else if (tone === 'warning') {
                toneClass = 'alert-warning';
            } else if (tone === 'success') {
                toneClass = 'alert-success';
            }
            statusEl.classList.remove('alert-danger', 'alert-warning', 'alert-info', 'alert-success', 'd-none');
            statusEl.classList.add(toneClass);
            statusEl.textContent = message;
        }

        function clearMessages() {
            while (messageContainer.firstChild) {
                messageContainer.removeChild(messageContainer.firstChild);
            }
        }

        function renderMessages(payload) {
            clearMessages();
            var viewerRole = payload.chat.viewer_role;
            var otherRole = payload.chat.other_role;
            var labels = Object.assign({}, state.labels);
            if (!labels[otherRole]) {
                labels[otherRole] = otherRole;
            }

            var messages = payload.messages || [];
            messages.forEach(function (item) {
                var bubble = createEl('div', 'bp-chat-message');
                if (item.role === viewerRole) {
                    bubble.classList.add('bp-chat-message--self');
                } else if (item.role === 'system') {
                    bubble.classList.add('bp-chat-message--system');
                } else {
                    bubble.classList.add('bp-chat-message--other');
                }

                var meta = createEl('div', 'bp-chat-message__meta');
                var author = createEl('span', 'bp-chat-message__author', labelFor(item.role, viewerRole, labels));
                meta.appendChild(author);
                var separator = createEl('span', 'bp-chat-message__separator', '•');
                meta.appendChild(separator);
                var timestamp = createEl('time', 'bp-chat-message__time', item.created_display);
                if (item.created_at) {
                    timestamp.setAttribute('datetime', item.created_at);
                }
                meta.appendChild(timestamp);
                bubble.appendChild(meta);

                var content = createEl('span', 'bp-chat-message__content', item.content);
                bubble.appendChild(content);
                messageContainer.appendChild(bubble);
            });
            if (messages.length && state.pendingInitialScroll) {
                scrollMessagesToBottomNow(messageContainer);
            }
        }

        function updateSummary(payload) {
            if (!summaryEl) {
                return;
            }

            var typeName = payload.chat.type_name || state.typeName || __('Blueprint');
            var typeId = payload.chat.type_id || state.typeId || null;
            var viewerLabel = state.labels[payload.chat.viewer_role] || payload.chat.viewer_role;
            var otherLabel = state.labels[payload.chat.other_role] || payload.chat.other_role;

            summaryEl.innerHTML = '';

            var panel = createEl('div', 'bp-chat-summary__panel');

            var headline = createEl('div', 'bp-chat-summary__headline');
            var nameEl = createEl('span', 'bp-chat-summary__type', typeName);
            headline.appendChild(nameEl);

            if (!payload.chat.is_open && payload.chat.closed_reason) {
                var reasonLabels = {
                    request_closed: __('Request closed'),
                    offer_accepted: __('Offer accepted'),
                    offer_rejected: __('Offer rejected'),
                    expired: __('Expired'),
                    manual: __('Closed'),
                    reopened: __('Reopened')
                };
                var reasonKey = payload.chat.closed_reason;
                var closeLabel = reasonLabels[reasonKey] || reasonKey.replace(/_/g, ' ');
                var closedBadge = createEl('span', 'bp-chat-summary__badge');
                closedBadge.textContent = closeLabel;
                headline.appendChild(closedBadge);
            }
            panel.appendChild(headline);

            var roles = createEl('div', 'bp-chat-summary__roles');
            var viewerBadge = createEl('span', 'bp-chat-summary__role badge rounded-pill bg-primary-subtle text-primary fw-semibold', viewerLabel);
            roles.appendChild(viewerBadge);
            var rolesDivider = createEl('span', 'bp-chat-summary__divider', '↔');
            roles.appendChild(rolesDivider);
            var otherBadge = createEl('span', 'bp-chat-summary__role badge rounded-pill bg-secondary-subtle text-secondary fw-semibold', otherLabel);
            roles.appendChild(otherBadge);
            panel.appendChild(roles);

            var me = payload.chat.material_efficiency;
            var te = payload.chat.time_efficiency;
            var runs = payload.chat.runs_requested;
            var copies = payload.chat.copies_requested;
            var detailParts = [];
            if (typeof me === 'number') {
                detailParts.push('ME ' + me);
            }
            if (typeof te === 'number') {
                detailParts.push('TE ' + te);
            }
            if (typeof runs === 'number') {
                detailParts.push(runs + ' ' + __('runs'));
            }
            if (typeof copies === 'number') {
                detailParts.push(copies + ' ' + __('copies'));
            }
            if (detailParts.length) {
                var detailRow = createEl('div', 'bp-chat-summary__meta text-muted small', detailParts.join(' · '));
                panel.appendChild(detailRow);
            }

            if (typeId) {
                var idRow = createEl('div', 'bp-chat-summary__meta text-muted small', '#' + typeId);
                panel.appendChild(idRow);
            }

            summaryEl.appendChild(panel);
        }

        function toggleForm(enabled) {
            var disabled = !enabled;
            if (disabled) {
                formEl.setAttribute('aria-disabled', 'true');
            } else {
                formEl.removeAttribute('aria-disabled');
            }
            inputEl.disabled = disabled;
            formEl.querySelector('button[type="submit"]').disabled = disabled;
        }

        function updateActions(decision) {
            if (!actionsEl) {
                return;
            }
            state.lastDecision = decision || null;
            state.decisionUrl = decision && decision.url ? decision.url : null;

            if (!decision) {
                actionsEl.classList.add('d-none');
                if (actionStatusEl) {
                    actionStatusEl.textContent = '';
                    actionStatusEl.classList.remove('text-danger', 'text-warning', 'text-success', 'text-primary', 'text-muted');
                }
                if (acceptBtn) {
                    acceptBtn.classList.add('d-none');
                    acceptBtn.disabled = true;
                }
                if (rejectBtn) {
                    rejectBtn.classList.add('d-none');
                    rejectBtn.disabled = true;
                }
                return;
            }

            var toneMap = {
                error: 'text-danger',
                warning: 'text-warning',
                success: 'text-success',
                info: 'text-primary'
            };

            if (actionStatusEl) {
                actionStatusEl.classList.remove('text-danger', 'text-warning', 'text-success', 'text-primary', 'text-muted');
                if (decision.status_label) {
                    actionStatusEl.textContent = decision.status_label;
                    var toneClass = decision.status_tone && toneMap[decision.status_tone] ? toneMap[decision.status_tone] : '';
                    if (toneClass) {
                        actionStatusEl.classList.add(toneClass);
                    } else {
                        actionStatusEl.classList.add('text-muted');
                    }
                } else {
                    actionStatusEl.textContent = '';
                }
            }

            var canAccept = Boolean(decision.viewer_can_accept);
            var canReject = Boolean(decision.viewer_can_reject);

            if (acceptBtn) {
                if (decision.accept_label) {
                    acceptBtn.innerHTML = '<i class="fas fa-check me-1"></i>' + decision.accept_label;
                }
                acceptBtn.classList.toggle('d-none', !canAccept);
                acceptBtn.disabled = !canAccept || state.actionSubmitting;
            }

            if (rejectBtn) {
                if (decision.reject_label) {
                    rejectBtn.innerHTML = '<i class="fas fa-times me-1"></i>' + decision.reject_label;
                }
                rejectBtn.classList.toggle('d-none', !canReject);
                rejectBtn.disabled = !canReject || state.actionSubmitting;
            }

            var shouldShow = Boolean(decision.status_label) || canAccept || canReject;
            actionsEl.classList.toggle('d-none', !shouldShow);
        }

        function setActionSubmitting(submitting) {
            state.actionSubmitting = submitting;
            if (!actionsEl || !state.lastDecision) {
                return;
            }
            if (acceptBtn && !acceptBtn.classList.contains('d-none')) {
                acceptBtn.disabled = submitting || !state.lastDecision.viewer_can_accept;
            }
            if (rejectBtn && !rejectBtn.classList.contains('d-none')) {
                rejectBtn.disabled = submitting || !state.lastDecision.viewer_can_reject;
            }
        }

        function submitDecision(decisionValue) {
            if (!state.decisionUrl || state.actionSubmitting) {
                return;
            }
            setActionSubmitting(true);
            if (actionStatusEl && state.lastDecision && state.lastDecision.pending_label) {
                actionStatusEl.textContent = state.lastDecision.pending_label;
                actionStatusEl.classList.remove('text-danger', 'text-warning', 'text-success', 'text-primary');
                actionStatusEl.classList.add('text-muted');
            }

            var decisionUrl = withViewerRole(state.decisionUrl, state.viewerRole);
            var decisionPayload = { decision: decisionValue };
            if (state.viewerRole) {
                decisionPayload.viewer_role = state.viewerRole;
            }

            fetch(decisionUrl, {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                credentials: 'same-origin',
                body: JSON.stringify(decisionPayload)
            })
                .then(function (res) {
                    if (!res.ok) {
                        return res
                            .json()
                            .catch(function () {
                                throw new Error(__('Unable to update decision.'));
                            })
                            .then(function (data) {
                                var errMsg = data && data.error ? data.error : __('Unable to update decision.');
                                throw new Error(errMsg);
                            });
                    }
                    return res.json().catch(function () {
                        return {};
                    });
                })
                .then(function (result) {
                    if (result && result.request_closed) {
                        showStatus(__('This request has been closed.'), 'warning');
                        state.isOpen = false;
                        stopPolling();
                        toggleForm(false);
                        updateActions(null);
                        return null;
                    }
                    return fetchChat().catch(function (err) {
                        showStatus(err.message || __('Unable to refresh conversation.'), 'error');
                        return null;
                    });
                })
                .catch(function (err) {
                    showStatus(err.message || __('Unable to update decision.'), 'error');
                })
                .finally(function () {
                    setActionSubmitting(false);
                    if (!state.lastDecision) {
                        updateActions(null);
                    } else {
                        updateActions(state.lastDecision);
                    }
                });
        }

        function loadSeenChatIds() {
            if (typeof window === 'undefined' || !window.localStorage) {
                return [];
            }
            try {
                var raw = window.localStorage.getItem('indyhub_seen_chats');
                if (!raw) {
                    return [];
                }
                var parsed = JSON.parse(raw);
                if (!Array.isArray(parsed)) {
                    return [];
                }
                return parsed.map(function (value) {
                    return String(value);
                });
            } catch (err) {
                return [];
            }
        }

        function storeSeenChatId(chatId) {
            if (!chatId || typeof window === 'undefined' || !window.localStorage) {
                return;
            }
            var stringId = String(chatId);
            var seen = loadSeenChatIds();
            if (seen.indexOf(stringId) !== -1) {
                return;
            }
            seen.unshift(stringId);
            if (seen.length > 100) {
                seen = seen.slice(0, 100);
            }
            try {
                window.localStorage.setItem('indyhub_seen_chats', JSON.stringify(seen));
            } catch (err) {
                return;
            }
        }

        function applyChatState(payload) {
            if (payload && payload.chat && payload.chat.id) {
                storeSeenChatId(payload.chat.id);
            }
            state.isOpen = Boolean(payload.chat.is_open);
            if (payload.chat && payload.chat.viewer_role) {
                state.viewerRole = payload.chat.viewer_role;
            }
            updateSummary(payload);
            renderMessages(payload);
            updateActions(payload.chat && payload.chat.decision ? payload.chat.decision : null);
            if (!useBootstrap && state.pendingInitialScroll) {
                scrollMessagesToBottomNow(messageContainer);
                state.pendingInitialScroll = false;
            }
            if (!payload.chat.can_send) {
                toggleForm(false);
                if (!payload.chat.is_open) {
                    showStatus(__('This chat has been closed.'), 'warning');
                }
            } else {
                toggleForm(true);
                showStatus(null);
            }
        }

        function onModalClosed() {
            stopPolling();
            showStatus(null);
            clearMessages();
            inputEl.value = '';
            updateActions(null);
            state.decisionUrl = null;
            state.lastDecision = null;
            state.actionSubmitting = false;
            state.pendingInitialScroll = false;
        }

        function fetchChat() {
            if (!state.fetchUrl) {
                return Promise.reject(new Error(__('Missing chat URL')));
            }
            var historyUrl = withViewerRole(state.fetchUrl, state.viewerRole);
            return fetch(historyUrl, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json'
                },
                credentials: 'same-origin'
            })
                .then(function (res) {
                    if (!res.ok) {
                        throw new Error(__('Unable to load chat'));
                    }
                    return res.json();
                })
                .then(function (data) {
                    applyChatState(data);
                    return data;
                })
                .catch(function (err) {
                    showStatus(err.message || __('Unable to load chat history.'), 'error');
                    throw err;
                });
        }

        function startPolling() {
            stopPolling();
            if (!state.isOpen) {
                return;
            }
            state.polling = window.setInterval(function () {
                fetchChat().catch(function () {
                    stopPolling();
                });
            }, 12000);
        }

        function stopPolling() {
            if (state.polling) {
                window.clearInterval(state.polling);
                state.polling = null;
            }
        }

        function openChat(trigger) {
            state.fetchUrl = trigger.dataset.chatFetchUrl;
            state.sendUrl = trigger.dataset.chatSendUrl;
            state.typeName = trigger.dataset.chatTypeName || '';
            state.typeId = trigger.dataset.chatTypeId || null;
            if (trigger.dataset.chatRole) {
                state.viewerRole = trigger.dataset.chatRole;
            }
            state.pendingInitialScroll = true;

            showStatus(__('Loading conversation...'), 'info');
            toggleForm(false);
            clearMessages();
            updateActions(null);
            state.actionSubmitting = false;
            stopPolling();
            showModal();

            fetchChat()
                .then(function () {
                    startPolling();
                })
                .catch(function () {
                    state.isOpen = false;
                });
        }

        if (useBootstrap) {
            modalEl.addEventListener('hidden.bs.modal', onModalClosed);
            modalEl.addEventListener('shown.bs.modal', function () {
                if (state.pendingInitialScroll) {
                    scrollMessagesToBottom(messageContainer);
                    state.pendingInitialScroll = false;
                }
            });
        } else {
            modalEl.addEventListener('click', function (event) {
                var dismissTrigger = event.target.closest('[data-bs-dismiss="modal"]');
                if (dismissTrigger) {
                    event.preventDefault();
                    hideModal();
                    return;
                }
                if (event.target === modalEl) {
                    hideModal();
                }
            });
            modalEl.addEventListener('keydown', function (event) {
                if (event.key === 'Escape') {
                    hideModal();
                }
            });
            document.addEventListener('keydown', function (event) {
                if (event.key === 'Escape' && modalEl.classList.contains('show')) {
                    hideModal();
                }
            });
        }

        formEl.addEventListener('submit', function (event) {
            event.preventDefault();
            if (!state.sendUrl) {
                return;
            }
            var message = (inputEl.value || '').trim();
            if (!message) {
                return;
            }
            toggleForm(false);

            var sendPayload = { message: message };
            if (state.viewerRole) {
                sendPayload.viewer_role = state.viewerRole;
            }

            fetch(state.sendUrl, {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                credentials: 'same-origin',
                body: JSON.stringify(sendPayload)
            })
                .then(function (res) {
                    if (!res.ok) {
                        return res.json().then(function (data) {
                            var errMsg = data && data.error ? data.error : __('Message failed to send.');
                            throw new Error(errMsg);
                        }).catch(function () {
                            throw new Error(__('Message failed to send.'));
                        });
                    }
                    return res.json();
                })
                .then(function (data) {
                    inputEl.value = '';
                    toggleForm(true);
                    if (data && data.message) {
                        fetchChat();
                    }
                })
                .catch(function (err) {
                    toggleForm(true);
                    showStatus(err.message || __('Message failed to send.'), 'error');
                });
        });

        if (acceptBtn) {
            acceptBtn.addEventListener('click', function () {
                if (!state.lastDecision || !state.lastDecision.viewer_can_accept || state.actionSubmitting) {
                    return;
                }
                submitDecision('accept');
            });
        }

        if (rejectBtn) {
            rejectBtn.addEventListener('click', function () {
                if (!state.lastDecision || !state.lastDecision.viewer_can_reject || state.actionSubmitting) {
                    return;
                }
                submitDecision('reject');
            });
        }

        document.addEventListener('click', function (event) {
            var trigger = event.target.closest('.bp-chat-trigger');
            if (!trigger) {
                return;
            }
            event.preventDefault();
            debugLog('[IndyHub] Opening chat', trigger.dataset.chatFetchUrl, trigger.dataset.chatSendUrl);
            openChat(trigger);
            if (!useBootstrap) {
                showModal();
            }
        });

        var autoOpenRoot = document.querySelector('[data-auto-open-chat]');
        if (autoOpenRoot) {
            var autoChatId = autoOpenRoot.dataset.autoOpenChat;
            if (autoChatId) {
                var attemptAutoOpen = function () {
                    var selector = '.bp-chat-trigger[data-chat-id="' + autoChatId + '"]';
                    var autoTrigger = document.querySelector(selector);
                    if (!autoTrigger) {
                        return false;
                    }
                    openChat(autoTrigger);
                    if (!useBootstrap) {
                        showModal();
                    }
                    autoOpenRoot.dataset.autoOpenChat = '';
                    try {
                        var currentUrl = new URL(window.location.href);
                        if (currentUrl.searchParams.has('open_chat')) {
                            currentUrl.searchParams.delete('open_chat');
                            window.history.replaceState({}, document.title, currentUrl.toString());
                        }
                    } catch (err) {
                        console.warn('[IndyHub] Unable to clean auto-open query param', err);
                    }
                    return true;
                };

                if (!attemptAutoOpen()) {
                    window.setTimeout(attemptAutoOpen, 200);
                }
            }
        }
        state.boundClickListener = true;
        debugLog('[IndyHub] Chat listeners bound');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
