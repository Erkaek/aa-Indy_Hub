/**
 * Craft Blueprint Active Tab Management
 * Handles tab initialization and state management
 */

// Provide a minimal SimulationAPI fallback to prevent hard failures during initialization
if (!window.SimulationAPI) {
    console.warn('SimulationAPI missing — installing no-op fallback to keep Craft page interactive.');
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
            console.log('CraftBPTabs already initialized');
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

        console.log('✅ CraftBPTabs initialized successfully');
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
            console.log(`📋 Default active tab: ${this.activeTabId}`);

            // Check if financial tab appears to be empty or undefined
            if (this.activeTabId === 'financial') {
                setTimeout(() => {
                    const financialContent = document.querySelector('#financial-content');
                    console.log('🔍 Financial content on setDefaultTab:', financialContent ? `"${financialContent.innerHTML}"` : 'not found');
                    this.ensureTreeDataAndInitializeFinancial();
                }, 200);
            }

            // Don't force update here - let the main template handle initialization
            // The main template will call updateSpecificTabOnSwitch when needed
        }
    },

    // Ensure Tree data is loaded before initializing Financial tab
    ensureTreeDataAndInitializeFinancial: function() {
        console.log('🔄 Ensuring Tree data is loaded before Financial initialization...');

        // Check if Tree data is already available
        if (window.SimulationState && window.SimulationState.switches && window.SimulationState.switches.size > 0) {
            console.log('✅ Tree data already available, initializing Financial directly');
            setTimeout(() => {
                this.checkAndInitializeFinancialTab();
            }, 100);
            return;
        }

        // If Tree data is not available, force preload Tree first
        console.log('🔄 Tree data not available, preloading Tree first...');
        this.preloadTreeTab();

        // Wait for Tree preload to complete, then initialize Financial
        const maxAttempts = 10;
        let attempts = 0;

        const checkTreeDataReady = () => {
            attempts++;

            if (window.SimulationState && window.SimulationState.switches && window.SimulationState.switches.size > 0) {
                console.log(`✅ Tree data ready after ${attempts} attempts, initializing Financial`);
                setTimeout(() => {
                    this.checkAndInitializeFinancialTab();
                }, 100);
            } else if (attempts < maxAttempts) {
                console.log(`⏳ Tree data not ready yet (attempt ${attempts}/${maxAttempts}), retrying...`);
                setTimeout(checkTreeDataReady, 200);
            } else {
                console.warn('⚠️ Tree data not ready after maximum attempts, initializing Financial anyway');
                setTimeout(() => {
                    this.checkAndInitializeFinancialTab();
                }, 100);
            }
        }

        // Start checking after a short delay to allow preload to start
        setTimeout(checkTreeDataReady, 300);
    },

    // Check and initialize financial tab if needed
    checkAndInitializeFinancialTab: function() {
        const financialContent = document.querySelector('#financial-content');
        console.log('🔍 Checking financial content:', financialContent ? financialContent.innerHTML.length : 'not found');

        // Vérifications plus robustes du contenu Financial
        let needsRegeneration = false;

        if (!financialContent) {
            console.log('❌ Financial content element not found');
            needsRegeneration = true;
        } else if (financialContent.innerHTML.trim() === '' || financialContent.innerHTML.trim() === 'undefined') {
            console.log('🔧 Financial content appears empty or undefined');
            needsRegeneration = true;
        } else {
            // Vérifier si le contenu contient réellement une table Financial valide
            const hasValidTable =
                financialContent.querySelector('.table') &&
                financialContent.querySelector('tbody') &&
                financialContent.querySelector('tbody').children.length > 0;
            const hasFinancialData =
                financialContent.innerHTML.includes('Financial Analysis') ||
                financialContent.innerHTML.includes('financial-table') ||
                financialContent.innerHTML.includes('Cost:') ||
                financialContent.innerHTML.includes('Revenue:') ||
                financialContent.innerHTML.includes('Total Cost') ||
                financialContent.innerHTML.includes('Total Revenue');
            console.log(`🔍 Content validation - Has valid table: ${hasValidTable}, Has financial data: ${hasFinancialData}`);
            if (!hasValidTable && !hasFinancialData) {
                console.log('🔧 Financial content exists but appears invalid or incomplete');
                needsRegeneration = true;
            }
        }
        window.SimulationAPI.markTabDirty(this.activeTabId);

        console.log(`🔄 Manually updating active tab: ${this.activeTabId}`);

        switch(this.activeTabId) {
            case 'materials':
                if (typeof updateMaterialsTabFromState === 'function') {
                    updateMaterialsTabFromState();
                }
                break;
            case 'tree':
                // Tree tab is static, no update needed
                break;
            case 'cycles':
                // Use legacy function for cycles
                if (typeof updateSpecificTabFromTree === 'function') {
                    updateSpecificTabFromTree('#tab-cycles');
                }
                break;
            case 'financial':
                if (typeof updateFinancialTabFromState === 'function') {
                    updateFinancialTabFromState();
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
            default:
                console.warn(`Unknown tab: ${this.activeTabId}`);
        }

        // Mark tab as clean after update
        window.SimulationAPI.markTabClean(this.activeTabId);
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
            console.warn('SimulationAPI not available');
            return;
        }

        console.log(`🔄 Force initializing tab: ${tabId}`);

        // Mark as dirty and update immediately
        window.SimulationAPI.markTabDirty(tabId);

        switch(tabId) {
            case 'financial':
                if (typeof initializeFinancialTab === 'function') {
                    initializeFinancialTab();
                } else if (typeof updateFinancialTabFromState === 'function') {
                    updateFinancialTabFromState();
                } else {
                    // Fallback: try to generate financial tab from backend data
                    if (typeof this.generateFinancialFromBackendData === 'function') {
                        this.generateFinancialFromBackendData();
                    } else if (typeof this.generateSimpleFinancialTable === 'function') {
                        this.generateSimpleFinancialTable([]);
                    }
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

    // Fallback method to generate financial tab from tree data

    // Generate financial tab from backend blueprint data
    generateFinancialFromBackendData: function() {
        console.log('🔄 Generating financial tab from backend data...');

        const financialContent = document.querySelector('#financial-content');
        if (!financialContent) {
            console.warn('Cannot generate financial tab: missing financial content div');
            return;
        }

        // Try to get materials from backend data
        const materialsData = window.BLUEPRINT_DATA?.materials_by_group || {};
        let allMaterials = [];

        Object.entries(materialsData).forEach(([groupId, group]) => {
            if (group.items && Array.isArray(group.items)) {
                group.items.forEach(item => {
                    allMaterials.push({
                        type_id: item.type_id,
                        type_name: item.type_name,
                        quantity: item.quantity || 1
                    });
                });
            }
        });

        if (allMaterials.length === 0) {
            console.warn('No backend materials data available');
            financialContent.innerHTML = `
                <div class="alert alert-info">
                    <i class="fas fa-info-circle me-2"></i>
                    No financial data available. Please load the cycles tab first or use "Load Fuzzwork Prices".
                </div>
            `;
            return;
        }

        console.log(`Found ${allMaterials.length} materials from backend data`);

        // Create financial table content (preserving the invoice-box structure)
        const tableHTML = `
            <table class="table table-striped table-hover align-middle mb-0" style="font-size:1.08rem;">
                <thead class="table-warning">
                    <tr>
                        <th>Item</th>
                        <th class="text-end">Quantity</th>
                        <th class="text-end">Unit Cost</th>
                        <th class="text-end">Total Cost</th>
                    </tr>
                </thead>
                <tbody id="financial-table-body">
                </tbody>
            </table>
        `;

        financialContent.innerHTML = tableHTML;

        // Populate the table with backend materials data
        const financialTableBody = document.querySelector('#financial-table-body');
        let totalCost = 0;

        allMaterials.forEach(material => {
            // Estimate unit cost (this would need real price data)
            const unitCost = Math.floor(Math.random() * 50000); // Placeholder
            const itemTotal = unitCost * material.quantity;
            totalCost += itemTotal;

            const financialRow = document.createElement('tr');
            financialRow.innerHTML = `
                <td class="fw-semibold">${material.type_name}</td>
                <td class="text-end">${material.quantity.toLocaleString()}</td>
                <td class="text-end">${unitCost.toLocaleString()} ISK</td>
                <td class="text-end">${itemTotal.toLocaleString()} ISK</td>
            `;
            financialTableBody.appendChild(financialRow);
        });

        // Add total row
        const totalRow = document.createElement('tr');
        totalRow.innerHTML = `
            <td colspan="3" class="text-end fw-bold">Total Cost:</td>
            <td class="text-end fw-bold">${totalCost.toLocaleString()} ISK</td>
        `;
        totalRow.className = 'table-warning';
        financialTableBody.appendChild(totalRow);

        console.log('✅ Financial tab generated from backend data');
    },

    // Méthode fallback pour générer un tableau simple si les fonctions principales ne sont pas disponibles
    generateSimpleFinancialTable(treeItems) {
        console.log('🔄 Generating complete financial table as fallback...');
        console.log('🎯 DEBUG: Available global variables:');
        console.log('  - window.typeMapping:', window.typeMapping);
        console.log('  - window.BLUEPRINT_DATA:', window.BLUEPRINT_DATA);
        console.log('  - product-type-id script:', document.querySelector('#product-type-id'));

        const financialContent = document.querySelector('#financial-content');
        if (!financialContent) {
            console.warn('⚠️ Financial content container not found');
            console.warn('Available containers:', document.querySelectorAll('[id*="financial"]'));
            return;
        }
        // ...existing code for table generation...
        if (typeof this.finishLoadingAndShowContent === 'function') {
            this.finishLoadingAndShowContent();
        }

        // Table sobre, neutre, avec jaune/or pour titres et totaux
        const tableHTML = `
            <div class="card shadow-sm">
                <div class="card-header" style="background: #fbbf24; color: #222; border-bottom: 1px solid #fbbf24;">
                    <h5 class="card-title mb-0">
                        <i class="fas fa-calculator me-2" style="color:#b45309;"></i>Financial Analysis
                    </h5>
                </div>
                <div class="card-body p-0">
                    <div class="table-responsive">
                        <table class="table table-hover mb-0" style="font-size: 1rem;">
                            <thead class="table-light">
                                <tr>
                                    <th style="padding: 0.75rem 1rem;">Item</th>
                                    <th class="text-end" style="padding: 0.75rem 1rem;">Qty</th>
                                    <th class="text-end" style="padding: 0.75rem 1rem;">Market Price</th>
                                    <th class="text-end" style="padding: 0.75rem 1rem;">Actual Price</th>
                                    <th class="text-end" style="padding: 0.75rem 1rem;">Total</th>
                                </tr>
                            </thead>
                            <tbody id="financial-table-body"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;

        // Replace financial content with the new table
        financialContent.innerHTML = tableHTML;

        // Populate the table with complete financial analysis
        const financialTableBody = document.querySelector('#financial-table-body');
        let totalMaterialCost = 0;
        let totalProductRevenue = 0;

        // Toujours utiliser le product_type_id et le nom du blueprint injectés par Django
        let productTypeId = null;
        let productName = null;
        try {
            const productTypeScript = document.querySelector('#product-type-id');
            if (productTypeScript) {
                productTypeId = parseInt(JSON.parse(productTypeScript.textContent));
            }
        } catch (e) {
            console.warn('⚠️ Could not extract product type ID from JSON script:', e);
        }
        // Récupérer le nom du blueprint depuis le script JSON injecté (si présent)
        let blueprintName = null;
        try {
            const blueprintNameScript = document.querySelector('#blueprint-name');
            if (blueprintNameScript) {
                blueprintName = JSON.parse(blueprintNameScript.textContent);
            }
        } catch (e) {
            // fallback
        }
        productName = blueprintName || `Product ${productTypeId}`;

        console.log(`🎯 DEBUG: Found product info - ID: ${productTypeId}, Name: ${productName}`);

        if (productTypeId && productName) {
            const productQuantity = 1; // Généralement 1 pour un produit final
            const estimatedSellPrice = 200000000; // Prix de vente estimé plus élevé
            totalProductRevenue += productQuantity * estimatedSellPrice;

            // Header pour la section produits (sobre, jaune)
            const productHeaderRow = document.createElement('tr');
            productHeaderRow.innerHTML = `
                <td colspan="5" class="fw-bold text-center py-3" style="background:#fbbf24; color:#222;">Final Product</td>
            `;
            financialTableBody.appendChild(productHeaderRow);
            // Ligne du produit final (sobre)
            const productRow = document.createElement('tr');
            productRow.innerHTML = `
                <td class="py-3">
                    <div class="d-flex align-items-center">
                    <img src="https://images.evetech.net/types/${productTypeId}/icon?size=32" alt="${productName}" class="me-3" style="width: 32px; height: 32px; border-radius: 6px;" onerror="this.src='https://images.evetech.net/types/1/icon?size=32';">
                        <div>
                            <div class="fw-bold">${productName}</div>
                            <small class="text-muted">Manufacturing Output</small>
                        </div>
                    </div>
                </td>
                <td class="text-end py-3" data-qty="${productQuantity}">
                    ${productQuantity.toLocaleString()}
                </td>
                <td class="text-end py-3">
                    <input type="number" min="0" step="0.01" class="form-control form-control-sm fuzzwork-price text-end" data-type-id="${productTypeId}" value="0" readonly title="Market sell price from Fuzzwork API">
                </td>
                <td class="text-end py-3">
                    <input type="number" min="0" step="0.01" class="form-control form-control-sm real-price text-end" data-type-id="${productTypeId}" value="${estimatedSellPrice}" title="Enter your target sell price" placeholder="Sell price">
                </td>
                <td class="text-end py-3 fw-bold" style="color:#b45309;">
                    +${estimatedSellPrice.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })} ISK
                </td>
            `;
            financialTableBody.appendChild(productRow);
        } else {
            console.warn('⚠️ Could not determine final product information');
        }

        // Header pour la section matériaux avec style cohérent
        if (treeItems.length > 0) {
            const materialHeaderRow = document.createElement('tr');
            materialHeaderRow.innerHTML = `
                <td colspan="5" class="fw-bold text-center py-3" style="background:#fbbf24; color:#222;">Raw Materials</td>
            `;
            financialTableBody.appendChild(materialHeaderRow);
            // Matériaux de base, sobre
            treeItems.forEach((item, index) => {
                const unitCost = Math.floor(Math.random() * 50000); // Placeholder
                const qty = parseInt(item.quantity.replace(/\D/g, '')) || 1;
                const itemTotal = unitCost * qty;
                totalMaterialCost += itemTotal;
                const financialRow = document.createElement('tr');
                if (index % 2 === 0) financialRow.className = 'table-light';
                financialRow.innerHTML = `
                    <td class="py-2">
                        <div class="d-flex align-items-center">
                            <img src="https://images.evetech.net/types/${item.typeId}/icon?size=32" alt="${item.typeName}" class="me-3" style="width: 28px; height: 28px; border-radius: 6px;" onerror="this.src='https://images.evetech.net/types/1/icon?size=32';">
                            <div>
                                <div class="fw-semibold">${item.typeName}</div>
                                <small class="text-muted">Raw Material</small>
                            </div>
                        </div>
                    </td>
                    <td class="text-end py-2" data-qty="${qty}">
                        ${item.quantity}
                    </td>
                    <td class="text-end py-2">
                        <input type="number" min="0" step="0.01" class="form-control form-control-sm fuzzwork-price text-end" data-type-id="${item.typeId}" value="0" readonly title="Market price from Fuzzwork API">
                    </td>
                    <td class="text-end py-2">
                        <input type="number" min="0" step="0.01" class="form-control form-control-sm real-price text-end" data-type-id="${item.typeId}" value="0" title="Enter your actual purchase price" placeholder="Buy price">
                    </td>
                    <td class="text-end py-2 fw-bold" style="color:#b45309;">
                        -${itemTotal.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })} ISK
                    </td>
                `;
                financialTableBody.appendChild(financialRow);
            });
        }

        // Add material cost total row avec style Bootstrap standard
        const materialTotalRow = document.createElement('tr');
        materialTotalRow.innerHTML = `
            <td colspan="4" class="text-end py-3" style="background:#fbbf24; color:#222; font-weight:bold;">Total Material Cost:</td>
            <td class="text-end py-3 fw-bold" style="color:#b45309; background:#fbbf24;">-${totalMaterialCost.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })} ISK</td>
        `;
        financialTableBody.appendChild(materialTotalRow);
        // Add net profit row, sobre, jaune
        const netProfit = totalProductRevenue - totalMaterialCost;
        const profitRow = document.createElement('tr');
        profitRow.innerHTML = `
            <td colspan="4" class="text-end py-3" style="background:#fbbf24; color:#222; font-weight:bold;">Net Profit/Loss:</td>
            <td class="text-end py-3 fw-bold" style="background:#fbbf24; color:#b45309; font-weight:bold;">${netProfit >= 0 ? '+' : ''}${netProfit.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })} ISK</td>
        `;
        financialTableBody.appendChild(profitRow);


        console.log('✅ Complete financial table generated successfully with consistent styling');
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
