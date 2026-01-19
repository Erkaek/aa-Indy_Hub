/* Indy Hub Index Page JavaScript */

function __(message) {
    if (typeof window !== 'undefined' && typeof window.gettext === 'function') {
        return window.gettext(message);
    }
    return message;
}

function n__(singular, plural, count) {
    if (typeof window !== 'undefined' && typeof window.ngettext === 'function') {
        return window.ngettext(singular, plural, count);
    }
    return Number(count) === 1 ? singular : plural;
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

function clearSeenChatIds() {
    if (typeof window === 'undefined' || !window.localStorage) {
        return;
    }
    try {
        window.localStorage.removeItem('indyhub_seen_chats');
    } catch (err) {
        return;
    }
}

function refreshCopyChatAlerts() {
    var seenIds = loadSeenChatIds();
    if (!seenIds.length) {
        return;
    }

    var container = document.getElementById('copy-chat-alerts');
    var header = document.getElementById('copy-chat-alerts-header');
    var emptyState = document.getElementById('copy-chat-alerts-empty');
    var moreHint = document.getElementById('copy-chat-alerts-more');
    var unreadBadge = document.getElementById('copy-chat-unread-badge');
    var unreadValue = document.getElementById('copy-chat-unread-value');
    var removed = 0;

    if (container) {
        var alertNodes = Array.from(container.querySelectorAll('[data-chat-id]'));
        alertNodes.forEach(function (node) {
            var chatId = node.getAttribute('data-chat-id');
            if (!chatId) {
                return;
            }
            if (seenIds.indexOf(String(chatId)) !== -1) {
                node.remove();
                removed += 1;
            }
        });

        if (alertNodes.length - removed <= 0) {
            if (container) {
                container.classList.add('d-none');
            }
            if (header) {
                header.classList.add('d-none');
            }
            if (moreHint) {
                moreHint.classList.add('d-none');
            }
            if (emptyState) {
                emptyState.classList.remove('d-none');
            }
        }
    }

    if (removed > 0) {
        var updateCount = function (node) {
            if (!node) {
                return;
            }
            var text = String(node.textContent || '');
            var match = text.match(/(\d+)/);
            if (!match) {
                return;
            }
            var current = parseInt(match[1], 10);
            if (isNaN(current)) {
                return;
            }
            var next = Math.max(0, current - removed);
            node.textContent = text.replace(match[1], String(next));
        };

        updateCount(unreadBadge);
        updateCount(unreadValue);
    }

    clearSeenChatIds();
}

var indyHubPopupTimer = null;

function hideIndyHubPopup() {
    var popup = document.getElementById('indy-hub-popup');
    if (!popup) {
        return;
    }
    popup.classList.remove('is-visible');
    delete popup.dataset.popupVisible;
    popup.removeAttribute('data-popup-message');
    popup.removeAttribute('aria-label');
    if (indyHubPopupTimer) {
        clearTimeout(indyHubPopupTimer);
        indyHubPopupTimer = null;
    }
}

// Global popup function for showing messages
function showIndyHubPopup(message, type) {
    var popup = document.getElementById('indy-hub-popup');
    if (!popup) {
        return;
    }

    var tone = (type || 'info').toLowerCase();
    var allowedTones = ['success', 'warning', 'danger', 'secondary', 'info'];
    if (allowedTones.indexOf(tone) === -1) {
        tone = 'info';
    }
    popup.setAttribute('data-popup-type', tone);

    var text = message == null ? '' : String(message);
    var messageNode = document.getElementById('indy-hub-popup-message');
    if (messageNode) {
        messageNode.textContent = text;
    }
    popup.setAttribute('data-popup-message', text);
    popup.setAttribute('aria-label', text);

    var iconNode = popup.querySelector('.indy-hub-popup-icon i');
    if (iconNode) {
        var iconMap = {
            success: 'fa-circle-check',
            warning: 'fa-triangle-exclamation',
            danger: 'fa-circle-xmark',
            secondary: 'fa-bell',
            info: 'fa-circle-info'
        };
        var iconClass = iconMap[tone] || iconMap.info;
        iconNode.className = 'fas ' + iconClass;
    }

    popup.classList.add('is-visible');
    popup.dataset.popupVisible = 'true';

    if (indyHubPopupTimer) {
        clearTimeout(indyHubPopupTimer);
    }
        indyHubPopupTimer = setTimeout(hideIndyHubPopup, 5000);
}

// Initialize index page functionality
document.addEventListener('DOMContentLoaded', function() {
    refreshCopyChatAlerts();
    var popupElement = document.getElementById('indy-hub-popup');
    if (popupElement) {
        var dismissButton = popupElement.querySelector('.indy-hub-popup-dismiss');
        if (dismissButton) {
            dismissButton.addEventListener('click', hideIndyHubPopup);
        }
    }
    refreshCopyChatAlerts();

    var jobNotificationState = Object.assign({
        frequency: 'disabled',
        customDays: 3,
        hint: ''
    }, window.jobNotificationState || {});

    var notifyGroup = document.getElementById('job-notification-group');
    var notifyButtons = notifyGroup ? Array.from(notifyGroup.querySelectorAll('[data-frequency]')) : [];
    var customWrapper = document.getElementById('job-notification-custom-wrapper');
    var customDaysInput = document.getElementById('job-notification-custom-days');
    var applyBtn = document.getElementById('job-notification-apply');
    var applyWrapper = document.getElementById('job-notification-apply-wrapper');
    var notifyHint = document.getElementById('notify-hint');

    function setNotifyHint(text) {
        if (notifyHint) {
            notifyHint.textContent = text || '';
        }
    }

    function toggleCustomVisibility(value) {
        if (!customWrapper) {
            return;
        }
        if (value === 'custom') {
            customWrapper.classList.remove('d-none');
            if (applyWrapper) {
                applyWrapper.classList.remove('d-none');
            }
        } else {
            customWrapper.classList.add('d-none');
            if (applyWrapper) {
                applyWrapper.classList.add('d-none');
            }
        }
    }

    function getNotificationButton(frequency) {
        if (!notifyGroup) {
            return null;
        }
        return notifyGroup.querySelector('[data-frequency="' + frequency + '"]');
    }

    function getFrequencyHint(frequency) {
        var button = getNotificationButton(frequency);
        if (!button) {
            return null;
        }

        if (frequency === 'custom') {
            var template = button.getAttribute('data-hint-template');
            if (template) {
                var placeholder = button.getAttribute('data-hint-placeholder') || '__days__';
                var days = null;
                if (customDaysInput) {
                    days = parseCustomDays(customDaysInput.value);
                }
                if (days == null) {
                    if (typeof jobNotificationState.customDays === 'number') {
                        days = jobNotificationState.customDays;
                    } else {
                        var defaultDays = parseInt(button.getAttribute('data-hint-default-days'), 10);
                        if (!isNaN(defaultDays)) {
                            days = defaultDays;
                        }
                    }
                }
                if (days == null) {
                    days = 1;
                }
                var stringDays = String(days);
                return template.split(placeholder).join(stringDays);
            }
        }

        var hint = button.getAttribute('data-hint');
        return hint && hint.length ? hint : null;
    }

    function previewFrequencyHint(frequency) {
        var hint = getFrequencyHint(frequency);
        if (hint) {
            setNotifyHint(hint);
            jobNotificationState.hint = hint;
        }
    }

    function setActiveFrequency(frequency) {
        if (!notifyGroup) {
            return;
        }
        notifyGroup.dataset.currentFrequency = frequency || 'disabled';
        notifyButtons.forEach(function(btn) {
            var isActive = btn.dataset.frequency === frequency;
            btn.classList.toggle('is-active', isActive);
            btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        });
    }

    function getActiveFrequency() {
        if (notifyGroup) {
            var current = notifyGroup.dataset.currentFrequency;
            if (current) {
                return current;
            }
        }
        return jobNotificationState.frequency || 'disabled';
    }

    function parseCustomDays(value) {
        var parsed = parseInt(value, 10);
        if (isNaN(parsed) || parsed < 1) {
            return null;
        }
        return Math.min(parsed, 365);
    }

    function submitNotificationPreference(frequency, customDays) {
        if (!window.updateJobNotificationsUrl) {
            return;
        }

        var payload = { frequency: frequency };
        if (frequency === 'custom') {
            payload.custom_days = customDays;
        }

        var previousFrequency = jobNotificationState.frequency;
        var previousCustomDays = jobNotificationState.customDays;
        var previousHint = jobNotificationState.hint;

        if (applyBtn) {
            applyBtn.disabled = true;
        }
        notifyButtons.forEach(function(btn) {
            btn.disabled = true;
        });

        fetch(window.updateJobNotificationsUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': window.csrfToken,
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        })
            .then(function(response) {
                if (!response.ok) {
                    throw new Error('bad_response');
                }
                return response.json();
            })
            .then(function(data) {
                jobNotificationState.frequency = data.frequency || frequency;
                if (typeof data.custom_days === 'number') {
                    jobNotificationState.customDays = data.custom_days;
                } else if (frequency === 'custom' && typeof customDays === 'number') {
                    jobNotificationState.customDays = customDays;
                }
                if (typeof data.hint === 'string' && data.hint.length) {
                    jobNotificationState.hint = data.hint;
                }

                setActiveFrequency(jobNotificationState.frequency);
                toggleCustomVisibility(jobNotificationState.frequency);

                if (customDaysInput && jobNotificationState.customDays) {
                    customDaysInput.value = jobNotificationState.customDays;
                }

                previewFrequencyHint(jobNotificationState.frequency);

                var popupMessage = data.message || __('Job notification preferences updated.');
                showIndyHubPopup(popupMessage, 'success');
            })
            .catch(function() {
                jobNotificationState.frequency = previousFrequency;
                jobNotificationState.customDays = previousCustomDays;
                jobNotificationState.hint = previousHint;
                setActiveFrequency(previousFrequency);
                toggleCustomVisibility(previousFrequency);
                if (customDaysInput && previousCustomDays) {
                    customDaysInput.value = previousCustomDays;
                }
                previewFrequencyHint(previousFrequency);
                showIndyHubPopup(__('Error updating job notification preferences.'), 'danger');
            })
            .finally(function() {
                if (applyBtn) {
                    applyBtn.disabled = false;
                }
                notifyButtons.forEach(function(btn) {
                    btn.disabled = false;
                });
            });
    }

    if (notifyGroup) {
        var initialFrequency = jobNotificationState.frequency || notifyGroup.dataset.currentFrequency || 'disabled';
        setActiveFrequency(initialFrequency);
        toggleCustomVisibility(initialFrequency);

        var initialHint = getFrequencyHint(initialFrequency) || jobNotificationState.hint;
        setNotifyHint(initialHint);
        if (initialHint) {
            jobNotificationState.hint = initialHint;
        }

        notifyButtons.forEach(function(btn) {
            btn.addEventListener('click', function() {
                var desired = btn.dataset.frequency;
                if (!desired) {
                    return;
                }

                previewFrequencyHint(desired);

                var current = getActiveFrequency();
                if (desired === current && desired !== 'custom') {
                    return;
                }

                setActiveFrequency(desired);
                toggleCustomVisibility(desired);

                if (desired === 'custom') {
                    jobNotificationState.frequency = 'custom';
                    if (customDaysInput) {
                        customDaysInput.focus();
                        customDaysInput.select();
                    }
                    return;
                }

                submitNotificationPreference(desired, jobNotificationState.customDays);
            });
        });
    } else {
        toggleCustomVisibility(jobNotificationState.frequency);
        previewFrequencyHint(jobNotificationState.frequency);
    }

    if (applyBtn) {
        applyBtn.addEventListener('click', function() {
            var selected = getActiveFrequency();
            var customValue = customDaysInput ? parseCustomDays(customDaysInput.value) : null;

            if (selected === 'custom' && !customValue) {
                showIndyHubPopup(__('Please enter a valid number of days for the custom cadence.'), 'warning');
                if (customDaysInput) {
                    customDaysInput.focus();
                }
                return;
            }

            var cadenceDays = selected === 'custom' ? (customValue || jobNotificationState.customDays) : jobNotificationState.customDays;
            if (selected === 'custom' && typeof cadenceDays === 'number') {
                jobNotificationState.customDays = cadenceDays;
                previewFrequencyHint('custom');
            }
            submitNotificationPreference(selected, cadenceDays);
        });
    }

    if (customDaysInput) {
        customDaysInput.addEventListener('input', function() {
            var parsed = parseCustomDays(customDaysInput.value);
            if (parsed) {
                jobNotificationState.customDays = parsed;
            }
            if (getActiveFrequency() === 'custom') {
                previewFrequencyHint('custom');
            }
        });
    }

    // Corporation job notification controls (per-corporation state management)
    var corpJobNotificationStates = {};

    function getCorpJobsHeaderBadge(corpId) {
        return document.querySelector('.corp-header-jobs-badge[data-corp-id="' + corpId + '"]');
    }

    function getCorpShareHeaderBadge(corpId) {
        return document.querySelector('.corp-header-share-badge[data-corp-id="' + corpId + '"]');
    }

    function getCorpJobsBadgeClass(frequency) {
        if (frequency === 'disabled') {
            return 'bg-danger-subtle text-danger';
        }
        if (frequency === 'immediate') {
            return 'bg-success-subtle text-success';
        }
        if (frequency === 'daily') {
            return 'bg-info-subtle text-info';
        }
        if (frequency === 'weekly') {
            return 'bg-warning-subtle text-warning';
        }
        if (frequency === 'monthly') {
            return 'bg-secondary-subtle text-secondary-emphasis';
        }
        if (frequency === 'custom') {
            return 'bg-light-subtle text-body';
        }
        return 'bg-info-subtle text-info';
    }

    function getCorpJobsBadgeLabel(frequency, customDays) {
        if (frequency === 'disabled') {
            return __('Muted');
        }
        if (frequency === 'immediate') {
            return __('Instant');
        }
        if (frequency === 'daily') {
            return __('Daily');
        }
        if (frequency === 'weekly') {
            return __('Weekly');
        }
        if (frequency === 'monthly') {
            return __('Monthly');
        }
        if (frequency === 'custom') {
            var days = typeof customDays === 'number' ? customDays : 3;
            return String(days) + 'd';
        }
        return String(frequency || '');
    }

    function updateCorpAccordionJobsHeader(corpId, frequency, customDays) {
        var badge = getCorpJobsHeaderBadge(corpId);
        if (!badge) {
            return;
        }
        badge.className = 'badge rounded-pill corp-header-jobs-badge ' + getCorpJobsBadgeClass(frequency);
        badge.setAttribute('data-corp-id', corpId);

        var label = badge.querySelector('.corp-header-jobs-label');
        if (label) {
            label.textContent = getCorpJobsBadgeLabel(frequency, customDays);
        }
    }

    function updateCorpAccordionShareHeader(corpId, payload) {
        var badge = getCorpShareHeaderBadge(corpId);
        if (!badge || !payload) {
            return;
        }
        if (payload.badge_class) {
            badge.className = 'badge rounded-pill corp-header-share-badge ' + payload.badge_class;
            badge.setAttribute('data-corp-id', corpId);
        }
        var label = badge.querySelector('.corp-header-share-label');
        if (label && payload.status_label) {
            label.textContent = payload.status_label;
        }
    }

    // Initialize per-corporation states from context
    if (window.corporationJobNotificationControls && Array.isArray(window.corporationJobNotificationControls)) {
        window.corporationJobNotificationControls.forEach(function(corp) {
            corpJobNotificationStates[corp.corporation_id] = {
                frequency: corp.frequency || 'disabled',
                customDays: corp.custom_days || 3,
                hint: corp.hint || ''
            };
        });
    }

    // Helper function to get state for a corporation
    function getCorpState(corpId) {
        if (!corpJobNotificationStates[corpId]) {
            corpJobNotificationStates[corpId] = {
                frequency: 'disabled',
                customDays: 3,
                hint: ''
            };
        }
        return corpJobNotificationStates[corpId];
    }

    // Setup event listeners for each corporation's job notification controls
    var corpJobAlertControls = document.querySelectorAll('.corp-job-alert-control');
    corpJobAlertControls.forEach(function(controlDiv) {
        var corpId = controlDiv.getAttribute('data-corp-id');
        if (!corpId) {
            return;
        }

        var notifyGroup = controlDiv.querySelector('[data-corp-id="' + corpId + '"][role="group"]');
        var notifyButtons = notifyGroup ? Array.from(notifyGroup.querySelectorAll('[data-frequency]')) : [];
        var customWrapper = controlDiv.querySelector('#corp-job-notification-custom-wrapper-' + corpId);
        var customDaysInput = customWrapper ? customWrapper.querySelector('input[type="number"]') : null;
        var applyBtn = controlDiv.querySelector('[data-corp-id="' + corpId + '"].corp-job-notification-apply');
        var applyWrapper = controlDiv.querySelector('#corp-job-notification-apply-wrapper-' + corpId);
        var corpNotifyHint = controlDiv.querySelector('.corp-notify-hint');

        var corpState = getCorpState(corpId);

        function setCorpNotifyHint(text) {
            if (corpNotifyHint) {
                corpNotifyHint.textContent = text || '';
            }
        }

        function toggleCorpCustomVisibility(value) {
            if (!customWrapper) {
                return;
            }
            if (value === 'custom') {
                customWrapper.classList.remove('d-none');
                if (applyWrapper) {
                    applyWrapper.classList.remove('d-none');
                }
            } else {
                customWrapper.classList.add('d-none');
                if (applyWrapper) {
                    applyWrapper.classList.add('d-none');
                }
            }
        }

        function getCorpNotificationButton(frequency) {
            if (!notifyGroup) {
                return null;
            }
            return notifyGroup.querySelector('[data-frequency="' + frequency + '"]');
        }

        function getCorpFrequencyHint(frequency) {
            var button = getCorpNotificationButton(frequency);
            if (!button) {
                return null;
            }

            if (frequency === 'custom') {
                var template = button.getAttribute('data-hint-template');
                if (template) {
                    var placeholder = button.getAttribute('data-hint-placeholder') || '__days__';
                    var days = null;
                    if (customDaysInput) {
                        days = parseCustomDays(customDaysInput.value);
                    }
                    if (days == null) {
                        if (typeof corpState.customDays === 'number') {
                            days = corpState.customDays;
                        } else {
                            var defaultDays = parseInt(button.getAttribute('data-hint-default-days'), 10);
                            if (!isNaN(defaultDays)) {
                                days = defaultDays;
                            }
                        }
                    }
                    if (days == null) {
                        days = 1;
                    }
                    var stringDays = String(days);
                    return template.split(placeholder).join(stringDays);
                }
            }

            var hint = button.getAttribute('data-hint');
            return hint && hint.length ? hint : null;
        }

        function previewCorpFrequencyHint(frequency) {
            var hint = getCorpFrequencyHint(frequency);
            if (hint) {
                setCorpNotifyHint(hint);
                corpState.hint = hint;
            }
        }

        function setCorpActiveFrequency(frequency) {
            if (!notifyGroup) {
                return;
            }
            notifyGroup.dataset.currentFrequency = frequency || 'disabled';
            notifyButtons.forEach(function(btn) {
                var isActive = btn.dataset.frequency === frequency;
                btn.classList.toggle('is-active', isActive);
                btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            });
        }

        function getCorpActiveFrequency() {
            if (notifyGroup) {
                var current = notifyGroup.dataset.currentFrequency;
                if (current) {
                    return current;
                }
            }
            return corpState.frequency || 'disabled';
        }

        function submitCorpNotificationPreference(frequency, customDays) {
            if (!window.updateCorporationJobNotificationsUrl) {
                return;
            }

            var payload = {
                frequency: frequency,
                corporation_id: parseInt(corpId, 10)
            };
            if (frequency === 'custom') {
                payload.custom_days = customDays;
            }

            var previousFrequency = corpState.frequency;
            var previousCustomDays = corpState.customDays;
            var previousHint = corpState.hint;

            if (applyBtn) {
                applyBtn.disabled = true;
            }
            notifyButtons.forEach(function(btn) {
                btn.disabled = true;
            });

            fetch(window.updateCorporationJobNotificationsUrl, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': window.csrfToken,
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            })
                .then(function(response) {
                    if (!response.ok) {
                        throw new Error('bad_response');
                    }
                    return response.json();
                })
                .then(function(data) {
                    corpState.frequency = data.frequency || frequency;
                    if (typeof data.custom_days === 'number') {
                        corpState.customDays = data.custom_days;
                    } else if (frequency === 'custom' && typeof customDays === 'number') {
                        corpState.customDays = customDays;
                    }
                    if (typeof data.hint === 'string' && data.hint.length) {
                        corpState.hint = data.hint;
                    }

                    setCorpActiveFrequency(corpState.frequency);
                    toggleCorpCustomVisibility(corpState.frequency);

                    if (customDaysInput && corpState.customDays) {
                        customDaysInput.value = corpState.customDays;
                    }

                    previewCorpFrequencyHint(corpState.frequency);

                    updateCorpAccordionJobsHeader(corpId, corpState.frequency, corpState.customDays);

                    var popupMessage = data.message || __('Corporation job notification preferences updated.');
                    showIndyHubPopup(popupMessage, 'success');
                })
                .catch(function() {
                    corpState.frequency = previousFrequency;
                    corpState.customDays = previousCustomDays;
                    corpState.hint = previousHint;
                    setCorpActiveFrequency(previousFrequency);
                    toggleCorpCustomVisibility(previousFrequency);
                    if (customDaysInput && previousCustomDays) {
                        customDaysInput.value = previousCustomDays;
                    }
                    previewCorpFrequencyHint(previousFrequency);
                    showIndyHubPopup(__('Error updating corporation job notification preferences.'), 'danger');
                })
                .finally(function() {
                    if (applyBtn) {
                        applyBtn.disabled = false;
                    }
                    notifyButtons.forEach(function(btn) {
                        btn.disabled = false;
                    });
                });
        }

        // Setup notification button clicks for this corporation
        notifyButtons.forEach(function(button) {
            button.addEventListener('click', function() {
                var selectedFrequency = button.dataset.frequency;
                setCorpActiveFrequency(selectedFrequency);
                toggleCorpCustomVisibility(selectedFrequency);
                previewCorpFrequencyHint(selectedFrequency);

                // Auto-save for non-custom frequencies
                if (selectedFrequency !== 'custom') {
                    submitCorpNotificationPreference(selectedFrequency, null);
                }

                // For custom, preview only; save is manual via button click
                if (selectedFrequency === 'custom') {
                    previewCorpFrequencyHint('custom');
                }
            });
        });

        // Setup custom days input change listener
        if (customDaysInput) {
            customDaysInput.addEventListener('input', function() {
                var parsed = parseCustomDays(customDaysInput.value);
                if (parsed) {
                    corpState.customDays = parsed;
                }
                if (getCorpActiveFrequency() === 'custom') {
                    previewCorpFrequencyHint('custom');
                }
            });
        }

        // Setup apply button for custom frequency
        if (applyBtn) {
            applyBtn.addEventListener('click', function() {
                var selectedFrequency = getCorpActiveFrequency();
                var customDays = customDaysInput ? parseCustomDays(customDaysInput.value) : corpState.customDays;

                if (selectedFrequency === 'custom' && !customDays) {
                    showIndyHubPopup(__('Please enter a valid number of days for the custom cadence.'), 'warning');
                    if (customDaysInput) {
                        customDaysInput.focus();
                    }
                    return;
                }

                if (selectedFrequency === 'custom' && typeof customDays === 'number') {
                    corpState.customDays = customDays;
                    submitCorpNotificationPreference('custom', customDays);
                }
            });
        }
    });

    // Blueprint copy sharing segmented control
    var shareGroup = document.getElementById('share-mode-group');
    var shareStates = window.copySharingStates || {};

    if (shareGroup) {
        var shareButtons = Array.from(shareGroup.querySelectorAll('[data-share-scope]'));
        var shareConfirmModalElement = document.getElementById('copy-sharing-confirm-modal');
        var shareConfirmModal = null;
        var shareConfirmAcceptBtn = document.getElementById('copy-sharing-confirm-accept');
        var shareConfirmMessage = document.getElementById('copy-sharing-confirm-message');
        var shareConfirmList = document.getElementById('copy-sharing-confirm-list');
        var pendingShareChange = null;

        if (shareConfirmModalElement && window.bootstrap && typeof window.bootstrap.Modal === 'function') {
            shareConfirmModal = new window.bootstrap.Modal(shareConfirmModalElement);
            shareConfirmModalElement.addEventListener('hidden.bs.modal', function() {
                pendingShareChange = null;
                if (shareConfirmAcceptBtn) {
                    shareConfirmAcceptBtn.disabled = false;
                }
            });
        }

        function setShareButtonsDisabled(disabled) {
            shareButtons.forEach(function(btn) {
                btn.disabled = !!disabled;
            });
        }

        function setActiveScope(scope) {
            shareGroup.dataset.currentScope = scope || '';
            shareButtons.forEach(function(btn) {
                var isActive = btn.dataset.shareScope === scope;
                btn.classList.toggle('is-active', isActive);
                btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            });
        }

        function applyShareState(data, fallbackScope) {
            var scope = (data && data.scope) || fallbackScope || shareGroup.dataset.currentScope || 'none';
            setActiveScope(scope);

            var shareState = document.getElementById('copy-sharing-state');
            var shareHint = document.getElementById('copy-sharing-hint');
            var shareBadge = document.getElementById('share-status-badge');
            var shareStatusText = document.getElementById('share-status-text');
            var fulfillHint = document.getElementById('share-fulfill-hint');
            var shareSubtitle = document.getElementById('share-subtitle');
            var shareExplanation = document.getElementById('copy-sharing-explanation');

            if (shareState) {
                var badgeClassRoot = (data && data.badge_class) ? data.badge_class : 'bg-danger-subtle text-danger';
                shareState.className = 'badge rounded-pill share-mode-badge ' + badgeClassRoot;
                if (data && Object.prototype.hasOwnProperty.call(data, 'status_label')) {
                    shareState.textContent = data.status_label || '';
                } else if (data && Object.prototype.hasOwnProperty.call(data, 'button_label')) {
                    shareState.textContent = data.button_label || '';
                }
            }

            if (shareHint && data && Object.prototype.hasOwnProperty.call(data, 'button_hint')) {
                shareHint.textContent = data.button_hint || '';
            }

            if (shareBadge) {
                var badgeClass = data && data.badge_class ? data.badge_class : 'bg-danger-subtle text-danger';
                shareBadge.className = 'badge rounded-pill fw-semibold ' + badgeClass;
                if (data && Object.prototype.hasOwnProperty.call(data, 'status_label')) {
                    shareBadge.textContent = data.status_label || '';
                }
            }

            if (shareStatusText && data && Object.prototype.hasOwnProperty.call(data, 'status_hint')) {
                shareStatusText.textContent = data.status_hint || '';
            }

            if (fulfillHint && data && Object.prototype.hasOwnProperty.call(data, 'fulfill_hint')) {
                fulfillHint.textContent = data.fulfill_hint || '';
            }

            if (shareSubtitle && data && Object.prototype.hasOwnProperty.call(data, 'subtitle')) {
                shareSubtitle.textContent = data.subtitle || '';
            }

            if (shareExplanation && data && Object.prototype.hasOwnProperty.call(data, 'explanation')) {
                shareExplanation.textContent = data.explanation || '';
            }
        }

        function handleShareSuccess(desiredScope, data) {
            shareStates[desiredScope] = Object.assign({}, shareStates[desiredScope] || {}, data);
            applyShareState(data, desiredScope);
            var popupTone = data.enabled ? 'success' : 'secondary';
            if (data.declined_count) {
                popupTone = 'warning';
            }
            var popupMessage = data.popup_message || (data.enabled ? __('Blueprint sharing enabled.') : __('Blueprint sharing disabled.'));
            if (data.declined_message) {
                popupMessage += ' ' + data.declined_message;
            }
            showIndyHubPopup(popupMessage, popupTone);
        }

        function handleShareFailure() {
            showIndyHubPopup(__('Error updating blueprint sharing.'), 'danger');
        }

        function requestShareChange(desiredScope, options) {
            options = options || {};
            if (!window.toggleCopySharingUrl) {
                return Promise.reject(new Error('missing_url'));
            }
            var payload = { scope: desiredScope };
            if (options.confirmed) {
                payload.confirmed = true;
            }
            setShareButtonsDisabled(true);
            return fetch(window.toggleCopySharingUrl, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': window.csrfToken,
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            })
                .then(function(response) {
                    return response
                        .json()
                        .catch(function() { return {}; })
                        .then(function(data) {
                            return { response: response, data: data };
                        });
                })
                .finally(function() {
                    setShareButtonsDisabled(false);
                });
        }

        function finalizeConfirmedShare(scope) {
            return requestShareChange(scope, { confirmed: true })
                .then(function(result) {
                    if (!result || !result.response) {
                        handleShareFailure();
                        return;
                    }
                    if (result.response.ok) {
                        if (shareConfirmModal) {
                            shareConfirmModal.hide();
                        }
                        handleShareSuccess(scope, result.data || {});
                    } else if (result.data && result.data.requires_confirmation) {
                        renderShareConfirmation(result.data, scope);
                    } else {
                        handleShareFailure();
                    }
                })
                .catch(handleShareFailure);
        }

        function renderShareConfirmation(payload, desiredScope) {
            if (!shareConfirmModal) {
                var fallbackMessage = payload.confirmation_message || __('Changing sharing scope will decline accepted requests. Continue?');
                if (window.confirm(fallbackMessage)) {
                    finalizeConfirmedShare(desiredScope);
                }
                return;
            }
            pendingShareChange = {
                scope: desiredScope,
                payload: payload
            };
            if (shareConfirmMessage) {
                shareConfirmMessage.textContent = payload.confirmation_message || '';
            }
            if (shareConfirmList) {
                shareConfirmList.innerHTML = '';
                if (Array.isArray(payload.impacted_examples) && payload.impacted_examples.length) {
                    payload.impacted_examples.forEach(function(example) {
                        var item = document.createElement('li');
                        item.className = 'list-group-item py-2 px-3 d-flex justify-content-between align-items-start';
                        var label = document.createElement('div');
                        label.className = 'me-2';
                        label.textContent = example.type_name || ('Request #' + example.request_id);
                        item.appendChild(label);
                        if (example.buyer) {
                            var buyer = document.createElement('span');
                            buyer.className = 'badge bg-secondary-subtle text-secondary-emphasis rounded-pill';
                            buyer.textContent = example.buyer;
                            item.appendChild(buyer);
                        }
                        shareConfirmList.appendChild(item);
                    });
                    if (payload.impacted_count > payload.impacted_examples.length) {
                        var remaining = payload.impacted_count - payload.impacted_examples.length;
                        var moreItem = document.createElement('li');
                        moreItem.className = 'list-group-item py-2 px-3 text-muted fst-italic';
                        var template = n__('%(count)s more request', '%(count)s more requests', remaining);
                        moreItem.textContent = '...' + template.replace('%(count)s', remaining);
                        shareConfirmList.appendChild(moreItem);
                    }
                } else {
                    var emptyItem = document.createElement('li');
                    emptyItem.className = 'list-group-item py-2 px-3 text-muted';
                    emptyItem.textContent = __('Accepted requests will be declined.');
                    shareConfirmList.appendChild(emptyItem);
                }
            }
            if (shareConfirmAcceptBtn) {
                shareConfirmAcceptBtn.disabled = false;
                shareConfirmAcceptBtn.textContent = shareConfirmAcceptBtn.dataset.confirmLabel || __('Confirm');
            }
            shareConfirmModal.show();
        }

        if (shareConfirmAcceptBtn && shareConfirmModal) {
            shareConfirmAcceptBtn.addEventListener('click', function() {
                if (!pendingShareChange) {
                    shareConfirmModal.hide();
                    return;
                }
                shareConfirmAcceptBtn.disabled = true;
                shareConfirmAcceptBtn.textContent = shareConfirmAcceptBtn.dataset.loadingLabel || __('Updating...');
                finalizeConfirmedShare(pendingShareChange.scope).finally(function() {
                    shareConfirmAcceptBtn.disabled = false;
                    shareConfirmAcceptBtn.textContent = shareConfirmAcceptBtn.dataset.confirmLabel || __('Confirm');
                });
            });
        }

        var initialScope = shareGroup.dataset.currentScope || 'none';
        if (shareStates[initialScope]) {
            shareStates[initialScope].scope = initialScope;
            applyShareState(shareStates[initialScope], initialScope);
        } else {
            setActiveScope(initialScope);
        }

        shareButtons.forEach(function(btn) {
            btn.addEventListener('click', function() {
                var desiredScope = btn.dataset.shareScope;
                if (!desiredScope || shareGroup.dataset.currentScope === desiredScope) {
                    if (desiredScope && shareStates[desiredScope]) {
                        applyShareState(shareStates[desiredScope], desiredScope);
                    }
                    return;
                }

                requestShareChange(desiredScope, { confirmed: false })
                    .then(function(result) {
                        if (!result || !result.response) {
                            handleShareFailure();
                            return;
                        }
                        if (result.response.ok) {
                            handleShareSuccess(desiredScope, result.data || {});
                            return;
                        }
                        if (result.data && result.data.requires_confirmation) {
                            renderShareConfirmation(result.data, desiredScope);
                            return;
                        }
                        handleShareFailure();
                    })
                    .catch(handleShareFailure);
            });
        });
    }

    // Corporation-level sharing controls
    var corpGroups = Array.from(document.querySelectorAll('.corp-share-mode-group'));
    if (corpGroups.length) {
        corpGroups.forEach(function(group) {
            var corpId = group.dataset.corpId;
            if (!corpId) {
                return;
            }
            var corpButtons = Array.from(group.querySelectorAll('[data-share-scope]'));
            function setCorpActive(scope) {
                group.dataset.currentScope = scope || '';
                corpButtons.forEach(function(btn) {
                    var isActive = btn.dataset.shareScope === scope;
                    btn.classList.toggle('is-active', isActive);
                    btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
                });
            }

            function updateCorpUI(payload) {
                if (!payload) {
                    return;
                }
                setCorpActive(payload.scope);
                var container = group.closest('.corp-share-control');
                if (!container) {
                    return;
                }
                var badge = container.querySelector('.corp-share-badge');
                if (badge) {
                    var nextBadgeClass = payload.badge_class ? payload.badge_class : null;
                    if (nextBadgeClass) {
                        badge.className = 'badge rounded-pill corp-share-badge ' + nextBadgeClass;
                    }
                    if (payload.status_label) {
                        badge.textContent = payload.status_label;
                    }
                }
                var hint = container.querySelector('.corp-share-hint');
                if (hint) {
                    if (payload.status_hint) {
                        hint.textContent = payload.status_hint;
                    } else if (payload.button_hint) {
                        hint.textContent = payload.button_hint;
                    }
                }
            }

            corpButtons.forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var desiredScope = btn.dataset.shareScope;
                    if (!desiredScope || group.dataset.currentScope === desiredScope) {
                        return;
                    }
                    if (!group.dataset.hasBlueprintScope || group.dataset.hasBlueprintScope !== 'true') {
                        showIndyHubPopup(__('Authorize a director blueprint token before enabling sharing.'), 'warning');
                        return;
                    }
                    fetch(window.toggleCorporationCopySharingUrl, {
                        method: 'POST',
                        headers: {
                            'X-CSRFToken': window.csrfToken,
                            'Accept': 'application/json',
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            corporation_id: corpId,
                            scope: desiredScope
                        })
                    })
                        .then(function(r) { return r.json(); })
                        .then(function(data) {
                            if (data.error) {
                                showIndyHubPopup(__('Error updating corporate sharing.'), 'danger');
                                return;
                            }
                            updateCorpUI(data);
                            updateCorpAccordionShareHeader(corpId, data);
                            var popupMessage = data.popup_message || __('Corporate blueprint sharing updated.');
                            showIndyHubPopup(popupMessage, data.enabled ? 'success' : 'secondary');
                        })
                        .catch(function() {
                            showIndyHubPopup(__('Error updating corporate sharing.'), 'danger');
                        });
                });
            });
        });
    }
});

window.addEventListener('pageshow', function () {
    refreshCopyChatAlerts();
});
