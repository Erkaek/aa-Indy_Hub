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
                notifyBtn.className = 'btn flex-fill ' + (data.enabled ? 'btn-success' : 'btn-outline-secondary');
                document.getElementById('notify-label').textContent = data.enabled ? window.notificationsOnText : window.notificationsOffText;
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
                shareBtn.className = 'btn flex-fill ' + (data.enabled ? 'btn-success' : 'btn-outline-secondary');
                document.getElementById('copy-sharing-label').textContent = data.enabled ? window.sharingOnText : window.sharingOffText;
                showIndyHubPopup(
                    data.enabled ? "Blueprint sharing enabled." : "Blueprint sharing disabled.",
                    data.enabled ? 'success' : 'secondary'
                );
            });
        });
    }
});
