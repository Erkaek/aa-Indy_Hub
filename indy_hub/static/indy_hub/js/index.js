/* Indy Hub Index Page JavaScript */

// Global popup function for showing messages
function showIndyHubPopup(message, type) {
    var popup = document.getElementById('indy-hub-popup');
    popup.className = 'alert alert-' + (type || 'info') + ' position-fixed top-0 start-50 translate-middle-x mt-3';
    popup.textContent = message;
    popup.classList.remove('d-none');
    setTimeout(function() { popup.classList.add('d-none'); }, 2500);
}

// Initialize index page functionality
document.addEventListener('DOMContentLoaded', function() {
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
    if (!shareGroup) {
        return;
    }

    var shareButtons = Array.from(shareGroup.querySelectorAll('[data-share-scope]'));
    var shareStates = window.copySharingStates || {};

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

    shareButtons.forEach(function(btn) {
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
    });
});
