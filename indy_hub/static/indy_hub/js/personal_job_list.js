/* Personal Job List JavaScript */

document.addEventListener('DOMContentLoaded', function() {
    // Image onerror handler
    document.querySelectorAll('img').forEach(function(img) {
        img.onerror = function() {
            this.style.display = 'none';
            if (this.nextElementSibling) {
                this.nextElementSibling.style.display = 'inline';
            }
        };
    });

    // Updated job progress update function
    function updateJobProgressBars() {
        document.querySelectorAll('.progress.job-progress').forEach(function(bar) {
            var fill = bar.querySelector('.progress-bar');
            var percentLabel = fill.querySelector('.job-progress-percent');
            var etaLabel = bar.nextElementSibling.querySelector('.job-progress-eta');
            if (!fill) return;
            var start = parseInt(fill.getAttribute('data-start'));
            var end = parseInt(fill.getAttribute('data-end'));
            var now = Math.floor(Date.now()/1000);
            if (end > start) {
                var percent = Math.max(0, Math.min(100, ((now-start)/(end-start))*100));
                fill.style.width = percent + '%';
                fill.setAttribute('aria-valuenow', Math.round(percent));
                if (percentLabel) percentLabel.textContent = Math.round(percent) + '%';
                var secLeft = Math.max(0, end-now);
                var h = Math.floor(secLeft/3600);
                var m = Math.floor((secLeft%3600)/60);
                var s = secLeft%60;
                if (etaLabel) etaLabel.textContent = (h>0?h+':':'') + (m<10?'0':'')+m + ':' + (s<10?'0':'')+s;
            } else {
                fill.style.width = '0%';
                fill.setAttribute('aria-valuenow', 0);
                if (percentLabel) percentLabel.textContent = '0%';
                if (etaLabel) etaLabel.textContent = '--:--:--';
            }
        });
    }
    updateJobProgressBars();
    setInterval(updateJobProgressBars,1000);
});
