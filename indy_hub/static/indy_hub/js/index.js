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

    // Job notifications toggle
    var notifyBtn = document.getElementById('toggle-job-notify');
    if (notifyBtn) {
        notifyBtn.addEventListener('click', function() {
            fetch(window.toggleJobNotificationsUrl, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': window.csrfToken,
                    'Accept': 'application/json'
                }
            })
            .then(r => r.json())
            .then(data => {
                notifyBtn.dataset.enabled = data.enabled ? 'true' : 'false';
                notifyBtn.classList.toggle('is-active', Boolean(data.enabled));
                notifyBtn.setAttribute('aria-pressed', data.enabled ? 'true' : 'false');

                var notifyState = document.getElementById('notify-state');
                var notifyHint = document.getElementById('notify-hint');
                if (notifyState) {
                    notifyState.textContent = data.enabled ? notifyBtn.dataset.onLabel : notifyBtn.dataset.offLabel;
                }
                if (notifyHint) {
                    notifyHint.textContent = data.enabled ? notifyBtn.dataset.onHint : notifyBtn.dataset.offHint;
                }

                showIndyHubPopup(
                    data.enabled ? 'Job notifications enabled.' : 'Job notifications disabled.',
                    data.enabled ? 'success' : 'secondary'
                );
            })
            .catch(function() {
                showIndyHubPopup('Error updating job notifications.', 'danger');
            });
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
