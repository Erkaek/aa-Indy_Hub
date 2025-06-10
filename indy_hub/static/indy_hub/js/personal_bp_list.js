/* Personal Blueprint List JavaScript */

function refreshBlueprints() {
    const btns = document.querySelectorAll('button[onclick="refreshBlueprints()"]');
    btns.forEach(btn => {
        const original = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>' + btn.textContent.trim();
        btn.disabled = true;
    });
    // Actually trigger the refresh by reloading with ?refresh=1
    const url = new URL(window.location.href);
    url.searchParams.set('refresh', '1');
    window.location.href = url.toString();
}

document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.blueprint-icon img').forEach(function(img) {
        img.onerror = function() {
            this.style.display = 'none';
            if (this.nextElementSibling) {
                this.nextElementSibling.style.display = 'flex';
            }
        };
    });
});
