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
    loadingFailsafeTimer: null,


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

        if (this.loadingFailsafeTimer) {
            window.clearTimeout(this.loadingFailsafeTimer);
        }
        this.loadingFailsafeTimer = window.setTimeout(() => {
            console.warn('[IndyHub] Craft workspace loading exceeded expected time; revealing workspace');
            this.finishLoadingAndShowContent();
        }, 4000);

        // Silently preload the Tree tab to initialize switches, then show the content
        setTimeout(() => {
            try {
                this.preloadTreeTab();
            } catch (error) {
                console.error('[IndyHub] Failed during initial craft workspace hydration', error);
            } finally {
                this.finishLoadingAndShowContent();
            }
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
        if (window.CraftBPLoading && typeof window.CraftBPLoading.show === 'function') {
            window.CraftBPLoading.show({
                title: __('Preparing production workspace'),
                message: __('We are synchronising materials, production tree and financial data.'),
            });
        }
    },

    finishLoadingAndShowContent: function() {
        if (this.loadingFailsafeTimer) {
            window.clearTimeout(this.loadingFailsafeTimer);
            this.loadingFailsafeTimer = null;
        }

        if (window.CraftBPLoading && typeof window.CraftBPLoading.hide === 'function') {
            window.CraftBPLoading.hide();
        }

        // Only reveal the legacy tab rail on pages that actually use it.
        // The redesigned craft page uses the modern tab system (#craftMainTabs)
        // and keeps #bpTabs hidden purely for JS compatibility.
        const usesModernTabs = !!document.getElementById('craftMainTabs');
        const nav = document.querySelector('#bpTabs');
        if (nav && !usesModernTabs) {
            nav.style.removeProperty('display');
            nav.classList.remove('d-none');
        }
    },

    // Set the default active tab
    setDefaultTab: function() {
        const activeTab = document.querySelector('#bpTabs .nav-link.active');
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
                if (typeof window.updateBuildTabFromState === 'function' && document.getElementById('build-pane')) {
                    window.updateBuildTabFromState();
                } else if (typeof updateSpecificTabFromTree === 'function') {
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

    // Called by SimulationState init when everything is ready
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
