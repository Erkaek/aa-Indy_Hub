/* Token Management Page JavaScript */

document.addEventListener('DOMContentLoaded', function() {
    // Add smooth animations for token cards
    const tokenCards = document.querySelectorAll('.token-card');
    tokenCards.forEach(card => {
        card.addEventListener('mouseenter', function() {
            this.style.transition = 'all 0.3s ease';
        });
    });

    // Add click feedback for authorization buttons
    const authButtons = document.querySelectorAll('a[href*="authorize"]');
    authButtons.forEach(button => {
        button.addEventListener('click', function() {
            // Add loading state
            const originalText = this.innerHTML;
            this.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>' + window.redirectingText;
            this.disabled = true;

            // Reset after a delay (in case user comes back quickly)
            setTimeout(() => {
                this.innerHTML = originalText;
                this.disabled = false;
            }, 5000);
        });
    });
});
