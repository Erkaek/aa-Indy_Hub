/**
 * Industry Jobs JavaScript - Optimized for large datasets and real-time progress
 */

class JobManager {
    constructor() {
        this.jobs = [];
        this.filteredJobs = [];
        this.currentPage = 1;
        this.itemsPerPage = 50;
        this.sortBy = 'start_date';
        this.sortDirection = 'desc';
        this.filters = {
            search: '',
            status: '',
            activity: '',
            characterId: ''
        };
        this.progressUpdateInterval = null;

        this.init();
    }

    init() {
        this.loadJobs();
        this.bindEvents();
        this.startProgressUpdates();
        this.updateDisplay();
    }

    loadJobs() {
        const jobElements = document.querySelectorAll('.job-item');
        this.jobs = Array.from(jobElements).map(el => ({
            element: el,
            jobId: el.dataset.jobId,
            activityId: parseInt(el.dataset.activityId),
            status: el.dataset.status,
            characterId: el.dataset.characterId,
            startDate: new Date(el.dataset.startDate),
            endDate: new Date(el.dataset.endDate),
            blueprintTypeId: el.querySelector('[data-blueprint-type-id]')?.dataset.blueprintTypeId,
            productTypeId: el.querySelector('[data-product-type-id]')?.dataset.productTypeId,
            runs: parseInt(el.querySelector('.badge')?.textContent || 0)
        }));

        this.filteredJobs = [...this.jobs];
    }

    bindEvents() {
        // Search input with debouncing
        const searchInput = document.getElementById('jobSearch');
        if (searchInput) {
            let searchTimeout;
            searchInput.addEventListener('input', (e) => {
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(() => {
                    this.filters.search = e.target.value.toLowerCase();
                    this.applyFilters();
                }, 300);
            });
        }

        // Filter selects
        ['statusFilter', 'activityFilter', 'characterFilter'].forEach(id => {
            const element = document.getElementById(id);
            if (element) {
                element.addEventListener('change', (e) => {
                    const filterType = id.replace('Filter', '');
                    this.filters[filterType === 'activity' ? 'activity' : filterType === 'character' ? 'characterId' : filterType] = e.target.value;
                    this.applyFilters();
                });
            }
        });

        // Page size selector
        const pageSizeSelect = document.getElementById('pageSize');
        if (pageSizeSelect) {
            pageSizeSelect.addEventListener('change', (e) => {
                this.itemsPerPage = parseInt(e.target.value);
                this.currentPage = 1;
                this.updateDisplay();
            });
        }

        // Export button
        const exportBtn = document.getElementById('exportBtn');
        if (exportBtn) {
            exportBtn.addEventListener('click', () => this.exportData());
        }

        // Clear filters function
        window.clearFilters = () => {
            this.filters = {
                search: '',
                status: '',
                activity: '',
                characterId: ''
            };

            // Reset form elements
            const searchInput = document.getElementById('jobSearch');
            if (searchInput) searchInput.value = '';

            ['statusFilter', 'activityFilter', 'characterFilter'].forEach(id => {
                const element = document.getElementById(id);
                if (element) element.value = '';
            });

            this.applyFilters();
        };
    }

    applyFilters() {
        this.filteredJobs = this.jobs.filter(job => {
            // Search filter
            if (this.filters.search) {
                const searchableText = `${job.element.textContent}`.toLowerCase();
                if (!searchableText.includes(this.filters.search)) {
                    return false;
                }
            }

            // Status filter
            if (this.filters.status && job.status !== this.filters.status) {
                return false;
            }

            // Activity filter
            if (this.filters.activity && job.activityId.toString() !== this.filters.activity) {
                return false;
            }

            // Character filter
            if (this.filters.characterId && job.characterId !== this.filters.characterId) {
                return false;
            }

            return true;
        });

        this.currentPage = 1;
        this.applySort();
        this.updateDisplay();
    }

    applySort() {
        this.filteredJobs.sort((a, b) => {
            let aVal, bVal;

            switch (this.sortBy) {
                case 'start_date':
                    aVal = a.startDate;
                    bVal = b.startDate;
                    break;
                case 'end_date':
                    aVal = a.endDate;
                    bVal = b.endDate;
                    break;
                case 'activity':
                    aVal = a.activityId;
                    bVal = b.activityId;
                    break;
                case 'status':
                    aVal = a.status;
                    bVal = b.status;
                    break;
                case 'runs':
                    aVal = a.runs;
                    bVal = b.runs;
                    break;
                default:
                    return 0;
            }

            if (aVal instanceof Date) {
                return this.sortDirection === 'asc'
                    ? aVal - bVal
                    : bVal - aVal;
            } else if (typeof aVal === 'string') {
                return this.sortDirection === 'asc'
                    ? aVal.localeCompare(bVal)
                    : bVal.localeCompare(aVal);
            } else {
                return this.sortDirection === 'asc'
                    ? aVal - bVal
                    : bVal - aVal;
            }
        });
    }

    updateDisplay() {
        const container = document.getElementById('jobsContainer');
        if (!container) return;

        // Calculate pagination
        const totalItems = this.filteredJobs.length;
        const totalPages = Math.ceil(totalItems / this.itemsPerPage);
        const startIndex = (this.currentPage - 1) * this.itemsPerPage;
        const endIndex = Math.min(startIndex + this.itemsPerPage, totalItems);

        // Hide all job items first
        this.jobs.forEach(job => {
            job.element.style.display = 'none';
        });

        // Show current page items
        for (let i = startIndex; i < endIndex; i++) {
            const job = this.filteredJobs[i];
            if (job && job.element) {
                job.element.style.display = '';
            }
        }

        // Update pagination info
        this.updatePaginationInfo(startIndex + 1, endIndex, totalItems, totalPages);
        this.updatePaginationControls(totalPages);
        this.updateFilteredStatistics();
    }

    updatePaginationInfo(start, end, total, totalPages) {
        const paginationInfo = document.getElementById('paginationInfo');
        if (paginationInfo) {
            paginationInfo.textContent = `Showing ${start}-${end} of ${total} jobs`;
        }

        const pageInfo = document.getElementById('pageInfo');
        if (pageInfo) {
            pageInfo.textContent = `Page ${this.currentPage} of ${totalPages}`;
        }
    }

    updatePaginationControls(totalPages) {
        const prevBtn = document.getElementById('prevPage');
        const nextBtn = document.getElementById('nextPage');

        if (prevBtn) {
            prevBtn.disabled = this.currentPage <= 1;
            prevBtn.onclick = () => {
                if (this.currentPage > 1) {
                    this.currentPage--;
                    this.updateDisplay();
                }
            };
        }

        if (nextBtn) {
            nextBtn.disabled = this.currentPage >= totalPages;
            nextBtn.onclick = () => {
                if (this.currentPage < totalPages) {
                    this.currentPage++;
                    this.updateDisplay();
                }
            };
        }
    }

    updateFilteredStatistics() {
        const stats = this.calculateStatistics(this.filteredJobs);
        const filteredStats = document.getElementById('filteredStats');
        if (filteredStats) {
            filteredStats.innerHTML = `
                <small class="text-muted">
                    Filtered: ${stats.total} total, ${stats.active} active, ${stats.completed} completed
                </small>
            `;
        }
    }

    calculateStatistics(jobs) {
        return {
            total: jobs.length,
            active: jobs.filter(job => job.status === 'active').length,
            completed: jobs.filter(job => ['ready', 'delivered'].includes(job.status)).length,
            paused: jobs.filter(job => job.status === 'paused').length
        };
    }

    startProgressUpdates() {
        // Update progress bars every 30 seconds
        this.progressUpdateInterval = setInterval(() => {
            this.updateActiveJobProgress();
        }, 30000);

        // Initial update
        this.updateActiveJobProgress();
    }

    updateActiveJobProgress() {
        const now = new Date();

        this.jobs.forEach(job => {
            if (job.status === 'active') {
                const progressBar = job.element.querySelector('.job-progress .progress-bar');
                if (progressBar) {
                    const totalDuration = job.endDate - job.startDate;
                    const elapsed = now - job.startDate;
                    const progress = Math.min(Math.max((elapsed / totalDuration) * 100, 0), 100);

                    progressBar.style.width = `${progress}%`;
                    progressBar.setAttribute('aria-valuenow', progress);
                    progressBar.querySelector('small').textContent = `${Math.round(progress)}%`;

                    // Add time remaining
                    if (progress < 100) {
                        const remaining = job.endDate - now;
                        if (remaining > 0) {
                            const timeRemaining = this.formatDuration(remaining);
                            progressBar.title = `Time remaining: ${timeRemaining}`;
                        }
                    }
                }
            }
        });
    }

    formatDuration(milliseconds) {
        const seconds = Math.floor(milliseconds / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);

        if (days > 0) {
            return `${days}d ${hours % 24}h ${minutes % 60}m`;
        } else if (hours > 0) {
            return `${hours}h ${minutes % 60}m`;
        } else {
            return `${minutes}m ${seconds % 60}s`;
        }
    }

    exportData() {
        const data = this.filteredJobs.map(job => {
            const row = job.element;
            return {
                'Job ID': job.jobId,
                'Activity': row.querySelector('.activity-badge')?.textContent?.trim() || '',
                'Blueprint': row.querySelector('.item-info strong')?.textContent?.trim() || '',
                'Product': row.querySelectorAll('.item-info strong')[1]?.textContent?.trim() || '',
                'Runs': job.runs,
                'Status': job.status,
                'Start Date': job.startDate.toISOString(),
                'End Date': job.endDate.toISOString(),
                'Character ID': job.characterId
            };
        });

        this.downloadCSV(data, 'industry_jobs.csv');
    }

    downloadCSV(data, filename) {
        if (data.length === 0) return;

        const headers = Object.keys(data[0]);
        const csvContent = [
            headers.join(','),
            ...data.map(row => headers.map(header => `"${row[header]}"`).join(','))
        ].join('\n');

        const blob = new Blob([csvContent], { type: 'text/csv' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        window.URL.revokeObjectURL(url);
    }

    destroy() {
        if (this.progressUpdateInterval) {
            clearInterval(this.progressUpdateInterval);
        }
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    if (document.querySelector('.job-item')) {
        window.jobManager = new JobManager();
    }

    // Performance monitoring
    if (window.performance && window.performance.mark) {
        window.performance.mark('jobs-js-loaded');
    }
});

// Cleanup on page unload
window.addEventListener('beforeunload', function() {
    if (window.jobManager) {
        window.jobManager.destroy();
    }
});
