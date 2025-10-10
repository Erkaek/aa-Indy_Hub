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
                    'Accept': 'application/json',
                },
            })
            .then(r => r.json())
            .then(data => {
                notifyBtn.dataset.enabled = data.enabled ? 'true' : 'false';
                notifyBtn.classList.toggle('is-active', Boolean(data.enabled));

                var notifyState = document.getElementById('notify-state');
                var notifyHint = document.getElementById('notify-hint');
                if (notifyState) {
                    notifyState.textContent = data.enabled ? notifyBtn.dataset.onLabel : notifyBtn.dataset.offLabel;
                }
                if (notifyHint) {
                    notifyHint.textContent = data.enabled ? notifyBtn.dataset.onHint : notifyBtn.dataset.offHint;
                }

                showIndyHubPopup(
                    data.enabled ? "Job notifications enabled." : "Job notifications disabled.",
                    data.enabled ? 'success' : 'secondary'
                );
            });
        });
    }

    // Blueprint copy sharing toggle
    var shareBtn = document.getElementById('toggle-copy-sharing');
    if (shareBtn) {
        shareBtn.addEventListener('click', function() {
            fetch(window.toggleCopySharingUrl, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': window.csrfToken,
                    'Accept': 'application/json',
                },
            })
            .then(r => r.json())
            .then(data => {
                shareBtn.dataset.enabled = data.enabled ? 'true' : 'false';
                shareBtn.classList.toggle('is-active', Boolean(data.enabled));

                var shareState = document.getElementById('copy-sharing-state');
                var shareHint = document.getElementById('copy-sharing-hint');
                if (shareState) {
                    shareState.textContent = data.enabled ? shareBtn.dataset.onLabel : shareBtn.dataset.offLabel;
                }
                if (shareHint) {
                    shareHint.textContent = data.enabled ? shareBtn.dataset.onHint : shareBtn.dataset.offHint;
                }

                showIndyHubPopup(
                    data.enabled ? "Blueprint sharing enabled." : "Blueprint sharing disabled.",
                    data.enabled ? 'success' : 'secondary'
                );
            });
        });
    }
});
