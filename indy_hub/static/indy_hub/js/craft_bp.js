/**
 * Craft Blueprint JavaScript functionality
 * Handles financial calculations, price fetching, and UI interactions
 */

// Global configuration
const CRAFT_BP = {
    fuzzworkUrl: null, // Will be set from Django template
    productTypeId: null, // Will be set from Django template
};

const __ = (typeof window !== 'undefined' && typeof window.gettext === 'function') ? window.gettext.bind(window) : (msg => msg);

function updatePriceInputManualState(input, isManual) {
    if (!input) {
        return;
    }

    input.dataset.userModified = isManual ? 'true' : 'false';
    input.classList.toggle('is-manual', isManual);

    const cell = input.closest('td');
    if (cell) {
        cell.classList.toggle('has-manual', isManual);
    }

    const row = input.closest('tr');
    if (row) {
        const manualInRow = Array.from(row.querySelectorAll('.real-price, .sale-price-unit')).some(el => {
            if (el === input) {
                return isManual;
            }
            return el.dataset.userModified === 'true';
        });
        row.classList.toggle('has-manual', manualInRow);
        if (!manualInRow) {
            row.querySelectorAll('td.has-manual').forEach(td => td.classList.remove('has-manual'));
        }
    }
}

function escapeHtml(value) {
    if (value === null || value === undefined) {
        return '';
    }
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatInteger(value) {
    const num = Number(value) || 0;
    return num.toLocaleString();
}

function mapLikeToMap(source) {
    if (!source) {
        return new Map();
    }
    if (source instanceof Map) {
        return source;
    }
    if (Array.isArray(source)) {
        return new Map(source);
    }
    if (typeof source.entries === 'function') {
        try {
            return new Map(source.entries());
        } catch (error) {
            // Fall back to Object.entries below
        }
    }
    return new Map(Object.entries(source));
}

function getProductTypeIdValue() {
    const fromConfig = Number(CRAFT_BP.productTypeId);
    if (Number.isFinite(fromConfig) && fromConfig > 0) {
        return fromConfig;
    }
    const fromBlueprint = Number(window.BLUEPRINT_DATA?.product_type_id || window.BLUEPRINT_DATA?.productTypeId || 0);
    return Number.isFinite(fromBlueprint) ? fromBlueprint : 0;
}

function getSimulationPricesMap() {
    if (!window.SimulationAPI || typeof window.SimulationAPI.getState !== 'function') {
        return new Map();
    }
    const state = window.SimulationAPI.getState();
    if (!state || !state.prices) {
        return new Map();
    }
    return mapLikeToMap(state.prices);
}

function attachPriceInputListener(input) {
    if (!input || input.dataset.priceListenerAttached === 'true') {
        return;
    }

    input.addEventListener('input', () => {
        updatePriceInputManualState(input, true);

        if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function') {
            const typeId = input.getAttribute('data-type-id');
            if (typeId) {
                const priceType = input.classList.contains('sale-price-unit') ? 'sale' : 'real';
                window.SimulationAPI.setPrice(typeId, priceType, parseFloat(input.value) || 0);
            }
        }

        if (typeof recalcFinancials === 'function') {
            recalcFinancials();
        }
    });

    input.dataset.priceListenerAttached = 'true';
}

function refreshTabsAfterStateChange(options = {}) {
    if (typeof updateMaterialsTabFromState === 'function') {
        updateMaterialsTabFromState();
    }
    if (typeof updateFinancialTabFromState === 'function') {
        updateFinancialTabFromState();
    }
    if (typeof updateNeededTabFromState === 'function') {
        updateNeededTabFromState(Boolean(options.forceNeeded));
    }
}

/**
 * Public API for configuration
 */
window.CraftBP = {
    init: function(config) {
        CRAFT_BP.fuzzworkUrl = config.fuzzworkPriceUrl;
        CRAFT_BP.productTypeId = config.productTypeId;

        // Initialize financial calculations after configuration
        initializeFinancialCalculations();
    },

    loadFuzzworkPrices: function(typeIds) {
        return fetchAllPrices(typeIds);
    },

    refreshFinancials: function() {
        if (window.SimulationAPI && typeof window.SimulationAPI.refreshFromDom === 'function') {
            window.SimulationAPI.refreshFromDom();
        }
        recalcFinancials();
    },

    refreshTabs: function(options = {}) {
        refreshTabsAfterStateChange(options);
    },

    markPriceOverride: function(element, isManual = true) {
        updatePriceInputManualState(element, isManual);
    },

    pushStatus: function(message, variant = 'info') {
        const event = new CustomEvent('CraftBP:status', {
            detail: {
                message,
                variant
            }
        });
        document.dispatchEvent(event);
    }
};

/**
 * Initialize the application
 */
document.addEventListener('DOMContentLoaded', function() {
    initializeBlueprintIcons();
    initializeCollapseHandlers();
    initializeBuyCraftSwitches();
    restoreBuyCraftStateFromURL();
    // Financial calculations will be initialized via CraftBP.init()
});

/**
 * Initialize blueprint icon error handling
 */
function initializeBlueprintIcons() {
    document.querySelectorAll('.blueprint-icon img').forEach(function(img) {
        img.onerror = function() {
            this.style.display = 'none';
            if (this.nextElementSibling) {
                this.nextElementSibling.style.display = 'flex';
            }
        };
    });
}

/**
 * Initialize buy/craft switch handlers for material tree
 * DISABLED - Now handled by template event listeners to prevent page reloads
 */
function initializeBuyCraftSwitches() {
    const treeTab = document.getElementById('tab-tree');
    if (!treeTab) {
        console.warn('Tree tab not found; skipping buy/craft switch initialization');
        return;
    }

    if (treeTab.dataset.switchesInitialized === 'true') {
        refreshTreeSwitchHierarchy();
        return;
    }
    treeTab.dataset.switchesInitialized = 'true';

    window.refreshTreeSwitchHierarchy = refreshTreeSwitchHierarchy;

    const switches = Array.from(treeTab.querySelectorAll('input.mat-switch'));
    switches.forEach(sw => {
        if (!sw.dataset.userState) {
            if (sw.disabled && sw.closest('.mat-switch-group')?.querySelector('.mode-label')?.textContent?.trim().toLowerCase() === 'useless') {
                sw.dataset.userState = 'useless';
                sw.dataset.fixedMode = 'useless';
            } else {
                sw.dataset.userState = sw.checked ? 'prod' : 'buy';
            }
        }
        if (!sw.dataset.parentLockDepth) {
            sw.dataset.parentLockDepth = '0';
        }
        if (!sw.dataset.lockedByParent) {
            sw.dataset.lockedByParent = 'false';
        }
        if (!sw.dataset.initialUserDisabled) {
            sw.dataset.initialUserDisabled = sw.disabled ? 'true' : 'false';
        }
        updateSwitchLabel(sw);
    });

    refreshTreeSwitchHierarchy();

    treeTab.addEventListener('change', handleTreeSwitchChange, true);
}

function handleTreeSwitchChange(event) {
    const switchEl = event.target;
    if (!switchEl || !switchEl.classList || !switchEl.classList.contains('mat-switch')) {
        return;
    }

    if (switchEl.disabled || switchEl.dataset.fixedMode === 'useless') {
        event.preventDefault();
        return;
    }

    const newState = switchEl.checked ? 'prod' : 'buy';
    switchEl.dataset.userState = newState;
    updateSwitchLabel(switchEl);

    refreshTreeSwitchHierarchy();

    if (window.SimulationAPI && typeof window.SimulationAPI.refreshFromDom === 'function') {
        window.SimulationAPI.refreshFromDom();
    }

    refreshTabsAfterStateChange();
}

function refreshTreeSwitchHierarchy() {
    const treeTab = document.getElementById('tab-tree');
    if (!treeTab) {
        return;
    }

    const switches = Array.from(treeTab.querySelectorAll('input.mat-switch'));
    switches.forEach(applyParentLockState);
}

if (typeof window !== 'undefined' && !window.refreshTreeSwitchHierarchy) {
    window.refreshTreeSwitchHierarchy = refreshTreeSwitchHierarchy;
}

function applyParentLockState(switchEl) {
    const group = switchEl.closest('.mat-switch-group');
    const toggleContainer = group ? group.querySelector('.form-switch') : null;
    const isFixedUseless = switchEl.dataset.fixedMode === 'useless' || switchEl.dataset.userState === 'useless';
    if (isFixedUseless) {
        switchEl.disabled = true;
        switchEl.checked = false;
        switchEl.dataset.lockedByParent = 'false';
        switchEl.dataset.parentLockDepth = '0';
        if (toggleContainer) {
            toggleContainer.classList.add('d-none');
        }
        updateSwitchLabel(switchEl);
        return;
    }

    const ancestorBuyCount = countBuyAncestors(switchEl);
    if (ancestorBuyCount > 0) {
        switchEl.disabled = true;
        switchEl.checked = false;
        switchEl.dataset.lockedByParent = 'true';
        switchEl.dataset.parentLockDepth = String(ancestorBuyCount);
        if (toggleContainer) {
            toggleContainer.classList.add('d-none');
        }
    } else {
        const desiredState = switchEl.dataset.userState || (switchEl.checked ? 'prod' : 'buy');
        switchEl.disabled = false;
        switchEl.dataset.lockedByParent = 'false';
        switchEl.dataset.parentLockDepth = '0';
        switchEl.checked = desiredState !== 'buy';
        if (toggleContainer) {
            toggleContainer.classList.remove('d-none');
        }
    }

    updateSwitchLabel(switchEl);
}

function countBuyAncestors(switchEl) {
    let count = 0;
    let currentDetail = switchEl.closest('details');
    if (!currentDetail) {
        return 0;
    }

    currentDetail = currentDetail.parentElement ? currentDetail.parentElement.closest('details') : null;
    while (currentDetail) {
        const ancestorSwitch = currentDetail.querySelector('summary input.mat-switch');
        if (ancestorSwitch) {
            const ancestorMode = ancestorSwitch.dataset.fixedMode;
            const ancestorForced = ancestorSwitch.dataset.lockedByParent === 'true';
            const ancestorIsBuy = (!ancestorSwitch.checked) || ancestorMode === 'useless';
            if (ancestorIsBuy || ancestorForced) {
                count += 1;
            }
        }
        currentDetail = currentDetail.parentElement ? currentDetail.parentElement.closest('details') : null;
    }

    return count;
}

function updateDetailsCaret(detailsEl) {
    if (!detailsEl) {
        return;
    }
    const icon = detailsEl.querySelector(':scope > summary .summary-icon i');
    if (!icon) {
        return;
    }
    icon.classList.remove('fa-caret-right', 'fa-caret-down');
    icon.classList.add(detailsEl.open ? 'fa-caret-down' : 'fa-caret-right');
}

function refreshTreeSummaryIcons() {
    const treeTab = document.getElementById('tab-tree');
    if (!treeTab) {
        return;
    }
    treeTab.querySelectorAll('details').forEach(updateDetailsCaret);
}

function expandAllTreeNodes() {
    const treeTab = document.getElementById('tab-tree');
    if (!treeTab) {
        return;
    }
    treeTab.querySelectorAll('details').forEach(detailsEl => {
        if (!detailsEl.open) {
            detailsEl.open = true;
        }
        updateDetailsCaret(detailsEl);
    });
}

function collapseAllTreeNodes() {
    const treeTab = document.getElementById('tab-tree');
    if (!treeTab) {
        return;
    }
    treeTab.querySelectorAll('details').forEach(detailsEl => {
        if (detailsEl.open) {
            detailsEl.open = false;
        }
        updateDetailsCaret(detailsEl);
    });
}

function setTreeModeForAll(mode) {
    const treeTab = document.getElementById('tab-tree');
    if (!treeTab) {
        return;
    }

    const desiredState = mode === 'buy' ? 'buy' : 'prod';
    const switches = Array.from(treeTab.querySelectorAll('input.mat-switch'));

    switches.forEach(sw => {
        if (sw.dataset.fixedMode === 'useless') {
            return;
        }
        sw.dataset.userState = desiredState;
        sw.checked = desiredState !== 'buy';
    });

    refreshTreeSwitchHierarchy();
    if (window.SimulationAPI && typeof window.SimulationAPI.refreshFromDom === 'function') {
        window.SimulationAPI.refreshFromDom();
    }

    refreshTabsAfterStateChange();
}

/**
 * Collect current buy/craft decisions from the tree
 */
function getCurrentBuyCraftDecisions() {
    const buyDecisions = [];

    // Traverse the material tree and collect items marked for buying
    document.querySelectorAll('.mat-switch').forEach(function(switchEl) {
        const typeId = switchEl.getAttribute('data-type-id');
        if (!switchEl.checked) { // Unchecked means "buy" instead of "craft"
            buyDecisions.push(typeId);
        }
    });

    return buyDecisions;
}

/**
 * Update blueprint configurations based on buy/craft decisions
 * DISABLED - Now handled by template logic to prevent page reloads
 */
function updateBuyCraftDecisions() {
    // DISABLED - This function used to reload the page on every switch change
    // Now the template handles switch changes with immediate visual updates
    // and deferred URL/database updates when changing tabs
    console.log('updateBuyCraftDecisions: Disabled - handled by template logic');
}

/**
 * Restore buy/craft switch states from URL parameters
 */
function restoreBuyCraftStateFromURL() {
    const urlParams = new URLSearchParams(window.location.search);
    const buyList = urlParams.get('buy');

    if (buyList) {
        const buyDecisions = buyList.split(',').map(id => id.trim()).filter(id => id);
        console.log('Restoring buy decisions from URL:', buyDecisions);

        // Set all switches to default (checked = craft)
        document.querySelectorAll('.mat-switch').forEach(function(switchEl) {
            switchEl.checked = true; // Default to craft
            updateSwitchLabel(switchEl);
        });

        // Set switches for buy decisions to unchecked
        buyDecisions.forEach(function(typeId) {
            const switchEl = document.querySelector(`.mat-switch[data-type-id="${typeId}"]`);
            if (switchEl) {
                switchEl.checked = false; // Set to buy
                updateSwitchLabel(switchEl);
            }
        });

        // Trigger visual updates for tree hierarchy (children switches)
        // Use setTimeout to ensure all switches are set before updating visuals
        setTimeout(function() {
            if (typeof window.refreshTreeSwitchHierarchy === 'function') {
                window.refreshTreeSwitchHierarchy();
            }
            if (window.SimulationAPI && typeof window.SimulationAPI.refreshFromDom === 'function') {
                window.SimulationAPI.refreshFromDom();
            }
            refreshTabsAfterStateChange();
        }, 100);
    }
}

/**
 * Update the label next to a switch based on its state
 */
function updateSwitchLabel(switchEl) {
    const group = switchEl.closest('.mat-switch-group');
    if (!group) {
        return;
    }
    const label = group.querySelector('.mode-label');
    if (!label) {
        return;
    }

    label.className = 'mode-label badge px-2 py-1 fw-bold';

    const isLockedByParent = switchEl.dataset.lockedByParent === 'true' && switchEl.disabled;

    if (switchEl.dataset.fixedMode === 'useless' || switchEl.dataset.userState === 'useless') {
        label.textContent = 'Useless';
        label.classList.add('bg-secondary', 'text-white');
        label.removeAttribute('title');
        return;
    }

    if (isLockedByParent) {
        label.textContent = 'Parent Buy';
        label.classList.add('bg-secondary', 'text-white');
        label.setAttribute('title', 'Mode hérité : un parent est en Buy');
        return;
    }

    if (switchEl.checked) {
        label.textContent = 'Prod';
        label.classList.add('bg-success', 'text-white');
    } else {
        label.textContent = 'Buy';
        label.classList.add('bg-danger', 'text-white');
    }

    label.removeAttribute('title');
}

/**
 * Initialize collapse/expand handlers for sub-levels
 */
function initializeCollapseHandlers() {
    document.querySelectorAll('.toggle-subtree').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var targetId = btn.getAttribute('data-target');
            var subtree = document.getElementById(targetId);
            var icon = btn.querySelector('i');
            if (subtree) {
                var expanded = btn.getAttribute('aria-expanded') === 'true';
                subtree.classList.toggle('show', !expanded);
                btn.setAttribute('aria-expanded', !expanded);
                if (!expanded) {
                    icon.classList.remove('fa-chevron-right');
                    icon.classList.add('fa-chevron-down');
                } else {
                    icon.classList.remove('fa-chevron-down');
                    icon.classList.add('fa-chevron-right');
                }
            }
        });
    });

    const treeTab = document.getElementById('tab-tree');
    if (treeTab && !treeTab.dataset.summaryIconsInitialized) {
        treeTab.dataset.summaryIconsInitialized = 'true';
        treeTab.addEventListener('toggle', function(event) {
            if (event.target && event.target.tagName === 'DETAILS') {
                updateDetailsCaret(event.target);
            }
        });
        refreshTreeSummaryIcons();
    }

    const expandBtn = document.getElementById('expand-tree');
    if (expandBtn) {
        expandBtn.addEventListener('click', function() {
            expandAllTreeNodes();
        });
    }

    const collapseBtn = document.getElementById('collapse-tree');
    if (collapseBtn) {
        collapseBtn.addEventListener('click', function() {
            collapseAllTreeNodes();
        });
    }

    const setProdBtn = document.getElementById('set-tree-prod');
    if (setProdBtn) {
        setProdBtn.addEventListener('click', function() {
            setTreeModeForAll('prod');
        });
    }

    const setBuyBtn = document.getElementById('set-tree-buy');
    if (setBuyBtn) {
        setBuyBtn.addEventListener('click', function() {
            setTreeModeForAll('buy');
        });
    }
}

/**
 * Initialize financial calculations
 */
function initializeFinancialCalculations() {
    // On change recalc (use real-price and sale-price-unit)
    const recalcInputs = Array.from(document.querySelectorAll('.real-price, .sale-price-unit'));
    recalcInputs.forEach(inp => {
        attachPriceInputListener(inp);

        if (inp.dataset.userModified === 'true') {
            updatePriceInputManualState(inp, true);
        }
    });

    const recalcNowBtn = document.getElementById('recalcNowBtn');
    if (recalcNowBtn) {
        recalcNowBtn.addEventListener('click', () => {
            recalcNowBtn.classList.add('pulse');
            window.CraftBP.refreshFinancials();
            window.setTimeout(() => recalcNowBtn.classList.remove('pulse'), 600);
        });
    }

    // Batch fetch Fuzzwork prices for display (fuzzwork-price and sale-price-unit), only include valid positive type IDs
    const fetchInputs = Array.from(document.querySelectorAll('input.fuzzwork-price[data-type-id], input.sale-price-unit[data-type-id]'))
        .filter(inp => {
            const id = parseInt(inp.getAttribute('data-type-id'), 10);
            return id > 0;
        });
    let typeIds = fetchInputs.map(inp => inp.getAttribute('data-type-id')).filter(Boolean);

    // Include the final product type_id
    if (CRAFT_BP.productTypeId && !typeIds.includes(CRAFT_BP.productTypeId)) {
        typeIds.push(CRAFT_BP.productTypeId);
    }
    typeIds = [...new Set(typeIds)];

    fetchAllPrices(typeIds).then(prices => {
        populatePrices(fetchInputs, prices);
        recalcFinancials();
    });

    // Bind Load Fuzzwork Prices button
    const loadBtn = document.getElementById('loadFuzzworkBtn');
    if (loadBtn) {
        loadBtn.addEventListener('click', function() {
            fetchAllPrices(typeIds).then(prices => {
                populatePrices(fetchInputs, prices);
                recalcFinancials();
            });
        });
    }

    const resetBtn = document.getElementById('resetManualPricesBtn');
    if (resetBtn) {
        resetBtn.addEventListener('click', () => {
            const priceInputs = document.querySelectorAll('.real-price[data-type-id], .sale-price-unit[data-type-id]');
            priceInputs.forEach(input => {
                const tid = input.getAttribute('data-type-id');
                const fuzzInp = document.querySelector(`.fuzzwork-price[data-type-id="${tid}"]`);
                if (fuzzInp) {
                    input.value = fuzzInp.value || '0';
                } else {
                    input.value = '0';
                }
                updatePriceInputManualState(input, false);

                if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function' && tid) {
                    const priceType = input.classList.contains('sale-price-unit') ? 'sale' : 'real';
                    window.SimulationAPI.setPrice(tid, priceType, parseFloat(input.value) || 0);
                }
            });

            recalcFinancials();
            if (window.CraftBP && typeof window.CraftBP.pushStatus === 'function') {
                window.CraftBP.pushStatus(__('Manual overrides reset'), 'info');
            }
        });
    }

    // Initialize purchase list computation
    const computeButton = document.getElementById('compute-needed');
    if (computeButton) {
        computeButton.addEventListener('click', computeNeededPurchases);
    }

    // Initialize ME/TE configuration change handlers
    initializeMETEHandlers();
}

/**
 * Initialize ME/TE configuration change handlers
 */
function initializeMETEHandlers() {
    // Flag to track pending ME/TE changes
    window.craftBPFlags = window.craftBPFlags || {};
    window.craftBPFlags.hasPendingMETEChanges = false;

    function markMETEChanges() {
        if (!window.craftBPFlags.hasPendingMETEChanges) {
            window.craftBPFlags.hasPendingMETEChanges = true;
            console.log('ME/TE changes detected - will apply on next tab change');

            // Visual feedback: add a subtle indicator that changes are pending
            const configTab = document.querySelector('#config-tab');
            if (configTab && !configTab.querySelector('.pending-changes-indicator')) {
                const indicator = document.createElement('span');
                indicator.className = 'pending-changes-indicator badge bg-warning text-dark ms-2';
                indicator.textContent = '*';
                indicator.title = 'Changes pending - will apply when switching tabs';
                configTab.appendChild(indicator);
            }
        }
    }

    // Listen to ME/TE input changes in Config tab - just mark as changed, don't reload
    const meTeInputs = document.querySelectorAll('#tab-config input[name^="me_"], #tab-config input[name^="te_"]');
    console.log(`Found ${meTeInputs.length} ME/TE inputs to monitor for changes`);

    meTeInputs.forEach(input => {
        input.addEventListener('input', markMETEChanges);
        input.addEventListener('change', markMETEChanges);
        console.log(`Added listeners to ${input.name} input`);
    });

    // Also listen to main runs input change - just mark as changed
    const runsInput = document.getElementById('runsInput');
    if (runsInput) {
        runsInput.addEventListener('input', markMETEChanges);
        runsInput.addEventListener('change', markMETEChanges);
        console.log('Added listeners to runs input');
    }
}

/**
 * Apply pending ME/TE changes by reloading the page with new parameters
 * Called when user switches away from Config tab
 */
function applyPendingMETEChanges() {
    if (!window.craftBPFlags?.hasPendingMETEChanges) {
        return false; // No changes to apply
    }

    console.log('Applying pending ME/TE changes...');

    try {
        // Get current configuration values
        const config = getCurrentMETEConfig();
        const runs = parseInt(document.getElementById('runsInput')?.value || 1);

        // Get current blueprint type ID from the page
        const bpTypeId = window.BLUEPRINT_DATA?.type_id || getCurrentBlueprintTypeId();

        if (!bpTypeId) {
            console.error('Cannot determine blueprint type ID for recalculation');
            return false;
        }

        // Build URL with current ME/TE values
        const url = new URL(window.location.href);
        url.searchParams.set('runs', runs);

        // Set ME/TE for main blueprint
        if (config.mainME !== undefined) url.searchParams.set('me', config.mainME);
        if (config.mainTE !== undefined) url.searchParams.set('te', config.mainTE);

        // Keep the target tab (where user is switching to)
        const targetTab = window.craftBPFlags.switchingToTab || 'materials';
        url.searchParams.set('active_tab', targetTab);

        // Reset the flag
        window.craftBPFlags.hasPendingMETEChanges = false;

        // Navigate to new URL with updated parameters
        window.location.href = url.toString();
        return true; // Page will reload

    } catch (error) {
        console.error('Error applying ME/TE changes:', error);
        return false;
    }
}

/**
 * Get current ME/TE configuration from Config tab
 */
function getCurrentMETEConfig() {
    const config = {
        mainME: 0,
        mainTE: 0,
        blueprintConfigs: {}
    };

    // Get ME/TE inputs from config tab
    const meTeInputs = document.querySelectorAll('#tab-config input[name^="me_"], #tab-config input[name^="te_"]');

    meTeInputs.forEach(input => {
        const name = input.name;
        const value = parseInt(input.value) || 0;

        if (name.startsWith('me_')) {
            const typeId = name.replace('me_', '');
            if (!config.blueprintConfigs[typeId]) {
                config.blueprintConfigs[typeId] = {};
            }
            config.blueprintConfigs[typeId].me = Math.max(0, Math.min(value, 10));

            // If this is the main blueprint, store it separately
            const currentBpId = getCurrentBlueprintTypeId();
            if (typeId == currentBpId) {
                config.mainME = config.blueprintConfigs[typeId].me;
            }
        } else if (name.startsWith('te_')) {
            const typeId = name.replace('te_', '');
            if (!config.blueprintConfigs[typeId]) {
                config.blueprintConfigs[typeId] = {};
            }
            config.blueprintConfigs[typeId].te = Math.max(0, Math.min(value, 20));

            // If this is the main blueprint, store it separately
            const currentBpId = getCurrentBlueprintTypeId();
            if (typeId == currentBpId) {
                config.mainTE = config.blueprintConfigs[typeId].te;
            }
        }
    });

    return config;
}

/**
 * Get current blueprint type ID from the page
 */
function getCurrentBlueprintTypeId() {
    // First try to get from page data
    if (window.BLUEPRINT_DATA?.bp_type_id) {
        return window.BLUEPRINT_DATA.bp_type_id;
    }

    // Try to get from URL path
    const pathMatch = window.location.pathname.match(/\/craft\/(\d+)\//);
    if (pathMatch) {
        return pathMatch[1];
    }

    // Fallback: try to get from page data (legacy)
    return window.BLUEPRINT_DATA?.type_id;
}

/**
 * Show loading indicator during recalculation
 */
function showLoadingIndicator() {
    // Add loading overlay or spinner
    const indicator = document.createElement('div');
    indicator.id = 'craft-bp-loading';
    indicator.innerHTML = `
        <div class="d-flex justify-content-center align-items-center position-fixed top-0 start-0 w-100 h-100"
            style="background: rgba(0,0,0,0.7); z-index: 9999;">
            <div class="bg-white rounded p-4 text-center">
                <div class="spinner-border text-primary mb-3" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="mb-0">Recalculating with new ME/TE values...</p>
            </div>
        </div>
    `;
    document.body.appendChild(indicator);
}

/**
 * Hide loading indicator
 */
function hideLoadingIndicator() {
    const indicator = document.getElementById('craft-bp-loading');
    if (indicator) {
        indicator.remove();
    }
}

/**
 * Format a number as a price with ISK suffix
 * @param {number} num - The number to format
 * @returns {string} Formatted price string
 */
function formatPrice(num) {
    return num.toLocaleString('de-DE', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' ISK';
}

/**
 * Format a number with thousand separators
 * @param {number} num - The number to format
 * @returns {string} Formatted number string
 */
function formatNumber(num) {
    return num.toLocaleString('de-DE', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

/**
 * Recalculate financial totals
 */
function recalcFinancials() {
    let costTotal = 0;
    let revTotal = 0;

    document.querySelectorAll('#tab-financial tbody tr').forEach(tr => {
        const qtyCell = tr.querySelector('[data-qty]');
        if (!qtyCell) {
            return;
        }

        let rawQty = null;
        if (typeof qtyCell.getAttribute === 'function') {
            rawQty = qtyCell.getAttribute('data-qty');
        }
        if ((rawQty === null || rawQty === undefined || rawQty === '') && qtyCell.dataset) {
            rawQty = qtyCell.dataset.qty;
        }
        if (rawQty === null || rawQty === undefined || rawQty === '') {
            return;
        }

        const qty = Math.max(0, Math.ceil(parseFloat(rawQty))) || 0;
        const costInput = tr.querySelector('.real-price');
        const revInput = tr.querySelector('.sale-price-unit');

        if (costInput) {
            const cost = (parseFloat(costInput.value) || 0) * qty;
            const totalCostEl = tr.querySelector('.total-cost');
            if (totalCostEl) {
                totalCostEl.textContent = formatPrice(cost);
            }
            costTotal += cost;
        }

        if (revInput) {
            const rev = (parseFloat(revInput.value) || 0) * qty;
            const totalRevenueEl = tr.querySelector('.total-revenue');
            if (totalRevenueEl) {
                totalRevenueEl.textContent = formatPrice(rev);
            }
            revTotal += rev;
        }
    });

    const profit = revTotal - costTotal;
    const marginValue = costTotal > 0 ? (profit / costTotal) * 100 : 0;
    const marginText = marginValue.toFixed(1);

    const grandTotalCostEl = document.querySelector('.grand-total-cost');
    const grandTotalRevEl = document.querySelector('.grand-total-rev');
    const profitEl = document.querySelector('.profit');
    const profitPctEl = document.querySelector('.profit-pct');

    if (grandTotalCostEl) {
        grandTotalCostEl.textContent = formatPrice(costTotal);
    }

    if (grandTotalRevEl) {
        grandTotalRevEl.textContent = formatPrice(revTotal);
    }

    if (profitEl && profitEl.childNodes.length > 0) {
        profitEl.childNodes[0].textContent = formatPrice(profit) + ' ';
        if (profitPctEl) {
            profitPctEl.textContent = `(${marginText}%)`;
        }
    }

    const summaryCostEl = document.getElementById('financialSummaryCost');
    if (summaryCostEl) {
        summaryCostEl.textContent = formatPrice(costTotal);
    }

    const summaryRevenueEl = document.getElementById('financialSummaryRevenue');
    if (summaryRevenueEl) {
        summaryRevenueEl.textContent = formatPrice(revTotal);
    }

    const summaryProfitEl = document.getElementById('financialSummaryProfit');
    if (summaryProfitEl) {
        summaryProfitEl.textContent = formatPrice(profit);
        summaryProfitEl.classList.remove('text-success', 'text-danger');
        summaryProfitEl.classList.add(profit >= 0 ? 'text-success' : 'text-danger');
    }

    const summaryMarginEl = document.getElementById('financialSummaryMargin');
    if (summaryMarginEl) {
        summaryMarginEl.textContent = `${marginText}%`;
        summaryMarginEl.classList.remove('bg-success-subtle', 'text-success-emphasis', 'bg-danger-subtle', 'text-danger-emphasis');
        if (profit >= 0) {
            summaryMarginEl.classList.add('bg-success-subtle', 'text-success-emphasis');
        } else {
            summaryMarginEl.classList.add('bg-danger-subtle', 'text-danger-emphasis');
        }
    }

    const summaryUpdatedEl = document.getElementById('financialSummaryUpdated');
    const heroProfitEl = document.getElementById('heroProfit');
    const heroMarginEl = document.getElementById('heroMargin');
    const heroUpdatedEl = document.getElementById('heroUpdated');

    if (heroProfitEl) {
        heroProfitEl.textContent = formatPrice(profit);
        const profitCard = heroProfitEl.closest('.hero-kpi');
        if (profitCard) {
            profitCard.classList.toggle('negative', profit < 0);
            profitCard.classList.toggle('positive', profit >= 0);
        }
    }

    if (heroMarginEl) {
        heroMarginEl.textContent = `${marginText}%`;
        const marginCard = heroMarginEl.closest('.hero-kpi');
        if (marginCard) {
            marginCard.classList.toggle('negative', marginValue < 0);
            marginCard.classList.toggle('positive', marginValue >= 0);
        }
    }

    const now = new Date();
    const formattedTime = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    if (summaryUpdatedEl) {
        summaryUpdatedEl.textContent = formattedTime;
        summaryUpdatedEl.setAttribute('title', now.toLocaleString());
    }

    if (heroUpdatedEl) {
        heroUpdatedEl.textContent = formattedTime;
        heroUpdatedEl.setAttribute('title', now.toLocaleString());
    }
}

/**
 * Batch fetch prices from Fuzzwork API
 * @param {Array} typeIds - Array of EVE type IDs
 * @returns {Promise<Object>} Promise resolving to price data
 */
async function fetchAllPrices(typeIds) {
    const ids = Array.isArray(typeIds) ? typeIds : [];
    const numericIds = ids
        .map(id => String(id).trim())
        .filter(Boolean)
        .filter(id => /^\d+$/.test(id));
    const uniqueTypeIds = [...new Set(numericIds)];

    if (uniqueTypeIds.length === 0) {
        console.warn('fetchAllPrices called without valid type IDs');
        return {};
    }

    if (!CRAFT_BP.fuzzworkUrl) {
        const fallbackUrl = window.BLUEPRINT_DATA?.fuzzwork_price_url;
        if (fallbackUrl) {
            CRAFT_BP.fuzzworkUrl = fallbackUrl;
        }
    }

    const baseUrl = CRAFT_BP.fuzzworkUrl;
    if (!baseUrl) {
        console.error('No Fuzzwork URL configured; skipping price fetch.');
        return {};
    }

    const separator = baseUrl.includes('?') ? '&' : '?';
    const requestUrl = `${baseUrl}${separator}type_id=${uniqueTypeIds.join(',')}`;

    try {
        console.debug('[CraftBP] Loading Fuzzwork prices from', requestUrl);
        const resp = await fetch(requestUrl, { credentials: 'same-origin' });
        if (!resp.ok) {
            console.error('Fuzzwork price request failed:', resp.status, resp.statusText);
            try {
                const errorPayload = await resp.json();
                console.error('Fuzzwork response body:', errorPayload);
            } catch (jsonErr) {
                console.error('Unable to parse error response JSON', jsonErr);
            }
            return {};
        }
        const data = await resp.json();
        console.debug('[CraftBP] Fuzzwork prices received', data);
        return data;
    } catch (e) {
        console.error('Error fetching prices from Fuzzwork, URL:', requestUrl, e);
        return {};
    }
}

/**
 * Populate price inputs with fetched data
 * @param {Array} allInputs - Array of input elements
 * @param {Object} prices - Price data from API
 */
function populatePrices(allInputs, prices) {
    // Populate all material and sale price inputs
    allInputs.forEach(inp => {
        const tid = inp.getAttribute('data-type-id');
        const raw = prices[tid] ?? prices[String(parseInt(tid, 10))];
        let price = raw != null ? parseFloat(raw) : NaN;
        if (isNaN(price)) price = 0;

        inp.value = price.toFixed(2);

        if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function') {
            window.SimulationAPI.setPrice(tid, 'fuzzwork', price);
        }

        // Initialize real-price inputs with fetched market cost
        if (inp.classList.contains('fuzzwork-price')) {
            const realInp = document.querySelector(`input.real-price[data-type-id="${tid}"]`);
            if (realInp && realInp.dataset.userModified !== 'true') {
                realInp.value = price.toFixed(2);
                updatePriceInputManualState(realInp, false);
            }
        }

        if (price <= 0) {
            inp.classList.add('bg-warning', 'border-warning');
            inp.setAttribute('title', 'Price not available (Fuzzwork)');
        } else {
            inp.classList.remove('bg-warning', 'border-warning');
            inp.removeAttribute('title');
        }
    });

    // Override final product sale price using its true type_id
    if (CRAFT_BP.productTypeId) {
        const finalKey = String(CRAFT_BP.productTypeId);
        const rawFinal = prices[finalKey] ?? prices[String(parseInt(finalKey, 10))];
        let finalPrice = rawFinal != null ? parseFloat(rawFinal) : NaN;
        if (isNaN(finalPrice)) finalPrice = 0;

        const saleSelector = `.sale-price-unit[data-type-id="${finalKey}"]`;
        const saleInput = document.querySelector(saleSelector);
        if (saleInput) {
            if (saleInput.dataset.userModified !== 'true') {
                saleInput.value = finalPrice.toFixed(2);
                updatePriceInputManualState(saleInput, false);
            }
            if (finalPrice <= 0) {
                saleInput.classList.add('bg-warning', 'border-warning');
                saleInput.setAttribute('title', 'Price not available (Fuzzwork)');
            } else {
                saleInput.classList.remove('bg-warning', 'border-warning');
                saleInput.removeAttribute('title');
            }
        }

        if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function') {
            window.SimulationAPI.setPrice(CRAFT_BP.productTypeId, 'sale', finalPrice);
        }
    }
}

function buildFinancialRow(item, pricesMap) {
    const row = document.createElement('tr');
    row.setAttribute('data-type-id', String(item.typeId));

    row.innerHTML = `
        <td class="fw-semibold">
            <div class="d-flex align-items-center gap-3">
                <img src="https://images.evetech.net/types/${item.typeId}/icon?size=32" alt="${escapeHtml(item.typeName)}" class="rounded" style="width:28px;height:28px;background:#f3f4f6;" onerror="this.style.display='none';">
                <span class="badge bg-info-subtle text-info-emphasis px-2 py-1">${escapeHtml(item.typeName)}</span>
            </div>
        </td>
        <td class="text-end">
            <span class="badge bg-primary text-white" data-qty="${item.quantity}">${formatInteger(item.quantity)}</span>
        </td>
        <td class="text-end">
            <input type="number" min="0" step="0.01" class="form-control form-control-sm fuzzwork-price text-end bg-light" data-type-id="${item.typeId}" value="0" readonly>
        </td>
        <td class="text-end">
            <input type="number" min="0" step="0.01" class="form-control form-control-sm real-price text-end" data-type-id="${item.typeId}" value="0">
        </td>
        <td class="text-end total-cost">0</td>
    `;

    const fuzzInput = row.querySelector('.fuzzwork-price');
    const realInput = row.querySelector('.real-price');

    const priceEntry = pricesMap.get(item.typeId) || {};
    const fuzzPrice = Number(priceEntry.fuzzwork || 0);
    const realPrice = Number(priceEntry.real || 0);

    fuzzInput.value = fuzzPrice.toFixed(2);
    if (fuzzPrice <= 0) {
        fuzzInput.classList.add('bg-warning', 'border-warning');
        fuzzInput.setAttribute('title', 'Price not available (Fuzzwork)');
    } else {
        fuzzInput.classList.remove('bg-warning', 'border-warning');
        fuzzInput.removeAttribute('title');
    }

    if (realPrice > 0) {
        realInput.value = realPrice.toFixed(2);
        updatePriceInputManualState(realInput, true);
    } else {
        realInput.value = (fuzzPrice > 0 ? fuzzPrice : 0).toFixed(2);
        updatePriceInputManualState(realInput, false);
    }

    attachPriceInputListener(realInput);

    return { row, typeId: item.typeId, fuzzInput, realInput };
}

function updateFinancialRow(row, item) {
    row.setAttribute('data-type-id', String(item.typeId));

    const nameBadge = row.querySelector('.badge.bg-info-subtle');
    if (nameBadge) {
        nameBadge.textContent = item.typeName;
    }

    const img = row.querySelector('img');
    if (img) {
        img.alt = item.typeName;
        img.src = `https://images.evetech.net/types/${item.typeId}/icon?size=32`;
    }

    const qtyBadge = row.querySelector('[data-qty]');
    if (qtyBadge) {
        qtyBadge.dataset.qty = String(item.quantity);
        qtyBadge.textContent = formatInteger(item.quantity);
    }
}

function updateFinancialTabFromState() {
    const tableBody = document.getElementById('financialItemsBody');
    if (!tableBody || !window.SimulationAPI || typeof window.SimulationAPI.getFinancialItems !== 'function') {
        return;
    }

    const finalRow = document.getElementById('finalProductRow');
    const productTypeId = getProductTypeIdValue();
    const pricesMap = getSimulationPricesMap();

    const aggregated = new Map();
    const items = window.SimulationAPI.getFinancialItems() || [];

    items.forEach(item => {
        const typeId = Number(item.typeId ?? item.type_id);
        if (!typeId || (productTypeId && typeId === productTypeId)) {
            return;
        }
        const quantity = Math.ceil(Number(item.quantity ?? item.qty ?? 0));
        if (quantity <= 0) {
            return;
        }
        const existing = aggregated.get(typeId) || {
            typeId,
            typeName: item.typeName || item.type_name || '',
            quantity: 0
        };
        existing.quantity += quantity;
        aggregated.set(typeId, existing);
    });

    const sortedItems = Array.from(aggregated.values()).sort((a, b) => a.typeName.localeCompare(b.typeName, undefined, { sensitivity: 'base' }));

    const existingRows = new Map();
    tableBody.querySelectorAll('tr[data-type-id]').forEach(row => {
        if (finalRow && row === finalRow) {
            return;
        }
        const typeId = Number(row.getAttribute('data-type-id'));
        if (!typeId) {
            return;
        }
        existingRows.set(typeId, row);
    });

    const newRows = [];

    sortedItems.forEach(item => {
        let row = existingRows.get(item.typeId);
        if (row) {
            updateFinancialRow(row, item);
            tableBody.insertBefore(row, finalRow || null);
            existingRows.delete(item.typeId);
        } else {
            const buildResult = buildFinancialRow(item, pricesMap);
            row = buildResult.row;
            tableBody.insertBefore(row, finalRow || null);
            newRows.push(buildResult);
        }
    });

    existingRows.forEach(row => row.remove());

    if (finalRow && finalRow.parentElement !== tableBody) {
        tableBody.appendChild(finalRow);
    }

    if (newRows.length > 0) {
        const typeIds = newRows.map(entry => entry.typeId);
        fetchAllPrices(typeIds).then(prices => {
            newRows.forEach(({ typeId, fuzzInput, realInput }) => {
                const priceValue = parseFloat(prices[typeId] ?? prices[String(typeId)]) || 0;
                fuzzInput.value = priceValue.toFixed(2);
                if (priceValue <= 0) {
                    fuzzInput.classList.add('bg-warning', 'border-warning');
                    fuzzInput.setAttribute('title', 'Price not available (Fuzzwork)');
                } else {
                    fuzzInput.classList.remove('bg-warning', 'border-warning');
                    fuzzInput.removeAttribute('title');
                }
                if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function') {
                    window.SimulationAPI.setPrice(typeId, 'fuzzwork', priceValue);
                }
                if (realInput.dataset.userModified !== 'true') {
                    realInput.value = priceValue.toFixed(2);
                    updatePriceInputManualState(realInput, false);
                }
            });
            if (typeof recalcFinancials === 'function') {
                recalcFinancials();
            }
        });
    } else if (typeof recalcFinancials === 'function') {
        recalcFinancials();
    }
}

function updateMaterialsTabFromState() {
    const container = document.getElementById('materialsGroupsContainer');
    if (!container || !window.SimulationAPI || typeof window.SimulationAPI.getFinancialItems !== 'function') {
        return;
    }

    const emptyState = document.getElementById('materialsEmptyState');
    const productTypeId = getProductTypeIdValue();
    const fallbackGroupName = __('Other');
    const aggregated = new Map();
    const items = window.SimulationAPI.getFinancialItems() || [];

    items.forEach(item => {
        const typeId = Number(item.typeId ?? item.type_id);
        if (!typeId || (productTypeId && typeId === productTypeId)) {
            return;
        }
        const quantity = Math.ceil(Number(item.quantity ?? item.qty ?? 0));
        if (quantity <= 0) {
            return;
        }
        const existing = aggregated.get(typeId) || {
            typeId,
            typeName: item.typeName || item.type_name || '',
            quantity: 0,
            marketGroup: item.marketGroup || item.market_group || ''
        };
        existing.quantity += quantity;
        aggregated.set(typeId, existing);
    });

    const groups = new Map();
    aggregated.forEach(entry => {
        const groupName = entry.marketGroup ? entry.marketGroup : fallbackGroupName;
        if (!groups.has(groupName)) {
            groups.set(groupName, []);
        }
        groups.get(groupName).push(entry);
    });

    if (groups.size === 0) {
        container.innerHTML = '';
        if (emptyState) {
            emptyState.style.display = '';
        }
        return;
    }

    const sortedGroups = Array.from(groups.entries()).sort((a, b) => a[0].localeCompare(b[0], undefined, { sensitivity: 'base' }));
    container.innerHTML = '';

    sortedGroups.forEach(([groupName, groupItems]) => {
        groupItems.sort((a, b) => a.typeName.localeCompare(b.typeName, undefined, { sensitivity: 'base' }));
        const rowsHtml = groupItems.map(item => `
            <tr data-type-id="${item.typeId}">
                <td class="fw-semibold">
                    <div class="d-flex align-items-center gap-3">
                        <img src="https://images.evetech.net/types/${item.typeId}/icon?size=32" alt="${escapeHtml(item.typeName)}" class="rounded" style="width:30px;height:30px;background:#f3f4f6;" onerror="this.style.display='none';">
                        <span class="badge bg-info-subtle text-info-emphasis px-2 py-1">${escapeHtml(item.typeName)}</span>
                    </div>
                </td>
                <td class="text-end">
                    <span class="badge bg-primary text-white" data-qty="${item.quantity}">${formatInteger(item.quantity)}</span>
                </td>
            </tr>
        `).join('');

        const card = document.createElement('div');
        card.className = 'card shadow-sm mb-4';
        card.innerHTML = `
            <div class="card-header d-flex align-items-center justify-content-between bg-body-secondary">
                <span class="fw-semibold">
                    <i class="fas fa-layer-group text-primary me-2"></i>${escapeHtml(groupName)}
                </span>
                <span class="badge bg-primary-subtle text-primary fw-semibold">${groupItems.length}</span>
            </div>
            <div class="card-body p-0">
                <div class="table-responsive">
                    <table class="table table-hover table-sm align-middle mb-0">
                        <thead class="table-light">
                            <tr>
                                <th>${__('Material')}</th>
                                <th class="text-end">${__('Quantity')}</th>
                            </tr>
                        </thead>
                        <tbody>${rowsHtml}</tbody>
                    </table>
                </div>
            </div>
        `;
        container.appendChild(card);
    });

    if (emptyState) {
        emptyState.style.display = 'none';
    }
}

function updateNeededTabFromState(force = false) {
    const neededTab = document.getElementById('tab-needed');
    if (!neededTab) {
        return;
    }
    if (!force && !neededTab.classList.contains('active')) {
        return;
    }
    if (typeof computeNeededPurchases === 'function') {
        computeNeededPurchases();
    }
}

/**
 * Compute needed purchase list based on user selections
 */
function computeNeededPurchases() {
    const purchases = {};

    function traverse(summary) {
        const detail = summary.parentElement;
        const childDetails = detail.querySelectorAll(':scope > details');

        if (childDetails.length > 0) {
            // Non-leaf
            const cb = summary.querySelector('.mat-checkbox');
            if (cb && !cb.checked) {
                // User chooses to buy this intermediate product
                const tid = summary.dataset.typeId;
                const name = summary.dataset.typeName;
                const qty = parseInt(summary.dataset.qty) || 0;
                purchases[tid] = purchases[tid] || {name: name, qty: 0};
                purchases[tid].qty += Math.ceil(qty);
            } else {
                // Produce: recurse into children
                childDetails.forEach(child => {
                    const childSum = child.querySelector('summary');
                    if (childSum) traverse(childSum);
                });
            }
        } else {
            // Leaf: always purchase raw material
            const tid = summary.dataset.typeId;
            const name = summary.dataset.typeName;
            const qty = parseInt(summary.dataset.qty) || 0;
            purchases[tid] = purchases[tid] || {name: name, qty: 0};
            purchases[tid].qty += qty;
        }
    }

    // Start from roots
    document.querySelectorAll('#tab-tree details > summary').forEach(rootSum => {
        traverse(rootSum);
    });

    // Render purchases
    const tbody = document.querySelector('#needed-table tbody');
    tbody.innerHTML = '';

    // Fetch prices for purchase items
    const pIds = Object.keys(purchases);
    fetchAllPrices(pIds).then(prices => {
        let totalCost = 0;
        Object.entries(purchases).forEach(([tid, item]) => {
            const unit = parseFloat(prices[tid]) || 0;
            const line = unit * item.qty;
            totalCost += line;

            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${item.name}</td>
                <td class="text-end">${formatNumber(item.qty)}</td>
                <td class="text-end">${formatPrice(unit)}</td>
                <td class="text-end">${formatPrice(line)}</td>
            `;
            tbody.appendChild(row);
        });
        document.querySelector('.purchase-total').textContent = formatPrice(totalCost);
    });
}

/**
 * Set configuration values from Django template
 * @param {string} fuzzworkUrl - URL for Fuzzwork API
 * @param {string} productTypeId - Product type ID
 */
function setCraftBPConfig(fuzzworkUrl, productTypeId) {
    CRAFT_BP.fuzzworkUrl = fuzzworkUrl;
    CRAFT_BP.productTypeId = productTypeId;
}

window.updateMaterialsTabFromState = updateMaterialsTabFromState;
window.updateFinancialTabFromState = updateFinancialTabFromState;
window.updateNeededTabFromState = updateNeededTabFromState;
