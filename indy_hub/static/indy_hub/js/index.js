/* Indy Hub Index Page JavaScript */

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
    var popupElement = document.getElementById('indy-hub-popup');
    if (popupElement) {
        var dismissButton = popupElement.querySelector('.indy-hub-popup-dismiss');
        if (dismissButton) {
            dismissButton.addEventListener('click', hideIndyHubPopup);
        }
    }

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
        } else {
            customWrapper.classList.add('d-none');
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

                var popupMessage = data.message || 'Job notification preferences updated.';
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
                showIndyHubPopup('Error updating job notification preferences.', 'danger');
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
                showIndyHubPopup('Please enter a valid number of days for the custom cadence.', 'warning');
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

    // Blueprint copy sharing segmented control
    var shareGroup = document.getElementById('share-mode-group');
    var shareStates = window.copySharingStates || {};

    if (shareGroup) {
        var shareButtons = Array.from(shareGroup.querySelectorAll('[data-share-scope]'));

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
            var shareExplanation = document.getElementById('copy-sharing-explanation');

            if (shareState) {
                var stateClass = 'badge rounded-pill share-mode-badge ' + (data && data.badge_class ? data.badge_class : 'bg-secondary-subtle text-secondary');
                shareState.className = stateClass;
                if (data && Object.prototype.hasOwnProperty.call(data, 'button_label')) {
                    shareState.textContent = data.button_label || '';
                }
            }

            if (shareHint && data && Object.prototype.hasOwnProperty.call(data, 'button_hint')) {
                shareHint.textContent = data.button_hint || '';
            }

            if (shareBadge) {
                var badgeClass = data && data.badge_class ? data.badge_class : 'bg-secondary-subtle text-secondary';
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

            if (shareExplanation && data && Object.prototype.hasOwnProperty.call(data, 'explanation')) {
                shareExplanation.textContent = data.explanation || '';
            }
        }

        var initialScope = shareGroup.dataset.currentScope || 'none';
        if (shareStates[initialScope]) {
            shareStates[initialScope].scope = initialScope;
            applyShareState(shareStates[initialScope], initialScope);
        } else {
            setActiveScope(initialScope);
        }

        function bindShareButton(btn) {
            btn.addEventListener('click', function() {
                var desiredScope = btn.dataset.shareScope;
                if (!desiredScope) {
                    return;
                }

                if (shareGroup.dataset.currentScope === desiredScope) {
                    if (shareStates[desiredScope]) {
                        applyShareState(shareStates[desiredScope], desiredScope);
                    }
                    return;
                }

                fetch(window.toggleCopySharingUrl, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': window.csrfToken,
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ scope: desiredScope })
                })
                .then(r => r.json())
                .then(data => {
                    shareStates[desiredScope] = Object.assign({}, shareStates[desiredScope] || {}, data);
                    applyShareState(data, desiredScope);
                    showIndyHubPopup(
                        data.popup_message || (data.enabled ? 'Blueprint sharing enabled.' : 'Blueprint sharing disabled.'),
                        data.enabled ? 'success' : 'secondary'
                    );
                })
                .catch(function() {
                    showIndyHubPopup('Error updating blueprint sharing.', 'danger');
                });
            });
        }

        shareButtons.forEach(bindShareButton);
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
                if (badge && payload.badge_class) {
                    badge.className = 'badge rounded-pill corp-share-badge ' + payload.badge_class;
                    if (payload.status_label) {
                        badge.textContent = payload.status_label;
                    }
                }
                var hint = container.querySelector('.corp-share-hint');
                if (hint && payload.status_hint) {
                    hint.textContent = payload.status_hint;
                }
            }

            corpButtons.forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var desiredScope = btn.dataset.shareScope;
                    if (!desiredScope || group.dataset.currentScope === desiredScope) {
                        return;
                    }
                    if (!group.dataset.hasBlueprintScope || group.dataset.hasBlueprintScope !== 'true') {
                        showIndyHubPopup('Authorize a director blueprint token before enabling sharing.', 'warning');
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
                                showIndyHubPopup('Error updating corporate sharing.', 'danger');
                                return;
                            }
                            updateCorpUI(data);
                            var popupMessage = data.popup_message || 'Corporate blueprint sharing updated.';
                            showIndyHubPopup(popupMessage, data.enabled ? 'success' : 'secondary');
                        })
                        .catch(function() {
                            showIndyHubPopup('Error updating corporate sharing.', 'danger');
                        });
                });
            });
        });
    }
});
