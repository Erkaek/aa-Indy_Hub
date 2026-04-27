/**
 * Craft Blueprint JavaScript functionality
 * Handles financial calculations, price fetching, and UI interactions
 */

// Global configuration shared across the craft page helpers.
const CRAFT_BP = {
    fuzzworkUrl: null,
    productTypeId: null,
    adjustedPriceCache: new Map(),
    adjustedPriceRequests: new Map(),
};

const __ = (typeof window !== 'undefined' && typeof window.gettext === 'function')
    ? window.gettext.bind(window)
    : (message => message);

function craftBPIsDebugEnabled() {
    return (typeof window !== 'undefined' && window.INDY_HUB_DEBUG === true);
}

function craftBPDebugLog() {
    if (!craftBPIsDebugEnabled() || typeof console === 'undefined') {
        return;
    }

    if (typeof console.log === 'function') {
        console.log.apply(console, arguments);
        return;
    }

    if (typeof console.info === 'function') {
        console.info.apply(console, arguments);
    }
}

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
    if (!row) {
        return;
    }

    const hasManualInput = Array.from(
        row.querySelectorAll('.real-price, .sale-price-unit')
    ).some((element) => element.dataset.userModified === 'true');

    row.classList.toggle('has-manual', hasManualInput);
    if (!hasManualInput) {
        row.querySelectorAll('td.has-manual').forEach((element) => {
            element.classList.remove('has-manual');
        });
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

function getBlueprintRecipeMap() {
    const recipeMap = window.BLUEPRINT_DATA?.recipe_map || window.BLUEPRINT_DATA?.recipeMap;
    if (!recipeMap || typeof recipeMap !== 'object') {
        return {};
    }
    return recipeMap;
}

function getRecipeEntryForType(typeId) {
    const numericTypeId = Number(typeId) || 0;
    if (!(numericTypeId > 0)) {
        return null;
    }

    const recipeMap = getBlueprintRecipeMap();
    const recipe = recipeMap[String(numericTypeId)] ?? recipeMap[numericTypeId] ?? null;
    return recipe && typeof recipe === 'object' ? recipe : null;
}

function getRecipeInputsPerCycle(recipe, preferMe0 = false) {
    if (!recipe || typeof recipe !== 'object') {
        return [];
    }

    const preferred = preferMe0
        ? (recipe.inputs_per_cycle_me0 ?? recipe.inputsPerCycleMe0)
        : (recipe.inputs_per_cycle ?? recipe.inputsPerCycle);
    const fallback = preferMe0
        ? (recipe.inputs_per_cycle ?? recipe.inputsPerCycle)
        : (recipe.inputs_per_cycle_me0 ?? recipe.inputsPerCycleMe0);
    const inputs = Array.isArray(preferred) ? preferred : (Array.isArray(fallback) ? fallback : []);
    return inputs;
}

function getCachedAdjustedPrice(typeId) {
    const numericTypeId = Number(typeId) || 0;
    if (!(numericTypeId > 0)) {
        return null;
    }
    return CRAFT_BP.adjustedPriceCache.get(numericTypeId) || null;
}

function getAdjustedPriceValue(typeId) {
    const priceRecord = getCachedAdjustedPrice(typeId);
    const adjustedPrice = Number(priceRecord?.adjusted_price ?? priceRecord?.adjustedPrice ?? 0);
    return adjustedPrice > 0 ? adjustedPrice : 0;
}

function computeRecipeEstimatedItemValue(typeId) {
    const recipe = getRecipeEntryForType(typeId);
    const inputs = getRecipeInputsPerCycle(recipe, true);
    if (!Array.isArray(inputs) || inputs.length === 0) {
        return { value: 0, hasAllAdjustedPrices: false, componentCount: 0, pricedComponentCount: 0 };
    }

    let totalValue = 0;
    let componentCount = 0;
    let pricedComponentCount = 0;
    inputs.forEach((input) => {
        const inputTypeId = Number(input?.type_id ?? input?.typeId ?? 0);
        const quantity = Number(input?.quantity ?? input?.qty ?? 0);
        if (!(inputTypeId > 0) || !(quantity > 0)) {
            return;
        }
        componentCount += 1;
        const adjustedPrice = getAdjustedPriceValue(inputTypeId);
        if (!(adjustedPrice > 0)) {
            return;
        }
        pricedComponentCount += 1;
        totalValue += adjustedPrice * quantity;
    });

    return {
        value: totalValue,
        hasAllAdjustedPrices: componentCount > 0 && pricedComponentCount === componentCount,
        componentCount,
        pricedComponentCount,
    };
}

function getStructureSummaryAdjustedPriceTypeIds() {
    if (!window.SimulationAPI || typeof window.SimulationAPI.getProductionCycles !== 'function') {
        return [];
    }

    const typeIds = new Set();
    const productionCycles = window.SimulationAPI.getProductionCycles() || [];
    productionCycles.forEach((entry) => {
        const typeId = Number(entry.typeId || entry.type_id || 0) || 0;
        if (!(typeId > 0)) {
            return;
        }
        const recipe = getRecipeEntryForType(typeId);
        getRecipeInputsPerCycle(recipe, true).forEach((input) => {
            const inputTypeId = Number(input?.type_id ?? input?.typeId ?? 0);
            if (inputTypeId > 0) {
                typeIds.add(inputTypeId);
            }
        });
    });
    return Array.from(typeIds.values());
}

function buildPriceRequestUrl(typeIds, options = {}) {
    const ids = Array.isArray(typeIds) ? typeIds : [];
    const numericIds = ids
        .map(id => String(id).trim())
        .filter(Boolean)
        .filter(id => /^\d+$/.test(id));
    const uniqueTypeIds = [...new Set(numericIds)];

    if (uniqueTypeIds.length === 0) {
        return null;
    }

    if (!CRAFT_BP.fuzzworkUrl) {
        const fallbackUrl = window.BLUEPRINT_DATA?.fuzzwork_price_url;
        if (fallbackUrl) {
            CRAFT_BP.fuzzworkUrl = fallbackUrl;
        }
    }

    const baseUrl = CRAFT_BP.fuzzworkUrl;
    if (!baseUrl) {
        return null;
    }

    const separator = baseUrl.includes('?') ? '&' : '?';
    const params = [`type_id=${uniqueTypeIds.join(',')}`];
    if (options.full === true) {
        params.push('full=1');
    }
    if (options.priceSource) {
        params.push(`price_source=${encodeURIComponent(String(options.priceSource))}`);
    }
    return `${baseUrl}${separator}${params.join('&')}`;
}

function ensureAdjustedPricesLoaded(typeIds) {
    const ids = Array.isArray(typeIds) ? typeIds : [];
    const missingIds = ids
        .map(id => Number(id) || 0)
        .filter(id => id > 0)
        .filter(id => !CRAFT_BP.adjustedPriceCache.has(id));

    if (missingIds.length === 0) {
        return Promise.resolve(false);
    }

    const requestKey = missingIds.slice().sort((a, b) => a - b).join(',');
    if (CRAFT_BP.adjustedPriceRequests.has(requestKey)) {
        return CRAFT_BP.adjustedPriceRequests.get(requestKey);
    }

    const requestUrl = buildPriceRequestUrl(missingIds, { full: true, priceSource: 'adjusted' });
    if (!requestUrl) {
        return Promise.resolve(false);
    }

    const requestPromise = fetch(requestUrl, { credentials: 'same-origin' })
        .then((resp) => {
            if (!resp.ok) {
                throw new Error(`Adjusted price request failed: ${resp.status}`);
            }
            return resp.json();
        })
        .then((data) => {
            let loadedAny = false;
            if (data && typeof data === 'object') {
                Object.entries(data).forEach(([typeIdStr, record]) => {
                    const typeId = Number(typeIdStr) || 0;
                    if (!(typeId > 0) || !record || typeof record !== 'object') {
                        return;
                    }
                    const adjustedPrice = Number(record.adjusted_price ?? record.adjustedPrice ?? 0);
                    const averagePrice = Number(record.average_price ?? record.averagePrice ?? 0);
                    CRAFT_BP.adjustedPriceCache.set(typeId, {
                        adjusted_price: adjustedPrice,
                        average_price: averagePrice,
                    });
                    loadedAny = loadedAny || adjustedPrice > 0 || averagePrice > 0;
                });
            }
            return loadedAny;
        })
        .catch((error) => {
            console.error('Error fetching adjusted prices', error);
            return false;
        })
        .finally(() => {
            CRAFT_BP.adjustedPriceRequests.delete(requestKey);
        });

    CRAFT_BP.adjustedPriceRequests.set(requestKey, requestPromise);
    return requestPromise;
}

function ensureStructureSummaryAdjustedPrices() {
    const typeIds = getStructureSummaryAdjustedPriceTypeIds();
    if (typeIds.length === 0) {
        return;
    }
    ensureAdjustedPricesLoaded(typeIds).then((loadedAny) => {
        if (loadedAny && typeof recalcFinancials === 'function') {
            recalcFinancials();
        }
    });
}

function getProductTypeIdValue() {
    const fromConfig = Number(CRAFT_BP.productTypeId);
    if (Number.isFinite(fromConfig) && fromConfig > 0) {
        return fromConfig;
    }
    const fromBlueprint = Number(window.BLUEPRINT_DATA?.product_type_id || window.BLUEPRINT_DATA?.productTypeId || 0);
    return Number.isFinite(fromBlueprint) ? fromBlueprint : 0;
}

function getFinalOutputEntries(payload = window.BLUEPRINT_DATA) {
    const source = payload || {};
    const rawOutputs = Array.isArray(source.final_outputs)
        ? source.final_outputs
        : (Array.isArray(source.finalOutputs) ? source.finalOutputs : []);
    const normalizedOutputs = rawOutputs
        .map((entry) => {
            const typeId = Number(entry?.type_id || entry?.typeId || 0) || 0;
            if (!(typeId > 0)) {
                return null;
            }
            return {
                type_id: typeId,
                type_name: entry?.type_name || entry?.typeName || '',
                quantity: Math.max(0, Math.ceil(Number(entry?.quantity || entry?.qty || 0))) || 0,
                produced_per_cycle: Math.max(0, Math.ceil(Number(entry?.produced_per_cycle || entry?.producedPerCycle || 0))) || 0,
            };
        })
        .filter(Boolean);
    if (normalizedOutputs.length > 0) {
        return normalizedOutputs;
    }

    const productTypeId = Number(source.product_type_id || source.productTypeId || 0) || 0;
    if (!(productTypeId > 0)) {
        return [];
    }
    return [{
        type_id: productTypeId,
        type_name: source.name || '',
        quantity: Math.max(0, Math.ceil(Number(source.final_product_qty || source.finalProductQty || 0))) || 0,
        produced_per_cycle: Math.max(0, Math.ceil(Number(source.product_output_per_cycle || source.productOutputPerCycle || 0))) || 0,
    }];
}

function getFinalOutputTypeIds(payload = window.BLUEPRINT_DATA) {
    return new Set(
        getFinalOutputEntries(payload)
            .map((entry) => Number(entry?.type_id || entry?.typeId || 0) || 0)
            .filter((typeId) => typeId > 0)
    );
}

function getFinalOutputRows() {
    return Array.from(document.querySelectorAll('#financialItemsBody tr[data-final-output="true"]'));
}

function buildFinalOutputRowMarkup(output, isFirstRow) {
    const typeId = Number(output?.type_id || output?.typeId || 0) || 0;
    const typeName = output?.type_name || output?.typeName || __('Final product');
    const quantity = Math.max(0, Math.ceil(Number(output?.quantity || output?.qty || 0))) || 0;

    return `
        <tr${isFirstRow ? ' id="finalProductRow"' : ''} class="table-success fw-semibold" data-type-id="${typeId}" data-final-output="true">
            <td data-manual-label="${escapeHtml(__('Manual'))}">
                <div class="d-flex align-items-center gap-2 craft-planner-item-flex">
                    <img src="https://images.evetech.net/types/${typeId}/icon?size=32" alt="${escapeHtml(typeName)}" loading="lazy" decoding="async" fetchpriority="low" class="rounded eve-type-icon eve-type-icon--28" onerror="this.style.display='none';">
                    <span class="craft-planner-item-name-wrap">
                        <span class="text-xs fw-bold craft-planner-item-name">${escapeHtml(typeName)}</span>
                    </span>
                </div>
            </td>
            <td class="text-end text-xs" data-qty="${quantity}">
                <span class="badge bg-success text-white">${formatInteger(quantity)}</span>
            </td>
            <td class="text-end">
                <input type="number" min="0" step="0.01" class="form-control form-control-sm fuzzwork-price text-end bg-light text-xs" data-type-id="${typeId}" value="0" readonly>
            </td>
            <td class="text-end">
                <input type="number" min="0" step="0.01" class="form-control form-control-sm sale-price-unit text-end text-xs" data-type-id="${typeId}" value="0">
            </td>
            <td class="text-end text-xs total-revenue fw-semibold">0</td>
            <td class="text-end text-xs">-</td>
        </tr>
    `;
}

function computeFinalOutputRevenue(api) {
    let revenueTotal = 0;
    getFinalOutputRows().forEach((row) => {
        const typeId = Number(row.getAttribute('data-type-id') || 0) || 0;
        const finalQtyEl = row.querySelector('[data-qty]');
        const rawFinalQty = finalQtyEl ? (finalQtyEl.getAttribute('data-qty') || finalQtyEl.dataset?.qty) : null;
        const finalQty = Math.max(0, Math.ceil(Number(rawFinalQty))) || 0;
        if (!(typeId > 0) || !(finalQty > 0)) {
            return;
        }
        const unit = api.getPrice(typeId, 'sale');
        const unitPrice = unit && typeof unit.value === 'number' ? unit.value : 0;
        if (unitPrice > 0) {
            revenueTotal += unitPrice * finalQty;
        }
    });
    return revenueTotal;
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

    if (document.body && document.body.dataset.financialPriceDelegationAttached === 'true') {
        input.dataset.priceListenerAttached = 'true';
        return;
    }

    input.addEventListener('input', () => {
        handleFinancialPriceInputChange(input);
    });

    input.dataset.priceListenerAttached = 'true';
}

function handleFinancialPriceInputChange(input) {
    if (!input) {
        return;
    }

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

    persistCraftPageSessionState();
}

function initializeDelegatedFinancialPriceInputs() {
    if (!document.body || document.body.dataset.financialPriceDelegationAttached === 'true') {
        return;
    }

    document.addEventListener('input', (event) => {
        const target = event.target;
        if (!target || !(target.classList?.contains('real-price') || target.classList?.contains('sale-price-unit'))) {
            return;
        }
        handleFinancialPriceInputChange(target);
    }, true);

    document.body.dataset.financialPriceDelegationAttached = 'true';
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
    if (typeof updateStockManagementTabFromState === 'function') {
        updateStockManagementTabFromState(Boolean(options.forceNeeded));
    }
    if (typeof updateBuildTabFromState === 'function') {
        updateBuildTabFromState();
    }
    if (typeof renderStructurePlanner === 'function') {
        renderStructurePlanner();
    }
    if (typeof renderDecisionStrategyPanel === 'function') {
        renderDecisionStrategyPanel({ ensurePrices: false });
    }
    if (typeof updateTreeModeBadges === 'function') {
        updateTreeModeBadges();
    }
    if (typeof window.validateBlueprintRuns === 'function') {
        window.validateBlueprintRuns();
    }
    persistCraftPageSessionState();
}

function updatePendingWorkspaceRefreshNotice() {
    const notice = document.getElementById('craftPendingRefreshNotice');
    if (!notice) {
        return;
    }

    const hasPendingRefresh = Boolean(
        window.craftBPFlags?.hasPendingWorkspaceRefresh || window.craftBPFlags?.hasPendingMETEChanges
    );
    notice.classList.toggle('d-none', !hasPendingRefresh);
}

function markPendingWorkspaceRefresh(options = {}) {
    window.craftBPFlags = window.craftBPFlags || {};
    window.craftBPFlags.hasPendingWorkspaceRefresh = true;
    window.craftBPFlags.hasPendingMETEChanges = true;

    const sourceTabName = String(options.sourceTabName || '').trim();
    if (sourceTabName) {
        window.craftBPFlags.pendingWorkspaceSourceTab = sourceTabName;
    }

    updatePendingWorkspaceRefreshNotice();
}

function clearPendingWorkspaceRefresh(options = {}) {
    window.craftBPFlags = window.craftBPFlags || {};
    window.craftBPFlags.hasPendingWorkspaceRefresh = false;
    window.craftBPFlags.hasPendingMETEChanges = false;
    delete window.craftBPFlags.pendingWorkspaceSourceTab;
    delete window.craftBPFlags.pendingWorkspaceTargetTab;
    delete window.craftBPFlags.switchingToTab;

    updatePendingWorkspaceRefreshNotice();

    if (options.persist !== false) {
        persistCraftPageSessionState();
    }
}

function clearPendingMETEChanges(options = {}) {
    clearPendingWorkspaceRefresh(options);
}

function normalizeBlueprintPayloadWindowData(data) {
    const payload = (data && typeof data === 'object') ? data : {};
    return Object.assign({}, payload, {
        save_url: payload.urls ? payload.urls.save : undefined,
        load_url: payload.urls ? payload.urls.load_list : undefined,
        load_config_url: payload.urls ? payload.urls.load_config : undefined,
        fuzzwork_price_url: payload.urls ? payload.urls.fuzzwork_price : undefined,
    });
}

function getCurrentBuyTypeIds() {
    return Array.from(getCurrentDecisionsFromSimulationOrDom().entries())
        .filter(([, mode]) => mode === 'buy')
        .map(([typeId]) => Number(typeId) || 0)
        .filter((typeId) => typeId > 0);
}

function buildCleanCraftBrowserUrl() {
    const cleanUrl = new URL(window.location.pathname, window.location.origin);
    cleanUrl.hash = window.location.hash || '';
    return cleanUrl;
}

function syncCraftBrowserUrl() {
    const cleanUrl = buildCleanCraftBrowserUrl();
    const currentUrl = new URL(window.location.href);
    if (currentUrl.pathname !== cleanUrl.pathname || currentUrl.search || currentUrl.hash !== cleanUrl.hash) {
        window.history.replaceState({}, '', cleanUrl.toString());
    }
}

function buildCraftRecalculationUrl(options = {}) {
    const config = getCurrentMETEConfig();
    const requestedRuns = options.runs ?? document.getElementById('runsInput')?.value ?? 1;
    const runsValue = Math.max(1, parseInt(requestedRuns, 10) || 1);
    const targetTab = options.activeTab
        || window.craftBPFlags?.pendingWorkspaceTargetTab
        || window.craftBPFlags?.switchingToTab
        || getCurrentActiveBlueprintTab()
        || 'materials';

    const cleanUrl = new URL(window.location.pathname, window.location.origin);
    const originalUrl = new URL(window.location.href);

    ['buy', 'next'].forEach((param) => {
        const value = originalUrl.searchParams.get(param);
        if (value) {
            cleanUrl.searchParams.set(param, value);
        }
    });

    if (window.INDY_HUB_DEBUG) {
        cleanUrl.searchParams.set('indy_debug', '1');
    }

    cleanUrl.searchParams.set('runs', String(runsValue));
    cleanUrl.searchParams.set('me', String(config.mainME || 0));
    cleanUrl.searchParams.set('te', String(config.mainTE || 0));
    cleanUrl.searchParams.set('active_tab', targetTab);

    Object.entries(config.blueprintConfigs || {}).forEach(([typeId, bpConfig]) => {
        if (bpConfig.me !== undefined) {
            cleanUrl.searchParams.set(`me_${typeId}`, String(bpConfig.me));
        }
        if (bpConfig.te !== undefined) {
            cleanUrl.searchParams.set(`te_${typeId}`, String(bpConfig.te));
        }
    });

    return cleanUrl;
}

function getCurrentActiveBlueprintTab() {
    const activeMainTab = document.querySelector('#craftMainTabs .nav-link.active');
    const mainTabName = activeMainTab ? String(activeMainTab.getAttribute('data-tab-name') || '').trim() : '';
    const hiddenActiveTab = document.querySelector('#bpTabs .nav-link.active');
    const hiddenTarget = hiddenActiveTab ? String(hiddenActiveTab.getAttribute('data-bs-target') || '').trim() : '';

    if (mainTabName === 'build') {
        return 'cycles';
    }
    if (mainTabName === 'structure') {
        return 'config';
    }
    if (mainTabName === 'configure') {
        return 'config';
    }
    if (mainTabName === 'buy') {
        return 'financial';
    }
    if (mainTabName === 'stock') {
        return 'stock';
    }
    if (mainTabName === 'plan') {
        return hiddenTarget ? hiddenTarget.replace('#tab-', '') : 'materials';
    }
    return hiddenTarget ? hiddenTarget.replace('#tab-', '') : 'materials';
}

function showCraftMainTab(tabName) {
    const normalizedTabName = String(tabName || '').trim();
    if (!normalizedTabName) {
        return;
    }

    const tabButton = document.querySelector(`#craftMainTabs .nav-link[data-tab-name="${normalizedTabName}"]`);
    if (!tabButton) {
        return;
    }

    if (window.bootstrap?.Tab && typeof window.bootstrap.Tab.getOrCreateInstance === 'function') {
        window.bootstrap.Tab.getOrCreateInstance(tabButton).show();
        return;
    }

    tabButton.click();
}

function getActiveCraftMainTabName() {
    const activeMainTab = document.querySelector('#craftMainTabs .nav-link.active');
    return String(activeMainTab?.getAttribute('data-tab-name') || 'plan').trim() || 'plan';
}

function hydrateVisibleCraftStartupTab() {
    switch (getActiveCraftMainTabName()) {
        case 'buy':
            if (typeof updateFinancialTabFromState === 'function') {
                updateFinancialTabFromState();
            }
            break;
        case 'stock':
            if (typeof updateStockManagementTabFromState === 'function') {
                updateStockManagementTabFromState(true);
            }
            break;
        case 'build':
            if (typeof updateBuildTabFromState === 'function') {
                updateBuildTabFromState();
            } else {
                sortBuildCyclesTable();
            }
            break;
        case 'timing':
            if (typeof updateCraftTimingTabFromState === 'function') {
                updateCraftTimingTabFromState();
            }
            break;
        case 'steps':
            if (typeof updateCraftStepsTabFromState === 'function') {
                updateCraftStepsTabFromState();
            }
            break;
        case 'structure':
            if (typeof renderStructurePlanner === 'function') {
                renderStructurePlanner();
            }
            break;
        case 'configure':
            if (typeof window.updateConfigTabFromState === 'function') {
                window.updateConfigTabFromState();
            }
            if (typeof window.validateBlueprintRuns === 'function') {
                window.validateBlueprintRuns();
            }
            break;
        case 'plan':
        default:
            if (typeof updateMaterialsTabFromState === 'function') {
                updateMaterialsTabFromState();
            }
            break;
    }
}

async function fetchCraftPageSnapshot(url) {
    const response = await fetch(url.toString(), {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
    });
    if (!response.ok) {
        throw new Error(`craft page fetch failed: ${response.status}`);
    }

    const html = await response.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    const payloadNode = doc.getElementById('blueprint-payload');
    if (!payloadNode) {
        throw new Error('Missing blueprint-payload in refreshed page');
    }

    let payload;
    try {
        payload = JSON.parse(payloadNode.textContent || '{}');
    } catch (error) {
        throw new Error('Unable to parse refreshed blueprint payload');
    }

    return {
        doc,
        payload,
        treeNode: doc.getElementById('tab-tree'),
    };
}

function syncBlueprintPayloadNode(payload) {
    const payloadNode = document.getElementById('blueprint-payload');
    if (payloadNode) {
        payloadNode.textContent = JSON.stringify(payload);
    }
    window.BLUEPRINT_DATA = normalizeBlueprintPayloadWindowData(payload);
}

function readMaterialsTreeChildren(node) {
    if (!node || typeof node !== 'object') {
        return [];
    }
    const children = node.sub_materials || node.subMaterials;
    return Array.isArray(children) ? children : [];
}

function buildTreeModeBadgeClass(state) {
    if (state === 'buy') {
        return 'bg-secondary text-white';
    }
    if (state === 'useless') {
        return 'bg-warning text-dark';
    }
    return 'bg-success text-white';
}

function getDecisionSwitchRoot() {
    // Prefer the Decisions tab strategy rows if populated, but fall back to
    // the materials tree when that container is empty (e.g. user clicks the
    // "all to buy / all to prod" buttons on the Tree tab before the Decisions
    // tab has been opened, so its rows have not been lazily rendered yet).
    const decisionRoot = document.getElementById('decisionStrategyRows');
    if (decisionRoot && decisionRoot.querySelector('input.mat-switch[data-type-id]')) {
        return decisionRoot;
    }
    return document.getElementById('tab-tree') || decisionRoot;
}

function getDecisionSwitchElements() {
    const root = getDecisionSwitchRoot();
    if (!root) {
        return [];
    }
    return Array.from(root.querySelectorAll('input.mat-switch[data-type-id]'));
}

function updateTreeModeBadges() {
    const decisions = getCurrentDecisionsFromSimulationOrDom();
    document.querySelectorAll('#tab-tree .tree-mode-label[data-type-id]').forEach((badge) => {
        const typeId = Number(badge.getAttribute('data-type-id') || 0) || 0;
        const fallbackState = String(badge.dataset.switchState || 'prod').trim() || 'prod';
        const state = decisions.get(typeId) || fallbackState;
        const label = state === 'buy' ? __('Buy') : state === 'useless' ? __('Useless') : __('Prod');

        badge.className = `tree-mode-label mode-label badge px-2 py-1 fw-bold ${buildTreeModeBadgeClass(state)}`;
        badge.dataset.switchState = state;
        badge.textContent = label;
    });
}

function buildMaterialsTreeMarkup(nodes, level = 0) {
    if (!Array.isArray(nodes) || nodes.length === 0) {
        return '';
    }

    const marginLevel = Math.min(level + 1, 5);
    const listItems = nodes.map((node) => {
        const typeId = Number(node?.type_id || node?.typeId || 0) || 0;
        const typeName = String(node?.type_name || node?.typeName || typeId || '');
        const quantity = Math.max(0, Math.ceil(Number(node?.quantity ?? node?.qty ?? 0))) || 0;
        const children = readMaterialsTreeChildren(node);
        const hasChildren = children.length > 0;
        const fallbackSwitchState = String(node?.project_inclusion_mode || node?.projectInclusionMode || 'prod').trim() || 'prod';
        const switchState = typeof window.SimulationAPI?.getSwitchState === 'function'
            ? (window.SimulationAPI.getSwitchState(typeId) || fallbackSwitchState)
            : fallbackSwitchState;
        const isUseless = switchState === 'useless';
        const isProd = !isUseless && switchState !== 'buy';
        const modeLabel = isUseless ? __('Useless') : (isProd ? __('Prod') : __('Buy'));
        const childMarkup = hasChildren ? buildMaterialsTreeMarkup(children, level + 1) : '';

        return `
            <li class="craft-tree-branch">
                <details class="mb-2">
                    <summary class="d-flex align-items-center gap-2 py-1" data-type-id="${typeId}" data-type-name="${escapeHtml(typeName)}" data-qty="${quantity}" data-tree-id="${typeId}">
                        ${hasChildren
                            ? '<span class="summary-icon"><i class="fas fa-caret-right"></i></span>'
                            : '<span class="me-2" style="width:2.5rem;display:inline-block;"></span>'}
                        <span class="blueprint-icon" style="width:28px;height:28px;">
                            <img src="https://images.evetech.net/types/${typeId}/icon?size=32" alt="${escapeHtml(typeName)}" loading="lazy" decoding="async" fetchpriority="low" style="width:28px;height:28px;object-fit:cover;border-radius:6px;background:#f3f4f6;" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
                            <span class="fallback" style="display:none;"><i class="fas fa-cube"></i></span>
                        </span>
                        <span class="fw-bold">${escapeHtml(typeName)}</span>
                        <span class="text-muted">x${formatInteger(quantity)}</span>
                        ${hasChildren ? `
                        <span class="ms-auto"></span>
                        <span class="tree-mode-label mode-label badge px-2 py-1 fw-bold ${buildTreeModeBadgeClass(switchState)}" data-type-id="${typeId}" data-switch-state="${escapeHtml(switchState)}" style="font-size:0.85em;">${escapeHtml(modeLabel)}</span>` : ''}
                    </summary>
                    ${childMarkup}
                </details>
            </li>
        `;
    }).join('');

    return `<ul class="list-unstyled ms-${marginLevel}">${listItems}</ul>`;
}

function renderPlanTreeFromPayload() {
    const treeTab = document.getElementById('tab-tree');
    if (!treeTab) {
        return false;
    }

    const tree = Array.isArray(window.BLUEPRINT_DATA?.materials_tree) ? window.BLUEPRINT_DATA.materials_tree : [];
    if (tree.length === 0) {
        treeTab.innerHTML = `<div class="alert alert-info mb-0">${escapeHtml(__('No sub-productions detected for this blueprint.'))}</div>`;
    } else {
        treeTab.innerHTML = buildMaterialsTreeMarkup(tree, 0);
    }

    delete treeTab.dataset.switchesInitialized;
    delete treeTab.dataset.summaryIconsInitialized;
    return true;
}

function hydratePlanPane() {
    const planPane = document.getElementById('plan-pane');
    if (!planPane) {
        return false;
    }

    if (planPane.dataset.planHydrated === 'true') {
        return false;
    }

    const template = document.getElementById('plan-pane-template');
    if (!template) {
        return false;
    }

    planPane.appendChild(template.content.cloneNode(true));
    planPane.dataset.planHydrated = 'true';

    renderPlanTreeFromPayload();

    const notice = document.getElementById('planPaneLazyNotice');
    if (notice) {
        notice.remove();
    }

    initializeBlueprintIcons();
    initializeCollapseHandlers();
    initializeBuyCraftSwitches();

    if (Array.isArray(window.craftBPFlags?.pendingBuyTypeIds)) {
        applyBuyCraftStateFromBuyDecisions(window.craftBPFlags.pendingBuyTypeIds, {
            refreshTabs: false,
            refreshSimulation: false,
        });
    }

    try {
        getDashboardMaterialsOrdering();
    } catch (e) {
        // ignore
    }

    return true;
}

function replaceTreeMarkup(nextTreeNode) {
    const treeTab = document.getElementById('tab-tree');
    if (!nextTreeNode || !treeTab) {
        return;
    }

    renderPlanTreeFromPayload();

    initializeBuyCraftSwitches();
    refreshTreeSummaryIcons();
}

function updateFinalProductRowFromPayload(payload) {
    const tableBody = document.getElementById('financialItemsBody');
    if (!tableBody) {
        return;
    }

    const outputs = getFinalOutputEntries(payload);
    if (outputs.length === 0) {
        return;
    }

    const preservedSaleValues = new Map();
    getFinalOutputRows().forEach((row) => {
        const typeId = Number(row.getAttribute('data-type-id') || 0) || 0;
        const saleInput = row.querySelector('.sale-price-unit');
        preservedSaleValues.set(typeId, saleInput ? saleInput.value : '0');
        row.remove();
    });

    outputs.forEach((output, index) => {
        const template = document.createElement('template');
        template.innerHTML = buildFinalOutputRowMarkup(output, index === 0).trim();
        const row = template.content.firstElementChild;
        if (!row) {
            return;
        }
        tableBody.appendChild(row);
        const typeId = Number(output?.type_id || output?.typeId || 0) || 0;
        const saleInput = row.querySelector('.sale-price-unit');
        if (saleInput && preservedSaleValues.has(typeId)) {
            saleInput.value = preservedSaleValues.get(typeId) || '0';
        }
        attachPriceInputListener(row.querySelector('.fuzzwork-price'));
        attachPriceInputListener(saleInput);
    });
}

function getLoadingOverlayElements() {
    return {
        overlay: document.getElementById('bpTabs-loading'),
        workspace: document.getElementById('craft-bp-workspace'),
        title: document.getElementById('craft-bp-loading-title'),
        message: document.getElementById('craft-bp-loading-message'),
        progressBar: document.getElementById('craft-bp-loading-progress-bar'),
        progressLabel: document.getElementById('craft-bp-loading-progress-label'),
        steps: document.getElementById('craft-bp-loading-steps'),
    };
}

function setLoadingOverlayCopy(title, message) {
    const elements = getLoadingOverlayElements();
    if (elements.title && title) {
        elements.title.textContent = title;
    }
    if (elements.message && message) {
        elements.message.textContent = message;
    }
}

const loadingOverlayState = {
    bootstrap: null,
    lastBootstrap: null,
    tickerId: null,
    hideTimerId: null,
    minimumVisibleUntil: 0,
    replayStartedAt: 0,
    replayCurrentStepStartedAt: 0,
    replayStepDurations: [],
    replayCurrentIndex: -1,
    replayRequestedIndex: -1,
};

function getLoadingNow() {
    if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
        return performance.now();
    }
    return Date.now();
}

function formatLoadingDuration(ms) {
    const value = Number(ms) || 0;
    if (value < 1000) {
        return `${Math.round(value)} ms`;
    }
    return `${(value / 1000).toFixed(1)} s`;
}

function getServerWorkspaceTimingSteps() {
    const timing = window.BLUEPRINT_DATA?.page?.workspace_timing;
    if (!timing || !Array.isArray(timing.steps) || timing.steps.length === 0) {
        return [];
    }

    const groupedSteps = [
        {
            id: 'loading-tree',
            label: __('Loading production tree'),
            detail: __('Preparing the production tree, required quantities, and timings.'),
            sourceIds: ['selected-items', 'type-names', 'materials-tree', 'cycle-summary', 'market-groups', 'production-times'],
        },
        {
            id: 'loading-structures',
            label: __('Calculating recommended structures'),
            detail: __('Selecting the best structures for each production line.'),
            sourceIds: ['structure-planner'],
        },
        {
            id: 'loading-blueprints',
            label: __('Loading blueprints'),
            detail: __('Preparing available blueprints and their options.'),
            sourceIds: ['blueprint-names', 'blueprint-configs', 'character-advisor'],
        },
    ];

    return groupedSteps
        .map((group) => {
            const matchingSteps = timing.steps.filter((step) => group.sourceIds.includes(String(step.id || '')));
            if (matchingSteps.length === 0) {
                return null;
            }

            const durationMs = matchingSteps.reduce((total, step) => {
                return total + Math.max(0, Number(step.duration_ms ?? step.durationMs ?? 0) || 0);
            }, 0);

            return {
                id: group.id,
                label: group.label,
                detail: group.detail,
                status: 'done',
                durationMs,
                startedAt: 0,
                completedAt: durationMs,
            };
        })
        .filter(Boolean);
}

function buildWorkspaceBootstrapSteps() {
    const serverSteps = getServerWorkspaceTimingSteps();
    const steps = [];

    if (serverSteps.length > 0) {
        steps.push(...serverSteps);
    } else {
        steps.push({
            id: 'loading-workspace-data',
            label: __('Loading workspace data'),
            detail: __('Preparing the data needed to open the workspace.'),
        });
    }

    steps.push(
        { id: 'loading-interface', label: __('Preparing the page'), detail: __('Restoring your session and setting up the workspace interface.') },
        { id: 'loading-prices', label: __('Loading prices'), detail: __('Fetching market prices and updating calculations.') },
        { id: 'finalize', label: __('Finalizing workspace'), detail: __('Applying the last updates before showing the page.') },
    );

    return steps;
}

function stopLoadingOverlayTicker() {
    if (loadingOverlayState.tickerId) {
        window.clearInterval(loadingOverlayState.tickerId);
        loadingOverlayState.tickerId = null;
    }
}

function resetLoadingOverlayReplayState() {
    loadingOverlayState.replayStartedAt = 0;
    loadingOverlayState.replayCurrentStepStartedAt = 0;
    loadingOverlayState.replayStepDurations = [];
    loadingOverlayState.replayCurrentIndex = -1;
    loadingOverlayState.replayRequestedIndex = -1;
}

function buildLoadingReplayDurations(steps, minimumVisibleMs) {
    const durations = Array.isArray(steps)
        ? steps.map((step) => Math.max(0, Number(step.durationMs) || 0))
        : [];

    if (durations.length === 0) {
        return [];
    }

    const totalMeasured = durations.reduce((sum, value) => sum + value, 0);
    const targetTotal = Math.max(Number(minimumVisibleMs) || 0, durations.length * 140);

    if (totalMeasured <= 0) {
        return durations.map(() => targetTotal / durations.length);
    }

    const scaled = durations.map((value) => Math.max(140, (value / totalMeasured) * targetTotal));
    const scaledTotal = scaled.reduce((sum, value) => sum + value, 0);
    const factor = scaledTotal > 0 ? targetTotal / scaledTotal : 1;
    return scaled.map((value) => value * factor);
}

function setLoadingReplayStepActive(nextIndex) {
    const sequence = loadingOverlayState.bootstrap;
    if (!sequence || !Array.isArray(sequence.steps)) {
        return;
    }

    sequence.steps.forEach((step, index) => {
        if (index < nextIndex) {
            step.status = 'done';
        } else if (index === nextIndex) {
            step.status = 'current';
        } else {
            step.status = 'pending';
        }
    });

    loadingOverlayState.replayCurrentIndex = nextIndex;
    loadingOverlayState.replayCurrentStepStartedAt = getLoadingNow();
}

function advanceLoadingOverlayReplay(forceComplete = false) {
    const sequence = loadingOverlayState.bootstrap;
    if (!sequence || !Array.isArray(sequence.steps) || sequence.steps.length === 0) {
        return;
    }

    if (loadingOverlayState.replayCurrentIndex < 0) {
        setLoadingReplayStepActive(0);
        renderLoadingOverlayBootstrap();
        return;
    }

    if (forceComplete) {
        sequence.steps.forEach((step) => {
            step.status = 'done';
        });
        loadingOverlayState.replayCurrentIndex = sequence.steps.length - 1;
        renderLoadingOverlayBootstrap();
        return;
    }

    if (loadingOverlayState.replayCurrentIndex >= loadingOverlayState.replayRequestedIndex) {
        renderLoadingOverlayBootstrap();
        return;
    }

    const currentIndex = loadingOverlayState.replayCurrentIndex;
    const currentDuration = Math.max(80, Number(loadingOverlayState.replayStepDurations[currentIndex]) || 0);
    const now = getLoadingNow();
    if ((now - loadingOverlayState.replayCurrentStepStartedAt) < currentDuration) {
        renderLoadingOverlayBootstrap();
        return;
    }

    const nextIndex = Math.min(currentIndex + 1, sequence.steps.length - 1);
    setLoadingReplayStepActive(nextIndex);
    renderLoadingOverlayBootstrap();
}

function clearLoadingOverlayHideTimer() {
    if (loadingOverlayState.hideTimerId) {
        window.clearTimeout(loadingOverlayState.hideTimerId);
        loadingOverlayState.hideTimerId = null;
    }
}

function hideLoadingIndicatorNow() {
    const elements = getLoadingOverlayElements();
    if (elements.workspace) {
        elements.workspace.classList.remove('is-loading');
        elements.workspace.setAttribute('aria-hidden', 'false');
    }
    if (elements.overlay) {
        elements.overlay.classList.add('is-hidden');
        elements.overlay.setAttribute('aria-busy', 'false');
    }
    resetLoadingOverlayBootstrap();
}

function renderLoadingOverlayBootstrap() {
    const elements = getLoadingOverlayElements();
    const sequence = loadingOverlayState.bootstrap;

    if (!elements.progressBar || !elements.progressLabel || !elements.steps) {
        return;
    }

    if (!sequence || !Array.isArray(sequence.steps) || sequence.steps.length === 0) {
        elements.progressBar.style.width = '0%';
        elements.progressLabel.textContent = '';
        elements.progressLabel.classList.add('d-none');
        elements.steps.innerHTML = '';
        elements.steps.classList.add('d-none');
        return;
    }

    const now = getLoadingNow();
    const completedCount = sequence.steps.filter((step) => step.status === 'done').length;
    const activeStep = sequence.steps.find((step) => step.status === 'current') || null;
    const progress = ((completedCount + (activeStep ? 0.5 : 0)) / sequence.steps.length) * 100;
    elements.progressBar.style.width = `${Math.max(0, Math.min(100, progress)).toFixed(0)}%`;
    elements.progressLabel.textContent = activeStep
        ? `${completedCount}/${sequence.steps.length} - ${activeStep.label}`
        : `${completedCount}/${sequence.steps.length} - ${__('Finishing workspace')}`;
    elements.progressLabel.classList.remove('d-none');
    elements.steps.classList.remove('d-none');

    elements.steps.innerHTML = sequence.steps.map((step, index) => {
        const detail = step.detail
            || (step.status === 'done'
                ? __('Done')
                : (step.status === 'current' ? __('In progress') : __('Queued')));

        return `
            <li class="craft-bp-loading-step is-${step.status}">
                <span class="craft-bp-loading-step__index">${index + 1}</span>
                <div>
                    <div class="craft-bp-loading-step__title">${escapeHtml(step.label || step.id || `Step ${index + 1}`)}</div>
                    <div class="craft-bp-loading-step__detail">${escapeHtml(detail)}</div>
                </div>
            </li>
        `;
    }).join('');
}

function ensureLoadingOverlayTicker() {
    if (loadingOverlayState.tickerId || !loadingOverlayState.bootstrap) {
        return;
    }
    loadingOverlayState.tickerId = window.setInterval(() => {
        if (!loadingOverlayState.bootstrap) {
            stopLoadingOverlayTicker();
            return;
        }
        advanceLoadingOverlayReplay(false);
    }, 100);
}

function startLoadingOverlayBootstrap(options = {}) {
    const steps = Array.isArray(options.steps) ? options.steps : [];
    const navigationEntry = (typeof performance !== 'undefined' && typeof performance.getEntriesByType === 'function')
        ? performance.getEntriesByType('navigation')[0]
        : null;
    const responseEndMs = navigationEntry ? (Number(navigationEntry.responseEnd) || 0) : 0;

    loadingOverlayState.bootstrap = {
        key: options.key || 'workspace-bootstrap',
        startedAt: 0,
        steps: steps.map((step) => ({
            id: step.id,
            label: step.label || step.id,
            detail: step.detail || '',
            status: step.status || 'pending',
            startedAt: step.startedAt ?? null,
            completedAt: step.completedAt ?? null,
            durationMs: step.durationMs ?? null,
        })),
    };
    const minimumVisibleMs = Math.max(0, Number(options.minimumVisibleMs) || 1200);
    loadingOverlayState.minimumVisibleUntil = getLoadingNow() + minimumVisibleMs;
    loadingOverlayState.replayStartedAt = getLoadingNow();
    loadingOverlayState.replayStepDurations = buildLoadingReplayDurations(loadingOverlayState.bootstrap.steps, minimumVisibleMs);
    loadingOverlayState.replayRequestedIndex = Math.max(0, loadingOverlayState.bootstrap.steps.length - 1);
    loadingOverlayState.replayCurrentIndex = -1;
    loadingOverlayState.replayCurrentStepStartedAt = loadingOverlayState.replayStartedAt;
    loadingOverlayState.bootstrap.steps.forEach((step) => {
        step.status = 'pending';
    });
    clearLoadingOverlayHideTimer();

    showLoadingIndicator({
        title: options.title || __('Preparing production workspace'),
        message: options.message || __('Loading workspace...'),
    });

    const serverStep = loadingOverlayState.bootstrap.steps.find((step) => step.id === 'loading-workspace-data') || null;
    if (serverStep && responseEndMs > 0) {
        serverStep.startedAt = 0;
        serverStep.completedAt = responseEndMs;
        serverStep.durationMs = responseEndMs;
        if (!serverStep.detail) {
            serverStep.detail = __('Preparing the data needed to open the workspace.');
        }
    }

    setLoadingReplayStepActive(0);
    renderLoadingOverlayBootstrap();

    ensureLoadingOverlayTicker();
}

function setLoadingOverlayBootstrapStep(stepId, options = {}) {
    const sequence = loadingOverlayState.bootstrap;
    if (!sequence || !Array.isArray(sequence.steps)) {
        return false;
    }

    const now = getLoadingNow();

    const targetStep = sequence.steps.find((step) => step.id === stepId);
    if (!targetStep) {
        return false;
    }
    const targetIndex = sequence.steps.findIndex((step) => step.id === stepId);

    targetStep.startedAt = Number(targetStep.startedAt) || now;
    if (typeof options.detail === 'string') {
        targetStep.detail = options.detail;
    }
    if (typeof options.label === 'string' && options.label) {
        targetStep.label = options.label;
    }

    if (targetIndex >= 0) {
        loadingOverlayState.replayRequestedIndex = Math.max(loadingOverlayState.replayRequestedIndex, targetIndex);
        if (options.complete === true) {
            loadingOverlayState.replayRequestedIndex = Math.max(
                loadingOverlayState.replayRequestedIndex,
                Math.min(targetIndex + 1, sequence.steps.length - 1)
            );
        }
    }

    if (options.title || options.message) {
        setLoadingOverlayCopy(options.title || null, options.message || null);
    }

    advanceLoadingOverlayReplay(false);
    ensureLoadingOverlayTicker();
    return true;
}

function completeLoadingOverlayBootstrap(options = {}) {
    const sequence = loadingOverlayState.bootstrap;
    if (!sequence || !Array.isArray(sequence.steps)) {
        return null;
    }

    const now = getLoadingNow();
    const replayDurationMs = Math.max(1600, sequence.steps.length * 180);
    const replayTailMs = 180;
    sequence.steps.forEach((step) => {
        step.durationMs = Number(step.durationMs) || 0;
        step.completedAt = step.completedAt ?? now;
        if (!step.detail) {
            step.detail = __('No additional startup work was recorded for this phase.');
        }
    });

    sequence.completedAt = now;
    loadingOverlayState.lastBootstrap = {
        key: sequence.key,
        totalDurationMs: Math.max(0, now - sequence.startedAt),
        completedAtIso: new Date().toISOString(),
        steps: sequence.steps.map((step) => ({
            id: step.id,
            label: step.label,
            detail: step.detail,
            status: 'done',
            durationMs: Number(step.durationMs) || 0,
        })),
    };

    window.CraftBPStartupMetrics = loadingOverlayState.lastBootstrap;

    if (options.title || options.message) {
        setLoadingOverlayCopy(options.title || null, options.message || null);
    }

    loadingOverlayState.minimumVisibleUntil = now + replayDurationMs + replayTailMs;
    loadingOverlayState.replayStartedAt = now;
    loadingOverlayState.replayCurrentStepStartedAt = now;
    loadingOverlayState.replayStepDurations = sequence.steps.map(() => replayDurationMs / Math.max(1, sequence.steps.length));
    loadingOverlayState.replayCurrentIndex = -1;
    loadingOverlayState.replayRequestedIndex = Math.max(0, sequence.steps.length - 1);
    sequence.steps.forEach((step) => {
        step.status = 'pending';
    });

    setLoadingReplayStepActive(0);
    renderLoadingOverlayBootstrap();
    ensureLoadingOverlayTicker();
    return loadingOverlayState.lastBootstrap;
}

function resetLoadingOverlayBootstrap() {
    loadingOverlayState.bootstrap = null;
    loadingOverlayState.minimumVisibleUntil = 0;
    resetLoadingOverlayReplayState();
    clearLoadingOverlayHideTimer();
    stopLoadingOverlayTicker();
    renderLoadingOverlayBootstrap();
}

let scheduledBlueprintRecalculationTimer = null;
let scheduledBlueprintRecalculationOptions = null;
let scheduledBlueprintRecalculationPromise = null;
let scheduledBlueprintRecalculationResolver = null;
let scheduledBlueprintRecalculationRejecter = null;

async function recalculateBlueprintWorkspace(options = {}) {
    const statusPusher = window.CraftBP && typeof window.CraftBP.pushStatus === 'function'
        ? window.CraftBP.pushStatus.bind(window.CraftBP)
        : null;
    const buyTypeIds = getCurrentBuyTypeIds();
    const url = buildCraftRecalculationUrl(options);

    if (statusPusher) {
        statusPusher(__('Refreshing production workspace…'), 'info');
    }

    showLoadingIndicator({
        title: __('Refreshing production workspace'),
        message: __('Applying blueprint changes and recalculating materials, costs and structures.'),
    });

    try {
        const snapshot = await fetchCraftPageSnapshot(url);
        syncBlueprintPayloadNode(snapshot.payload);

        if (window.SimulationAPI && typeof window.SimulationAPI.replacePayload === 'function') {
            window.SimulationAPI.replacePayload(window.BLUEPRINT_DATA, {
                preservePrices: true,
                preserveStructures: true,
                preserveSwitches: false,
            });
        }

        replaceTreeMarkup(snapshot.treeNode);
        applyBuyCraftStateFromBuyDecisions(buyTypeIds, {
            refreshSimulation: false,
            refreshTabs: false,
        });
        updateFinalProductRowFromPayload(window.BLUEPRINT_DATA);

        const runsInput = document.getElementById('runsInput');
        if (runsInput) {
            runsInput.value = String(Math.max(1, parseInt(snapshot.payload?.num_runs || options.runs || runsInput.value || 1, 10) || 1));
        }

        const activeTabInput = document.getElementById('activeTabInput');
        if (activeTabInput) {
            activeTabInput.value = String(url.searchParams.get('active_tab') || getCurrentActiveBlueprintTab() || 'materials');
        }

        if (window.SimulationAPI && typeof window.SimulationAPI.refreshFromDom === 'function') {
            window.SimulationAPI.refreshFromDom();
        }

        // Some derived client state is refreshed after the payload swap; rerender the
        // visible production tree from the final in-memory payload so root quantities
        // stay aligned with the rest of the workspace.
        renderPlanTreeFromPayload();
        initializeBuyCraftSwitches();
        refreshTreeSummaryIcons();

        refreshTabsAfterStateChange({ forceNeeded: true });
        clearPendingMETEChanges();
        syncCraftBrowserUrl();

        if (statusPusher) {
            statusPusher(__('Workspace updated'), 'success');
        }

        return window.BLUEPRINT_DATA;
    } finally {
        hideLoadingIndicator();
    }
}

function queueBlueprintWorkspaceRecalculation(options = {}) {
    const queueOptions = { ...(options || {}) };
    const delay = Number(queueOptions.delay);
    const shouldRunImmediately = queueOptions.immediate === true;

    delete queueOptions.delay;
    delete queueOptions.immediate;

    scheduledBlueprintRecalculationOptions = {
        ...(scheduledBlueprintRecalculationOptions || {}),
        ...queueOptions,
    };

    if (!scheduledBlueprintRecalculationPromise) {
        scheduledBlueprintRecalculationPromise = new Promise((resolve, reject) => {
            scheduledBlueprintRecalculationResolver = resolve;
            scheduledBlueprintRecalculationRejecter = reject;
        });
    }

    const queuedPromise = scheduledBlueprintRecalculationPromise;

    if (scheduledBlueprintRecalculationTimer) {
        window.clearTimeout(scheduledBlueprintRecalculationTimer);
        scheduledBlueprintRecalculationTimer = null;
    }

    const runQueuedRecalculation = () => {
        scheduledBlueprintRecalculationTimer = null;
        const nextOptions = scheduledBlueprintRecalculationOptions || {};
        const resolver = scheduledBlueprintRecalculationResolver;
        const rejecter = scheduledBlueprintRecalculationRejecter;

        scheduledBlueprintRecalculationOptions = null;
        scheduledBlueprintRecalculationPromise = null;
        scheduledBlueprintRecalculationResolver = null;
        scheduledBlueprintRecalculationRejecter = null;

        recalculateBlueprintWorkspace(nextOptions).then(resolver).catch(rejecter);
    };

    if (shouldRunImmediately) {
        runQueuedRecalculation();
    } else {
        scheduledBlueprintRecalculationTimer = window.setTimeout(
            runQueuedRecalculation,
            Number.isFinite(delay) ? Math.max(0, delay) : 250
        );
    }

    return queuedPromise;
}

function getCraftPageSessionStorageKey() {
    const productTypeId = getProductTypeIdValue();
    return `indy_hub:crafter:session:${window.location.pathname}:${productTypeId || 0}`;
}

function getCraftPageNavigationType() {
    try {
        if (window.performance && typeof window.performance.getEntriesByType === 'function') {
            const navigationEntries = window.performance.getEntriesByType('navigation');
            if (Array.isArray(navigationEntries) && navigationEntries.length > 0) {
                return navigationEntries[0].type || 'navigate';
            }
        }
        if (window.performance && window.performance.navigation) {
            if (window.performance.navigation.type === window.performance.navigation.TYPE_RELOAD) {
                return 'reload';
            }
        }
    } catch (error) {
        craftBPDebugLog('[CraftCache] Failed to inspect navigation type', error);
    }
    return 'navigate';
}

function isCraftPageReloadNavigation() {
    return getCraftPageNavigationType() === 'reload';
}

function withCraftPageSessionStorage(callback) {
    try {
        if (!window.sessionStorage || typeof callback !== 'function') {
            return null;
        }
        return callback(window.sessionStorage);
    } catch (error) {
        craftBPDebugLog('[CraftCache] Session storage unavailable', error);
        return null;
    }
}

function getCraftPageMETEStorageKey() {
    const bpTypeId = getCurrentBlueprintTypeId();
    return `indy_hub:crafter:mete:${window.location.pathname}:${bpTypeId || 0}`;
}

function showBlueprintSubTab(tabName) {
    const normalizedTab = String(tabName || '').trim();
    if (!normalizedTab) {
        return;
    }

    const tabBtn = document.querySelector(`#bpTabs .nav-link[data-bs-target="#tab-${normalizedTab}"]`);
    if (!tabBtn) {
        return;
    }

    if (window.bootstrap && window.bootstrap.Tab && typeof window.bootstrap.Tab.getOrCreateInstance === 'function') {
        window.bootstrap.Tab.getOrCreateInstance(tabBtn).show();
        return;
    }

    document.querySelectorAll('#bpTabs .nav-link').forEach((btn) => btn.classList.remove('active'));
    document.querySelectorAll('#bpTabsContent .tab-pane').forEach((pane) => pane.classList.remove('show', 'active'));
    tabBtn.classList.add('active');
    const targetSelector = tabBtn.getAttribute('data-bs-target');
    if (!targetSelector) {
        return;
    }
    const targetPane = document.querySelector(targetSelector);
    if (targetPane) {
        targetPane.classList.add('show', 'active');
    }
}

function applyCraftPageRunsValue(value) {
    const runsInput = document.getElementById('runsInput');
    if (!runsInput) {
        return;
    }
    runsInput.value = String(Math.max(1, parseInt(value, 10) || 1));
}

function collectManualPriceOverrides() {
    return Array.from(document.querySelectorAll('.real-price[data-type-id], .sale-price-unit[data-type-id]'))
        .filter((input) => input.dataset.userModified === 'true')
        .map((input) => ({
            typeId: Number(input.getAttribute('data-type-id')) || 0,
            priceType: input.classList.contains('sale-price-unit') ? 'sale' : 'real',
            value: Number.parseFloat(input.value) || 0,
        }))
        .filter((entry) => entry.typeId > 0);
}

function normalizeCraftStockAllocations(rawAllocations) {
    const source = rawAllocations && typeof rawAllocations === 'object' && !Array.isArray(rawAllocations)
        ? rawAllocations
        : {};

    return Object.fromEntries(
        Object.entries(source)
            .map(([typeId, quantity]) => [String(Number(typeId) || 0), Math.max(0, Math.floor(Number(quantity) || 0))])
            .filter(([typeId, quantity]) => typeId !== '0' && quantity > 0)
    );
}

function getCraftCharacterStockSnapshot() {
    const snapshot = window.BLUEPRINT_DATA?.character_stock_snapshot;
    return snapshot && typeof snapshot === 'object'
        ? snapshot
        : { totals_by_type: {}, characters: [], scope_missing: false, synced_at: '' };
}

function getCraftStockAllocations() {
    window.craftBPFlags = window.craftBPFlags || {};
    const normalized = normalizeCraftStockAllocations(
        window.craftBPFlags.stockAllocations
        || window.craftBPFlags.restoredSessionState?.stockAllocations
        || window.BLUEPRINT_DATA?.workspace_state?.stockAllocations
    );
    window.craftBPFlags.stockAllocations = normalized;
    return normalized;
}

function getCraftNormalizedStockAllocationsForCurrentPlan() {
    const requirements = new Map(
        getCraftSourceRequirementRows().map((row) => [String(Number(row.typeId) || 0), Math.max(0, Math.ceil(Number(row.quantity) || 0))])
    );
    const normalized = {};

    Object.entries(getCraftStockAllocations()).forEach(([typeId, quantity]) => {
        const requiredQty = requirements.get(typeId) || 0;
        const availableQty = getCraftAvailableStockQty(typeId);
        const clampedQty = Math.min(requiredQty, availableQty, Math.max(0, Math.floor(Number(quantity) || 0)));
        if (typeId !== '0' && clampedQty > 0) {
            normalized[typeId] = clampedQty;
        }
    });

    window.craftBPFlags.stockAllocations = normalized;
    return normalized;
}

function getCraftAvailableStockQty(typeId) {
    const totalsByType = getCraftCharacterStockSnapshot()?.totals_by_type;
    if (!totalsByType || typeof totalsByType !== 'object') {
        return 0;
    }
    return Math.max(0, Math.floor(Number(totalsByType[String(Number(typeId) || 0)] || 0)));
}

function getCraftStockCharacterBreakdown(typeId) {
    const normalizedTypeId = String(Number(typeId) || 0);
    return (Array.isArray(getCraftCharacterStockSnapshot()?.characters) ? getCraftCharacterStockSnapshot().characters : [])
        .map((character) => ({
            characterId: Number(character?.character_id || 0) || 0,
            characterName: String(character?.character_name || character?.character_id || ''),
            quantity: Math.max(0, Math.floor(Number(character?.items_by_type?.[normalizedTypeId] || 0))),
        }))
        .filter((entry) => entry.characterId > 0 && entry.quantity > 0);
}

function getCraftStockAllocationSummary(typeId, requiredQty) {
    const normalizedRequiredQty = Math.max(0, Math.ceil(Number(requiredQty) || 0));
    const availableQty = getCraftAvailableStockQty(typeId);
    const requestedQty = Math.max(0, Math.floor(Number(getCraftNormalizedStockAllocationsForCurrentPlan()[String(Number(typeId) || 0)] || 0)));
    const allocatedQty = Math.min(normalizedRequiredQty, availableQty, requestedQty);
    return {
        requiredQty: normalizedRequiredQty,
        availableQty,
        requestedQty,
        allocatedQty,
        remainingQty: Math.max(0, normalizedRequiredQty - allocatedQty),
        characters: getCraftStockCharacterBreakdown(typeId),
    };
}

function setCraftStockAllocation(typeId, quantity, options = {}) {
    const normalizedTypeId = String(Number(typeId) || 0);
    if (normalizedTypeId === '0') {
        return;
    }

    const normalizedQuantity = Math.max(0, Math.floor(Number(quantity) || 0));
    const nextAllocations = { ...getCraftStockAllocations() };
    if (normalizedQuantity > 0) {
        nextAllocations[normalizedTypeId] = normalizedQuantity;
    } else {
        delete nextAllocations[normalizedTypeId];
    }
    window.craftBPFlags.stockAllocations = normalizeCraftStockAllocations(nextAllocations);

    if (options.refresh !== false) {
        if (typeof updateFinancialTabFromState === 'function') {
            updateFinancialTabFromState();
        }
        if (typeof updateStockManagementTabFromState === 'function') {
            updateStockManagementTabFromState(true);
        }
        if (typeof updateNeededTabFromState === 'function') {
            updateNeededTabFromState(true);
        }
        if (typeof recalcFinancials === 'function') {
            recalcFinancials();
        }
    }

    if (options.persist !== false) {
        persistCraftPageSessionState();
    }
}

function getCraftSourceRequirementRows() {
    const api = window.SimulationAPI;
    if (!api || typeof api.getFinancialItems !== 'function') {
        return [];
    }

    // NOTE: Items returned by getFinancialItems() are leafNeeds + buyCraftables.
    // Final-outputs are only present here when the user explicitly toggled them
    // to 'buy' mode (e.g. project workspace items set to all-buy). They MUST
    // appear as buy rows so their purchase cost is summed into Total Material
    // Cost. They will also remain as revenue rows (final-outputs are rendered
    // separately) which is correct: cost = buy price, revenue = sale price.
    const aggregated = new Map();
    const items = api.getFinancialItems() || [];

    items.forEach((item) => {
        const typeId = Number(item.typeId ?? item.type_id) || 0;
        if (!typeId) {
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
            marketGroup: item.marketGroup || item.market_group || '',
        };
        existing.quantity += quantity;
        if (!existing.marketGroup && (item.marketGroup || item.market_group)) {
            existing.marketGroup = item.marketGroup || item.market_group || '';
        }
        aggregated.set(typeId, existing);
    });

    const ordering = getDashboardMaterialsOrdering();
    return Array.from(aggregated.values()).sort((a, b) => {
        const typeA = Number(a.typeId) || 0;
        const typeB = Number(b.typeId) || 0;

        const dashboardA = ordering.itemOrder.get(typeA);
        const dashboardB = ordering.itemOrder.get(typeB);
        const groupA = a.marketGroup || ordering.fallbackGroupName;
        const groupB = b.marketGroup || ordering.fallbackGroupName;

        const groupIdxA = dashboardA ? dashboardA.groupIdx : (ordering.groupOrder.has(groupA) ? ordering.groupOrder.get(groupA) : Number.POSITIVE_INFINITY);
        const groupIdxB = dashboardB ? dashboardB.groupIdx : (ordering.groupOrder.has(groupB) ? ordering.groupOrder.get(groupB) : Number.POSITIVE_INFINITY);
        if (groupIdxA !== groupIdxB) {
            return groupIdxA - groupIdxB;
        }

        const itemIdxA = dashboardA ? dashboardA.itemIdx : Number.POSITIVE_INFINITY;
        const itemIdxB = dashboardB ? dashboardB.itemIdx : Number.POSITIVE_INFINITY;
        if (itemIdxA !== itemIdxB) {
            return itemIdxA - itemIdxB;
        }

        const groupCmp = String(groupA).localeCompare(String(groupB), undefined, { sensitivity: 'base' });
        if (groupCmp !== 0) {
            return groupCmp;
        }
        return String(a.typeName).localeCompare(String(b.typeName), undefined, { sensitivity: 'base' });
    });
}

function collectCraftStockAllocationsFromDom() {
    const nextAllocations = {};
    document.querySelectorAll('.craft-stock-allocation-input[data-type-id]').forEach((input) => {
        const typeId = String(Number(input.getAttribute('data-type-id')) || 0);
        const quantity = Math.max(0, Math.floor(Number(input.value) || 0));
        if (typeId !== '0' && quantity > 0) {
            nextAllocations[typeId] = quantity;
        }
    });
    return normalizeCraftStockAllocations(nextAllocations);
}

function collectFuzzworkPriceSnapshot() {
    const snapshot = {};
    const prices = getSimulationPricesMap();

    prices.forEach((value, key) => {
        const typeId = Number(key) || 0;
        if (!(typeId > 0)) {
            return;
        }
        snapshot[String(typeId)] = Number(value?.fuzzwork) || 0;
    });

    return snapshot;
}

function applyManualPriceOverrides(overrides) {
    (Array.isArray(overrides) ? overrides : []).forEach((entry) => {
        const typeId = Number(entry?.typeId || entry?.type_id || 0) || 0;
        const priceType = String(entry?.priceType || entry?.price_type || '').trim();
        if (!(typeId > 0) || (priceType !== 'real' && priceType !== 'sale')) {
            return;
        }

        const selector = priceType === 'sale'
            ? `.sale-price-unit[data-type-id="${typeId}"]`
            : `.real-price[data-type-id="${typeId}"]`;
        const input = document.querySelector(selector);
        if (!input) {
            return;
        }

        const value = Number.parseFloat(entry?.value) || 0;
        input.value = value.toFixed(2);
        updatePriceInputManualState(input, true);
        if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function') {
            window.SimulationAPI.setPrice(typeId, priceType, value);
        }
    });
}

function collectStructureAssignments() {
    if (!window.SimulationAPI || typeof window.SimulationAPI.getState !== 'function') {
        return [];
    }

    const state = window.SimulationAPI.getState() || {};
    return Array.from(mapLikeToMap(state.structureAssignments).entries())
        .map(([typeId, structureId]) => ({
            typeId: Number(typeId) || 0,
            structureId: Number(structureId) || 0,
        }))
        .filter((entry) => entry.typeId > 0 && entry.structureId > 0);
}

function applyStructureAssignments(assignments) {
    if (!window.SimulationAPI || typeof window.SimulationAPI.setStructureAssignment !== 'function') {
        return;
    }

    (Array.isArray(assignments) ? assignments : []).forEach((entry) => {
        const typeId = Number(entry?.typeId || entry?.type_id || 0) || 0;
        const structureId = Number(entry?.structureId || entry?.structure_id || 0) || 0;
        if (typeId > 0 && structureId > 0) {
            window.SimulationAPI.setStructureAssignment(typeId, structureId);
        }
    });
}

function isConfigurePaneHydrated() {
    const pane = document.getElementById('configure-pane');
    return Boolean(pane && pane.dataset.configHydrated === 'true');
}

function buildDefaultMETEConfig() {
    const config = {
        mainME: 0,
        mainTE: 0,
        blueprintConfigs: {}
    };
    const currentBpId = Number(getCurrentBlueprintTypeId() || 0) || 0;
    const blueprintConfigs = Array.isArray(window.BLUEPRINT_DATA?.page?.blueprint_configs)
        ? window.BLUEPRINT_DATA.page.blueprint_configs
        : [];

    blueprintConfigs.forEach((bp) => {
        const typeId = Number(bp?.type_id || 0) || 0;
        if (!(typeId > 0)) {
            return;
        }

        const me = Math.max(0, Math.min(parseInt(bp?.material_efficiency, 10) || 0, 10));
        const te = Math.max(0, Math.min(parseInt(bp?.time_efficiency, 10) || 0, 20));
        config.blueprintConfigs[String(typeId)] = { me, te };

        if (typeId === currentBpId) {
            config.mainME = me;
            config.mainTE = te;
        }
    });

    return config;
}

function normalizeMETEConfig(rawConfig) {
    const fallback = buildDefaultMETEConfig();
    const source = rawConfig && typeof rawConfig === 'object' ? rawConfig : {};
    const normalized = {
        mainME: fallback.mainME,
        mainTE: fallback.mainTE,
        blueprintConfigs: { ...fallback.blueprintConfigs }
    };
    const currentBpId = Number(getCurrentBlueprintTypeId() || 0) || 0;

    Object.entries(source.blueprintConfigs || {}).forEach(([typeId, bpConfig]) => {
        normalized.blueprintConfigs[String(typeId)] = {
            me: Math.max(0, Math.min(parseInt(bpConfig?.me, 10) || 0, 10)),
            te: Math.max(0, Math.min(parseInt(bpConfig?.te, 10) || 0, 20)),
        };
    });

    if (currentBpId > 0 && normalized.blueprintConfigs[String(currentBpId)]) {
        normalized.mainME = normalized.blueprintConfigs[String(currentBpId)].me;
        normalized.mainTE = normalized.blueprintConfigs[String(currentBpId)].te;
    } else {
        normalized.mainME = Math.max(0, Math.min(parseInt(source.mainME, 10) || normalized.mainME || 0, 10));
        normalized.mainTE = Math.max(0, Math.min(parseInt(source.mainTE, 10) || normalized.mainTE || 0, 20));
    }

    return normalized;
}

function applyMETEConfigToInputs(config) {
    if (!isConfigurePaneHydrated()) {
        return false;
    }

    const normalized = normalizeMETEConfig(config);
    Object.entries(normalized.blueprintConfigs || {}).forEach(([typeId, bpConfig]) => {
        const meInput = document.querySelector(`#configure-pane input[name="me_${typeId}"]`);
        const teInput = document.querySelector(`#configure-pane input[name="te_${typeId}"]`);
        if (meInput) {
            meInput.value = String(bpConfig.me);
        }
        if (teInput) {
            teInput.value = String(bpConfig.te);
        }
    });

    return true;
}

function applyDeferredCraftBlueprintInputState() {
    window.craftBPFlags = window.craftBPFlags || {};
    if (!isConfigurePaneHydrated()) {
        return false;
    }

    if (window.craftBPFlags.pendingMETEConfig) {
        applyMETEConfigToInputs(window.craftBPFlags.pendingMETEConfig);
    }

    if (Array.isArray(window.craftBPFlags.pendingBlueprintCopyRequests)) {
        applyBlueprintCopyRequestState(window.craftBPFlags.pendingBlueprintCopyRequests);
    }

    return true;
}

function collectBlueprintCopyRequestState() {
    if (!isConfigurePaneHydrated()) {
        return Array.isArray(window.craftBPFlags?.pendingBlueprintCopyRequests)
            ? window.craftBPFlags.pendingBlueprintCopyRequests
            : [];
    }

    return Array.from(document.querySelectorAll('select[id^="bpCopySelect"]')).map((select) => {
        const match = String(select.id || '').match(/^bpCopySelect(\d+)$/);
        const typeId = match ? (Number(match[1]) || 0) : 0;
        if (!(typeId > 0)) {
            return null;
        }

        const runsInput = document.getElementById(`bpRunsRequested${typeId}`);
        const copiesInput = document.getElementById(`bpCopiesRequested${typeId}`);
        return {
            typeId,
            selectValue: String(select.value || ''),
            runs: Math.max(1, parseInt(runsInput?.value || '1', 10) || 1),
            copies: Math.max(1, parseInt(copiesInput?.value || '1', 10) || 1),
        };
    }).filter(Boolean);
}

function applyBlueprintCopyRequestState(items) {
    window.craftBPFlags = window.craftBPFlags || {};
    window.craftBPFlags.pendingBlueprintCopyRequests = Array.isArray(items) ? items : [];

    if (!isConfigurePaneHydrated()) {
        return;
    }

    (Array.isArray(items) ? items : []).forEach((entry) => {
        const typeId = Number(entry?.typeId || entry?.type_id || 0) || 0;
        if (!(typeId > 0)) {
            return;
        }

        const select = document.getElementById(`bpCopySelect${typeId}`);
        const runsInput = document.getElementById(`bpRunsRequested${typeId}`);
        const copiesInput = document.getElementById(`bpCopiesRequested${typeId}`);
        if (select && entry?.selectValue != null) {
            select.value = String(entry.selectValue);
        }
        if (runsInput) {
            runsInput.value = String(Math.max(1, parseInt(entry?.runs, 10) || 1));
        }
        if (copiesInput) {
            copiesInput.value = String(Math.max(1, parseInt(entry?.copies, 10) || 1));
        }
    });
}

function collectCraftPageSessionState() {
    return {
        buyTypeIds: getCurrentBuyTypeIds(),
        stockAllocations: getCraftNormalizedStockAllocationsForCurrentPlan(),
        runs: Math.max(1, parseInt(document.getElementById('runsInput')?.value || '1', 10) || 1),
        activeBlueprintTab: getCurrentActiveBlueprintTab() || 'materials',
        manualPrices: collectManualPriceOverrides(),
        fuzzworkPrices: collectFuzzworkPriceSnapshot(),
        simulationName: String(document.getElementById('simulationName')?.value || ''),
        decisionBuyTolerance: String(document.getElementById('decisionBuyToleranceInput')?.value || ''),
        meTeConfig: getCurrentMETEConfig(),
        copyRequests: collectBlueprintCopyRequestState(),
        structure: {
            motherSystemInput: String(document.getElementById('structureMotherSystemInput')?.value || ''),
            selectedSolarSystemId: Number(structureMotherSystemState.selectedSolarSystemId || 0) || null,
            selectedSolarSystemName: String(structureMotherSystemState.selectedSolarSystemName || ''),
            assignments: collectStructureAssignments(),
        },
        pendingWorkspaceRefresh: Boolean(window.craftBPFlags?.hasPendingWorkspaceRefresh || window.craftBPFlags?.hasPendingMETEChanges),
        pendingWorkspaceSourceTab: String(window.craftBPFlags?.pendingWorkspaceSourceTab || ''),
        updatedAt: Date.now(),
    };
}

function applyCraftPageSessionState(parsedState) {
    if (!parsedState || typeof parsedState !== 'object') {
        return false;
    }

    const buyTypeIds = Array.isArray(parsedState?.buyTypeIds) ? parsedState.buyTypeIds : [];
    craftBPDebugLog('[CraftCache] Applying craft tree session state', parsedState);

    window.craftBPFlags = window.craftBPFlags || {};
    window.craftBPFlags.restoringSessionState = true;
    window.craftBPFlags.restoredSessionState = parsedState;
    window.craftBPFlags.pendingBuyTypeIds = buyTypeIds;
    window.craftBPFlags.stockAllocations = normalizeCraftStockAllocations(parsedState?.stockAllocations);

    applyCraftPageRunsValue(parsedState?.runs);
    if (parsedState?.meTeConfig) {
        window.craftBPFlags.pendingMETEConfig = normalizeMETEConfig(parsedState.meTeConfig);
        applyMETEConfigToInputs(window.craftBPFlags.pendingMETEConfig);
    }
    applyBuyCraftStateFromBuyDecisions(buyTypeIds, {
        refreshTabs: false,
        refreshSimulation: false,
    });
    if (parsedState?.activeBlueprintTab) {
        if (parsedState.activeBlueprintTab === 'financial') {
            showCraftMainTab('buy');
        } else if (parsedState.activeBlueprintTab === 'stock') {
            showCraftMainTab('stock');
        } else if (parsedState.activeBlueprintTab === 'cycles') {
            showCraftMainTab('build');
        }
        showBlueprintSubTab(parsedState.activeBlueprintTab);
    }
    applyManualPriceOverrides(parsedState?.manualPrices);

    const simulationNameInput = document.getElementById('simulationName');
    if (simulationNameInput && parsedState?.simulationName != null) {
        simulationNameInput.value = String(parsedState.simulationName);
    }

    const decisionToleranceInput = document.getElementById('decisionBuyToleranceInput');
    if (decisionToleranceInput && parsedState?.decisionBuyTolerance != null) {
        decisionToleranceInput.value = String(parsedState.decisionBuyTolerance);
    }

    applyBlueprintCopyRequestState(parsedState?.copyRequests);

    const structureInput = document.getElementById('structureMotherSystemInput');
    if (structureInput && parsedState?.structure?.motherSystemInput != null) {
        structureInput.value = String(parsedState.structure.motherSystemInput);
    }

    if (parsedState?.structure) {
        structureMotherSystemState.selectedSolarSystemId = Number(parsedState.structure.selectedSolarSystemId || 0) || null;
        structureMotherSystemState.selectedSolarSystemName = String(parsedState.structure.selectedSolarSystemName || parsedState.structure.motherSystemInput || '');
        applyStructureAssignments(parsedState.structure.assignments);
    }

    if (window.SimulationAPI && typeof window.SimulationAPI.refreshFromDom === 'function') {
        window.SimulationAPI.refreshFromDom();
    }

    const payloadWorkspaceState = window.BLUEPRINT_DATA?.workspace_state;
    const shouldRestorePendingWorkspaceRefresh = Boolean(
        parsedState?.pendingWorkspaceRefresh
        && payloadWorkspaceState?.pendingWorkspaceRefresh
    );

    if (shouldRestorePendingWorkspaceRefresh) {
        window.craftBPFlags.hasPendingWorkspaceRefresh = true;
        window.craftBPFlags.hasPendingMETEChanges = true;
        window.craftBPFlags.pendingWorkspaceSourceTab = String(parsedState.pendingWorkspaceSourceTab || '');
    } else {
        window.craftBPFlags.hasPendingWorkspaceRefresh = false;
        window.craftBPFlags.hasPendingMETEChanges = false;
        delete window.craftBPFlags.pendingWorkspaceSourceTab;
        delete window.craftBPFlags.pendingWorkspaceTargetTab;
    }
    updatePendingWorkspaceRefreshNotice();

    window.craftBPFlags.restoringSessionState = false;
    return true;
}

function applyBuyCraftStateFromBuyDecisions(buyDecisions, options = {}) {
    const shouldRefreshSimulation = options.refreshSimulation !== false;
    const shouldRefreshTabs = options.refreshTabs !== false;
    const normalizedBuyDecisions = new Set(
        (Array.isArray(buyDecisions) ? buyDecisions : [])
            .map((typeId) => String(typeId || '').trim())
            .filter((typeId) => typeId)
    );

    document.querySelectorAll('.mat-switch').forEach(function (switchEl) {
        if (switchEl.dataset.fixedMode === 'useless') {
            switchEl.dataset.userState = 'useless';
            switchEl.checked = false;
            updateSwitchLabel(switchEl);
            return;
        }

        const typeId = String(switchEl.getAttribute('data-type-id') || '').trim();
        const isBuy = typeId && normalizedBuyDecisions.has(typeId);
        switchEl.dataset.userState = isBuy ? 'buy' : 'prod';
        switchEl.checked = !isBuy;
        updateSwitchLabel(switchEl);
    });

    if (typeof window.refreshTreeSwitchHierarchy === 'function') {
        window.refreshTreeSwitchHierarchy();
    }
    if (shouldRefreshSimulation && window.SimulationAPI && typeof window.SimulationAPI.refreshFromDom === 'function') {
        window.SimulationAPI.refreshFromDom();
    }
    if (shouldRefreshTabs) {
        refreshTabsAfterStateChange(options.refreshTabOptions || {});
    }
}

function restoreCraftPageSessionState() {
    const storageKey = getCraftPageSessionStorageKey();

    if (!isCraftPageReloadNavigation()) {
        withCraftPageSessionStorage((storage) => {
            storage.removeItem(storageKey);
            storage.removeItem(getCraftPageMETEStorageKey());
        });
        return false;
    }

    try {
        const rawState = withCraftPageSessionStorage((storage) => storage.getItem(storageKey));
        if (!rawState) {
            return false;
        }
        const parsedState = JSON.parse(rawState);
        return applyCraftPageSessionState(parsedState);
    } catch (error) {
        console.error('[IndyHub] Failed to restore craft page session state', error);
        if (window.craftBPFlags) {
            window.craftBPFlags.restoringSessionState = false;
        }
        return false;
    }
}

function persistCraftPageSessionState() {
    try {
        if (window.craftBPFlags?.restoringSessionState) {
            return;
        }

        const sessionState = collectCraftPageSessionState();

        withCraftPageSessionStorage((storage) => {
            storage.setItem(
                getCraftPageSessionStorageKey(),
                JSON.stringify(sessionState)
            );
        });

        document.dispatchEvent(new CustomEvent('CraftBP:sessionStateChanged', {
            detail: {
                state: sessionState,
            }
        }));
    } catch (error) {
        craftBPDebugLog('[CraftCache] Failed to persist craft page session state', error);
    }
}

function initializeCraftPageSessionPersistence() {
    const persistOnChange = () => persistCraftPageSessionState();

    const runsInput = document.getElementById('runsInput');
    if (runsInput && runsInput.dataset.sessionPersistenceAttached !== 'true') {
        runsInput.addEventListener('input', persistOnChange);
        runsInput.addEventListener('change', persistOnChange);
        runsInput.dataset.sessionPersistenceAttached = 'true';
    }

    const simulationNameInput = document.getElementById('simulationName');
    if (simulationNameInput && simulationNameInput.dataset.sessionPersistenceAttached !== 'true') {
        simulationNameInput.addEventListener('input', persistOnChange);
        simulationNameInput.addEventListener('change', persistOnChange);
        simulationNameInput.dataset.sessionPersistenceAttached = 'true';
    }

    const decisionToleranceInput = document.getElementById('decisionBuyToleranceInput');
    if (decisionToleranceInput && decisionToleranceInput.dataset.sessionPersistenceAttached !== 'true') {
        decisionToleranceInput.addEventListener('input', persistOnChange);
        decisionToleranceInput.addEventListener('change', persistOnChange);
        decisionToleranceInput.dataset.sessionPersistenceAttached = 'true';
    }

    if (document.body && document.body.dataset.blueprintSessionPersistenceAttached !== 'true') {
        document.addEventListener('input', (event) => {
            const target = event.target;
            if (!target || !target.id) {
                return;
            }
            if (/^(bpCopySelect|bpRunsRequested|bpCopiesRequested)\d+$/.test(target.id)) {
                persistOnChange();
            }
        }, true);
        document.addEventListener('change', (event) => {
            const target = event.target;
            if (!target || !target.id) {
                return;
            }
            if (/^(bpCopySelect|bpRunsRequested|bpCopiesRequested)\d+$/.test(target.id)) {
                persistOnChange();
            }
        }, true);
        document.body.dataset.blueprintSessionPersistenceAttached = 'true';
    }

    document.querySelectorAll('#bpTabs .nav-link').forEach((button) => {
        if (button.dataset.sessionPersistenceAttached === 'true') {
            return;
        }
        button.addEventListener('shown.bs.tab', persistOnChange);
        button.dataset.sessionPersistenceAttached = 'true';
    });
}

/**
 * Public API for configuration
 */
window.CraftBP = {
    init: function(config) {
        CRAFT_BP.fuzzworkUrl = config.fuzzworkPriceUrl;
        CRAFT_BP.productTypeId = config.productTypeId;

        // Initialize financial calculations after configuration
        return initializeFinancialCalculations();
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

    persistSessionState: function() {
        persistCraftPageSessionState();
    },

    recalculate: function(options = {}) {
        return recalculateBlueprintWorkspace(options);
    },

    queueRecalculate: function(options = {}) {
        return queueBlueprintWorkspaceRecalculation(options);
    },

    markPendingWorkspaceRefresh: function(sourceTabName = '') {
        markPendingWorkspaceRefresh({ sourceTabName });
        persistCraftPageSessionState();
    },

    clearPendingWorkspaceRefresh: function() {
        clearPendingWorkspaceRefresh();
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
    },

    collectSessionState: function() {
        return collectCraftPageSessionState();
    },

    applySessionState: function(state) {
        return applyCraftPageSessionState(state);
    }
};

/**
 * Initialize the application
 */
document.addEventListener('DOMContentLoaded', function() {
    if (window.CraftBPLoading && typeof window.CraftBPLoading.stepBootstrap === 'function') {
        window.CraftBPLoading.stepBootstrap('loading-interface', {
            detail: __('Restoring your session and preparing the workspace interface.'),
            message: __('Preparing production workspace'),
        });
    }

    // Capture the initial dashboard Materials ordering before any UI updates replace the markup.
    try {
        getDashboardMaterialsOrdering();
    } catch (e) {
        // ignore
    }

    initializeBlueprintIcons();
    initializeCollapseHandlers();
    initializeBuyCraftSwitches();
    initializeDecisionStrategyTab();
    initializeCraftPageSessionPersistence();
    if (!restoreCraftPageSessionState()) {
        restoreBuyCraftStateFromURL();
    }
    initializeStructureMotherSystemControls();
    if (window.CraftBPLoading && typeof window.CraftBPLoading.stepBootstrap === 'function') {
        window.CraftBPLoading.stepBootstrap('loading-interface', {
            detail: __('Opening the main workspace view.'),
            message: __('Loading workspace...'),
        });
    }
    hydrateVisibleCraftStartupTab();
    persistCraftPageSessionState();
    syncCraftBrowserUrl();
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
    const switchRoot = document.getElementById('decisionStrategySection') || document.getElementById('tab-tree');
    if (!switchRoot) {
        return;
    }

    if (switchRoot.dataset.switchesInitialized === 'true') {
        refreshTreeSwitchHierarchy();
        return;
    }
    switchRoot.dataset.switchesInitialized = 'true';

    window.refreshTreeSwitchHierarchy = refreshTreeSwitchHierarchy;

    const switches = getDecisionSwitchElements();
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

    switchRoot.addEventListener('change', handleTreeSwitchChange, true);
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
    getDecisionSwitchElements().forEach(applyParentLockState);
    updateTreeModeBadges();
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
    const explicitAncestorIds = String(switchEl?.dataset?.ancestorIds || '').split(',').map((entry) => Number(entry) || 0).filter((entry) => entry > 0);
    if (explicitAncestorIds.length > 0) {
        return explicitAncestorIds.reduce((count, ancestorId) => {
            const ancestorSwitch = document.querySelector(`input.mat-switch[data-type-id="${ancestorId}"]`);
            if (!ancestorSwitch) {
                return count;
            }
            const ancestorMode = ancestorSwitch.dataset.fixedMode;
            const ancestorForced = ancestorSwitch.dataset.lockedByParent === 'true';
            const ancestorIsBuy = (!ancestorSwitch.checked) || ancestorMode === 'useless';
            return (ancestorIsBuy || ancestorForced) ? count + 1 : count;
        }, 0);
    }

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
    const switches = getDecisionSwitchElements();
    if (switches.length === 0) {
        return;
    }

    const desiredState = mode === 'buy' ? 'buy' : 'prod';

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

let decisionStrategyPanelPromise = null;

function getDecisionBuyToleranceValue() {
    const input = document.getElementById('decisionBuyToleranceInput');
    const value = Number(input?.value || 0);
    return Number.isFinite(value) ? Math.max(0, value) : 0;
}

function getCraftDecisionBuyUnitPrice(typeId) {
    const state = (typeof window.SimulationAPI?.getState === 'function') ? window.SimulationAPI.getState() : null;
    const prices = state && state.prices ? state.prices : null;
    const record = prices && (prices instanceof Map ? prices.get(Number(typeId)) : prices[Number(typeId)]);
    const real = record ? (Number(record.real) || 0) : 0;
    if (real > 0) return real;
    const fuzz = record ? (Number(record.fuzzwork) || 0) : 0;
    if (fuzz > 0) return fuzz;
    return 0;
}

function getCraftDecisionSellUnitPrice(typeId) {
    const state = (typeof window.SimulationAPI?.getState === 'function') ? window.SimulationAPI.getState() : null;
    const prices = state && state.prices ? state.prices : null;
    const record = prices && (prices instanceof Map ? prices.get(Number(typeId)) : prices[Number(typeId)]);
    const sale = record ? (Number(record.sale) || 0) : 0;
    if (sale > 0) return sale;
    const fuzz = record ? (Number(record.fuzzwork) || 0) : 0;
    if (fuzz > 0) return fuzz;
    const real = record ? (Number(record.real) || 0) : 0;
    if (real > 0) return real;
    return 0;
}

function getCraftDecisionBuyUnitPriceOrInf(typeId) {
    const price = getCraftDecisionBuyUnitPrice(typeId);
    return price > 0 ? price : Number.POSITIVE_INFINITY;
}

async function ensureCraftDecisionPricesLoaded(nodes, options = {}) {
    if (!window.SimulationAPI || typeof window.SimulationAPI.setPrice !== 'function') {
        return false;
    }

    const allTypeIds = Array.from(collectTypeIdsFromMaterialsTree(nodes || []));
    if (!allTypeIds.length || typeof fetchAllPrices !== 'function') {
        return false;
    }

    const buttonIds = Array.isArray(options.buttonIds) ? options.buttonIds : [];
    const buttons = buttonIds
        .map((id) => document.getElementById(id))
        .filter(Boolean);

    buttons.forEach((button) => {
        button.disabled = true;
    });

    try {
        const prices = await fetchAllPrices(allTypeIds);
        allTypeIds.forEach((typeId) => {
            const raw = prices[typeId] ?? prices[String(parseInt(typeId, 10))];
            const price = raw != null ? (parseFloat(raw) || 0) : 0;
            if (price > 0) {
                window.SimulationAPI.setPrice(typeId, 'fuzzwork', price);
            }
        });

        if (typeof window.SimulationAPI.refreshFromDom === 'function') {
            window.SimulationAPI.refreshFromDom();
        }

        return true;
    } catch (error) {
        if (window.CraftBP && typeof window.CraftBP.pushStatus === 'function') {
            window.CraftBP.pushStatus(__('Failed to load prices for the decision center'), 'warning');
        }
        return false;
    } finally {
        buttons.forEach((button) => {
            button.disabled = false;
        });
    }
}

async function computeCraftDecisionAnalysis(options = {}) {
    const tree = window.BLUEPRINT_DATA?.materials_tree;
    if (!Array.isArray(tree) || tree.length === 0) {
        return { error: __('No production tree to analyze'), rows: [] };
    }

    if (!window.SimulationAPI || typeof window.SimulationAPI.getPrice !== 'function' || typeof window.SimulationAPI.setPrice !== 'function') {
        return { error: __('Prices are not ready yet'), rows: [] };
    }

    if (typeof window.SimulationAPI.refreshFromDom === 'function') {
        window.SimulationAPI.refreshFromDom();
    }

    if (options.ensurePrices) {
        await ensureCraftDecisionPricesLoaded(tree, {
            buttonIds: ['optimize-profit', 'decisionStrategyRefreshBtn'],
        });
    }

    const toleranceISK = Number.isFinite(Number(options.toleranceISK))
        ? Math.max(0, Number(options.toleranceISK))
        : getDecisionBuyToleranceValue();
    const productTypeId = Number(CRAFT_BP.productTypeId) || 0;
    const occurrencesByType = new Map();
    const nameByType = new Map();
    const depthByType = new Map();
    const ancestorIdsByType = new Map();

    function readTypeId(node) {
        return Number(node?.type_id || node?.typeId) || 0;
    }

    function readQty(node) {
        const quantity = Number(node?.quantity ?? node?.qty ?? 0);
        return Number.isFinite(quantity) ? Math.max(0, Math.ceil(quantity)) : 0;
    }

    function readProducedPerCycle(node) {
        const producedPerCycle = Number(node?.produced_per_cycle ?? node?.producedPerCycle ?? 0);
        return Number.isFinite(producedPerCycle) ? Math.max(0, Math.ceil(producedPerCycle)) : 0;
    }

    function cloneDecisionNodeWithQuantity(node, quantity) {
        return Object.assign({}, node, { quantity: Math.max(0, Math.ceil(Number(quantity) || 0)) });
    }

    function getDecisionStructureMaterialBonusPercent(typeId) {
        if (!window.SimulationAPI || typeof window.SimulationAPI.getStructureOption !== 'function') {
            return 0;
        }
        const option = window.SimulationAPI.getStructureOption(typeId);
        if (!option) {
            return 0;
        }
        const bonus = Number(
            option.material_bonus_percent
            ?? option.materialBonusPercent
            ?? option.rig_material_bonus_percent
            ?? option.rigMaterialBonusPercent
            ?? 0
        );
        return Number.isFinite(bonus) && bonus > 0 ? bonus : 0;
    }

    function adjustDecisionChildrenForStructure(children, parentTypeId) {
        if (!Array.isArray(children) || children.length === 0) {
            return [];
        }
        const materialBonusPercent = getDecisionStructureMaterialBonusPercent(parentTypeId);
        if (!(materialBonusPercent > 0)) {
            return children;
        }
        const multiplier = Math.max(0, 1 - (materialBonusPercent / 100));
        return children.map((child) => {
            const quantity = readQty(child);
            const materialBonusApplicable = child?.material_bonus_applicable ?? child?.materialBonusApplicable;
            if (materialBonusApplicable === false) {
                return cloneDecisionNodeWithQuantity(child, quantity);
            }
            return cloneDecisionNodeWithQuantity(child, Math.ceil(quantity * multiplier));
        });
    }

    function readDecisionChildren(node) {
        return adjustDecisionChildrenForStructure(
            readMaterialsTreeChildren(node),
            readTypeId(node)
        );
    }

    const analysisTree = productTypeId > 0
        ? adjustDecisionChildrenForStructure(tree, productTypeId)
        : tree;

    (function collectOccurrences(nodes, ancestorIds = []) {
        (Array.isArray(nodes) ? nodes : []).forEach((node) => {
            const typeId = readTypeId(node);
            const children = readDecisionChildren(node);
            const typeName = String(node?.type_name || node?.typeName || '');

            if (typeId && typeName && !nameByType.has(typeId)) {
                nameByType.set(typeId, typeName);
            }

            if (typeId && children.length > 0) {
                if (!occurrencesByType.has(typeId)) {
                    occurrencesByType.set(typeId, []);
                }
                occurrencesByType.get(typeId).push(node);
                if (!ancestorIdsByType.has(typeId)) {
                    ancestorIdsByType.set(typeId, ancestorIds.slice());
                    depthByType.set(typeId, ancestorIds.length);
                }
            }

            if (children.length > 0) {
                const nextAncestorIds = typeId ? ancestorIds.concat(typeId) : ancestorIds;
                collectOccurrences(children, nextAncestorIds);
            }
        });
    })(analysisTree);

    const recipes = new Map();
    occurrencesByType.forEach((nodes, typeId) => {
        let best = null;
        let bestCycles = 0;
        nodes.forEach((node) => {
            const producedPerCycle = readProducedPerCycle(node);
            const needed = readQty(node);
            if (!producedPerCycle || !needed) {
                return;
            }
            const cycles = Math.max(1, Math.ceil(needed / producedPerCycle));
            if (cycles >= bestCycles) {
                bestCycles = cycles;
                best = node;
            }
        });

        if (!best) {
            return;
        }

        const producedPerCycle = readProducedPerCycle(best);
        const needed = readQty(best);
        const cycles = Math.max(1, Math.ceil(needed / producedPerCycle));
        const inputsPerCycle = new Map();

        readDecisionChildren(best).forEach((child) => {
            const childTypeId = readTypeId(child);
            const childQty = readQty(child);
            if (!(childTypeId > 0) || !(childQty > 0)) {
                return;
            }
            inputsPerCycle.set(childTypeId, childQty / cycles);
        });

        recipes.set(typeId, { producedPerCycle, inputsPerCycle });
    });

    const craftables = new Set(recipes.keys());
    const currentDecisions = getCurrentDecisionsFromSimulationOrDom();
    const decisions = new Map(currentDecisions);
    const hiddenByParentBuyCache = new Map();
    const dashboardOrdering = typeof getDashboardMaterialsOrdering === 'function'
        ? getDashboardMaterialsOrdering()
        : { groupOrder: new Map(), itemOrder: new Map(), fallbackGroupName: __('Other') };
    const decisionMarketGroupMap = window.BLUEPRINT_DATA?.market_group_map || {};

    function getDecisionMarketGroup(typeId) {
        const numericTypeId = Number(typeId) || 0;
        if (!(numericTypeId > 0)) {
            return dashboardOrdering.fallbackGroupName || __('Other');
        }
        const info = decisionMarketGroupMap[String(numericTypeId)] || decisionMarketGroupMap[numericTypeId];
        if (info && typeof info === 'object') {
            return info.group_name || info.groupName || dashboardOrdering.fallbackGroupName || __('Other');
        }
        return dashboardOrdering.fallbackGroupName || __('Other');
    }

    function isHiddenByParentBuy(typeId) {
        const numericTypeId = Number(typeId) || 0;
        if (!(numericTypeId > 0)) {
            return false;
        }
        if (hiddenByParentBuyCache.has(numericTypeId)) {
            return hiddenByParentBuyCache.get(numericTypeId);
        }

        const ancestorIds = ancestorIdsByType.get(numericTypeId) || [];
        const hidden = ancestorIds.some((ancestorId) => {
            const ancestorMode = currentDecisions.get(ancestorId) || 'prod';
            return ancestorMode === 'buy' || ancestorMode === 'useless' || isHiddenByParentBuy(ancestorId);
        });

        hiddenByParentBuyCache.set(numericTypeId, hidden);
        return hidden;
    }

    function chooseMode({ buyTotal, prodTotal, currentMode }) {
        if (!Number.isFinite(prodTotal) && !Number.isFinite(buyTotal)) {
            return currentMode || 'prod';
        }
        if (!Number.isFinite(prodTotal)) {
            return 'buy';
        }
        if (!Number.isFinite(buyTotal)) {
            return 'prod';
        }
        if (buyTotal <= prodTotal + toleranceISK) {
            return 'buy';
        }
        return prodTotal < buyTotal ? 'prod' : 'buy';
    }

    function addDemandQuantity(map, typeId, quantity) {
        const numericTypeId = Number(typeId) || 0;
        const normalizedQty = Number.isFinite(Number(quantity)) ? Math.max(0, Math.ceil(Number(quantity))) : 0;
        if (!(numericTypeId > 0) || !(normalizedQty > 0)) {
            return;
        }
        map.set(numericTypeId, (map.get(numericTypeId) || 0) + normalizedQty);
    }

    function computeDemand(currentModes) {
        const demand = new Map();

        (function walk(nodes, blockedByBuyAncestor = false) {
            (Array.isArray(nodes) ? nodes : []).forEach((node) => {
                const typeId = readTypeId(node);
                if (!(typeId > 0) || blockedByBuyAncestor) {
                    return;
                }

                const quantity = readQty(node);
                if (!(quantity > 0)) {
                    return;
                }

                const children = readDecisionChildren(node);
                if (children.length === 0) {
                    return;
                }

                const state = currentModes.get(typeId) || 'prod';
                if (state === 'useless') {
                    return;
                }

                addDemandQuantity(demand, typeId, quantity);
                if (state === 'buy') {
                    return;
                }

                walk(children, false);
            });
        })(analysisTree, false);

        return demand;
    }

    function createScenarioEvaluator(currentModes) {
        const scenarioCache = new Map();

        function buildScenarioKey(typeId, needed, forcedMode) {
            return `${typeId}:${needed}:${forcedMode || 'auto'}`;
        }

        function evaluateType(typeId, needed, forcedMode = null) {
            const normalizedTypeId = Number(typeId) || 0;
            const normalizedNeeded = Number.isFinite(Number(needed)) ? Math.max(0, Math.ceil(Number(needed))) : 0;
            const scenarioKey = buildScenarioKey(normalizedTypeId, normalizedNeeded, forcedMode);
            if (scenarioCache.has(scenarioKey)) {
                return scenarioCache.get(scenarioKey);
            }

            const buyUnitRaw = getCraftDecisionBuyUnitPrice(normalizedTypeId);
            const buyUnit = getCraftDecisionBuyUnitPriceOrInf(normalizedTypeId);
            const buyTotal = normalizedNeeded > 0 && Number.isFinite(buyUnit)
                ? buyUnit * normalizedNeeded
                : Number.POSITIVE_INFINITY;
            const recipe = recipes.get(normalizedTypeId);
            const sellUnit = getCraftDecisionSellUnitPrice(normalizedTypeId);

            let result;
            if (!(normalizedNeeded > 0)) {
                result = {
                    typeId: normalizedTypeId,
                    needed: normalizedNeeded,
                    buyUnit: buyUnitRaw,
                    buyTotal: 0,
                    prodTotal: 0,
                    prodUnit: 0,
                    chosenMode: forcedMode || (currentModes.get(normalizedTypeId) || 'prod'),
                    chosenTotal: 0,
                    cycles: 0,
                    produced: 0,
                    surplus: 0,
                    surplusCredit: 0,
                };
                scenarioCache.set(scenarioKey, result);
                return result;
            }

            if (!recipe || !recipe.producedPerCycle) {
                const fallbackMode = Number.isFinite(buyTotal) ? 'buy' : (currentModes.get(normalizedTypeId) || 'prod');
                const chosenMode = forcedMode || fallbackMode;
                const chosenTotal = chosenMode === 'buy' ? buyTotal : Number.POSITIVE_INFINITY;
                result = {
                    typeId: normalizedTypeId,
                    needed: normalizedNeeded,
                    buyUnit: buyUnitRaw,
                    buyTotal: Number.isFinite(buyTotal) ? buyTotal : null,
                    prodTotal: null,
                    prodUnit: null,
                    chosenMode,
                    chosenTotal,
                    cycles: null,
                    produced: null,
                    surplus: null,
                    surplusCredit: null,
                };
                scenarioCache.set(scenarioKey, result);
                return result;
            }

            const cycles = Math.max(1, Math.ceil(normalizedNeeded / recipe.producedPerCycle));
            const produced = cycles * recipe.producedPerCycle;
            const surplus = Math.max(0, produced - normalizedNeeded);
            let inputsCost = 0;

            recipe.inputsPerCycle.forEach((perCycleQty, childId) => {
                const childQtyTotal = Math.max(0, Math.ceil((perCycleQty * cycles) - 1e-9));
                if (!(childQtyTotal > 0)) {
                    return;
                }
                const childScenario = evaluateType(childId, childQtyTotal, null);
                const childTotal = Number.isFinite(childScenario.chosenTotal)
                    ? childScenario.chosenTotal
                    : (getCraftDecisionBuyUnitPriceOrInf(childId) * childQtyTotal);
                inputsCost += childTotal;
            });

            const prodTotal = inputsCost;
            const prodUnit = normalizedNeeded > 0 ? (prodTotal / normalizedNeeded) : Number.POSITIVE_INFINITY;
            const autoMode = chooseMode({
                buyTotal,
                prodTotal,
                currentMode: currentModes.get(normalizedTypeId) || 'prod',
            });
            const chosenMode = forcedMode || autoMode;
            const chosenTotal = chosenMode === 'buy' ? buyTotal : prodTotal;

            result = {
                typeId: normalizedTypeId,
                needed: normalizedNeeded,
                buyUnit: buyUnitRaw,
                buyTotal: Number.isFinite(buyTotal) ? buyTotal : null,
                prodTotal: Number.isFinite(prodTotal) ? prodTotal : null,
                prodUnit: Number.isFinite(prodUnit) ? prodUnit : null,
                chosenMode,
                chosenTotal,
                cycles,
                produced,
                surplus,
                surplusCredit: Number.isFinite(sellUnit) && sellUnit > 0 ? (sellUnit * surplus) : 0,
            };
            scenarioCache.set(scenarioKey, result);
            return result;
        }

        return evaluateType;
    }

    function computeBestUnitCosts(demand, currentModes) {
        const bestUnitCost = new Map();
        const chosenMode = new Map();

        const evaluateScenario = createScenarioEvaluator(currentModes);
        demand.forEach((needed, typeId) => {
            if (!craftables.has(typeId) || !(needed > 0)) {
                return;
            }

            const scenario = evaluateScenario(typeId, needed, null);
            chosenMode.set(typeId, scenario.chosenMode);
            if (scenario.chosenMode === 'buy') {
                bestUnitCost.set(typeId, getCraftDecisionBuyUnitPriceOrInf(typeId));
                return;
            }
            if (Number.isFinite(scenario.prodUnit)) {
                bestUnitCost.set(typeId, scenario.prodUnit);
            }
        });

        craftables.forEach((typeId) => {
            if (!chosenMode.has(typeId)) {
                chosenMode.set(typeId, currentModes.get(typeId) || 'prod');
            }
        });

        return { bestUnitCost, chosenMode };
    }

    function computeCostsBreakdown(demand, currentModes) {
        const bestUnitCost = new Map();
        const chosenMode = new Map();
        const breakdown = new Map();

        const evaluateScenario = createScenarioEvaluator(currentModes);
        demand.forEach((needed, typeId) => {
            if (!craftables.has(typeId) || !(needed > 0)) {
                return;
            }

            const automaticScenario = evaluateScenario(typeId, needed, null);
            const buyScenario = evaluateScenario(typeId, needed, 'buy');
            const prodScenario = evaluateScenario(typeId, needed, 'prod');
            const mode = automaticScenario.chosenMode;

            chosenMode.set(typeId, mode);
            if (mode === 'prod' && Number.isFinite(prodScenario.prodUnit)) {
                bestUnitCost.set(typeId, prodScenario.prodUnit);
            } else {
                bestUnitCost.set(typeId, getCraftDecisionBuyUnitPriceOrInf(typeId));
            }

            breakdown.set(typeId, {
                typeId,
                name: nameByType.get(typeId) || '',
                needed,
                buyUnit: buyScenario.buyUnit,
                buyTotal: buyScenario.buyTotal,
                prodTotal: prodScenario.prodTotal,
                prodUnit: prodScenario.prodUnit,
                cycles: prodScenario.cycles,
                produced: prodScenario.produced,
                surplus: prodScenario.surplus,
                surplusCredit: prodScenario.surplusCredit,
                mode,
            });
        });

        return { breakdown, chosenMode };
    }

    let lastChangeCount = 0;
    for (let iteration = 0; iteration < 6; iteration += 1) {
        const demand = computeDemand(decisions);
        const { chosenMode } = computeBestUnitCosts(demand, decisions);
        let changed = 0;

        chosenMode.forEach((mode, typeId) => {
            const previousMode = decisions.get(typeId) || 'prod';
            if (previousMode !== mode) {
                decisions.set(typeId, mode);
                changed += 1;
            }
        });

        lastChangeCount = changed;
        if (changed === 0) {
            break;
        }
    }

    const recommendedDecisions = decisions;
    const finalDemand = computeDemand(recommendedDecisions);
    const { breakdown } = computeCostsBreakdown(finalDemand, recommendedDecisions);
    const rows = Array.from(finalDemand.keys())
        .map((typeId) => {
            const row = breakdown.get(typeId);
            if (!row) {
                return null;
            }

            const currentMode = currentDecisions.get(typeId) || 'prod';
            const recommendedMode = recommendedDecisions.get(typeId) || row.mode || currentMode;
            const buyTotal = row.buyTotal;
            const prodTotal = row.prodTotal;
            const deltaISK = Number.isFinite(buyTotal) && Number.isFinite(prodTotal)
                ? buyTotal - prodTotal
                : null;
            const ratio = Number.isFinite(buyTotal) && Number.isFinite(prodTotal) && prodTotal > 0
                ? (buyTotal / prodTotal)
                : null;
            const withinTolerance = deltaISK !== null && deltaISK >= 0 && deltaISK <= toleranceISK;
            const currentTotal = currentMode === 'buy' ? buyTotal : prodTotal;
            const recommendedTotal = recommendedMode === 'buy' ? buyTotal : prodTotal;
            const potentialSavings = Number.isFinite(currentTotal) && Number.isFinite(recommendedTotal)
                ? Math.max(0, currentTotal - recommendedTotal)
                : 0;
            const baseNote = row.buyTotal == null && row.prodTotal == null
                ? __('Missing buy and produce costs')
                : row.buyTotal == null
                    ? __('Missing buy price')
                    : row.prodTotal == null
                        ? __('Cannot estimate production cost')
                        : withinTolerance && recommendedMode === 'buy' && deltaISK > 0
                            ? __('Buy stays preferred because it remains inside the global tolerance')
                            : recommendedMode === 'buy'
                                ? __('Buying is currently the smarter choice')
                                : __('Producing is currently the smarter choice');
            const surplusNote = row.surplus > 0 && row.surplusCredit > 0
                ? __('Surplus resale is tracked separately and is not deducted from production cost.')
                : row.surplus > 0
                    ? __('Surplus from one cycle is tracked separately and is not deducted from production cost.')
                    : '';

            return {
                ...row,
                currentMode,
                recommendedMode,
                deltaISK,
                ratio,
                withinTolerance,
                potentialSavings,
                note: surplusNote ? `${baseNote} ${surplusNote}` : baseNote,
                ancestorIds: ancestorIdsByType.get(typeId) || [],
                depth: depthByType.get(typeId) || 0,
                hiddenByParentBuy: isHiddenByParentBuy(typeId),
                marketGroup: getDecisionMarketGroup(typeId),
            };
        })
        .filter(Boolean)
        .filter((row) => !row.hiddenByParentBuy)
        .sort((left, right) => {
            const modeRank = { buy: 0, prod: 1 };
            const leftModeRank = Object.prototype.hasOwnProperty.call(modeRank, left.currentMode) ? modeRank[left.currentMode] : 99;
            const rightModeRank = Object.prototype.hasOwnProperty.call(modeRank, right.currentMode) ? modeRank[right.currentMode] : 99;
            if (leftModeRank !== rightModeRank) {
                return leftModeRank - rightModeRank;
            }

            const leftGroup = left.marketGroup || dashboardOrdering.fallbackGroupName || __('Other');
            const rightGroup = right.marketGroup || dashboardOrdering.fallbackGroupName || __('Other');
            const leftHasGroup = dashboardOrdering.groupOrder.has(leftGroup);
            const rightHasGroup = dashboardOrdering.groupOrder.has(rightGroup);
            if (leftHasGroup && rightHasGroup) {
                const leftGroupIdx = dashboardOrdering.groupOrder.get(leftGroup);
                const rightGroupIdx = dashboardOrdering.groupOrder.get(rightGroup);
                if (leftGroupIdx !== rightGroupIdx) {
                    return leftGroupIdx - rightGroupIdx;
                }
            } else if (leftHasGroup !== rightHasGroup) {
                return leftHasGroup ? -1 : 1;
            } else {
                const groupCmp = String(leftGroup).localeCompare(String(rightGroup), undefined, { sensitivity: 'base' });
                if (groupCmp !== 0) {
                    return groupCmp;
                }
            }

            const leftNeedsReview = left.currentMode !== left.recommendedMode ? 0 : 1;
            const rightNeedsReview = right.currentMode !== right.recommendedMode ? 0 : 1;
            if (leftNeedsReview !== rightNeedsReview) {
                return leftNeedsReview - rightNeedsReview;
            }

            const leftDashboardItem = dashboardOrdering.itemOrder.get(Number(left.typeId) || 0);
            const rightDashboardItem = dashboardOrdering.itemOrder.get(Number(right.typeId) || 0);
            const leftItemIdx = leftDashboardItem ? leftDashboardItem.itemIdx : Number.POSITIVE_INFINITY;
            const rightItemIdx = rightDashboardItem ? rightDashboardItem.itemIdx : Number.POSITIVE_INFINITY;
            if (leftItemIdx !== rightItemIdx) {
                return leftItemIdx - rightItemIdx;
            }

            return (right.potentialSavings || 0) - (left.potentialSavings || 0);
        });

    return {
        rows,
        currentDecisions,
        recommendedDecisions,
        toleranceISK,
        lastChangeCount,
        currentBuyCount: rows.filter((row) => row.currentMode === 'buy').length,
        recommendedBuyCount: rows.filter((row) => row.recommendedMode === 'buy').length,
        potentialSavingsISK: rows.reduce((sum, row) => sum + (row.potentialSavings || 0), 0),
    };
}

function renderDecisionStrategyPanel(options = {}) {
    const rowsBody = document.getElementById('decisionStrategyRows');
    if (!rowsBody) {
        return Promise.resolve(null);
    }

    if (decisionStrategyPanelPromise && !options.force) {
        return decisionStrategyPanelPromise;
    }

    decisionStrategyPanelPromise = (async () => {
        const analysis = await computeCraftDecisionAnalysis({
            ensurePrices: options.ensurePrices === true,
            toleranceISK: options.toleranceISK,
        });

        const summaryEl = document.getElementById('decisionStrategySummary');
        const itemCountEl = document.getElementById('decisionStrategyItemCount');
        const currentBuyCountEl = document.getElementById('decisionStrategyCurrentBuyCount');
        const recommendedBuyCountEl = document.getElementById('decisionStrategyRecommendedBuyCount');
        const potentialSavingsEl = document.getElementById('decisionStrategyPotentialSavings');

        if (!analysis || analysis.error) {
            rowsBody.innerHTML = `
                <tr>
                    <td colspan="8" class="text-center text-muted py-4">${escapeHtml(analysis?.error || __('Decision analysis is unavailable.'))}</td>
                </tr>
            `;
            if (summaryEl) {
                summaryEl.textContent = analysis?.error || __('Decision analysis is unavailable.');
            }
            if (itemCountEl) itemCountEl.textContent = '0';
            if (currentBuyCountEl) currentBuyCountEl.textContent = '0';
            if (recommendedBuyCountEl) recommendedBuyCountEl.textContent = '0';
            if (potentialSavingsEl) potentialSavingsEl.textContent = formatPrice(0);
            updateTreeModeBadges();
            return analysis;
        }

        function buildDecisionRowMarkup(row) {
            const recommendationClass = row.recommendedMode === 'buy'
                ? 'bg-danger-subtle text-danger-emphasis'
                : 'bg-success-subtle text-success-emphasis';
            const recommendationLabel = row.recommendedMode === 'buy' ? __('Buy') : __('Produce');
            const deltaLabel = row.deltaISK == null
                ? '—'
                : `${row.deltaISK >= 0 ? '+' : '-'}${formatPrice(Math.abs(row.deltaISK))}`;
            const ratioLabel = row.ratio == null ? '—' : `${row.ratio.toFixed(2)}x`;
            const actionChecked = row.currentMode !== 'buy';

            return `
                <tr class="${row.currentMode !== row.recommendedMode ? 'craft-decision-row-needs-review' : ''}">
                    <td>
                        <div class="d-flex align-items-center gap-2">
                            <span class="blueprint-icon" style="width:28px;height:28px;">
                                <img src="https://images.evetech.net/types/${row.typeId}/icon?size=32" alt="${escapeHtml(row.name)}" loading="lazy" decoding="async" fetchpriority="low" style="width:28px;height:28px;object-fit:cover;border-radius:6px;background:#f3f4f6;" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
                                <span class="fallback" style="display:none;"><i class="fas fa-cube"></i></span>
                            </span>
                            <div>
                                <div class="fw-semibold">${escapeHtml(row.name)}</div>
                                <div class="small text-muted">${escapeHtml(row.note)}</div>
                            </div>
                        </div>
                    </td>
                    <td class="text-end">${formatInteger(row.needed)}</td>
                    <td class="text-end">${row.buyTotal == null ? '—' : formatPrice(row.buyTotal)}</td>
                    <td class="text-end">${row.prodTotal == null ? '—' : formatPrice(row.prodTotal)}</td>
                    <td class="text-end">
                        <div class="fw-semibold ${row.deltaISK == null ? 'text-muted' : row.deltaISK > 0 ? 'text-danger' : row.deltaISK < 0 ? 'text-success' : 'text-body'}">${escapeHtml(deltaLabel)}</div>
                        ${row.withinTolerance ? `<div class="small text-muted">${escapeHtml(__('Inside tolerance'))}</div>` : ''}
                    </td>
                    <td class="text-end">${escapeHtml(ratioLabel)}</td>
                    <td>
                        <span class="badge ${recommendationClass}">${escapeHtml(recommendationLabel)}</span>
                        ${row.potentialSavings > 0 ? `<div class="small text-muted mt-1">${escapeHtml(__('Save'))} ${escapeHtml(formatPrice(row.potentialSavings))}</div>` : ''}
                    </td>
                    <td>
                        <div class="mat-switch-group d-inline-flex align-items-center gap-2">
                            <div class="form-switch mb-0">
                                <input class="form-check-input mat-switch" type="checkbox" data-type-id="${row.typeId}" data-user-state="${row.currentMode}" data-ancestor-ids="${escapeHtml((row.ancestorIds || []).join(','))}"${actionChecked ? ' checked' : ''}>
                            </div>
                            <span class="mode-label badge px-2 py-1 fw-bold ${buildTreeModeBadgeClass(row.currentMode === 'buy' ? 'buy' : 'prod')}">${escapeHtml(row.currentMode === 'buy' ? __('Buy') : __('Prod'))}</span>
                        </div>
                    </td>
                </tr>
            `;
        }

        const rowsMarkup = analysis.rows.length > 0
            ? (() => {
                const modeLabels = {
                    buy: __('Buy'),
                    prod: __('Prod'),
                };
                const sections = [];
                let currentMode = null;
                let currentCategory = null;

                analysis.rows.forEach((row) => {
                    const rowMode = row.currentMode === 'buy' ? 'buy' : 'prod';
                    const rowCategory = row.marketGroup || __('Other');

                    if (currentMode !== rowMode) {
                        currentMode = rowMode;
                        currentCategory = null;
                        const modeCount = analysis.rows.filter((entry) => (entry.currentMode === 'buy' ? 'buy' : 'prod') === rowMode).length;
                        sections.push(`
                            <tr class="table-secondary">
                                <td colspan="8" class="fw-semibold">${escapeHtml(modeLabels[rowMode] || rowMode)} <span class="text-muted fw-normal">(${escapeHtml(formatInteger(modeCount))})</span></td>
                            </tr>
                        `);
                    }

                    if (currentCategory !== rowCategory) {
                        currentCategory = rowCategory;
                        sections.push(`
                            <tr class="table-light">
                                <td colspan="8" class="ps-4 text-muted fw-semibold">${escapeHtml(rowCategory)}</td>
                            </tr>
                        `);
                    }

                    sections.push(buildDecisionRowMarkup(row));
                });

                return sections.join('');
            })()
            : `
                <tr>
                    <td colspan="8" class="text-center text-muted py-4">${escapeHtml(__('No craftable items require a buy / produce review.'))}</td>
                </tr>
            `;

        rowsBody.innerHTML = rowsMarkup;

        if (summaryEl) {
            summaryEl.textContent = analysis.rows.length > 0
                ? __(`Reviewed ${analysis.rows.length} craftable items. Global buy tolerance: ${formatPrice(analysis.toleranceISK)}.`)
                : __('No craftable items require a buy / produce review.');
        }
        if (itemCountEl) itemCountEl.textContent = formatInteger(analysis.rows.length);
        if (currentBuyCountEl) currentBuyCountEl.textContent = formatInteger(analysis.currentBuyCount);
        if (recommendedBuyCountEl) recommendedBuyCountEl.textContent = formatInteger(analysis.recommendedBuyCount);
        if (potentialSavingsEl) potentialSavingsEl.textContent = formatPrice(analysis.potentialSavingsISK || 0);

        initializeBuyCraftSwitches();
        refreshTreeSwitchHierarchy();
        updateTreeModeBadges();
        return analysis;
    })().finally(() => {
        decisionStrategyPanelPromise = null;
    });

    return decisionStrategyPanelPromise;
}

async function optimizeProfitabilityConfig() {
    const analysis = await renderDecisionStrategyPanel({
        ensurePrices: true,
        force: true,
        toleranceISK: getDecisionBuyToleranceValue(),
    });

    if (!analysis || !Array.isArray(analysis.rows) || analysis.rows.length === 0) {
        if (window.CraftBP && typeof window.CraftBP.pushStatus === 'function') {
            window.CraftBP.pushStatus(__('No production tree to optimize'), 'warning');
        }
        return;
    }

    const applied = { buy: 0, prod: 0 };
    analysis.recommendedDecisions.forEach((mode, typeId) => {
        if (window.SimulationAPI && typeof window.SimulationAPI.setSwitchState === 'function') {
            window.SimulationAPI.setSwitchState(typeId, mode);
        }
        document.querySelectorAll(`input.mat-switch[data-type-id="${typeId}"]`).forEach((switchEl) => {
            if (switchEl.dataset.fixedMode === 'useless') {
                return;
            }
            switchEl.dataset.userState = mode;
            switchEl.checked = mode !== 'buy';
            updateSwitchLabel(switchEl);
        });
        applied[mode] += 1;
    });

    refreshTreeSwitchHierarchy();
    if (window.SimulationAPI && typeof window.SimulationAPI.refreshFromDom === 'function') {
        window.SimulationAPI.refreshFromDom();
    }
    refreshTabsAfterStateChange({ forceNeeded: true });
    if (typeof recalcFinancials === 'function') {
        recalcFinancials();
    }

    if (window.CraftBP && typeof window.CraftBP.pushStatus === 'function') {
        window.CraftBP.pushStatus(
            __(`Recommendations applied: ${applied.prod} produce, ${applied.buy} buy`),
            analysis.lastChangeCount === 0 ? 'info' : 'success'
        );
    }
}

function collectTypeIdsFromMaterialsTree(nodes, out = new Set()) {
    (Array.isArray(nodes) ? nodes : []).forEach((node) => {
        const tid = Number(node?.type_id || node?.typeId) || 0;
        if (tid > 0) out.add(String(tid));
        const kids = node && (node.sub_materials || node.subMaterials);
        if (Array.isArray(kids) && kids.length) {
            collectTypeIdsFromMaterialsTree(kids, out);
        }
    });
    return out;
}

function getCurrentDecisionsFromDom() {
    const decisions = new Map();
    getDecisionSwitchElements().forEach((sw) => {
        const id = Number(sw.getAttribute('data-type-id')) || 0;
        if (!id) return;
        if (sw.dataset.fixedMode === 'useless' || sw.dataset.userState === 'useless') return;
        decisions.set(id, sw.checked ? 'prod' : 'buy');
    });
    return decisions;
}

function syncSimulationSwitchStatesFromDom() {
    const api = window.SimulationAPI;
    if (!api || typeof api.setSwitchState !== 'function') return;

    getDecisionSwitchElements().forEach((sw) => {
        const id = Number(sw.getAttribute('data-type-id')) || 0;
        if (!id) return;
        if (sw.dataset.fixedMode === 'useless' || sw.dataset.userState === 'useless') {
            api.setSwitchState(id, 'useless');
            return;
        }
        api.setSwitchState(id, sw.checked ? 'prod' : 'buy');
    });
}

function getCurrentDecisionsFromSimulationOrDom() {
    const api = window.SimulationAPI;
    if (api && typeof api.getSwitchState === 'function') {
        const decisions = new Map();
        const simulationState = typeof api.getState === 'function' ? api.getState() : null;
        const switchStateMap = simulationState?.switches;

        if (switchStateMap && typeof switchStateMap.forEach === 'function') {
            switchStateMap.forEach((entry, typeId) => {
                const state = entry && typeof entry === 'object' ? entry.state : entry;
                if (state === 'buy' || state === 'prod' || state === 'useless') {
                    decisions.set(Number(typeId) || 0, state);
                }
            });
        }

        getDecisionSwitchElements().forEach((sw) => {
            const id = Number(sw.getAttribute('data-type-id')) || 0;
            if (!id || decisions.has(id)) return;
            const state = api.getSwitchState(id);
            if (state === 'buy' || state === 'prod' || state === 'useless') {
                decisions.set(id, state);
            }
        });

        // If SimulationAPI doesn't have any states yet, fall back to DOM.
        if (decisions.size > 0) return decisions;
    }

    return getCurrentDecisionsFromDom();
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
    craftBPDebugLog('updateBuyCraftDecisions: Disabled - handled by template logic');
}

/**
 * Restore buy/craft switch states from URL parameters
 */
function restoreBuyCraftStateFromURL() {
    const urlParams = new URLSearchParams(window.location.search);
    const buyList = urlParams.get('buy');

    if (buyList) {
        const buyDecisions = buyList.split(',').map(id => id.trim()).filter(id => id);
        craftBPDebugLog('Restoring buy decisions from URL:', buyDecisions);
        applyBuyCraftStateFromBuyDecisions(buyDecisions);
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
        label.textContent = __('Useless');
        label.classList.add('bg-secondary', 'text-white');
        label.removeAttribute('title');
        return;
    }

    if (isLockedByParent) {
        label.textContent = __('Parent Buy');
        label.classList.add('bg-secondary', 'text-white');
        label.setAttribute('title', __('Inherited mode: a parent is set to Buy'));
        return;
    }

    if (switchEl.checked) {
        label.textContent = __('Prod');
        label.classList.add('bg-success', 'text-white');
    } else {
        label.textContent = __('Buy');
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

    const optimizeBtn = document.getElementById('optimize-profit');
    if (optimizeBtn) {
        optimizeBtn.addEventListener('click', function() {
            optimizeProfitabilityConfig();
        });
    }
}

function initializeDecisionStrategyTab() {
    const tabBtn = document.getElementById('run-optimized-tab-btn');
    const refreshBtn = document.getElementById('decisionStrategyRefreshBtn');
    const toleranceInput = document.getElementById('decisionBuyToleranceInput');

    if (tabBtn && tabBtn.dataset.decisionStrategyBound !== 'true') {
        tabBtn.addEventListener('shown.bs.tab', () => {
            renderDecisionStrategyPanel({ ensurePrices: true, force: true });
        });
        tabBtn.dataset.decisionStrategyBound = 'true';
    }

    if (refreshBtn && refreshBtn.dataset.decisionStrategyBound !== 'true') {
        refreshBtn.addEventListener('click', () => {
            renderDecisionStrategyPanel({ ensurePrices: true, force: true });
        });
        refreshBtn.dataset.decisionStrategyBound = 'true';
    }

    if (toleranceInput && toleranceInput.dataset.decisionStrategyBound !== 'true') {
        const refreshAnalysis = () => {
            renderDecisionStrategyPanel({ ensurePrices: false, force: true });
        };
        toleranceInput.addEventListener('input', refreshAnalysis);
        toleranceInput.addEventListener('change', refreshAnalysis);
        toleranceInput.dataset.decisionStrategyBound = 'true';
    }
}

/**
 * Initialize financial calculations
 */
function initializeFinancialCalculations() {
    initializeDelegatedFinancialPriceInputs();

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

    // Also fetch prices for *all* typeIds in the production tree so:
    // - optimizer can always compare buy vs prod
    // - surplus valuation can price any produced surplus item
    const treeTypeIds = [];
    try {
        const tree = window.BLUEPRINT_DATA?.materials_tree;
        const seen = new Set();
        const walk = (nodes) => {
            (Array.isArray(nodes) ? nodes : []).forEach(node => {
                const tid = String(Number(node?.type_id || node?.typeId || 0) || '').trim();
                if (tid && tid !== '0' && !seen.has(tid)) {
                    seen.add(tid);
                    treeTypeIds.push(tid);
                }
                const kids = node && (node.sub_materials || node.subMaterials);
                if (Array.isArray(kids) && kids.length) {
                    walk(kids);
                }
            });
        };
        walk(tree);
    } catch (e) {
        // ignore
    }

    // Include the final product type_id
    if (CRAFT_BP.productTypeId && !typeIds.includes(CRAFT_BP.productTypeId)) {
        typeIds.push(CRAFT_BP.productTypeId);
    }
    typeIds = [...new Set([...typeIds, ...treeTypeIds])];

    function stashExtraFuzzworkPrices(prices) {
        if (!window.SimulationAPI || typeof window.SimulationAPI.setPrice !== 'function') {
            return;
        }
        treeTypeIds.forEach(tid => {
            const raw = prices[tid] ?? prices[String(parseInt(tid, 10))];
            const price = raw != null ? (parseFloat(raw) || 0) : 0;
            if (price > 0) {
                window.SimulationAPI.setPrice(tid, 'fuzzwork', price);
            }
        });
    }

    const restoredFuzzworkPrices = window.craftBPFlags?.restoredSessionState?.fuzzworkPrices;
    const hasRestoredFuzzworkPrices = Boolean(
        restoredFuzzworkPrices
        && typeof restoredFuzzworkPrices === 'object'
        && Object.keys(restoredFuzzworkPrices).length > 0
    );

    function applyResolvedPriceState(prices) {
        populatePrices(fetchInputs, prices);
        stashExtraFuzzworkPrices(prices);
        applyManualPriceOverrides(window.craftBPFlags?.restoredSessionState?.manualPrices);
        if (typeof updateFinancialTabFromState === 'function') {
            return Promise.resolve(updateFinancialTabFromState()).then(() => {
                recalcFinancials();
            });
        }
        recalcFinancials();
        return Promise.resolve();
    }

    const initialFinancialSyncPromise = (window.BLUEPRINT_DATA?.project_ref && hasRestoredFuzzworkPrices)
        ? Promise.resolve(applyResolvedPriceState(restoredFuzzworkPrices))
        : fetchAllPrices(typeIds).then(prices => applyResolvedPriceState(prices));

    // Bind Load Fuzzwork Prices button
    const loadBtn = document.getElementById('loadFuzzworkBtn');
    if (loadBtn) {
        loadBtn.addEventListener('click', function() {
            fetchAllPrices(typeIds).then(prices => {
                applyResolvedPriceState(prices);
                persistCraftPageSessionState();
            });
        });
    }

    const resetBtn = document.getElementById('resetManualPricesBtn');
    if (resetBtn) {
        resetBtn.addEventListener('click', () => {
            const priceInputs = document.querySelectorAll('.real-price[data-type-id], .sale-price-unit[data-type-id]');
            priceInputs.forEach(input => {
                const tid = input.getAttribute('data-type-id');
                if (input.classList.contains('sale-price-unit')) {
                    const fuzzInp = document.querySelector(`.fuzzwork-price[data-type-id="${tid}"]`);
                    input.value = (fuzzInp ? (fuzzInp.value || '0') : '0');
                    updatePriceInputManualState(input, false);

                    if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function' && tid) {
                        window.SimulationAPI.setPrice(tid, 'sale', parseFloat(input.value) || 0);
                    }
                } else {
                    // Real price resets to 0; calculations fall back to fuzzwork.
                    input.value = '0';
                    updatePriceInputManualState(input, false);

                    if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function' && tid) {
                        window.SimulationAPI.setPrice(tid, 'real', 0);
                    }
                }
            });

            recalcFinancials();
            persistCraftPageSessionState();
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
    window.craftBPFlags = window.craftBPFlags || {};
    window.craftBPFlags.hasPendingMETEChanges = Boolean(window.craftBPFlags.hasPendingMETEChanges);
    window.craftBPFlags.hasPendingWorkspaceRefresh = Boolean(window.craftBPFlags.hasPendingWorkspaceRefresh);
    updatePendingWorkspaceRefreshNotice();

    const storageKey = getCraftPageMETEStorageKey();

    // Restore ME/TE from localStorage on page load
    restoreMETEFromLocalStorage(storageKey);

    function saveMETEToLocalStorage() {
        const config = getCurrentMETEConfig();
        try {
            withCraftPageSessionStorage((storage) => {
                storage.setItem(storageKey, JSON.stringify(config));
            });
            craftBPDebugLog('ME/TE config saved to localStorage');
        } catch (error) {
            console.error('Error saving to localStorage:', error);
        }
    }

    function markMETEChanges() {
        saveMETEToLocalStorage();
        markPendingWorkspaceRefresh({ sourceTabName: 'configure' });
        persistCraftPageSessionState();
        craftBPDebugLog('Blueprint configuration changes detected - workspace refresh deferred until tab switch');
    }

    if (document.body && document.body.dataset.meteDelegatedAttached !== 'true') {
        document.addEventListener('input', (event) => {
            const target = event.target;
            if (target && (target.classList?.contains('bp-me-input') || target.classList?.contains('bp-te-input'))) {
                markMETEChanges();
            }
        }, true);
        document.addEventListener('change', (event) => {
            const target = event.target;
            if (target && (target.classList?.contains('bp-me-input') || target.classList?.contains('bp-te-input'))) {
                markMETEChanges();
            }
        }, true);
        document.body.dataset.meteDelegatedAttached = 'true';
    }

    const tabButtons = document.querySelectorAll('#craftMainTabs button[data-bs-toggle="tab"]');
    tabButtons.forEach(button => {
        button.addEventListener('shown.bs.tab', function(event) {
            const targetTab = event.target.getAttribute('data-tab-name');
            craftBPDebugLog(`Tab switched to: ${targetTab}`);

            if (targetTab === 'stock' && typeof updateStockManagementTabFromState === 'function') {
                updateStockManagementTabFromState(true);
            }

            const sourceTab = String(window.craftBPFlags?.pendingWorkspaceSourceTab || '').trim();

            if (targetTab && targetTab !== sourceTab && window.craftBPFlags?.hasPendingWorkspaceRefresh) {
                window.craftBPFlags.pendingWorkspaceTargetTab = getCurrentActiveBlueprintTab();
                window.craftBPFlags.switchingToTab = getCurrentActiveBlueprintTab();
                craftBPDebugLog('Applying pending workspace changes...');
                applyPendingMETEChanges();
            }
        });
    });
    craftBPDebugLog(`Added tab change listeners to ${tabButtons.length} tabs`);
}

/**
 * Restore ME/TE configuration from localStorage
 */
function restoreMETEFromLocalStorage(storageKey) {
    try {
        if (!isCraftPageReloadNavigation()) {
            withCraftPageSessionStorage((storage) => {
                storage.removeItem(storageKey);
            });
            craftBPDebugLog('Cleared stale ME/TE config from sessionStorage');
            return;
        }

        const savedConfig = withCraftPageSessionStorage((storage) => storage.getItem(storageKey));
        if (!savedConfig) {
            craftBPDebugLog('No saved ME/TE config in localStorage');
            return;
        }

        const config = normalizeMETEConfig(JSON.parse(savedConfig));
        craftBPDebugLog('Restoring ME/TE from localStorage:', config);

        if (!window.craftBPFlags?.pendingMETEConfig) {
            window.craftBPFlags = window.craftBPFlags || {};
            window.craftBPFlags.pendingMETEConfig = config;
        }

        applyMETEConfigToInputs(window.craftBPFlags.pendingMETEConfig || config);

        craftBPDebugLog('ME/TE config restored from localStorage');
    } catch (error) {
        console.error('Error restoring from localStorage:', error);
    }
}

/**
 * Apply pending ME/TE changes by recalculating via AJAX without page reload
 * Called when user switches away from Config tab
 */
function applyPendingMETEChanges() {
    if (!window.craftBPFlags?.hasPendingWorkspaceRefresh) {
        return false;
    }

    craftBPDebugLog('Applying pending workspace changes via AJAX refresh...');

    try {
        const recalculateFn = window.CraftBP && typeof window.CraftBP.queueRecalculate === 'function'
            ? window.CraftBP.queueRecalculate.bind(window.CraftBP)
            : recalculateBlueprintWorkspace;

        recalculateFn({
            activeTab: window.craftBPFlags.pendingWorkspaceTargetTab || window.craftBPFlags.switchingToTab || 'materials',
            immediate: true,
        }).catch((error) => {
            console.error('Error applying pending workspace changes:', error);
            if (window.CraftBP && typeof window.CraftBP.pushStatus === 'function') {
                window.CraftBP.pushStatus(__('Unable to refresh workspace'), 'warning');
            }
        });
        return true;

    } catch (error) {
        console.error('Error applying pending workspace changes:', error);
        return false;
    }
}

/**
 * Get current ME/TE configuration from Config tab
 */
function getCurrentMETEConfig() {
    const config = normalizeMETEConfig(window.craftBPFlags?.pendingMETEConfig || buildDefaultMETEConfig());

    if (!isConfigurePaneHydrated()) {
        return config;
    }

    // Get ME/TE inputs from config tab
    const meTeInputs = document.querySelectorAll('#configure-pane input[name^="me_"], #configure-pane input[name^="te_"]');

    craftBPDebugLog(`getCurrentMETEConfig: Found ${meTeInputs.length} inputs`);

    meTeInputs.forEach(input => {
        const name = input.name;
        const value = parseInt(input.value) || 0;

        craftBPDebugLog(`Input ${name} = ${value}`);

        if (name.startsWith('me_')) {
            const typeId = name.replace('me_', '');
            if (!config.blueprintConfigs[typeId]) {
                config.blueprintConfigs[typeId] = {};
            }
            config.blueprintConfigs[typeId].me = Math.max(0, Math.min(value, 10));

            // If this is the main blueprint, store it separately
            const currentBpId = getCurrentBlueprintTypeId();
            if (parseInt(typeId) === parseInt(currentBpId)) {
                config.mainME = config.blueprintConfigs[typeId].me;
                craftBPDebugLog(`Detected main blueprint ME: ${config.mainME}`);
            }
        } else if (name.startsWith('te_')) {
            const typeId = name.replace('te_', '');
            if (!config.blueprintConfigs[typeId]) {
                config.blueprintConfigs[typeId] = {};
            }
            config.blueprintConfigs[typeId].te = Math.max(0, Math.min(value, 20));

            // If this is the main blueprint, store it separately
            const currentBpId = getCurrentBlueprintTypeId();
            if (parseInt(typeId) === parseInt(currentBpId)) {
                config.mainTE = config.blueprintConfigs[typeId].te;
                craftBPDebugLog(`Detected main blueprint TE: ${config.mainTE}`);
            }
        }
    });

    return config;
}

window.applyDeferredCraftBlueprintInputState = applyDeferredCraftBlueprintInputState;

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
    const options = arguments.length > 0 && arguments[0] ? arguments[0] : {};
    clearLoadingOverlayHideTimer();
    const elements = getLoadingOverlayElements();
    setLoadingOverlayCopy(
        options.title || __('Preparing production workspace'),
        options.message || __('We are synchronising materials, production tree and financial data.')
    );
    if (elements.workspace) {
        elements.workspace.classList.add('is-loading');
        elements.workspace.setAttribute('aria-hidden', 'true');
    }
    if (elements.overlay) {
        elements.overlay.classList.remove('is-hidden');
        elements.overlay.setAttribute('aria-busy', 'true');
    }
    renderLoadingOverlayBootstrap();
}

/**
 * Hide loading indicator
 */
function hideLoadingIndicator() {
    const remainingMs = Math.max(0, Number(loadingOverlayState.minimumVisibleUntil) - getLoadingNow());
    if (remainingMs > 0) {
        clearLoadingOverlayHideTimer();
        loadingOverlayState.hideTimerId = window.setTimeout(() => {
            loadingOverlayState.hideTimerId = null;
            hideLoadingIndicatorNow();
        }, remainingMs);
        return;
    }
    hideLoadingIndicatorNow();
}

window.CraftBPLoading = {
    show: showLoadingIndicator,
    hide: hideLoadingIndicator,
    startBootstrap: startLoadingOverlayBootstrap,
    stepBootstrap: setLoadingOverlayBootstrapStep,
    finishBootstrap: completeLoadingOverlayBootstrap,
    getLastBootstrap: () => loadingOverlayState.lastBootstrap,
};

if (document.getElementById('bpTabs-loading') && window.CraftBPLoading && typeof window.CraftBPLoading.startBootstrap === 'function') {
    window.CraftBPLoading.startBootstrap({
        title: __('Preparing production workspace'),
        message: __('Loading workspace...'),
        minimumVisibleMs: 1200,
        steps: buildWorkspaceBootstrapSteps(),
    });
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

function formatPercent(value, digits = 2) {
    const numericValue = Number(value) || 0;
    return `${numericValue.toFixed(digits)}%`;
}

function formatDurationCompact(totalSeconds) {
    const seconds = Math.max(0, Math.ceil(Number(totalSeconds) || 0));
    if (!(seconds > 0)) {
        return '0s';
    }

    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainingSeconds = seconds % 60;
    const parts = [];

    if (days > 0) {
        parts.push(`${days}d`);
    }
    if (hours > 0 || parts.length > 0) {
        parts.push(`${hours}h`);
    }
    if (minutes > 0 || parts.length > 0) {
        parts.push(`${minutes}m`);
    }
    if (parts.length === 0) {
        parts.push(`${remainingSeconds}s`);
    }

    return parts.join(' ');
}

const EVE_JOB_LAUNCH_WINDOW_SECONDS = 30 * 24 * 60 * 60;

function getEveJobLaunchMetrics(effectiveCycleSeconds, cycles) {
    const cycleSeconds = Math.max(0, Math.ceil(Number(effectiveCycleSeconds) || 0));
    const cycleCount = Math.max(0, Math.ceil(Number(cycles) || 0));

    if (!(cycleSeconds > 0) || !(cycleCount > 0)) {
        return {
            maxRunsPerJob: 0,
            jobsRequired: 0,
            lastRunStartSeconds: 0,
            exceedsLaunchWindow: false,
        };
    }

    const maxRunsPerJob = Math.max(1, Math.ceil(EVE_JOB_LAUNCH_WINDOW_SECONDS / cycleSeconds));
    const jobsRequired = Math.max(1, Math.ceil(cycleCount / maxRunsPerJob));
    const lastRunStartSeconds = Math.max(0, (cycleCount - 1) * cycleSeconds);

    return {
        maxRunsPerJob,
        jobsRequired,
        lastRunStartSeconds,
        exceedsLaunchWindow: cycleCount > maxRunsPerJob,
    };
}

const structureMotherSystemState = {
    searchTimer: null,
    suggestionsByName: new Map(),
    selectedSolarSystemId: null,
    selectedSolarSystemName: '',
    origin: null,
    jumpDistanceBySystemId: new Map(),
    initialized: false,
    applying: false
};

function getCraftBlueprintUrls() {
    return window.BLUEPRINT_DATA?.urls || {};
}

function getStructureMotherSystemStatusElement() {
    return document.getElementById('structureMotherSystemStatus');
}

function updateStructureMotherSystemStatus(message, variant = 'muted') {
    const statusEl = getStructureMotherSystemStatusElement();
    if (!statusEl) {
        return;
    }
    statusEl.textContent = message || '';
    statusEl.className = 'small mt-2';
    if (variant === 'danger') {
        statusEl.classList.add('text-danger');
        return;
    }
    if (variant === 'success') {
        statusEl.classList.add('text-success');
        return;
    }
    statusEl.classList.add('text-muted');
}

function formatJumpDistance(jumps) {
    if (jumps === null || jumps === undefined) {
        return __('Unreachable');
    }
    const numericJumps = Number(jumps);
    if (!Number.isFinite(numericJumps) || numericJumps < 0) {
        return __('Unreachable');
    }
    if (numericJumps === 0) {
        return __('0 jumps');
    }
    if (numericJumps === 1) {
        return __('1 jump');
    }
    return __(`${numericJumps} jumps`);
}

function populateStructureMotherSystemSuggestions(results) {
    const datalist = document.getElementById('structureMotherSystemSuggestions');
    if (!datalist) {
        return;
    }
    structureMotherSystemState.suggestionsByName = new Map();
    datalist.innerHTML = '';

    (Array.isArray(results) ? results : []).forEach((entry) => {
        const systemName = String(entry?.name || '').trim();
        if (!systemName) {
            return;
        }
        structureMotherSystemState.suggestionsByName.set(systemName.toLowerCase(), entry);
        const option = document.createElement('option');
        option.value = systemName;
        datalist.appendChild(option);
    });
}

async function fetchStructureMotherSystemSuggestions(query) {
    const urls = getCraftBlueprintUrls();
    if (!urls.structure_solar_system_search) {
        return;
    }
    const trimmedQuery = String(query || '').trim();
    if (trimmedQuery.length < 2) {
        populateStructureMotherSystemSuggestions([]);
        return;
    }
    const url = new URL(urls.structure_solar_system_search, window.location.origin);
    url.searchParams.set('q', trimmedQuery);
    const response = await fetch(url.toString(), {
        method: 'GET',
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });
    if (!response.ok) {
        throw new Error(`solar-system-search-${response.status}`);
    }
    const payload = await response.json();
    populateStructureMotherSystemSuggestions(Array.isArray(payload?.results) ? payload.results : []);
}

function syncStructureMotherSystemSelectionFromInput() {
    const input = document.getElementById('structureMotherSystemInput');
    if (!input) {
        return;
    }
    const typedName = String(input.value || '').trim();
    const exactSuggestion = structureMotherSystemState.suggestionsByName.get(typedName.toLowerCase()) || null;
    if (exactSuggestion) {
        structureMotherSystemState.selectedSolarSystemId = Number(exactSuggestion.id || 0) || null;
        structureMotherSystemState.selectedSolarSystemName = String(exactSuggestion.name || typedName);
        return;
    }
    if (typedName !== structureMotherSystemState.selectedSolarSystemName) {
        structureMotherSystemState.selectedSolarSystemId = null;
        structureMotherSystemState.selectedSolarSystemName = typedName;
    }
}

function getRelevantStructureSystemIds() {
    const systemIds = new Set();
    getStructurePlannerItems().forEach((item) => {
        const options = Array.isArray(item.options) ? item.options : [];
        options.forEach((option) => {
            const solarSystemId = Number(option.solar_system_id || option.solarSystemId || 0) || 0;
            if (solarSystemId > 0) {
                systemIds.add(solarSystemId);
            }
        });
    });
    return Array.from(systemIds.values());
}

function getExactJumpCountForOption(option) {
    if (!option || !structureMotherSystemState.origin) {
        return undefined;
    }
    const solarSystemId = Number(option.solar_system_id || option.solarSystemId || 0) || 0;
    if (!solarSystemId) {
        return undefined;
    }
    if (!structureMotherSystemState.jumpDistanceBySystemId.has(solarSystemId)) {
        return undefined;
    }
    return structureMotherSystemState.jumpDistanceBySystemId.get(solarSystemId);
}

function getDecoratedStructureOption(typeId, structureId) {
    if (!window.SimulationAPI || typeof window.SimulationAPI.getStructureOption !== 'function') {
        return null;
    }
    const baseOption = window.SimulationAPI.getStructureOption(typeId, structureId);
    if (!baseOption) {
        return null;
    }
    const jumps = getExactJumpCountForOption(baseOption);
    if (jumps === undefined) {
        return baseOption;
    }
    const distanceLabel = formatJumpDistance(jumps);
    return Object.assign({}, baseOption, {
        exact_jump_distance: jumps,
        exactJumpDistance: jumps,
        distance_label: distanceLabel,
        distanceLabel
    });
}

function getRankedStructureOptions(item) {
    const options = Array.isArray(item?.options) ? item.options.slice() : [];
    if (!structureMotherSystemState.origin) {
        return options;
    }
    return options
        .map((option, index) => ({
            option,
            index,
            jumps: getExactJumpCountForOption(option)
        }))
        .sort((left, right) => {
            const leftReachable = left.jumps !== null && left.jumps !== undefined;
            const rightReachable = right.jumps !== null && right.jumps !== undefined;
            if (leftReachable && rightReachable && left.jumps !== right.jumps) {
                return left.jumps - right.jumps;
            }
            if (leftReachable !== rightReachable) {
                return leftReachable ? -1 : 1;
            }
            return left.index - right.index;
        })
        .map((entry) => entry.option);
}

function chooseNearestStructureOption(item) {
    const rankedOptions = getRankedStructureOptions(item);
    return rankedOptions.length > 0 ? rankedOptions[0] : null;
}

async function applyStructureMotherSystemDistances() {
    const urls = getCraftBlueprintUrls();
    if (!urls.craft_structure_jump_distances) {
        updateStructureMotherSystemStatus(__('Jump-distance API unavailable.'), 'danger');
        return;
    }

    const input = document.getElementById('structureMotherSystemInput');
    const validateButton = document.getElementById('structureMotherSystemValidate');
    const typedName = String(input?.value || '').trim();
    syncStructureMotherSystemSelectionFromInput();

    if (!typedName) {
        structureMotherSystemState.origin = null;
        structureMotherSystemState.jumpDistanceBySystemId = new Map();
        updateStructureMotherSystemStatus(__('Enter a main production system first.'), 'danger');
        renderStructurePlanner();
        return;
    }

    const targetSystemIds = getRelevantStructureSystemIds();
    if (targetSystemIds.length === 0) {
        updateStructureMotherSystemStatus(__('No structure systems are available for the current production tree.'), 'danger');
        return;
    }

    structureMotherSystemState.applying = true;
    if (validateButton) {
        validateButton.disabled = true;
    }
    updateStructureMotherSystemStatus(__('Refreshing structure distances...'));

    try {
        const url = new URL(urls.craft_structure_jump_distances, window.location.origin);
        if (structureMotherSystemState.selectedSolarSystemId) {
            url.searchParams.set('solar_system_id', String(structureMotherSystemState.selectedSolarSystemId));
        }
        url.searchParams.set('solar_system_name', typedName);
        targetSystemIds.forEach((targetSystemId) => {
            url.searchParams.append('target_system_ids', String(targetSystemId));
        });

        const response = await fetch(url.toString(), {
            method: 'GET',
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload?.error || `jump-distance-${response.status}`);
        }

        structureMotherSystemState.origin = payload.origin || null;
        structureMotherSystemState.selectedSolarSystemId = Number(payload?.origin?.solar_system_id || 0) || null;
        structureMotherSystemState.selectedSolarSystemName = String(payload?.origin?.solar_system_name || typedName);
        if (input) {
            input.value = structureMotherSystemState.selectedSolarSystemName;
        }

        structureMotherSystemState.jumpDistanceBySystemId = new Map(
            (Array.isArray(payload?.distances) ? payload.distances : []).map((entry) => [
                Number(entry?.solar_system_id || 0) || 0,
                entry?.jumps ?? null
            ]).filter(([solarSystemId]) => solarSystemId > 0)
        );

        if (window.SimulationAPI && typeof window.SimulationAPI.setStructureAssignment === 'function') {
            getStructurePlannerItems().forEach((item) => {
                const nearestOption = chooseNearestStructureOption(item);
                const typeId = Number(item.typeId || item.type_id || 0) || 0;
                const structureId = Number(nearestOption?.structure_id || nearestOption?.structureId || 0) || 0;
                if (typeId > 0 && structureId > 0) {
                    window.SimulationAPI.setStructureAssignment(typeId, structureId);
                }
            });
        }

        renderStructurePlanner();
        markPendingWorkspaceRefresh({ sourceTabName: 'structure' });
        persistCraftPageSessionState();

        updateStructureMotherSystemStatus(
            __(`Nearest structures applied from ${structureMotherSystemState.selectedSolarSystemName}.`),
            'success'
        );
    } catch (error) {
        console.error('[IndyHub] Failed to refresh structure jump distances', error);
        updateStructureMotherSystemStatus(__('Unable to refresh exact jump distances for the selected system.'), 'danger');
    } finally {
        structureMotherSystemState.applying = false;
        if (validateButton) {
            validateButton.disabled = false;
        }
    }
}

function initializeStructureMotherSystemControls() {
    if (structureMotherSystemState.initialized) {
        return;
    }
    const input = document.getElementById('structureMotherSystemInput');
    const validateButton = document.getElementById('structureMotherSystemValidate');
    if (!input || !validateButton) {
        return;
    }

    input.addEventListener('input', () => {
        syncStructureMotherSystemSelectionFromInput();
        persistCraftPageSessionState();
        const query = String(input.value || '').trim();
        if (structureMotherSystemState.searchTimer) {
            window.clearTimeout(structureMotherSystemState.searchTimer);
        }
        structureMotherSystemState.searchTimer = window.setTimeout(() => {
            fetchStructureMotherSystemSuggestions(query).catch((error) => {
                console.error('[IndyHub] Solar system suggestions failed', error);
            });
        }, 180);
    });

    input.addEventListener('change', () => {
        syncStructureMotherSystemSelectionFromInput();
        persistCraftPageSessionState();
    });

    validateButton.addEventListener('click', () => {
        if (!structureMotherSystemState.applying) {
            applyStructureMotherSystemDistances();
        }
    });

    structureMotherSystemState.initialized = true;
    updateStructureMotherSystemStatus(__('Select your main production system, then validate to reassign by exact jumps.'));
}

function getStructurePlannerItems() {
    if (!window.SimulationAPI || typeof window.SimulationAPI.getStructureItems !== 'function') {
        return [];
    }
    const items = window.SimulationAPI.getStructureItems();
    if (!Array.isArray(items)) {
        return [];
    }
    if (typeof window.SimulationAPI.getProductionCycles !== 'function') {
        return items;
    }

    const productionCycles = window.SimulationAPI.getProductionCycles() || [];
    const activeTypeIds = new Set(
        productionCycles
            .map((entry) => Number(entry.typeId || entry.type_id || 0) || 0)
            .filter((typeId) => typeId > 0)
    );

    if (activeTypeIds.size === 0) {
        return [];
    }

    return items.filter((item) => activeTypeIds.has(Number(item.typeId || item.type_id || 0) || 0));
}

function getStructurePlannerOption(typeId, structureId) {
    return getDecoratedStructureOption(typeId, structureId);
}

let structurePlannerPayloadRequest = null;

function hasFullStructurePlannerOptions() {
    const planner = window.SimulationAPI && typeof window.SimulationAPI.getStructurePlanner === 'function'
        ? window.SimulationAPI.getStructurePlanner()
        : (window.BLUEPRINT_DATA?.structure_planner || window.BLUEPRINT_DATA?.structurePlanner || {});
    const summary = planner && typeof planner === 'object' ? (planner.summary || {}) : {};
    return summary.has_full_options !== false && summary.hasFullOptions !== false;
}

function buildCurrentCraftPayloadUrl(extraParams = {}) {
    const base = window.BLUEPRINT_DATA?.urls?.craft_bp_payload;
    if (!base) {
        return null;
    }

    const url = new URL(base, window.location.origin);
    const currentParams = new URLSearchParams(window.location.search || '');
    currentParams.forEach((value, key) => {
        url.searchParams.set(key, value);
    });
    Object.entries(extraParams).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== '') {
            url.searchParams.set(key, String(value));
        }
    });
    if (window.INDY_HUB_DEBUG) {
        url.searchParams.set('indy_debug', '1');
    }
    return url;
}

function ensureFullStructurePlannerLoaded() {
    if (hasFullStructurePlannerOptions()) {
        return Promise.resolve(true);
    }
    if (structurePlannerPayloadRequest) {
        return structurePlannerPayloadRequest;
    }

    const url = buildCurrentCraftPayloadUrl();
    if (!url) {
        return Promise.resolve(false);
    }

    structurePlannerPayloadRequest = fetch(url.toString(), {
        headers: {
            'X-Requested-With': 'XMLHttpRequest',
        },
    })
        .then((response) => {
            if (!response.ok) {
                throw new Error(`Unable to load structure planner (${response.status})`);
            }
            return response.json();
        })
        .then((nextPayload) => {
            const structurePlanner = nextPayload?.structure_planner || nextPayload?.structurePlanner || null;
            if (!structurePlanner) {
                return false;
            }
            if (window.SimulationAPI && typeof window.SimulationAPI.replaceStructurePlanner === 'function') {
                window.SimulationAPI.replaceStructurePlanner(structurePlanner, { preserveAssignments: true });
            }
            window.BLUEPRINT_DATA = window.BLUEPRINT_DATA || {};
            window.BLUEPRINT_DATA.structure_planner = structurePlanner;
            window.BLUEPRINT_DATA.structurePlanner = structurePlanner;
            return true;
        })
        .catch((error) => {
            console.error('Error loading full structure planner payload:', error);
            return false;
        })
        .finally(() => {
            structurePlannerPayloadRequest = null;
        });

    return structurePlannerPayloadRequest;
}

function computeStructureInstallationSummary() {
    const summary = {
        estimatedItemValue: 0,
        jobCost: 0,
        facilityTax: 0,
        sccSurcharge: 0,
        totalInstallation: 0,
        rows: []
    };

    if (!window.SimulationAPI || typeof window.SimulationAPI.getProductionCycles !== 'function' || typeof window.SimulationAPI.getPrice !== 'function') {
        return summary;
    }

    const productionCycles = window.SimulationAPI.getProductionCycles() || [];
    const plannerItemsByTypeId = new Map(
        getStructurePlannerItems().map((item) => [Number(item.typeId || item.type_id || 0) || 0, item])
    );
    productionCycles.forEach((entry) => {
        const typeId = Number(entry.typeId || entry.type_id || 0) || 0;
        if (!typeId) {
            return;
        }

        const plannerItem = plannerItemsByTypeId.get(typeId) || null;
        const option = getStructurePlannerOption(typeId);
        if (!option) {
            return;
        }

        const cycles = Math.max(0, Math.ceil(Number(entry.cycles || 0))) || 0;
        const producedPerCycle = Math.max(0, Math.ceil(Number(entry.producedPerCycle || entry.produced_per_cycle || 0))) || 0;
        if (!(cycles > 0) || !(producedPerCycle > 0)) {
            return;
        }

        const recipeEstimatedItemValue = computeRecipeEstimatedItemValue(typeId);
        const plannerEstimatedItemValue = Number(
            plannerItem && (plannerItem.estimated_item_value || plannerItem.estimatedItemValue || 0)
        ) || 0;
        const plannerProducedPerCycle = Math.max(
            0,
            Math.ceil(Number(plannerItem && (plannerItem.produced_per_cycle || plannerItem.producedPerCycle || 0)) || 0)
        );
        let estimatedItemValue = recipeEstimatedItemValue.value;
        let estimatedItemValueSource = 'adjusted_components';
        if (!(estimatedItemValue > 0)) {
            estimatedItemValue = plannerEstimatedItemValue;
            estimatedItemValueSource = 'planner';
        }
        if (!(estimatedItemValue > 0)) {
            const priceInfo = window.SimulationAPI.getPrice(typeId, 'buy');
            const fallbackSale = window.SimulationAPI.getPrice(typeId, 'sale');
            const unitValue = (priceInfo && typeof priceInfo.value === 'number' && priceInfo.value > 0)
                ? priceInfo.value
                : ((fallbackSale && typeof fallbackSale.value === 'number') ? fallbackSale.value : 0);
            if (!(unitValue > 0)) {
                return;
            }
            estimatedItemValue = producedPerCycle * unitValue;
            estimatedItemValueSource = 'market_fallback';
        } else if (plannerProducedPerCycle > 0 && plannerProducedPerCycle !== producedPerCycle) {
            estimatedItemValue = estimatedItemValue * (producedPerCycle / plannerProducedPerCycle);
        }

        const systemCostIndexPercent = Number(option.system_cost_index_percent || option.systemCostIndexPercent || 0) || 0;
        const jobCostBonusPercent = Number(option.job_cost_bonus_percent || option.jobCostBonusPercent || 0) || 0;
        const taxPercent = Number(option.tax_percent || option.taxPercent || 0) || 0;
        const baseJobCost = Math.ceil(estimatedItemValue * (systemCostIndexPercent / 100));
        const adjustedJobCost = Math.ceil(baseJobCost * Math.max(0, 1 - (jobCostBonusPercent / 100)));
        const facilityTax = Math.ceil(estimatedItemValue * (taxPercent / 100));
        const sccSurcharge = Math.ceil(estimatedItemValue * 0.04);
        const installationPerCycle = adjustedJobCost + facilityTax + sccSurcharge;
        const totalInstallation = installationPerCycle * cycles;

        summary.estimatedItemValue += estimatedItemValue * cycles;
        summary.jobCost += adjustedJobCost * cycles;
        summary.facilityTax += facilityTax * cycles;
        summary.sccSurcharge += sccSurcharge * cycles;
        summary.totalInstallation += totalInstallation;
        summary.rows.push({
            typeId,
            typeName: entry.typeName || entry.type_name || String(typeId),
            structureName: option.name || '',
            cycles,
            estimatedItemValue,
            estimatedItemValueTotal: estimatedItemValue * cycles,
            baseJobCost,
            adjustedJobCost,
            jobCostTotal: adjustedJobCost * cycles,
            facilityTax,
            facilityTaxTotal: facilityTax * cycles,
            sccSurcharge,
            sccSurchargeTotal: sccSurcharge * cycles,
            installationPerCycle,
            totalInstallation,
            systemCostIndexPercent,
            jobCostBonusPercent,
            taxPercent,
            estimatedItemValueSource,
            adjustedComponentCount: recipeEstimatedItemValue.componentCount,
            pricedAdjustedComponentCount: recipeEstimatedItemValue.pricedComponentCount,
            hasFullAdjustedPriceCoverage: recipeEstimatedItemValue.hasAllAdjustedPrices,
            distanceLabel: option.distance_label || option.distanceLabel || ''
        });
    });

    summary.rows.sort((a, b) => String(a.typeName).localeCompare(String(b.typeName), undefined, { sensitivity: 'base' }));
    return summary;
}

function renderStructureFinancialSummary() {
    ensureStructureSummaryAdjustedPrices();
    const summary = computeStructureInstallationSummary();
    const estimatedItemValueEl = document.getElementById('structureSummaryEstimatedValue');
    const jobCostEl = document.getElementById('structureSummaryJobCost');
    const facilityTaxEl = document.getElementById('structureSummaryFacilityTax');
    const sccEl = document.getElementById('structureSummaryScc');
    const installationEl = document.getElementById('structureSummaryInstallation');
    const installationModalEl = document.getElementById('structureSummaryInstallationModal');
    const emptyEl = document.getElementById('structureSummaryEmpty');
    const modalEmptyEl = document.getElementById('structureSummaryModalEmpty');
    const rowsContainer = document.getElementById('structureCostRows');
    const detailsButton = document.getElementById('structureSummaryDetailsBtn');

    if (estimatedItemValueEl) {
        estimatedItemValueEl.textContent = formatPrice(summary.estimatedItemValue);
    }
    if (jobCostEl) {
        jobCostEl.textContent = formatPrice(summary.jobCost);
    }
    if (facilityTaxEl) {
        facilityTaxEl.textContent = formatPrice(summary.facilityTax);
    }
    if (sccEl) {
        sccEl.textContent = formatPrice(summary.sccSurcharge);
    }
    if (installationEl) {
        installationEl.textContent = formatPrice(summary.totalInstallation);
    }
    if (installationModalEl) {
        installationModalEl.textContent = formatPrice(summary.totalInstallation);
    }
    if (emptyEl) {
        emptyEl.classList.toggle('d-none', summary.rows.length > 0);
    }
    if (modalEmptyEl) {
        modalEmptyEl.classList.toggle('d-none', summary.rows.length > 0);
    }
    if (detailsButton) {
        detailsButton.disabled = summary.rows.length === 0;
    }
    if (rowsContainer) {
        rowsContainer.innerHTML = summary.rows.map((row) => `
            <tr>
                <td>${escapeHtml(row.typeName)}</td>
                <td>
                    <div class="fw-semibold">${escapeHtml(row.structureName)}</div>
                    ${row.distanceLabel ? `<div class="small text-muted">${escapeHtml(row.distanceLabel)}</div>` : ''}
                </td>
                <td class="text-end">${formatInteger(row.cycles)}</td>
                <td>
                    <div class="small"><span class="text-muted">${escapeHtml(__('EIV'))}</span> ${formatPrice(row.estimatedItemValue)} / ${escapeHtml(__('cycle'))}</div>
                    <div class="small"><span class="text-muted">${escapeHtml(__('Job'))}</span> ${formatPrice(row.adjustedJobCost)} · <span class="text-muted">${escapeHtml(__('Tax'))}</span> ${formatPrice(row.facilityTax)} · <span class="text-muted">${escapeHtml(__('SCC'))}</span> ${formatPrice(row.sccSurcharge)}</div>
                    <div class="small text-muted">${escapeHtml(__('Source'))} ${escapeHtml(row.estimatedItemValueSource)}${row.adjustedComponentCount > 0 ? ` · ${escapeHtml(__('Adjusted inputs'))} ${formatInteger(row.pricedAdjustedComponentCount)}/${formatInteger(row.adjustedComponentCount)}` : ''}</div>
                    <div class="small text-muted">SCI ${formatPercent(row.systemCostIndexPercent, 2)} · ${escapeHtml(__('Job bonus'))} ${formatPercent(row.jobCostBonusPercent, 2)} · ${escapeHtml(__('Tax'))} ${formatPercent(row.taxPercent, 2)}</div>
                </td>
                <td class="text-end">
                    <div class="fw-semibold">${formatPrice(row.totalInstallation)}</div>
                    <div class="small text-muted">${formatPrice(row.installationPerCycle)} / ${escapeHtml(__('cycle'))}</div>
                </td>
            </tr>
        `).join('');
    }

    return summary;
}

function buildStructureOptionLabel(option) {
    const systemName = option.system_name || option.systemName || '';
    const distanceLabel = option.distance_label || option.distanceLabel || '';
    const materialBonus = formatPercent(option.material_bonus_percent || option.materialBonusPercent || option.rig_material_bonus_percent || option.rigMaterialBonusPercent || 0, 2);
    const timeBonus = formatPercent(option.time_bonus_percent || option.timeBonusPercent || option.rig_time_bonus_percent || option.rigTimeBonusPercent || 0, 2);
    const jobBonus = formatPercent(option.job_cost_bonus_percent || option.jobCostBonusPercent || 0, 2);
    const taxPercent = formatPercent(option.tax_percent || option.taxPercent || 0, 2);
    return `${option.name} · ${systemName} · ${distanceLabel || __('Standalone')} · ME ${materialBonus} · TE ${timeBonus} · Job ${jobBonus} · Tax ${taxPercent}`;
}

function renderStructurePlanner() {
    const summaryContainer = document.getElementById('structurePlannerSummary');
    const rowsContainer = document.getElementById('structurePlannerRows');
    const emptyContainer = document.getElementById('structurePlannerEmpty');

    if (!summaryContainer || !rowsContainer || !emptyContainer) {
        return;
    }

    if (!hasFullStructurePlannerOptions()) {
        emptyContainer.classList.add('d-none');
        summaryContainer.innerHTML = `<div class="alert alert-info mb-0">${escapeHtml(__('Loading complete structure options…'))}</div>`;
        rowsContainer.innerHTML = '';
        ensureFullStructurePlannerLoaded().then((loaded) => {
            if (loaded) {
                renderStructurePlanner();
                return;
            }
            summaryContainer.innerHTML = '';
            emptyContainer.classList.remove('d-none');
        });
        return;
    }

    const items = getStructurePlannerItems();

    if (items.length === 0) {
        summaryContainer.innerHTML = '';
        rowsContainer.innerHTML = '';
        emptyContainer.classList.remove('d-none');
        return;
    }

    emptyContainer.classList.add('d-none');
    const uniqueStructureNames = new Set();
    let weightedMaterialBonus = 0;
    let weightedItemCount = 0;

    items.forEach((item) => {
        const option = getStructurePlannerOption(item.typeId || item.type_id);
        if (!option) {
            return;
        }
        if (option.name) {
            uniqueStructureNames.add(option.name);
        }
        weightedMaterialBonus += Number(option.material_bonus_percent || option.materialBonusPercent || 0) || 0;
        weightedItemCount += 1;
    });

    const summary = computeStructureInstallationSummary();
    const averageMaterialBonus = weightedItemCount > 0 ? (weightedMaterialBonus / weightedItemCount) : 0;

    summaryContainer.innerHTML = `
        <div class="craft-structure-summary-card">
            <div class="craft-structure-summary-label">${__('Selected network')}</div>
            <div class="craft-structure-summary-value">${formatInteger(uniqueStructureNames.size)}</div>
            <div class="craft-structure-summary-meta">${Array.from(uniqueStructureNames).slice(0, 3).map(escapeHtml).join(' · ') || __('No selection')}</div>
        </div>
        <div class="craft-structure-summary-card">
            <div class="craft-structure-summary-label">${__('Average material bonus')}</div>
            <div class="craft-structure-summary-value">${formatPercent(averageMaterialBonus, 2)}</div>
            <div class="craft-structure-summary-meta">${__('Based on currently assigned produced items')}</div>
        </div>
        <div class="craft-structure-summary-card">
            <div class="craft-structure-summary-label">${__('Installation total')}</div>
            <div class="craft-structure-summary-value">${formatPrice(summary.totalInstallation)}</div>
            <div class="craft-structure-summary-meta">${__('Included in Buy and Plan profitability')}</div>
        </div>
    `;

    rowsContainer.innerHTML = items.map((item) => {
        const typeId = Number(item.typeId || item.type_id || 0) || 0;
        const recommendedStructureId = Number(item.recommendedStructureId || item.recommended_structure_id || 0) || 0;
        const selectedStructureId = Number(item.selectedStructureId || item.selected_structure_id || recommendedStructureId || 0) || 0;
        const selectedOption = getStructurePlannerOption(typeId, selectedStructureId);
        const nearestOption = structureMotherSystemState.origin ? chooseNearestStructureOption(item) : null;
        const recommendedName = nearestOption
            ? String(nearestOption.name || item.recommendedStructureName || item.recommended_structure_name || __('No recommendation'))
            : (item.recommendedStructureName || item.recommended_structure_name || __('No recommendation'));
        const distanceLabel = selectedOption ? (selectedOption.distance_label || selectedOption.distanceLabel || '') : '';
        const recommendedDistanceLabel = nearestOption
            ? formatJumpDistance(getExactJumpCountForOption(nearestOption))
            : (item.recommendedDistanceLabel || item.recommended_distance_label || '');
        const itemGroupLabel = item.groupName || item.group_name || item.categoryName || item.category_name || item.activityLabel || item.activity_label || '';
        const options = getRankedStructureOptions(item);
        const optionMarkup = options.map((option) => {
            const structureId = Number(option.structureId || option.structure_id || 0) || 0;
            const decoratedOption = getStructurePlannerOption(typeId, structureId) || option;
            return `<option value="${structureId}" ${structureId === selectedStructureId ? 'selected' : ''}>${escapeHtml(buildStructureOptionLabel(decoratedOption))}</option>`;
        }).join('');

        return `
            <tr>
                <td>
                    <div class="fw-semibold">${escapeHtml(item.typeName || item.type_name || String(typeId))}</div>
                    <div class="small text-muted">${escapeHtml(itemGroupLabel)}</div>
                </td>
                <td>
                    <div class="fw-semibold">${escapeHtml(recommendedName)}</div>
                    <div class="small text-muted">${escapeHtml(recommendedDistanceLabel)}</div>
                </td>
                <td>
                    <select class="form-select form-select-sm structure-assignment-select" data-type-id="${typeId}">
                        ${optionMarkup}
                    </select>
                </td>
                <td class="text-end fw-semibold">${selectedOption ? formatPercent(selectedOption.material_bonus_percent || selectedOption.materialBonusPercent || selectedOption.rig_material_bonus_percent || selectedOption.rigMaterialBonusPercent || 0, 2) : '0.00%'}</td>
                <td class="text-end fw-semibold">${selectedOption ? formatPercent(selectedOption.time_bonus_percent || selectedOption.timeBonusPercent || selectedOption.rig_time_bonus_percent || selectedOption.rigTimeBonusPercent || 0, 2) : '0.00%'}</td>
                <td class="text-end fw-semibold">${selectedOption ? formatPercent(selectedOption.job_cost_bonus_percent || selectedOption.jobCostBonusPercent || 0, 2) : '0.00%'}</td>
                <td class="text-end fw-semibold">${selectedOption ? formatPercent(selectedOption.tax_percent || selectedOption.taxPercent || 0, 2) : '0.00%'}</td>
                <td><span class="badge bg-secondary-subtle text-secondary-emphasis">${escapeHtml(distanceLabel || __('Standalone'))}</span></td>
            </tr>
        `;
    }).join('');

    rowsContainer.querySelectorAll('.structure-assignment-select').forEach((select) => {
        if (select.dataset.boundChange === 'true') {
            return;
        }
        select.addEventListener('change', () => {
            const typeId = Number(select.getAttribute('data-type-id')) || 0;
            const structureId = Number(select.value) || 0;
            if (!typeId || !structureId || !window.SimulationAPI || typeof window.SimulationAPI.setStructureAssignment !== 'function') {
                return;
            }
            window.SimulationAPI.setStructureAssignment(typeId, structureId);
            renderStructurePlanner();
            markPendingWorkspaceRefresh({ sourceTabName: 'structure' });
            persistCraftPageSessionState();
        });
        select.dataset.boundChange = 'true';
    });
}

/**
 * Recalculate financial totals
 */
function recalcFinancials() {
    let materialCostTotal = 0;
    let materialInvestmentTotal = 0;
    let stockValueTotal = 0;
    let revTotal = 0;

    document.querySelectorAll('#financialItemsBody tr').forEach(tr => {
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
            const typeId = Number(tr.getAttribute('data-type-id')) || 0;
            let unitCost = parseFloat(costInput.value) || 0;

            // If real price is 0, fall back to fuzzwork.
            if (unitCost <= 0) {
                if (window.SimulationAPI && typeof window.SimulationAPI.getPrice === 'function' && typeId) {
                    const info = window.SimulationAPI.getPrice(typeId, 'buy');
                    unitCost = info && typeof info.value === 'number' ? info.value : 0;
                } else {
                    const fuzzInp = tr.querySelector('.fuzzwork-price');
                    unitCost = parseFloat(fuzzInp ? fuzzInp.value : 0) || 0;
                }
            }

            const cost = unitCost * qty;
            const allocatedQty = Math.max(0, Math.floor(Number(tr.dataset.stockAllocatedQty || 0)));
            const remainingQty = Math.max(0, Math.ceil(Number(tr.dataset.buyRemainingQty || Math.max(0, qty - allocatedQty))));
            const investmentCost = unitCost * remainingQty;
            const stockValue = unitCost * allocatedQty;
            const totalCostEl = tr.querySelector('.total-cost');
            if (totalCostEl) {
                totalCostEl.innerHTML = stockValue > 0
                    ? `${escapeHtml(formatPrice(investmentCost))}<div class="small text-muted">${escapeHtml(__('Full cost'))}: ${escapeHtml(formatPrice(cost))}</div>`
                    : escapeHtml(formatPrice(investmentCost));
            }
            materialCostTotal += cost;
            materialInvestmentTotal += investmentCost;
            stockValueTotal += stockValue;
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

    // Credit any craft-cycle surplus (extra produced due to cycle rounding).
    // IMPORTANT: This must depend on the current Buy/Prod switches.
    // We therefore compute cycles from SimulationAPI state when available.
    let surplusRevenue = 0;
    try {
        const productTypeId = Number(CRAFT_BP.productTypeId) || 0;

        if (window.SimulationAPI && typeof window.SimulationAPI.getPrice === 'function') {
            const hasLiveCycles = typeof window.SimulationAPI.getProductionCycles === 'function';
            const cycles = hasLiveCycles ? (window.SimulationAPI.getProductionCycles() || []) : null;

            if (hasLiveCycles) {
                // SimulationAPI is the source of truth. An empty array means
                // no production is happening (e.g. every item is in *Buy*
                // mode), so there is no rounding surplus to credit. Do NOT
                // fall back to the static `craft_cycles_summary` here — it is
                // computed server-side under the "produce everything" view
                // and would inject phantom surplus revenue that ignores the
                // user's Buy/Prod choices.
                cycles.forEach(entry => {
                    const typeId = Number(entry.typeId || entry.type_id || 0) || 0;
                    const surplusQty = Number(entry.surplus) || 0;
                    if (!typeId || surplusQty <= 0) return;
                    if (productTypeId && typeId === productTypeId) return;

                    const priceInfo = window.SimulationAPI.getPrice(typeId, 'sale');
                    const unitPrice = priceInfo && typeof priceInfo.value === 'number' ? priceInfo.value : 0;
                    if (unitPrice > 0) {
                        surplusRevenue += unitPrice * surplusQty;
                    }
                });
            } else {
                // Fallback for older payloads that do not expose
                // getProductionCycles. Note: does NOT reflect switch state.
                const cyclesSummary = window.BLUEPRINT_DATA?.craft_cycles_summary || {};
                Object.keys(cyclesSummary).forEach(key => {
                    const entry = cyclesSummary[key] || {};
                    const typeId = Number(entry.type_id || key) || 0;
                    const surplusQty = Number(entry.surplus) || 0;
                    if (!typeId || surplusQty <= 0) return;
                    if (productTypeId && typeId === productTypeId) return;

                    const priceInfo = window.SimulationAPI.getPrice(typeId, 'sale');
                    const unitPrice = priceInfo && typeof priceInfo.value === 'number' ? priceInfo.value : 0;
                    if (unitPrice > 0) {
                        surplusRevenue += unitPrice * surplusQty;
                    }
                });
            }
        }
    } catch (e) {
        console.warn('Unable to compute surplus revenue credit:', e);
    }

    const surplusWrapperEl = document.getElementById('financialSurplusWrapper');
    const surplusValueEl = document.getElementById('financialSummarySurplus');
    if (surplusValueEl) {
        surplusValueEl.textContent = formatPrice(surplusRevenue);
    }
    if (surplusWrapperEl) {
        surplusWrapperEl.classList.toggle('d-none', !(surplusRevenue > 0));
    }

    revTotal += surplusRevenue;

    const structureSummary = renderStructureFinancialSummary();
    const installationCostTotal = structureSummary.totalInstallation;
    const costTotal = materialCostTotal + installationCostTotal;
    const investmentNeededTotal = materialInvestmentTotal + installationCostTotal;

    const profit = revTotal - costTotal;
    // Margin = profit / revenue (not markup on cost).
    const marginValue = revTotal > 0 ? (profit / revTotal) * 100 : 0;
    const marginText = marginValue.toFixed(1);

    const grandTotalMaterialCostEl = document.querySelector('.grand-total-material-cost');
    const grandTotalMaterialInvestmentEl = document.querySelector('.grand-total-material-investment');
    const grandTotalInstallationEl = document.querySelector('.grand-total-installation');
    const grandTotalFacilityTaxEl = document.querySelector('.grand-total-facility-tax');
    const grandTotalStockValueEl = document.querySelector('.grand-total-stock-value');
    const grandTotalInvestmentNeededEl = document.querySelector('.grand-total-investment-needed');
    const grandTotalCostEl = document.querySelector('.grand-total-cost');
    const grandTotalRevEl = document.querySelector('.grand-total-rev');
    const profitEl = document.querySelector('.profit');
    const profitPctEl = document.querySelector('.profit-pct');

    if (grandTotalMaterialCostEl) {
        grandTotalMaterialCostEl.textContent = formatPrice(materialCostTotal);
    }

    if (grandTotalMaterialInvestmentEl) {
        grandTotalMaterialInvestmentEl.textContent = formatPrice(materialInvestmentTotal);
    }

    if (grandTotalInstallationEl) {
        grandTotalInstallationEl.textContent = formatPrice(installationCostTotal);
    }

    if (grandTotalFacilityTaxEl) {
        grandTotalFacilityTaxEl.textContent = formatPrice(structureSummary.facilityTax);
    }

    if (grandTotalStockValueEl) {
        grandTotalStockValueEl.textContent = formatPrice(stockValueTotal);
    }

    if (grandTotalInvestmentNeededEl) {
        grandTotalInvestmentNeededEl.textContent = formatPrice(investmentNeededTotal);
    }

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

    const summaryMaterialCostEl = document.getElementById('financialSummaryMaterialCost');
    if (summaryMaterialCostEl) {
        summaryMaterialCostEl.textContent = formatPrice(materialCostTotal);
    }

    const summaryStockValueEl = document.getElementById('financialSummaryStockValue');
    if (summaryStockValueEl) {
        summaryStockValueEl.textContent = formatPrice(stockValueTotal);
    }

    const summaryInvestmentNeededEl = document.getElementById('financialSummaryInvestmentNeeded');
    if (summaryInvestmentNeededEl) {
        summaryInvestmentNeededEl.textContent = formatPrice(investmentNeededTotal);
    }

    const summaryInstallationCostEl = document.getElementById('financialSummaryInstallationCost');
    if (summaryInstallationCostEl) {
        summaryInstallationCostEl.textContent = formatPrice(installationCostTotal);
    }

    const summaryFacilityTaxEl = document.getElementById('financialSummaryFacilityTaxInline');
    if (summaryFacilityTaxEl) {
        summaryFacilityTaxEl.textContent = formatPrice(structureSummary.facilityTax);
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
    const quickProfitEl = document.getElementById('quickProfit');
    const quickMarginEl = document.getElementById('quickMargin');

    if (heroProfitEl) {
        heroProfitEl.textContent = formatPrice(profit);
        const profitCard = heroProfitEl.closest('.hero-kpi');
        if (profitCard) {
            profitCard.classList.toggle('negative', profit < 0);
            profitCard.classList.toggle('positive', profit >= 0);
        }
    }

    if (quickProfitEl) {
        quickProfitEl.textContent = formatPrice(profit);
        quickProfitEl.classList.remove('text-success', 'text-danger');
        quickProfitEl.classList.add(profit >= 0 ? 'text-success' : 'text-danger');
    }

    if (heroMarginEl) {
        heroMarginEl.textContent = `${marginText}%`;
        const marginCard = heroMarginEl.closest('.hero-kpi');
        if (marginCard) {
            marginCard.classList.toggle('negative', marginValue < 0);
            marginCard.classList.toggle('positive', marginValue >= 0);
        }
    }

    if (quickMarginEl) {
        quickMarginEl.textContent = `${marginText}%`;
    }

    applyFinancialPlannerFilters();

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
        craftBPDebugLog('[CraftBP] Loading Fuzzwork prices from', requestUrl);
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
        craftBPDebugLog('[CraftBP] Fuzzwork prices received', data);
        return data;
    } catch (e) {
        console.error('Error fetching prices from Fuzzwork, URL:', requestUrl, e);
        return {};
    }
}

async function fetchFuzzworkAggregates(typeIds) {
    const requestUrl = buildPriceRequestUrl(typeIds, { full: true });
    if (!requestUrl) {
        console.error('No Fuzzwork URL configured; skipping aggregates fetch.');
        return {};
    }

    try {
        craftBPDebugLog('[CraftBP] Loading Fuzzwork aggregates from', requestUrl);
        const resp = await fetch(requestUrl, { credentials: 'same-origin' });
        if (!resp.ok) {
            console.error('Fuzzwork aggregates request failed:', resp.status, resp.statusText);
            return {};
        }
        const data = await resp.json();
        craftBPDebugLog('[CraftBP] Fuzzwork aggregates received', data);
        return data && typeof data === 'object' ? data : {};
    } catch (e) {
        console.error('Error fetching aggregates from Fuzzwork, URL:', requestUrl, e);
        return {};
    }
}

function flattenFuzzworkEntry(entry) {
    const flat = {};
    if (!entry || typeof entry !== 'object') return flat;

    Object.keys(entry).forEach(k => {
        const v = entry[k];
        if (v && typeof v === 'object' && !Array.isArray(v)) {
            Object.keys(v).forEach(sub => {
                flat[`${k}.${sub}`] = v[sub];
            });
        } else {
            flat[k] = v;
        }
    });

    return flat;
}

function sortFuzzworkColumnKeys(keys) {
    const groupOrder = ['buy', 'sell'];
    const subOrder = ['volume', 'min', 'max', 'avg', 'median', 'percentile', 'wavg', 'stddev'];

    function keyRank(k) {
        const parts = String(k).split('.');
        const group = parts[0] || '';
        const sub = parts[1] || '';

        const gIdx = groupOrder.includes(group) ? groupOrder.indexOf(group) : groupOrder.length;
        const sIdx = subOrder.includes(sub) ? subOrder.indexOf(sub) : subOrder.length;

        return { gIdx, sIdx, group, sub, full: k };
    }

    return [...keys]
        .map(k => ({ k, r: keyRank(k) }))
        .sort((a, b) => {
            if (a.r.gIdx !== b.r.gIdx) return a.r.gIdx - b.r.gIdx;
            if (a.r.group !== b.r.group) return a.r.group.localeCompare(b.r.group);
            if (a.r.sIdx !== b.r.sIdx) return a.r.sIdx - b.r.sIdx;
            if (a.r.sub !== b.r.sub) return a.r.sub.localeCompare(b.r.sub);
            return a.r.full.localeCompare(b.r.full);
        })
        .map(x => x.k);
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

        if (price <= 0) {
            inp.classList.add('bg-warning', 'border-warning');
            inp.setAttribute('title', __('Price not available (Fuzzwork)'));
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
                saleInput.setAttribute('title', __('Price not available (Fuzzwork)'));
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

function updateMaterialsTabFromState() {
    const planPaneHydrated = hydratePlanPane();

    const container = document.getElementById('materialsGroupsContainer');
    if (!container || !window.SimulationAPI || typeof window.SimulationAPI.getFinancialItems !== 'function') {
        return;
    }

    const emptyState = document.getElementById('materialsEmptyState');
    // See getCraftSourceRequirementRows: do not filter out final-outputs here
    // either. When the user sets project items to 'buy', they must show up as
    // materials to purchase.
    const fallbackGroupName = __('Other');
    const aggregated = new Map();
    const items = window.SimulationAPI.getFinancialItems() || [];

    items.forEach(item => {
        const typeId = Number(item.typeId ?? item.type_id);
        if (!typeId) {
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
        if (typeof window.updateCraftQuickStats === 'function') {
            window.updateCraftQuickStats();
        }
        if (planPaneHydrated && typeof recalcFinancials === 'function') {
            recalcFinancials();
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
                        <img src="https://images.evetech.net/types/${item.typeId}/icon?size=32" alt="${escapeHtml(item.typeName)}" loading="lazy" decoding="async" fetchpriority="low" class="rounded" style="width:30px;height:30px;background:#f3f4f6;" onerror="this.style.display='none';">
                        <span class="fw-bold">${escapeHtml(item.typeName)}</span>
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
                <span class="small text-body-secondary fw-semibold">${groupItems.length}</span>
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

    if (typeof window.updateCraftQuickStats === 'function') {
        window.updateCraftQuickStats();
    }

    if (planPaneHydrated && typeof recalcFinancials === 'function') {
        recalcFinancials();
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

function updateStockManagementTabFromState(force = false) {
    const stockPane = document.getElementById('stock-pane');
    if (!stockPane) {
        return;
    }
    if (!force && !stockPane.classList.contains('active')) {
        return;
    }
    renderCraftStockManagement();
}

function initializeStockManagementInteractions() {
    const rowsBody = document.getElementById('stockManagementRows');
    const resetButton = document.getElementById('stockResetAllocationsBtn');
    if (rowsBody && rowsBody.dataset.stockBound !== 'true') {
        const handleStockInput = (event) => {
            const input = event.target.closest('.craft-stock-allocation-input[data-type-id]');
            if (!input) {
                return;
            }
            const typeId = Number(input.getAttribute('data-type-id')) || 0;
            const maxQty = Math.max(0, Math.floor(Number(input.getAttribute('max') || 0)));
            const nextValue = Math.min(maxQty, Math.max(0, Math.floor(Number(input.value) || 0)));
            input.value = String(nextValue);
            setCraftStockAllocation(typeId, nextValue);
        };

        rowsBody.addEventListener('input', handleStockInput);
        rowsBody.addEventListener('change', handleStockInput);
        rowsBody.dataset.stockBound = 'true';
    }

    if (resetButton && resetButton.dataset.stockBound !== 'true') {
        resetButton.addEventListener('click', () => {
            window.craftBPFlags = window.craftBPFlags || {};
            window.craftBPFlags.stockAllocations = {};
            if (typeof updateFinancialTabFromState === 'function') {
                updateFinancialTabFromState();
            }
            renderCraftStockManagement();
            computeNeededPurchases();
            recalcFinancials();
            persistCraftPageSessionState();
        });
        resetButton.dataset.stockBound = 'true';
    }
}

function renderCraftStockManagement() {
    const rowsBody = document.getElementById('stockManagementRows');
    if (!rowsBody) {
        return;
    }

    const rows = getCraftSourceRequirementRows();
    const api = window.SimulationAPI;
    let totalRequiredQty = 0;
    let totalAllocatedQty = 0;
    let totalRemainingQty = 0;
    let totalStockValue = 0;

    const renderedRows = rows.map((item) => {
        const stockSummary = getCraftStockAllocationSummary(item.typeId, item.quantity);
        const maxAllocatable = Math.min(stockSummary.requiredQty, stockSummary.availableQty);
        const unitInfo = api && typeof api.getPrice === 'function' ? api.getPrice(item.typeId, 'buy') : { value: 0 };
        const unitPrice = unitInfo && typeof unitInfo.value === 'number' ? unitInfo.value : 0;
        const stockValue = unitPrice * stockSummary.allocatedQty;
        const remainingValue = unitPrice * stockSummary.remainingQty;

        totalRequiredQty += stockSummary.requiredQty;
        totalAllocatedQty += stockSummary.allocatedQty;
        totalRemainingQty += stockSummary.remainingQty;
        totalStockValue += stockValue;

        const characterMarkup = stockSummary.characters.length > 0
            ? stockSummary.characters.map((entry) => `
                <span class="badge bg-secondary-subtle text-secondary-emphasis">${escapeHtml(entry.characterName)}: ${formatInteger(entry.quantity)}</span>
            `).join('')
            : `<span class="small text-muted">${escapeHtml(getCraftCharacterStockSnapshot().scope_missing ? __('Assets scope missing') : __('No cached stock'))}</span>`;

        return `
            <tr>
                <td>
                    <div class="d-flex align-items-start gap-2">
                        <img src="https://images.evetech.net/types/${item.typeId}/icon?size=32" alt="${escapeHtml(item.typeName)}" loading="lazy" decoding="async" fetchpriority="low" class="rounded eve-type-icon eve-type-icon--28" onerror="this.style.display='none';">
                        <div>
                            <div class="fw-semibold">${escapeHtml(item.typeName)}</div>
                            <div class="small text-muted">${escapeHtml(item.marketGroup || __('Other'))}</div>
                        </div>
                    </div>
                </td>
                <td class="text-end">${formatInteger(stockSummary.requiredQty)}</td>
                <td class="text-end">${formatInteger(stockSummary.availableQty)}</td>
                <td class="text-end">
                    <input type="number" min="0" max="${formatInteger(maxAllocatable)}" step="1" class="form-control form-control-sm text-end craft-stock-allocation-input" data-type-id="${item.typeId}" value="${formatInteger(stockSummary.allocatedQty)}" ${maxAllocatable > 0 ? '' : 'disabled'}>
                </td>
                <td class="text-end">${formatInteger(stockSummary.remainingQty)}</td>
                <td class="text-end">${formatPrice(stockValue)}</td>
                <td>
                    <div class="d-flex flex-wrap gap-1">${characterMarkup}</div>
                    ${remainingValue > 0 ? `<div class="small text-muted mt-1">${escapeHtml(__('Still to buy'))}: ${escapeHtml(formatPrice(remainingValue))}</div>` : ''}
                </td>
            </tr>
        `;
    });

    rowsBody.innerHTML = renderedRows.length > 0
        ? renderedRows.join('')
        : `
            <tr>
                <td colspan="7" class="text-center text-muted py-4">${escapeHtml(__('No sourceable items require stock management right now.'))}</td>
            </tr>
        `;

    const setText = (id, value) => {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value;
        }
    };

    setText('stockSummaryLineCount', formatInteger(rows.length));
    setText('stockSummaryRequiredQty', formatInteger(totalRequiredQty));
    setText('stockSummaryAllocatedQty', formatInteger(totalAllocatedQty));
    setText('stockSummaryRemainingQty', formatInteger(totalRemainingQty));
    setText('stockSummaryValue', formatPrice(totalStockValue));

    const syncEl = document.getElementById('stockSummaryUpdated');
    if (syncEl) {
        syncEl.textContent = getCraftCharacterStockSnapshot().synced_at
            ? new Date(getCraftCharacterStockSnapshot().synced_at).toLocaleString()
            : __('No asset sync yet');
    }

    const scopeMissingEl = document.getElementById('stockScopeMissingNotice');
    if (scopeMissingEl) {
        scopeMissingEl.classList.toggle('d-none', !getCraftCharacterStockSnapshot().scope_missing);
    }

    initializeStockManagementInteractions();
}

/**
 * Compute needed purchase list based on user selections
 */
function computeNeededPurchases() {
    const tbody = document.querySelector('#needed-table tbody');
    const totalEl = document.querySelector('.purchase-total');
    if (!tbody) {
        return;
    }

    tbody.innerHTML = '';
    if (totalEl) {
        totalEl.textContent = formatPrice(0);
    }

    const api = window.SimulationAPI;
    if (!api || typeof api.getNeededMaterials !== 'function') {
        return;
    }

    const rows = getCraftSourceRequirementRows().map((row) => ({
        typeId: row.typeId,
        name: row.typeName,
        qty: row.quantity,
        marketGroup: row.marketGroup,
    }));
    const typeIds = rows.map(r => String(r.typeId));

    // Ensure we have fuzzwork prices where possible, but keep real prices as user overrides.
    const ensurePrices = (typeIdsToFetch) => {
        if (!typeIdsToFetch || typeIdsToFetch.length === 0) {
            return Promise.resolve({});
        }
        if (typeof fetchAllPrices !== 'function') {
            return Promise.resolve({});
        }
        return fetchAllPrices(typeIdsToFetch).then((prices) => {
            try {
                typeIdsToFetch.forEach((tid) => {
                    const raw = prices[tid] ?? prices[String(parseInt(tid, 10))];
                    const price = raw != null ? (parseFloat(raw) || 0) : 0;
                    if (price > 0 && api && typeof api.setPrice === 'function') {
                        api.setPrice(tid, 'fuzzwork', price);
                    }
                });
            } catch (e) {
                // ignore
            }
            return prices || {};
        });
    };

    ensurePrices(typeIds).finally(() => {
        let totalCost = 0;
        rows.forEach((item) => {
            const stockSummary = getCraftStockAllocationSummary(item.typeId, item.qty);
            const unitInfo = (api && typeof api.getPrice === 'function') ? api.getPrice(item.typeId, 'buy') : { value: 0 };
            const unit = unitInfo && typeof unitInfo.value === 'number' ? unitInfo.value : 0;
            const line = (unit > 0 ? unit : 0) * stockSummary.remainingQty;
            totalCost += line;

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${escapeHtml(item.name || String(item.typeId))}</td>
                <td class="text-end">
                    <div>${formatNumber(stockSummary.remainingQty)}</div>
                    ${stockSummary.allocatedQty > 0 ? `<div class="small text-muted">${escapeHtml(__('Stock'))}: ${formatNumber(stockSummary.allocatedQty)} / ${formatNumber(stockSummary.requiredQty)}</div>` : ''}
                </td>
                <td class="text-end">${formatPrice(unit)}</td>
                <td class="text-end">${formatPrice(line)}</td>
            `;
            tbody.appendChild(tr);
        });

        if (totalEl) {
            totalEl.textContent = formatPrice(totalCost);
        }
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
window.updateStockManagementTabFromState = updateStockManagementTabFromState;

function getCraftProductionCyclesSummary() {
    if (window.SimulationAPI && typeof window.SimulationAPI.getProductionCycles === 'function') {
        const summary = {};
        const productionCycles = window.SimulationAPI.getProductionCycles() || [];

        productionCycles.forEach((entry) => {
            const typeId = Number(entry.typeId || entry.type_id || 0) || 0;
            if (!(typeId > 0)) {
                return;
            }

            summary[String(typeId)] = {
                type_id: typeId,
                type_name: entry.typeName || entry.type_name || '',
                market_group: entry.marketGroup || entry.market_group || '',
                total_needed: Math.max(0, Math.ceil(Number(entry.totalNeeded || entry.total_needed || 0))) || 0,
                produced_per_cycle: Math.max(0, Math.ceil(Number(entry.producedPerCycle || entry.produced_per_cycle || 0))) || 0,
                cycles: Math.max(0, Math.ceil(Number(entry.cycles || 0))) || 0,
                total_produced: Math.max(0, Math.ceil(Number(entry.totalProduced || entry.total_produced || 0))) || 0,
                surplus: Math.max(0, Math.ceil(Number(entry.surplus || 0))) || 0,
            };
        });

        return summary;
    }

    const staticSummary = window.BLUEPRINT_DATA?.craft_cycles_summary || {};
    const summary = {};
    Object.keys(staticSummary).forEach((key) => {
        const entry = staticSummary[key] || {};
        const typeId = Number(entry.type_id || key) || 0;
        if (!(typeId > 0)) {
            return;
        }
        summary[String(typeId)] = {
            type_id: typeId,
            type_name: entry.type_name || entry.typeName || '',
            market_group: entry.market_group || entry.marketGroup || '',
            total_needed: Math.max(0, Math.ceil(Number(entry.total_needed || entry.totalNeeded || 0))) || 0,
            produced_per_cycle: Math.max(0, Math.ceil(Number(entry.produced_per_cycle || entry.producedPerCycle || 0))) || 0,
            cycles: Math.max(0, Math.ceil(Number(entry.cycles || 0))) || 0,
            total_produced: Math.max(0, Math.ceil(Number(entry.total_produced || entry.totalProduced || 0))) || 0,
            surplus: Math.max(0, Math.ceil(Number(entry.surplus || 0))) || 0,
        };
    });
    return summary;
}

function getCurrentBuildFinalProductRows(cyclesSummary) {
    const runs = Math.max(0, Math.ceil(Number(document.getElementById('runsInput')?.value || window.BLUEPRINT_DATA?.num_runs || 0))) || 0;

    return getFinalOutputEntries(window.BLUEPRINT_DATA)
        .map((output) => {
            const typeId = Number(output?.type_id || output?.typeId || 0) || 0;
            if (!(typeId > 0)) {
                return null;
            }

            const summaryEntry = cyclesSummary[String(typeId)] || cyclesSummary[typeId] || null;
            const finalRow = getFinalOutputRows().find((row) => (Number(row.getAttribute('data-type-id') || 0) || 0) === typeId) || null;
            const finalQtyEl = finalRow ? finalRow.querySelector('[data-qty]') : null;
            const finalQty = Math.max(
                0,
                Math.ceil(
                    Number(
                        (finalQtyEl && (finalQtyEl.getAttribute('data-qty') || finalQtyEl.dataset?.qty))
                        || output?.quantity
                        || output?.qty
                        || 0
                    )
                )
            ) || 0;
            const producedPerCycle = summaryEntry
                ? (Math.max(0, Math.ceil(Number(summaryEntry.produced_per_cycle || summaryEntry.producedPerCycle || 0))) || 0)
                : (Math.max(0, Math.ceil(Number(output?.produced_per_cycle || output?.producedPerCycle || 0))) || 0);
            const totalNeeded = summaryEntry
                ? (Math.max(0, Math.ceil(Number(summaryEntry.total_needed || summaryEntry.totalNeeded || 0))) || 0)
                : finalQty;
            const cycles = summaryEntry
                ? (Math.max(0, Math.ceil(Number(summaryEntry.cycles || 0))) || 0)
                : runs;
            const totalProduced = summaryEntry
                ? (Math.max(0, Math.ceil(Number(summaryEntry.total_produced || summaryEntry.totalProduced || 0))) || 0)
                : (totalNeeded || (producedPerCycle * cycles));
            const surplus = summaryEntry
                ? (Math.max(0, Math.ceil(Number(summaryEntry.surplus || 0))) || 0)
                : Math.max(totalProduced - totalNeeded, 0);

            if (!(totalNeeded > 0) && !(cycles > 0) && !(producedPerCycle > 0) && !(totalProduced > 0)) {
                return null;
            }

            return {
                type_id: typeId,
                type_name: output?.type_name || output?.typeName || window.BLUEPRINT_DATA?.name || '',
                total_needed: totalNeeded,
                produced_per_cycle: producedPerCycle || totalNeeded,
                cycles,
                total_produced: totalProduced || totalNeeded,
                surplus,
            };
        })
        .filter(Boolean);
}

function getCurrentBuildFinalProductRow(cyclesSummary) {
    return getCurrentBuildFinalProductRows(cyclesSummary)[0] || null;
}

function renderBuildCycleRow(entry, options = {}) {
    const typeId = Number(entry?.type_id || entry?.typeId || 0) || 0;
    const typeName = entry?.type_name || entry?.typeName || String(typeId || '');
    const totalNeeded = Math.max(0, Math.ceil(Number(entry?.total_needed || entry?.totalNeeded || 0))) || 0;
    const producedPerCycle = Math.max(0, Math.ceil(Number(entry?.produced_per_cycle || entry?.producedPerCycle || 0))) || 0;
    const cycles = Math.max(0, Math.ceil(Number(entry?.cycles || 0))) || 0;
    const totalProduced = Math.max(0, Math.ceil(Number(entry?.total_produced || entry?.totalProduced || 0))) || 0;
    const surplus = Math.max(0, Math.ceil(Number(entry?.surplus || 0))) || 0;
    const isFinalProduct = options.finalProduct === true;
    const iconAlt = escapeHtml(typeName);
    const nameHtml = isFinalProduct
        ? `<span class="small fw-bold d-block"><i class="fas fa-star text-warning me-1"></i>${escapeHtml(typeName)}</span>`
        : `<span class="small fw-semibold">${escapeHtml(typeName)}</span>`;
    const surplusHtml = surplus > 0
        ? `<span class="badge bg-success-subtle text-success fw-semibold">+${formatInteger(surplus)}</span>`
        : `<span class="badge bg-secondary-subtle text-secondary">0</span>`;

    return `
        <tr${isFinalProduct ? ' class="table-primary"' : ''} data-type-id="${typeId}">
            <td>
                <div class="d-flex align-items-center gap-2">
                    <img src="https://images.evetech.net/types/${typeId}/icon?size=32" alt="${iconAlt}" loading="lazy" decoding="async" fetchpriority="low" class="rounded eve-type-icon eve-type-icon--30" onerror="this.style.display='none';">
                    <div class="flex-grow-1">
                        ${nameHtml}
                    </div>
                </div>
            </td>
            <td class="text-end text-xs${isFinalProduct ? ' fw-bold' : ''}">${formatInteger(totalNeeded)}</td>
            <td class="text-end text-xs">${formatInteger(producedPerCycle)}</td>
            <td class="text-end text-xs${isFinalProduct ? ' fw-bold' : ''}">${formatInteger(cycles)}</td>
            <td class="text-end text-success text-xs${isFinalProduct ? ' fw-bold' : ' fw-semibold'}">${formatInteger(totalProduced)}</td>
            <td class="text-end text-xs">${surplusHtml}</td>
        </tr>
    `;
}

function sortBuildCycleEntries(entries) {
    const ordering = getDashboardMaterialsOrdering();
    const marketGroupMap = window.BLUEPRINT_DATA?.market_group_map || {};
    const fallbackGroupName = ordering.fallbackGroupName || __('Other');

    const groupNameFor = (entry) => {
        const explicitGroup = entry?.market_group || entry?.marketGroup || '';
        if (explicitGroup) {
            return explicitGroup;
        }
        const typeId = Number(entry?.type_id || entry?.typeId || 0) || 0;
        const info = marketGroupMap[String(typeId)] || marketGroupMap[typeId];
        if (info && typeof info === 'object') {
            return info.group_name || info.groupName || fallbackGroupName;
        }
        return fallbackGroupName;
    };

    return entries.slice().sort((a, b) => {
        const typeA = Number(a?.type_id || a?.typeId || 0) || 0;
        const typeB = Number(b?.type_id || b?.typeId || 0) || 0;
        const groupA = groupNameFor(a);
        const groupB = groupNameFor(b);

        const hasA = ordering.groupOrder.has(groupA);
        const hasB = ordering.groupOrder.has(groupB);
        if (hasA && hasB) {
            const groupIdxA = ordering.groupOrder.get(groupA);
            const groupIdxB = ordering.groupOrder.get(groupB);
            if (groupIdxA !== groupIdxB) {
                return groupIdxA - groupIdxB;
            }
        } else if (hasA !== hasB) {
            return hasA ? -1 : 1;
        } else {
            const groupCmp = String(groupA).localeCompare(String(groupB), undefined, { sensitivity: 'base' });
            if (groupCmp !== 0) {
                return groupCmp;
            }
        }

        const dashA = ordering.itemOrder.get(typeA);
        const dashB = ordering.itemOrder.get(typeB);
        const itemIdxA = dashA ? dashA.itemIdx : Number.POSITIVE_INFINITY;
        const itemIdxB = dashB ? dashB.itemIdx : Number.POSITIVE_INFINITY;
        if (itemIdxA !== itemIdxB) {
            return itemIdxA - itemIdxB;
        }

        return String(a?.type_name || a?.typeName || '').localeCompare(String(b?.type_name || b?.typeName || ''), undefined, { sensitivity: 'base' });
    });
}

function updateBuildTabFromState() {
    const buildPane = document.getElementById('build-pane');
    if (!buildPane) {
        return;
    }

    const cyclesSummary = getCraftProductionCyclesSummary();
    const finalProductRows = getCurrentBuildFinalProductRows(cyclesSummary);
    const finalOutputTypeIds = new Set(finalProductRows.map((entry) => Number(entry?.type_id || 0) || 0).filter((typeId) => typeId > 0));
    const cycleEntries = sortBuildCycleEntries(
        Object.values(cyclesSummary).filter((entry) => !finalOutputTypeIds.has(Number(entry?.type_id || entry?.typeId || 0) || 0))
    );

    if (finalProductRows.length === 0 && cycleEntries.length === 0) {
        buildPane.innerHTML = `<div class="alert alert-info">${escapeHtml(__('No cycles data available.'))}</div>`;
        return;
    }

    const rowsHtml = [
        finalProductRows.map((entry) => renderBuildCycleRow(entry, { finalProduct: true })).join(''),
        cycleEntries.map((entry) => renderBuildCycleRow(entry)).join('')
    ].join('');

    buildPane.innerHTML = `
        <section class="craft-section">
            <h3 class="craft-section-title">
                <i class="fas fa-sync text-info"></i> ${escapeHtml(__('Cycles'))}
                <span class="small text-body-secondary fw-semibold ms-2">${formatInteger(finalProductRows.length + cycleEntries.length)}</span>
            </h3>
            <div class="table-responsive craft-cycles-table craft-table-text-120">
                <table class="table table-sm align-middle mb-0">
                    <thead class="table-light">
                        <tr>
                            <th>${escapeHtml(__('Item'))}</th>
                            <th class="text-end">${escapeHtml(__('Needed'))}</th>
                            <th class="text-end">${escapeHtml(__('Per cycle'))}</th>
                            <th class="text-end">${escapeHtml(__('Cycles'))}</th>
                            <th class="text-end">${escapeHtml(__('Produced'))}</th>
                            <th class="text-end">${escapeHtml(__('Surplus'))}</th>
                        </tr>
                    </thead>
                    <tbody>${rowsHtml}</tbody>
                </table>
            </div>
        </section>
    `;
}

function getCraftProductionTimeMap() {
    const timeMap = window.BLUEPRINT_DATA?.production_time_map || window.BLUEPRINT_DATA?.productionTimeMap;
    if (!timeMap || typeof timeMap !== 'object') {
        return {};
    }
    return timeMap;
}

function getCraftCharacterAdvisor() {
    const advisor = window.BLUEPRINT_DATA?.craft_character_advisor || window.BLUEPRINT_DATA?.craftCharacterAdvisor;
    if (!advisor || typeof advisor !== 'object') {
        return { characters: [], items: {}, summary: {} };
    }
    return advisor;
}

function getCraftCharacterAdvisorEntry(typeId) {
    const advisor = getCraftCharacterAdvisor();
    const items = advisor.items || {};
    return items[String(typeId)] || items[typeId] || null;
}

function estimateCraftElapsedSeconds(row) {
    const cycles = Math.max(0, Math.ceil(Number(row?.cycles || 0) || 0));
    const cycleSeconds = Math.max(0, Math.ceil(Number(row?.effectiveCycleSeconds || 0) || 0));
    if (!(cycles > 0) || !(cycleSeconds > 0)) {
        return 0;
    }

    const availableSlots = Math.max(0, Math.ceil(Number(row?.availableSlots || 0) || 0));
    const totalSlots = Math.max(0, Math.ceil(Number(row?.totalSlots || 0) || 0));
    const parallelSlots = Math.max(1, availableSlots > 0 ? availableSlots : totalSlots || 1);
    return Math.max(1, Math.ceil(cycles / parallelSlots) * cycleSeconds);
}

function renderCraftCapabilitySummary(summary) {
    const characterCount = Math.max(0, Math.ceil(Number(summary?.characters || 0) || 0));
    if (!(characterCount > 0)) {
        return '';
    }

    return `
        <section class="craft-section mb-4">
            <h3 class="craft-section-title">
                <i class="fas fa-user-gear text-primary"></i> ${escapeHtml(__('Character production capability'))}
            </h3>
            <div class="craft-kpi-row mb-0">
                <div class="craft-kpi craft-kpi-sm">
                    <span class="craft-kpi-label">${escapeHtml(__('Characters'))}</span>
                    <span class="craft-kpi-value">${formatInteger(characterCount)}</span>
                </div>
                <div class="craft-kpi craft-kpi-sm">
                    <span class="craft-kpi-label">${escapeHtml(__('Eligible items'))}</span>
                    <span class="craft-kpi-value">${formatInteger(summary?.eligible_items || 0)}</span>
                </div>
                <div class="craft-kpi craft-kpi-sm">
                    <span class="craft-kpi-label">${escapeHtml(__('Blocked items'))}</span>
                    <span class="craft-kpi-value">${formatInteger(summary?.blocked_items || 0)}</span>
                </div>
                <div class="craft-kpi craft-kpi-sm">
                    <span class="craft-kpi-label">${escapeHtml(__('Missing skill data'))}</span>
                    <span class="craft-kpi-value">${formatInteger(summary?.missing_skill_data_characters || 0)}</span>
                </div>
            </div>
        </section>
    `;
}

function getCurrentBlueprintTEByTypeId(blueprintTypeId) {
    const numericBlueprintTypeId = Number(blueprintTypeId || 0) || 0;
    if (!(numericBlueprintTypeId > 0)) {
        return 0;
    }

    const meteConfig = getCurrentMETEConfig();
    const configEntry = meteConfig.blueprintConfigs[String(numericBlueprintTypeId)] || meteConfig.blueprintConfigs[numericBlueprintTypeId] || null;
    if (configEntry && configEntry.te !== undefined) {
        return Math.max(0, Math.min(Number(configEntry.te) || 0, 20));
    }

    const currentBlueprintTypeId = Number(getCurrentBlueprintTypeId() || 0) || 0;
    if (numericBlueprintTypeId === currentBlueprintTypeId) {
        return Math.max(0, Math.min(Number(meteConfig.mainTE || 0) || 0, 20));
    }

    return 0;
}

function getCurrentProductionCycleInfoByTypeId() {
    const cyclesSummary = getCraftProductionCyclesSummary();
    const byTypeId = new Map();

    Object.values(cyclesSummary).forEach((entry) => {
        const typeId = Number(entry?.type_id || entry?.typeId || 0) || 0;
        if (typeId > 0) {
            byTypeId.set(typeId, entry);
        }
    });

    getCurrentBuildFinalProductRows(cyclesSummary).forEach((finalProductRow) => {
        const typeId = Number(finalProductRow?.type_id || 0) || 0;
        if (typeId > 0) {
            byTypeId.set(typeId, finalProductRow);
        }
    });

    return byTypeId;
}

function getCraftProductionTimeRows() {
    const timeMap = getCraftProductionTimeMap();
    const advisor = getCraftCharacterAdvisor();
    const currentCyclesByTypeId = getCurrentProductionCycleInfoByTypeId();
    const productTypeId = getProductTypeIdValue();

    return Object.values(timeMap)
        .map((entry) => {
            const typeId = Number(entry?.type_id || entry?.typeId || 0) || 0;
            if (!(typeId > 0)) {
                return null;
            }

            const cycleInfo = currentCyclesByTypeId.get(typeId) || null;
            if (!cycleInfo) {
                return null;
            }

            const advisorEntry = getCraftCharacterAdvisorEntry(typeId);
            const bestCharacter = advisorEntry?.best_character || advisorEntry?.bestCharacter || null;

            const blueprintTypeId = Number(entry?.blueprint_type_id || entry?.blueprintTypeId || 0) || 0;
            const baseTimeSeconds = Math.max(0, Math.ceil(Number(entry?.base_time_seconds || entry?.baseTimeSeconds || 0))) || 0;
            const tePercent = getCurrentBlueprintTEByTypeId(blueprintTypeId);
            const structureOption = (window.SimulationAPI && typeof window.SimulationAPI.getStructureOption === 'function')
                ? window.SimulationAPI.getStructureOption(typeId)
                : null;
            const structureTimeBonusPercent = Number(
                structureOption?.time_bonus_percent
                || structureOption?.timeBonusPercent
                || structureOption?.rig_time_bonus_percent
                || structureOption?.rigTimeBonusPercent
                || 0
            ) || 0;
            const characterTimeBonusPercent = Number(
                bestCharacter?.time_bonus_percent
                || bestCharacter?.timeBonusPercent
                || 0
            ) || 0;
            const effectiveCycleSeconds = baseTimeSeconds > 0
                ? Math.max(
                    1,
                    Math.ceil(
                        baseTimeSeconds
                        * Math.max(0, 1 - (tePercent / 100))
                        * Math.max(0, 1 - (structureTimeBonusPercent / 100))
                        * Math.max(0, 1 - (characterTimeBonusPercent / 100))
                    )
                )
                : 0;
            const cycles = Math.max(0, Math.ceil(Number(cycleInfo?.cycles || 0))) || 0;
            const totalSeconds = effectiveCycleSeconds > 0 ? effectiveCycleSeconds * cycles : 0;
            const launchMetrics = getEveJobLaunchMetrics(effectiveCycleSeconds, cycles);

            const row = {
                typeId,
                typeName: entry?.type_name || entry?.typeName || cycleInfo?.type_name || cycleInfo?.typeName || String(typeId),
                blueprintTypeId,
                activityId: Number(entry?.activity_id || entry?.activityId || 0) || 0,
                activityLabel: entry?.activity_label || entry?.activityLabel || '',
                producedPerCycle: Math.max(0, Math.ceil(Number(cycleInfo?.produced_per_cycle || cycleInfo?.producedPerCycle || entry?.produced_per_cycle || entry?.producedPerCycle || 0))) || 0,
                totalNeeded: Math.max(0, Math.ceil(Number(cycleInfo?.total_needed || cycleInfo?.totalNeeded || 0))) || 0,
                cycles,
                totalProduced: Math.max(0, Math.ceil(Number(cycleInfo?.total_produced || cycleInfo?.totalProduced || 0))) || 0,
                surplus: Math.max(0, Math.ceil(Number(cycleInfo?.surplus || 0))) || 0,
                baseTimeSeconds,
                tePercent,
                structureTimeBonusPercent,
                characterTimeBonusPercent,
                effectiveCycleSeconds,
                totalSeconds,
                maxRunsPerJob: launchMetrics.maxRunsPerJob,
                jobsRequired: launchMetrics.jobsRequired,
                lastRunStartSeconds: launchMetrics.lastRunStartSeconds,
                exceedsLaunchWindow: launchMetrics.exceedsLaunchWindow,
                structureName: structureOption?.name || __('Unassigned'),
                structureSystemName: structureOption?.system_name || structureOption?.systemName || '',
                hasBaseTime: baseTimeSeconds > 0,
                isFinalProduct: typeId === productTypeId,
                characterName: bestCharacter?.name || __('No eligible character'),
                availableSlots: Math.max(0, Math.ceil(Number(bestCharacter?.available_slots || bestCharacter?.availableSlots || 0) || 0)),
                totalSlots: Math.max(0, Math.ceil(Number(bestCharacter?.total_slots || bestCharacter?.totalSlots || 0) || 0)),
                hasEligibleCharacter: Boolean(bestCharacter),
                usesTotalSlotsFallback: !(Math.max(0, Math.ceil(Number(bestCharacter?.available_slots || bestCharacter?.availableSlots || 0) || 0)) > 0) && (Math.max(0, Math.ceil(Number(bestCharacter?.total_slots || bestCharacter?.totalSlots || 0) || 0)) > 0),
                blockedCharacters: Array.isArray(advisorEntry?.blocked_characters || advisorEntry?.blockedCharacters)
                    ? (advisorEntry?.blocked_characters || advisorEntry?.blockedCharacters)
                    : [],
            };
            row.elapsedSeconds = estimateCraftElapsedSeconds(row);

            return row;
        })
        .filter(Boolean)
        .sort((left, right) => {
            if (left.isFinalProduct !== right.isFinalProduct) {
                return left.isFinalProduct ? -1 : 1;
            }
            if (right.elapsedSeconds !== left.elapsedSeconds) {
                return right.elapsedSeconds - left.elapsedSeconds;
            }
            return String(left.typeName).localeCompare(String(right.typeName), undefined, { sensitivity: 'base' });
        });
}

function buildCraftProductionSteps(rows) {
    const rowByTypeId = new Map(rows.map((row) => [row.typeId, row]));
    const craftedTypeIds = new Set(rowByTypeId.keys());
    const dependencyMap = new Map();

    rows.forEach((row) => {
        const recipe = getRecipeEntryForType(row.typeId);
        const deps = getRecipeInputsPerCycle(recipe, false)
            .map((input) => Number(input?.type_id || input?.typeId || 0) || 0)
            .filter((typeId) => craftedTypeIds.has(typeId));
        dependencyMap.set(row.typeId, Array.from(new Set(deps)));
    });

    const memo = new Map();
    const inProgress = new Set();
    const resolveStep = (typeId) => {
        if (memo.has(typeId)) {
            return memo.get(typeId);
        }
        if (inProgress.has(typeId)) {
            return 1;
        }
        inProgress.add(typeId);
        const deps = dependencyMap.get(typeId) || [];
        const stepIndex = deps.length === 0 ? 1 : (1 + Math.max(...deps.map(resolveStep)));
        inProgress.delete(typeId);
        memo.set(typeId, stepIndex);
        return stepIndex;
    };

    const stepsByIndex = new Map();
    rows.forEach((row) => {
        const stepIndex = resolveStep(row.typeId);
        if (!stepsByIndex.has(stepIndex)) {
            stepsByIndex.set(stepIndex, []);
        }
        stepsByIndex.get(stepIndex).push(row);
    });

    const steps = Array.from(stepsByIndex.entries())
        .sort((left, right) => left[0] - right[0])
        .map(([stepIndex, stepRows]) => {
            const orderedRows = stepRows.slice().sort((left, right) => {
                if ((right.elapsedSeconds || right.totalSeconds) !== (left.elapsedSeconds || left.totalSeconds)) {
                    return (right.elapsedSeconds || right.totalSeconds) - (left.elapsedSeconds || left.totalSeconds);
                }
                return String(left.typeName).localeCompare(String(right.typeName), undefined, { sensitivity: 'base' });
            });
            const timedRows = orderedRows.filter((row) => row.hasBaseTime);
            return {
                stepIndex,
                rows: orderedRows,
                parallelSeconds: timedRows.length > 0 ? Math.max(...timedRows.map((row) => row.elapsedSeconds || row.totalSeconds)) : 0,
                serialSeconds: timedRows.reduce((sum, row) => sum + row.totalSeconds, 0),
            };
        });

    return {
        steps,
        totalWorkloadSeconds: rows.filter((row) => row.hasBaseTime).reduce((sum, row) => sum + row.totalSeconds, 0),
        criticalPathSeconds: steps.reduce((sum, step) => sum + step.parallelSeconds, 0),
        totalJobCount: rows.filter((row) => row.hasBaseTime).reduce((sum, row) => sum + (Number(row.jobsRequired) || 0), 0),
        splitJobCount: rows.filter((row) => row.hasBaseTime && (Number(row.jobsRequired) || 0) > 1).length,
    };
}

function renderCraftTimingTableRow(row) {
    const totalTimeHtml = row.hasBaseTime
        ? `<span class="fw-semibold">${escapeHtml(formatDurationCompact(row.elapsedSeconds || row.totalSeconds))}</span><div class="small text-muted">${escapeHtml(__('Workload'))} ${escapeHtml(formatDurationCompact(row.totalSeconds))}</div>`
        : `<span class="text-muted">${escapeHtml(__('Unavailable'))}</span>`;
    const cycleTimeHtml = row.hasBaseTime
        ? escapeHtml(formatDurationCompact(row.effectiveCycleSeconds))
        : escapeHtml(__('Unavailable'));
    const jobsHtml = row.hasBaseTime
        ? `${formatInteger(row.jobsRequired)} ${escapeHtml(row.jobsRequired === 1 ? __('job') : __('jobs'))}`
        : `<span class="text-muted">${escapeHtml(__('Unavailable'))}</span>`;
    const structureLabel = row.structureSystemName
        ? `${row.structureName} · ${row.structureSystemName}`
        : row.structureName;
    const launchWindowHint = row.hasBaseTime
        ? (row.exceedsLaunchWindow
            ? `${escapeHtml(__('Max'))} ${formatInteger(row.maxRunsPerJob)} / ${escapeHtml(__('job'))} · ${escapeHtml(__('Last run starts at'))} ${escapeHtml(formatDurationCompact(row.lastRunStartSeconds))}`
            : `${escapeHtml(__('Last run starts at'))} ${escapeHtml(formatDurationCompact(row.lastRunStartSeconds))}`)
        : escapeHtml(__('Unavailable'));
    const characterHint = row.hasEligibleCharacter
        ? `${escapeHtml(__('Best character'))} ${escapeHtml(row.characterName)} · ${escapeHtml(__('Skill bonus'))} ${formatPercent(row.characterTimeBonusPercent, 2)} · ${escapeHtml(__('Slots available'))} ${formatInteger(row.availableSlots)} / ${formatInteger(row.totalSlots)}`
        : `<span class="text-warning-emphasis">${escapeHtml(__('No eligible character can currently run this activity.'))}</span>`;
    const characterHtml = row.hasEligibleCharacter
        ? `<div class="small fw-semibold">${escapeHtml(row.characterName)}</div><div class="small text-muted">${formatInteger(row.availableSlots)} / ${formatInteger(row.totalSlots)} ${escapeHtml(row.usesTotalSlotsFallback ? __('using total slots') : __('available now'))}</div>`
        : `<span class="badge bg-warning-subtle text-warning-emphasis">${escapeHtml(__('Blocked'))}</span>`;

    return `
        <tr${row.isFinalProduct ? ' class="table-primary"' : ''} data-type-id="${row.typeId}">
            <td>
                <div class="d-flex align-items-center gap-2">
                    <img src="https://images.evetech.net/types/${row.typeId}/icon?size=32" alt="${escapeHtml(row.typeName)}" loading="lazy" decoding="async" fetchpriority="low" class="rounded eve-type-icon eve-type-icon--30" onerror="this.style.display='none';">
                    <div>
                        <div class="small ${row.isFinalProduct ? 'fw-bold' : 'fw-semibold'}">${row.isFinalProduct ? `<i class="fas fa-star text-warning me-1"></i>` : ''}${escapeHtml(row.typeName)}</div>
                        <div class="small text-muted">${escapeHtml(row.activityLabel || __('Production'))}</div>
                        <div class="small text-muted">${launchWindowHint}</div>
                        <div class="small text-muted">${characterHint}</div>
                    </div>
                </div>
            </td>
            <td class="text-end text-xs">${formatPercent(row.tePercent, 0)}</td>
            <td class="text-end text-xs">${formatPercent(row.structureTimeBonusPercent, 2)}</td>
            <td class="text-end text-xs">${formatPercent(row.characterTimeBonusPercent, 2)}</td>
            <td class="text-end text-xs">${cycleTimeHtml}</td>
            <td class="text-end text-xs">${formatInteger(row.cycles)}</td>
            <td class="text-end text-xs">${jobsHtml}</td>
            <td class="text-end text-xs">${totalTimeHtml}</td>
            <td class="text-end text-xs">${characterHtml}</td>
            <td class="text-end text-xs"><span class="badge bg-secondary-subtle text-secondary-emphasis">${escapeHtml(structureLabel)}</span></td>
        </tr>
    `;
}

function updateCraftTimingTabFromState() {
    const pane = document.getElementById('timing-pane');
    if (!pane) {
        return;
    }

    const timeMap = getCraftProductionTimeMap();
    const rows = getCraftProductionTimeRows();
    const advisor = getCraftCharacterAdvisor();
    if (Object.keys(timeMap).length === 0) {
        pane.innerHTML = `<div class="alert alert-warning mb-0">${escapeHtml(__('Production durations are unavailable. Sync SDE blueprint activities to populate time data.'))}</div>`;
        return;
    }
    if (rows.length === 0) {
        pane.innerHTML = `<div class="alert alert-info mb-0">${escapeHtml(__('No production timings are available for the current production plan.'))}</div>`;
        return;
    }

    const plan = buildCraftProductionSteps(rows);
    const missingRows = rows.filter((row) => !row.hasBaseTime);
    const splitRows = rows.filter((row) => row.hasBaseTime && row.exceedsLaunchWindow);

    pane.innerHTML = `
        ${renderCraftCapabilitySummary(advisor.summary)}
        <div class="craft-kpi-row mb-4">
            <div class="craft-kpi craft-kpi-sm">
                <span class="craft-kpi-label">${escapeHtml(__('Timed items'))}</span>
                <span class="craft-kpi-value">${formatInteger(rows.length - missingRows.length)}</span>
            </div>
            <div class="craft-kpi craft-kpi-sm">
                <span class="craft-kpi-label">${escapeHtml(__('Total workload'))}</span>
                <span class="craft-kpi-value">${escapeHtml(formatDurationCompact(plan.totalWorkloadSeconds))}</span>
            </div>
            <div class="craft-kpi craft-kpi-sm">
                <span class="craft-kpi-label">${escapeHtml(__('Critical path'))}</span>
                <span class="craft-kpi-value">${escapeHtml(formatDurationCompact(plan.criticalPathSeconds))}</span>
            </div>
            <div class="craft-kpi craft-kpi-sm">
                <span class="craft-kpi-label">${escapeHtml(__('Total jobs'))}</span>
                <span class="craft-kpi-value">${formatInteger(plan.totalJobCount)}</span>
            </div>
            <div class="craft-kpi craft-kpi-sm">
                <span class="craft-kpi-label">${escapeHtml(__('Missing times'))}</span>
                <span class="craft-kpi-value">${formatInteger(missingRows.length)}</span>
            </div>
        </div>
        ${(advisor.summary?.missing_skill_data_characters || 0) > 0 ? `<div class="alert alert-warning">${escapeHtml(__('Some characters are missing skill data. Refresh skills to improve this estimate.'))}</div>` : ''}
        ${(advisor.summary?.blocked_items || 0) > 0 ? `<div class="alert alert-warning">${escapeHtml(__('Some items currently have no eligible character with the required skills and slots.'))}</div>` : ''}
        ${splitRows.length > 0 ? `<div class="alert alert-info">${escapeHtml(__('EVE Online uses a 30-day launch window per job, not a hard cap on total duration. Items flagged here need multiple jobs because the last run would otherwise start after 30 days.'))}</div>` : ''}
        ${missingRows.length > 0 ? `<div class="alert alert-warning">${escapeHtml(__('Some items are missing base activity times, so their durations are shown as unavailable.'))}</div>` : ''}
        <section class="craft-section">
            <h3 class="craft-section-title">
                <i class="fas fa-clock text-primary"></i> ${escapeHtml(__('Production Times'))}
            </h3>
            <div class="table-responsive craft-cycles-table craft-table-text-120">
                <table class="table table-sm align-middle mb-0">
                    <thead class="table-light">
                        <tr>
                            <th>${escapeHtml(__('Item'))}</th>
                            <th class="text-end">TE</th>
                            <th class="text-end">${escapeHtml(__('Structure bonus'))}</th>
                            <th class="text-end">${escapeHtml(__('Skill bonus'))}</th>
                            <th class="text-end">${escapeHtml(__('Time / cycle'))}</th>
                            <th class="text-end">${escapeHtml(__('Cycles'))}</th>
                            <th class="text-end">${escapeHtml(__('Jobs'))}</th>
                            <th class="text-end">${escapeHtml(__('Elapsed'))}</th>
                            <th class="text-end">${escapeHtml(__('Character'))}</th>
                            <th class="text-end">${escapeHtml(__('Structure'))}</th>
                        </tr>
                    </thead>
                    <tbody>${rows.map(renderCraftTimingTableRow).join('')}</tbody>
                </table>
            </div>
        </section>
    `;
}

function renderCraftStepItem(row) {
    const durationLabel = row.hasBaseTime ? formatDurationCompact(row.elapsedSeconds || row.totalSeconds) : __('Unavailable');
    const cycleLabel = row.hasBaseTime ? formatDurationCompact(row.effectiveCycleSeconds) : __('Unavailable');
    const jobLabel = row.hasBaseTime
        ? `${formatInteger(row.jobsRequired)} ${row.jobsRequired === 1 ? __('job') : __('jobs')}`
        : __('Unavailable');
    const structureLabel = row.structureSystemName
        ? `${row.structureName} · ${row.structureSystemName}`
        : row.structureName;
    const launchWindowLabel = row.hasBaseTime
        ? (row.exceedsLaunchWindow
            ? `${__('Max')} ${formatInteger(row.maxRunsPerJob)} / ${__('job')} · ${__('Last run starts at')} ${formatDurationCompact(row.lastRunStartSeconds)}`
            : `${__('Last run starts at')} ${formatDurationCompact(row.lastRunStartSeconds)}`)
        : __('Unavailable');
    const characterLabel = row.hasEligibleCharacter
        ? `${__('Best character')} ${row.characterName} · ${__('Skill bonus')} ${formatPercent(row.characterTimeBonusPercent, 2)} · ${__('Slots available')} ${formatInteger(row.availableSlots)} / ${formatInteger(row.totalSlots)}`
        : __('No eligible character can currently run this activity.');

    return `
        <div class="border rounded-3 p-3 bg-body-tertiary">
            <div class="d-flex justify-content-between align-items-start gap-3">
                <div>
                    <div class="fw-semibold">${row.isFinalProduct ? `<i class="fas fa-star text-warning me-1"></i>` : ''}${escapeHtml(row.typeName)}</div>
                    <div class="small text-muted">${escapeHtml(row.activityLabel || __('Production'))}</div>
                </div>
                <span class="badge bg-primary-subtle text-primary-emphasis">${escapeHtml(durationLabel)}</span>
            </div>
            <div class="small text-muted mt-2">
                ${escapeHtml(__('Cycles'))} ${formatInteger(row.cycles)} · ${escapeHtml(__('Time / cycle'))} ${escapeHtml(cycleLabel)} · ${escapeHtml(__('Jobs'))} ${escapeHtml(jobLabel)} · TE ${formatPercent(row.tePercent, 0)} · ${escapeHtml(__('Structure bonus'))} ${formatPercent(row.structureTimeBonusPercent, 2)}
            </div>
            <div class="small text-muted mt-1">${escapeHtml(characterLabel)}</div>
            <div class="small text-muted mt-1">${escapeHtml(launchWindowLabel)}</div>
            <div class="small text-muted mt-1">${escapeHtml(structureLabel)}</div>
        </div>
    `;
}

function updateCraftStepsTabFromState() {
    const pane = document.getElementById('steps-pane');
    if (!pane) {
        return;
    }

    const timeMap = getCraftProductionTimeMap();
    const rows = getCraftProductionTimeRows();
    if (Object.keys(timeMap).length === 0) {
        pane.innerHTML = `<div class="alert alert-warning mb-0">${escapeHtml(__('Production durations are unavailable. Sync SDE blueprint activities to populate time data.'))}</div>`;
        return;
    }
    if (rows.length === 0) {
        pane.innerHTML = `<div class="alert alert-info mb-0">${escapeHtml(__('No production steps are available for the current production plan.'))}</div>`;
        return;
    }

    const plan = buildCraftProductionSteps(rows);
    const missingRows = rows.filter((row) => !row.hasBaseTime);
    const splitRows = rows.filter((row) => row.hasBaseTime && row.exceedsLaunchWindow);
    const stepsHtml = plan.steps.map((step) => `
        <section class="craft-section mb-3">
            <div class="craft-section-header-with-actions">
                <h3 class="craft-section-title">
                    <i class="fas fa-list-ol text-success"></i> ${escapeHtml(__('Step'))} ${formatInteger(step.stepIndex)}
                </h3>
                <div class="small text-muted">
                    ${escapeHtml(__('Parallel stage'))} ${escapeHtml(formatDurationCompact(step.parallelSeconds))} · ${escapeHtml(__('Serial sum'))} ${escapeHtml(formatDurationCompact(step.serialSeconds))}
                </div>
            </div>
            <div class="small text-muted mb-3">${step.stepIndex === 1 ? escapeHtml(__('Start with the deepest craftable inputs.')) : escapeHtml(__('Run this step after the previous one is secured.'))}</div>
            <div class="d-grid gap-3">${step.rows.map(renderCraftStepItem).join('')}</div>
        </section>
    `).join('');

    pane.innerHTML = `
        <div class="craft-kpi-row mb-4">
            <div class="craft-kpi craft-kpi-sm">
                <span class="craft-kpi-label">${escapeHtml(__('Steps'))}</span>
                <span class="craft-kpi-value">${formatInteger(plan.steps.length)}</span>
            </div>
            <div class="craft-kpi craft-kpi-sm">
                <span class="craft-kpi-label">${escapeHtml(__('Critical path'))}</span>
                <span class="craft-kpi-value">${escapeHtml(formatDurationCompact(plan.criticalPathSeconds))}</span>
            </div>
            <div class="craft-kpi craft-kpi-sm">
                <span class="craft-kpi-label">${escapeHtml(__('Total jobs'))}</span>
                <span class="craft-kpi-value">${formatInteger(plan.totalJobCount)}</span>
            </div>
            <div class="craft-kpi craft-kpi-sm">
                <span class="craft-kpi-label">${escapeHtml(__('Total workload'))}</span>
                <span class="craft-kpi-value">${escapeHtml(formatDurationCompact(plan.totalWorkloadSeconds))}</span>
            </div>
            <div class="craft-kpi craft-kpi-sm">
                <span class="craft-kpi-label">${escapeHtml(__('Missing times'))}</span>
                <span class="craft-kpi-value">${formatInteger(missingRows.length)}</span>
            </div>
        </div>
        ${splitRows.length > 0 ? `<div class="alert alert-info">${escapeHtml(__('Some items must be split into multiple jobs because EVE Online only requires the last run to start within a 30-day launch window. Total duration may exceed 30 days and still remain valid.'))}</div>` : ''}
        ${missingRows.length > 0 ? `<div class="alert alert-warning">${escapeHtml(__('Some steps include items without base activity times, so their duration cannot be scheduled precisely yet.'))}</div>` : ''}
        ${stepsHtml}
    `;
}

window.getCraftProductionCyclesSummary = getCraftProductionCyclesSummary;
window.updateBuildTabFromState = updateBuildTabFromState;
window.updateCraftTimingTabFromState = updateCraftTimingTabFromState;
window.updateCraftStepsTabFromState = updateCraftStepsTabFromState;

// One-time sort for the server-rendered Cycles table on the Build tab.
// This keeps the UI consistent with the dashboard category ordering.
function sortBuildCyclesTable() {
    const buildPane = document.getElementById('build-pane');
    if (!buildPane) {
        return;
    }

    const table = buildPane.querySelector('table');
    const tbody = table ? table.querySelector('tbody') : null;
    if (!tbody) {
        return;
    }

    const rows = Array.from(tbody.querySelectorAll('tr[data-type-id]'));
    if (rows.length === 0) {
        return;
    }

    const finalOutputTypeIds = getFinalOutputTypeIds();
    const payload = window.BLUEPRINT_DATA || {};
    const marketGroupMap = payload.market_group_map || {};
    const ordering = getDashboardMaterialsOrdering();

    const groupNameFor = (typeId) => {
        const info = marketGroupMap[String(typeId)] || marketGroupMap[typeId];
        if (info && typeof info === 'object') {
            return info.group_name || info.groupName || ordering.fallbackGroupName;
        }
        return ordering.fallbackGroupName;
    };

    const nameForRow = (row) => {
        const label = row.querySelector('.small.fw-semibold, .small.fw-bold');
        return (label && label.textContent ? label.textContent.trim() : '').toLowerCase();
    };

    const isFinalProductRow = (row) => {
        if (row.classList.contains('table-primary') || row.getAttribute('data-final-output') === 'true') {
            return true;
        }
        const tid = Number(row.getAttribute('data-type-id')) || 0;
        return finalOutputTypeIds.has(tid);
    };

    const finalRows = rows.filter(isFinalProductRow);
    const otherRows = rows.filter(r => !isFinalProductRow(r));

    otherRows.sort((a, b) => {
        const typeA = Number(a.getAttribute('data-type-id')) || 0;
        const typeB = Number(b.getAttribute('data-type-id')) || 0;
        const groupA = groupNameFor(typeA);
        const groupB = groupNameFor(typeB);

        const hasA = ordering.groupOrder.has(groupA);
        const hasB = ordering.groupOrder.has(groupB);

        if (hasA && hasB) {
            const groupIdxA = ordering.groupOrder.get(groupA);
            const groupIdxB = ordering.groupOrder.get(groupB);
            if (groupIdxA !== groupIdxB) {
                return groupIdxA - groupIdxB;
            }
        } else if (hasA !== hasB) {
            // Known dashboard groups first, then the rest.
            return hasA ? -1 : 1;
        } else {
            // Neither group exists in the dashboard list -> sort groups alphabetically.
            const groupCmp = String(groupA).localeCompare(String(groupB), undefined, { sensitivity: 'base' });
            if (groupCmp !== 0) {
                return groupCmp;
            }
        }

        // If the row type happens to exist in dashboard materials list, keep its exact item order.
        const dashA = ordering.itemOrder.get(typeA);
        const dashB = ordering.itemOrder.get(typeB);
        const itemIdxA = dashA ? dashA.itemIdx : Number.POSITIVE_INFINITY;
        const itemIdxB = dashB ? dashB.itemIdx : Number.POSITIVE_INFINITY;
        if (itemIdxA !== itemIdxB) {
            return itemIdxA - itemIdxB;
        }

        return nameForRow(a).localeCompare(nameForRow(b), undefined, { sensitivity: 'base' });
    });

    // Re-append in desired order.
    finalRows.forEach(r => tbody.appendChild(r));
    otherRows.forEach(r => tbody.appendChild(r));
}

try {
    document.addEventListener('DOMContentLoaded', () => {
        hydrateVisibleCraftStartupTab();

        const planTabBtn = document.querySelector('#plan-tab-btn');
        if (planTabBtn) {
            planTabBtn.addEventListener('shown.bs.tab', () => {
                if (typeof updateMaterialsTabFromState === 'function') {
                    updateMaterialsTabFromState();
                }
            });
        }

        const buyTabBtn = document.querySelector('#buy-tab-btn');
        if (buyTabBtn) {
            buyTabBtn.addEventListener('shown.bs.tab', () => {
                if (typeof updateFinancialTabFromState === 'function') {
                    updateFinancialTabFromState();
                }
            });
        }

        const buildTabBtn = document.querySelector('#build-tab-btn');
        if (buildTabBtn) {
            buildTabBtn.addEventListener('shown.bs.tab', () => {
                if (typeof updateBuildTabFromState === 'function') {
                    updateBuildTabFromState();
                } else {
                    sortBuildCyclesTable();
                }
            });
        }

        const timingTabBtn = document.querySelector('#timing-tab-btn');
        if (timingTabBtn) {
            timingTabBtn.addEventListener('shown.bs.tab', () => {
                if (typeof updateCraftTimingTabFromState === 'function') {
                    updateCraftTimingTabFromState();
                }
            });
        }

        const stepsTabBtn = document.querySelector('#steps-tab-btn');
        if (stepsTabBtn) {
            stepsTabBtn.addEventListener('shown.bs.tab', () => {
                if (typeof updateCraftStepsTabFromState === 'function') {
                    updateCraftStepsTabFromState();
                }
            });
        }

        const structureTabBtn = document.querySelector('#structure-tab-btn');
        if (structureTabBtn) {
            structureTabBtn.addEventListener('shown.bs.tab', () => {
                if (typeof renderStructurePlanner === 'function') {
                    renderStructurePlanner();
                }
            });
        }

        const configureTabBtn = document.querySelector('#configure-tab-btn');
        if (configureTabBtn) {
            configureTabBtn.addEventListener('shown.bs.tab', () => {
                if (typeof window.updateConfigTabFromState === 'function') {
                    window.updateConfigTabFromState();
                }
                if (typeof window.validateBlueprintRuns === 'function') {
                    window.validateBlueprintRuns();
                }
            });
        }
    });
} catch (e) {
    // ignore
}
