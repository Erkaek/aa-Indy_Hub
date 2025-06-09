/**
 * Blueprint List JavaScript - Optimized for large datasets
 */

class BlueprintManager {
    constructor() {
        this.blueprints = [];
        this.filteredBlueprints = [];
        this.currentPage = 1;
        this.itemsPerPage = 50;
        this.currentView = 'grid';
        this.sortBy = 'type_name';
        this.sortDirection = 'asc';
        this.filters = {
            search: '',
            efficiency: '',
            type: '',
            characterId: ''
        };

        this.init();
    }

    init() {
        this.loadBlueprints();
        this.bindEvents();
        this.setupIntersectionObserver();
        this.updateDisplay();
    }

    loadBlueprints() {
        const blueprintElements = document.querySelectorAll('.blueprint-item');
        this.blueprints = Array.from(blueprintElements).map(el => ({
            element: el,
            typeId: el.dataset.typeId,
            typeName: el.dataset.typeName || `Type ${el.dataset.typeId}`,
            me: parseInt(el.dataset.me),
            te: parseInt(el.dataset.te),
            runs: parseInt(el.dataset.runs),
            characterId: el.dataset.characterId,
            quantity: parseInt(el.dataset.quantity || 1)
        }));

        this.filteredBlueprints = [...this.blueprints];
    }

    bindEvents() {
        // Search input with debouncing
        const searchInput = document.getElementById('blueprintSearch');
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
        ['efficiencyFilter', 'typeFilter', 'characterFilter'].forEach(id => {
            const element = document.getElementById(id);
            if (element) {
                element.addEventListener('change', (e) => {
                    const filterType = id.replace('Filter', '');
                    this.filters[filterType] = e.target.value;
                    this.applyFilters();
                });
            }
        });

        // Sort controls
        const sortControls = document.querySelectorAll('.sort-control');
        sortControls.forEach(control => {
            control.addEventListener('click', (e) => {
                const sortBy = e.target.dataset.sort;
                if (this.sortBy === sortBy) {
                    this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
                } else {
                    this.sortBy = sortBy;
                    this.sortDirection = 'asc';
                }
                this.applySort();
                this.updateSortIndicators();
            });
        });

        // View mode toggles
        const viewToggles = document.querySelectorAll('.view-toggle button');
        viewToggles.forEach(toggle => {
            toggle.addEventListener('click', (e) => {
                this.currentView = e.target.dataset.view;
                this.updateViewMode();
            });
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

        // Bulk selection
        document.addEventListener('change', (e) => {
            if (e.target.classList.contains('blueprint-checkbox')) {
                this.updateBulkActions();
            }
        });
    }

    setupIntersectionObserver() {
        this.observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const card = entry.target;
                    card.classList.add('loaded');
                    this.observer.unobserve(card);
                }
            });
        }, {
            rootMargin: '50px'
        });
    }

    applyFilters() {
        this.filteredBlueprints = this.blueprints.filter(bp => {
            // Search filter
            if (this.filters.search) {
                const searchText = `${bp.typeName} ${bp.typeId}`.toLowerCase();
                if (!searchText.includes(this.filters.search)) {
                    return false;
                }
            }

            // Efficiency filter
            if (this.filters.efficiency) {
                switch (this.filters.efficiency) {
                    case 'perfect':
                        if (bp.me < 10 || bp.te < 20) return false;
                        break;
                    case 'researched':
                        if (bp.me === 0 && bp.te === 0) return false;
                        break;
                    case 'unresearched':
                        if (bp.me > 0 || bp.te > 0) return false;
                        break;
                }
            }

            // Type filter
            if (this.filters.type) {
                switch (this.filters.type) {
                    case 'original':
                        if (bp.runs !== -1) return false;
                        break;
                    case 'copy':
                        if (bp.runs === -1) return false;
                        break;
                }
            }

            // Character filter
            if (this.filters.character && bp.characterId !== this.filters.character) {
                return false;
            }

            return true;
        });

        this.currentPage = 1;
        this.applySort();
        this.updateDisplay();
    }

    applySort() {
        this.filteredBlueprints.sort((a, b) => {
            let aVal, bVal;

            switch (this.sortBy) {
                case 'type_name':
                    aVal = a.typeName;
                    bVal = b.typeName;
                    break;
                case 'me':
                    aVal = a.me;
                    bVal = b.me;
                    break;
                case 'te':
                    aVal = a.te;
                    bVal = b.te;
                    break;
                case 'runs':
                    aVal = a.runs === -1 ? Infinity : a.runs;
                    bVal = b.runs === -1 ? Infinity : b.runs;
                    break;
                case 'quantity':
                    aVal = a.quantity;
                    bVal = b.quantity;
                    break;
                default:
                    return 0;
            }

            if (typeof aVal === 'string') {
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
        const container = document.getElementById('blueprintsContainer');
        if (!container) return;

        // Calculate pagination
        const totalItems = this.filteredBlueprints.length;
        const totalPages = Math.ceil(totalItems / this.itemsPerPage);
        const startIndex = (this.currentPage - 1) * this.itemsPerPage;
        const endIndex = Math.min(startIndex + this.itemsPerPage, totalItems);

        // Hide all blueprint items first
        this.blueprints.forEach(bp => {
            bp.element.style.display = 'none';
        });

        // Show current page items
        for (let i = startIndex; i < endIndex; i++) {
            const bp = this.filteredBlueprints[i];
            if (bp && bp.element) {
                bp.element.style.display = '';
                // Add to intersection observer for lazy loading
                this.observer.observe(bp.element);
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
            paginationInfo.textContent = `Showing ${start}-${end} of ${total} blueprints`;
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

    updateSortIndicators() {
        const sortControls = document.querySelectorAll('.sort-control');
        sortControls.forEach(control => {
            const icon = control.querySelector('i');
            if (control.dataset.sort === this.sortBy) {
                control.classList.add('active');
                icon.className = this.sortDirection === 'asc'
                    ? 'fas fa-sort-up'
                    : 'fas fa-sort-down';
            } else {
                control.classList.remove('active');
                icon.className = 'fas fa-sort';
            }
        });
    }

    updateViewMode() {
        const container = document.getElementById('blueprintsContainer');
        const viewToggles = document.querySelectorAll('.view-toggle button');

        viewToggles.forEach(toggle => {
            toggle.classList.toggle('active', toggle.dataset.view === this.currentView);
        });

        if (container) {
            container.className = this.currentView === 'list'
                ? 'list-group'
                : 'row blueprint-grid';
        }
    }

    updateFilteredStatistics() {
        const stats = this.calculateStatistics(this.filteredBlueprints);
        const filteredStats = document.getElementById('filteredStats');
        if (filteredStats) {
            filteredStats.innerHTML = `
                <small class="text-muted">
                    Filtered: ${stats.total} total, ${stats.originals} originals, ${stats.copies} copies
                </small>
            `;
        }
    }

    calculateStatistics(blueprints) {
        return {
            total: blueprints.length,
            originals: blueprints.filter(bp => bp.runs === -1).length,
            copies: blueprints.filter(bp => bp.runs > 0).length
        };
    }

    exportData() {
        const data = this.filteredBlueprints.map(bp => ({
            'Type ID': bp.typeId,
            'Type Name': bp.typeName,
            'Material Efficiency': bp.me,
            'Time Efficiency': bp.te,
            'Runs': bp.runs === -1 ? 'Original' : bp.runs,
            'Quantity': bp.quantity,
            'Character ID': bp.characterId
        }));

        this.downloadCSV(data, 'blueprints.csv');
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

    updateBulkActions() {
        const checkedBoxes = document.querySelectorAll('.blueprint-checkbox:checked');
        const bulkActions = document.getElementById('bulkActions');

        if (bulkActions) {
            bulkActions.classList.toggle('active', checkedBoxes.length > 0);
        }

        const selectedCount = document.getElementById('selectedCount');
        if (selectedCount) {
            selectedCount.textContent = checkedBoxes.length;
        }
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    // Fallback universel pour toutes les images blueprint
    document.querySelectorAll('.blueprint-icon img').forEach(function(img) {
        img.onerror = function() {
            this.style.display = 'none';
            if (this.nextElementSibling) {
                this.nextElementSibling.style.display = 'flex';
            }
        };
    });

    if (document.querySelector('.blueprint-item')) {
        window.blueprintManager = new BlueprintManager();
    }

    // Performance monitoring
    if (window.performance && window.performance.mark) {
        window.performance.mark('blueprint-js-loaded');
    }
});
