/**
 * Craft Blueprint Active Tab Management
 * Handles tab initialization and state management
 */

const craftBPActiveTabDebugEnabled = (typeof window !== 'undefined' && window.INDY_HUB_DEBUG === true);
function craftBPActiveTabDebugLog() {
    if (!craftBPActiveTabDebugEnabled || typeof console === 'undefined') {
        return;
    }
    if (typeof console.debug === 'function') {
        console.debug.apply(console, arguments);
    }
}

// Provide a minimal SimulationAPI fallback to prevent hard failures during initialization
if (!window.SimulationAPI) {
    window.SimulationAPI = {
        getFinancialItems: () => [],
        getAllMaterials: () => [],
        getNeededMaterials: () => [],
        getPrice: () => ({ value: 0, source: 'default' }),
        getConfig: () => ({}),
        setConfig: () => {},
        getMaterialCount: () => 0,
        getTreeItemCount: () => 0,
        markTabDirty: () => {},
        markTabsDirty: () => {},
        markTabClean: () => {}
    };
}

if (!window.SimulationState) {
    window.SimulationState = {
        switches: new Map()
    };
}

window.typeMapping = window.typeMapping || {
    typeIdToNameMap: {},
    nameToTypeIdMap: {}
};

window.initializeDefaultSwitchStates = window.initializeDefaultSwitchStates || function() {
    if (!window.SimulationState?.switches) {
        return;
    }
    window.SimulationState.switches = new Map(window.SimulationState.switches);
};

// Global tab management object to avoid variable conflicts
window.CraftBPTabs = {
    initialized: false,
    activeTabId: null,


    // Initialize tab management
    init: function() {
        if (this.initialized) {
            return;
        }

        // Hide all main content except header and loading bar
        this.hideMainContentExceptHeaderAndLoading();

        this.initialized = true;
        this.bindTabEvents();
        this.setDefaultTab();

        // Pré-charger silencieusement l'onglet Tree pour initialiser les switches, puis afficher le contenu
        setTimeout(() => {
            this.preloadTreeTab();
            this.finishLoadingAndShowContent();
        }, 500);

    },

    // Bind events to tab elements
    bindTabEvents: function() {
        const self = this;
        const tabButtons = document.querySelectorAll('#bpTabs button[data-bs-toggle="tab"]');
        tabButtons.forEach(function(button) {
            button.addEventListener('shown.bs.tab', function(event) {
                const targetElement = event.target || event.currentTarget;
                const targetId = targetElement ? targetElement.getAttribute('data-bs-target') : null;
                if (!targetId) {
                    return;
                }
                self.activeTabId = targetId.replace('#tab-', '');
                if (window.SimulationAPI && typeof window.SimulationAPI.markTabDirty === 'function') {
                    window.SimulationAPI.markTabDirty(self.activeTabId);
                }
                self.updateActiveTab();
            });
        });
    },

    preloadTreeTab: function() {
        if (window.SimulationAPI && typeof window.SimulationAPI.refreshFromDom === 'function') {
            window.SimulationAPI.refreshFromDom();
            return;
        }

        const treeTab = document.getElementById('tab-tree');
        if (!treeTab) {
            return;
        }

        window.SimulationState = window.SimulationState || {};
        if (!(window.SimulationState.switches instanceof Map)) {
            window.SimulationState.switches = new Map();
        }

        treeTab.querySelectorAll('summary input.mat-switch').forEach(function(input) {
            const typeId = Number(input.getAttribute('data-type-id'));
            if (!typeId) {
                return;
            }
            let state = 'prod';
            if (input.disabled) {
                state = 'useless';
            } else if (!input.checked) {
                state = 'buy';
            }
            window.SimulationState.switches.set(typeId, {
                typeId: typeId,
                state: state
            });
        });
    },

    // Hide all main content except header and loading bar
    hideMainContentExceptHeaderAndLoading: function() {
        // Example: Hide all .tab-content except header and loading bar
        document.querySelectorAll('.tab-content, .main-content').forEach(el => {
            el.style.display = 'none';
        });
        // Show header and loading bar if present
        const header = document.querySelector('.blueprint-hero') || document.querySelector('.blueprint-header');
        if (header) header.style.display = '';
        const loading = document.getElementById('bpTabs-loading');
        if (loading) loading.style.display = '';
    },

    finishLoadingAndShowContent: function() {
        const loading = document.getElementById('bpTabs-loading');
        if (loading) {
            loading.style.display = 'none';
        }

        document.querySelectorAll('.tab-content').forEach(el => {
            el.style.removeProperty('display');
        });

        document.querySelectorAll('.main-content').forEach(el => {
            el.style.removeProperty('display');
        });

        const nav = document.querySelector('#bpTabs');
        if (nav) {
            nav.style.removeProperty('display');
            nav.classList.remove('d-none');
        }
    },

    // Set the default active tab
    setDefaultTab: function() {
        const activeTab = document.querySelector('.nav-link.active');
        if (activeTab) {
            this.activeTabId = activeTab.id.replace('-tab', '');
        }
    },

    // Force update all tabs
    updateAllTabs: function() {
        const tabs = ['materials', 'financial', 'needed', 'config'];
        if (window.SimulationAPI) {
            window.SimulationAPI.markTabsDirty(tabs);
        }

        // Update current tab
        if (window.CraftBP && typeof window.CraftBP.refreshTabs === 'function') {
            window.CraftBP.refreshTabs({ forceNeeded: true });
        } else {
            this.updateActiveTab();
        }
    },

    updateActiveTab: function() {
        if (!this.activeTabId) {
            return;
        }

        if (window.SimulationAPI && typeof window.SimulationAPI.refreshFromDom === 'function') {
            window.SimulationAPI.refreshFromDom();
        }

        switch (this.activeTabId) {
            case 'materials':
                if (typeof window.updateMaterialsTabFromState === 'function') {
                    window.updateMaterialsTabFromState();
                }
                break;
            case 'financial':
                if (typeof window.updateFinancialTabFromState === 'function') {
                    window.updateFinancialTabFromState();
                }
                break;
            case 'needed':
                if (typeof window.updateNeededTabFromState === 'function') {
                    window.updateNeededTabFromState(true);
                }
                break;
            case 'config':
                if (typeof window.updateConfigTabFromState === 'function') {
                    window.updateConfigTabFromState();
                }
                break;
            case 'cycles':
                if (typeof updateSpecificTabFromTree === 'function') {
                    updateSpecificTabFromTree('#tab-cycles');
                }
                break;
            default:
                this.forceInitializeTab(this.activeTabId);
        }

        if (window.SimulationAPI && typeof window.SimulationAPI.markTabClean === 'function') {
            window.SimulationAPI.markTabClean(this.activeTabId);
        }
    },

    // Force initialize a specific tab (useful for tabs that haven't been visited)
    forceInitializeTab: function(tabId) {
        if (!window.SimulationAPI) {
            craftBPActiveTabDebugLog('SimulationAPI not available');
            return;
        }

        // Mark as dirty and update immediately
        window.SimulationAPI.markTabDirty(tabId);

        switch(tabId) {
            case 'financial':
                if (typeof initializeFinancialTab === 'function') {
                    initializeFinancialTab();
                } else if (typeof updateFinancialTabFromState === 'function') {
                    updateFinancialTabFromState();
                }
                break;
            case 'materials':
                if (typeof updateMaterialsTabFromState === 'function') {
                    updateMaterialsTabFromState();
                }
                break;
            case 'needed':
                if (typeof updateNeededTabFromState === 'function') {
                    updateNeededTabFromState();
                }
                break;
            case 'config':
                if (typeof updateConfigTabFromState === 'function') {
                    updateConfigTabFromState();
                }
                break;
        }

        // Mark as clean after initialization
        window.SimulationAPI.markTabClean(tabId);
    },

    // Appelé par l'init de SimulationState quand tout est prêt
    onAllReady: function() {
        this.finishLoadingAndShowContent();
    }
};

// Auto-initialize when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    // Wait for SimulationAPI and CraftBPTabs to be ready, then call init
    const checkAndInit = () => {
        if (window.SimulationAPI && window.CraftBPTabs && typeof window.CraftBPTabs.init === 'function') {
            window.CraftBPTabs.init();
        } else {
            setTimeout(checkAndInit, 100);
        }
    };
    checkAndInit();
});
