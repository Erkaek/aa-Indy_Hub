/**
 * Craft Blueprint inline controller
 * Bridges page-specific UI (modals, summaries) with SimulationAPI/stateful helpers.
 */
(function () {
    const blueprintData = window.BLUEPRINT_DATA || {};
    const isProjectWorkspace = Boolean(blueprintData.project_ref || blueprintData.project_id);
    const __ = (typeof window !== 'undefined' && typeof window.gettext === 'function') ? window.gettext.bind(window) : (msg => msg);
    const n__ = (typeof window !== 'undefined' && typeof window.ngettext === 'function')
        ? window.ngettext.bind(window)
        : ((singular, plural, count) => (Number(count) === 1 ? singular : plural));
    let cachedSimulations = null;
    let isFetchingSimulations = false;
    let projectWorkspaceDirty = false;
    let projectWorkspaceStateInitialized = !isProjectWorkspace;

    function buildWorkspaceSignature(state) {
        const source = state && typeof state === 'object' ? { ...state } : {};
        delete source.updatedAt;
        return JSON.stringify(source);
    }

    function buildMeTeConfigFromLegacyEfficiencies(efficiencies) {
        const blueprintConfigs = {};
        (Array.isArray(efficiencies) ? efficiencies : []).forEach((entry) => {
            const blueprintTypeId = Number(entry?.blueprint_type_id || entry?.blueprintTypeId || 0) || 0;
            if (!(blueprintTypeId > 0)) {
                return;
            }
            blueprintConfigs[String(blueprintTypeId)] = {
                me: Math.max(0, Math.min(Number(entry?.material_efficiency || entry?.materialEfficiency || 0) || 0, 10)),
                te: Math.max(0, Math.min(Number(entry?.time_efficiency || entry?.timeEfficiency || 0) || 0, 20)),
            };
        });

        const mainBlueprintTypeId = Number((blueprintData.bp_type_id || blueprintData.type_id || 0)) || 0;
        const mainConfig = blueprintConfigs[String(mainBlueprintTypeId)] || { me: 0, te: 0 };
        return {
            mainME: mainConfig.me,
            mainTE: mainConfig.te,
            blueprintConfigs,
        };
    }

    function buildManualPricesFromLegacyPrices(customPrices) {
        return (Array.isArray(customPrices) ? customPrices : []).map((entry) => ({
            typeId: Number(entry?.item_type_id || entry?.itemTypeId || 0) || 0,
            priceType: Boolean(entry?.is_sale_price || entry?.isSalePrice) ? 'sale' : 'real',
            value: Number(entry?.unit_price || entry?.unitPrice || 0) || 0,
        })).filter((entry) => entry.typeId > 0);
    }

    function normalizeWorkspaceStateForSession(rawState) {
        const state = rawState && typeof rawState === 'object' ? rawState : {};
        const buyTypeIds = Array.isArray(state.buyTypeIds)
            ? state.buyTypeIds
            : (Array.isArray(state.items)
                ? state.items.filter((item) => String(item?.mode || '') === 'buy').map((item) => Number(item?.type_id || item?.typeId || 0)).filter((typeId) => typeId > 0)
                : []);
        const rawFuzzworkPrices = state.fuzzworkPrices || state.fuzzwork_prices;
        const fuzzworkPrices = rawFuzzworkPrices && typeof rawFuzzworkPrices === 'object' && !Array.isArray(rawFuzzworkPrices)
            ? Object.fromEntries(
                Object.entries(rawFuzzworkPrices)
                    .map(([typeId, value]) => [String(Number(typeId) || 0), Number(value) || 0])
                    .filter(([typeId]) => typeId !== '0')
            )
            : {};

        return {
            buyTypeIds,
            stockAllocations: state.stockAllocations && typeof state.stockAllocations === 'object' && !Array.isArray(state.stockAllocations)
                ? Object.fromEntries(
                    Object.entries(state.stockAllocations)
                        .map(([typeId, quantity]) => [String(Number(typeId) || 0), Math.max(0, Math.floor(Number(quantity) || 0))])
                        .filter(([typeId, quantity]) => typeId !== '0' && quantity > 0)
                )
                : {},
            runs: Math.max(1, parseInt(state.runs, 10) || 1),
            activeBlueprintTab: String(state.activeBlueprintTab || state.active_tab || 'materials'),
            manualPrices: Array.isArray(state.manualPrices)
                ? state.manualPrices
                : buildManualPricesFromLegacyPrices(state.custom_prices),
            simulationName: String(state.simulationName || state.simulation_name || state.project_name || ''),
            decisionBuyTolerance: String(state.decisionBuyTolerance || ''),
            revenueMode: (String(state.revenueMode || '').trim().toLowerCase() === 'total') ? 'total' : 'per_unit',
            revenueTotalOverride: (() => {
                const v = Number.parseFloat(state.revenueTotalOverride);
                return Number.isFinite(v) && v > 0 ? v : 0;
            })(),
            meTeConfig: state.meTeConfig && typeof state.meTeConfig === 'object'
                ? state.meTeConfig
                : buildMeTeConfigFromLegacyEfficiencies(state.blueprint_efficiencies),
            copyRequests: Array.isArray(state.copyRequests) ? state.copyRequests : [],
            structure: state.structure && typeof state.structure === 'object'
                ? state.structure
                : {
                    motherSystemInput: '',
                    selectedSolarSystemId: null,
                    selectedSolarSystemName: '',
                    assignments: [],
                },
            fuzzworkPrices,
            pendingWorkspaceRefresh: Boolean(state.pendingWorkspaceRefresh),
            pendingWorkspaceSourceTab: String(state.pendingWorkspaceSourceTab || ''),
        };
    }

    function cloneSerializable(value) {
        try {
            return JSON.parse(JSON.stringify(value));
        } catch (error) {
            return null;
        }
    }

    function applyInclusionModesToTreeSnapshot(nodes, buyTypeIds) {
        return (Array.isArray(nodes) ? nodes : []).map((node) => {
            const clonedNode = node && typeof node === 'object' ? { ...node } : {};
            const typeId = Number(clonedNode.type_id || clonedNode.typeId || 0) || 0;
            const inclusionMode = typeId > 0 && buyTypeIds.has(typeId) ? 'buy' : 'prod';
            clonedNode.project_inclusion_mode = inclusionMode;
            const children = Array.isArray(clonedNode.sub_materials)
                ? clonedNode.sub_materials
                : (Array.isArray(clonedNode.subMaterials) ? clonedNode.subMaterials : []);
            const nextChildren = applyInclusionModesToTreeSnapshot(children, buyTypeIds);
            if (Array.isArray(clonedNode.sub_materials)) {
                clonedNode.sub_materials = nextChildren;
            }
            if (Array.isArray(clonedNode.subMaterials)) {
                clonedNode.subMaterials = nextChildren;
            }
            return clonedNode;
        });
    }

    function collectCachedProjectPayloadSnapshot(normalizedState) {
        const snapshot = cloneSerializable(window.BLUEPRINT_DATA || {});
        if (!snapshot || typeof snapshot !== 'object') {
            return null;
        }

        const buyTypeIds = new Set(
            (Array.isArray(normalizedState.buyTypeIds) ? normalizedState.buyTypeIds : [])
                .map((typeId) => Number(typeId) || 0)
                .filter((typeId) => typeId > 0)
        );

        snapshot.active_tab = normalizedState.activeBlueprintTab;
        snapshot.workspace_state = {
            ...(snapshot.workspace_state && typeof snapshot.workspace_state === 'object' ? snapshot.workspace_state : {}),
            buyTypeIds: normalizedState.buyTypeIds,
            stockAllocations: normalizedState.stockAllocations,
            manualPrices: normalizedState.manualPrices,
            fuzzworkPrices: normalizedState.fuzzworkPrices,
            simulation_name: normalizedState.simulationName,
            simulationName: normalizedState.simulationName,
            runs: normalizedState.runs,
            active_tab: normalizedState.activeBlueprintTab,
            activeBlueprintTab: normalizedState.activeBlueprintTab,
            meTeConfig: normalizedState.meTeConfig,
            copyRequests: normalizedState.copyRequests,
            structure: normalizedState.structure,
            revenueMode: normalizedState.revenueMode,
            revenueTotalOverride: normalizedState.revenueTotalOverride,
            pendingWorkspaceRefresh: normalizedState.pendingWorkspaceRefresh,
            pendingWorkspaceSourceTab: normalizedState.pendingWorkspaceSourceTab,
        };

        if (Array.isArray(snapshot.materials_tree)) {
            snapshot.materials_tree = applyInclusionModesToTreeSnapshot(
                snapshot.materials_tree,
                buyTypeIds
            );
        }

        return snapshot;
    }

    function collectProjectWorkspacePayload() {
        const sessionState = window.CraftBP && typeof window.CraftBP.collectSessionState === 'function'
            ? window.CraftBP.collectSessionState()
            : normalizeWorkspaceStateForSession(blueprintData.workspace_state);
        const simulationNameInput = document.getElementById('simulationName');
        const normalizedState = normalizeWorkspaceStateForSession({
            ...sessionState,
            simulationName: simulationNameInput ? simulationNameInput.value.trim() : sessionState.simulationName,
            activeBlueprintTab: sessionState.activeBlueprintTab || getActiveTabId(),
        });

        const payload = {
            ...normalizedState,
            blueprint_type_id: blueprintData.bp_type_id || blueprintData.type_id,
            blueprint_name: blueprintData.name || document.querySelector('.blueprint-hero .hero-title')?.textContent?.trim() || document.querySelector('.blueprint-header h1')?.textContent?.trim() || __('Blueprint'),
            runs: normalizedState.runs,
            simulation_name: normalizedState.simulationName,
            active_tab: normalizedState.activeBlueprintTab,
            decisionBuyTolerance: normalizedState.decisionBuyTolerance,
            cachedPayload: collectCachedProjectPayloadSnapshot(normalizedState),
            items: gatherProductionItems(),
            blueprint_efficiencies: gatherBlueprintEfficiencies(),
            custom_prices: gatherCustomPrices(),
            estimated_cost: parseISK(document.getElementById('financialSummaryCost')?.textContent),
            estimated_revenue: parseISK(document.getElementById('financialSummaryRevenue')?.textContent),
            estimated_profit: parseISK(document.getElementById('financialSummaryProfit')?.textContent),
        };
        payload.total_items = payload.items.length;
        payload.total_buy_items = payload.items.filter((item) => item.mode === 'buy').length;
        payload.total_prod_items = payload.items.filter((item) => item.mode === 'prod').length;

        return {
            payload,
            normalizedState,
            signature: buildWorkspaceSignature(normalizedState),
        };
    }

    let lastSavedProjectStateSignature = buildWorkspaceSignature(
        normalizeWorkspaceStateForSession(blueprintData.workspace_state)
    );

    function getCsrfToken() {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : null;
    }

    function parseISK(value) {
        if (typeof value === 'number') {
            return value;
        }
        if (!value) {
            return 0;
        }
        let cleaned = String(value).replace(/[^\d.,-]/g, '');
        if (cleaned.includes(',') && cleaned.includes('.')) {
            cleaned = cleaned.replace(/,/g, '');
        } else if (cleaned.includes(',') && !cleaned.includes('.')) {
            cleaned = cleaned.replace(/,/g, '.');
        }
        const number = parseFloat(cleaned);
        return Number.isFinite(number) ? number : 0;
    }

    function hideSimulationStatus() {
        const badge = document.getElementById('simulationStatus');
        if (!badge) {
            return;
        }

        if (badge.dataset.timeoutId) {
            window.clearTimeout(Number(badge.dataset.timeoutId));
            delete badge.dataset.timeoutId;
        }

        delete badge.dataset.persistent;
        badge.classList.add('d-none');
    }

    function showSimulationStatus(message, variant = 'info', options = {}) {
        const persistent = !!options.persistent;
        const badge = document.getElementById('simulationStatus');
        if (!badge) {
            return;
        }

        badge.textContent = message;
        badge.classList.remove('d-none', 'bg-secondary', 'bg-success', 'bg-danger', 'bg-warning', 'bg-info', 'text-dark');

        switch (variant) {
            case 'success':
                badge.classList.add('bg-success');
                break;
            case 'danger':
                badge.classList.add('bg-danger');
                break;
            case 'warning':
                badge.classList.add('bg-warning', 'text-dark');
                break;
            case 'info':
            default:
                badge.classList.add('bg-info');
                break;
        }

        if (badge.dataset.timeoutId) {
            window.clearTimeout(Number(badge.dataset.timeoutId));
            delete badge.dataset.timeoutId;
        }

        if (persistent) {
            badge.dataset.persistent = 'true';
            return;
        }

        delete badge.dataset.persistent;
        const timeoutId = window.setTimeout(() => {
            hideSimulationStatus();
        }, 5000);
        badge.dataset.timeoutId = timeoutId;
    }

    function setProjectWorkspaceDirty(isDirty) {
        if (!isProjectWorkspace) {
            return;
        }

        projectWorkspaceDirty = !!isDirty;
        if (projectWorkspaceDirty) {
            showSimulationStatus(__('Unsaved changes. Click Save table to persist them.'), 'warning', { persistent: true });
            return;
        }

        const badge = document.getElementById('simulationStatus');
        if (badge && badge.dataset.persistent === 'true') {
            hideSimulationStatus();
        }
    }

    window.CraftBPProjectWorkspace = Object.assign(window.CraftBPProjectWorkspace || {}, {
        hasUnsavedChanges: function () {
            return Boolean(isProjectWorkspace && projectWorkspaceDirty);
        },
    });

    function getActiveTabId() {
        const activeTab = document.querySelector('#bpTabs .nav-link.active');
        if (!activeTab) {
            return 'materials';
        }
        const target = activeTab.getAttribute('data-bs-target');
        return target ? target.replace('#tab-', '') : 'materials';
    }

    function mapLikeToMap(source) {
        if (!source) {
            return new Map();
        }
        if (source instanceof Map) {
            return source;
        }
        return new Map(source.entries ? source.entries() : Object.entries(source));
    }

    function gatherProductionItems() {
        if (!window.SimulationAPI || typeof window.SimulationAPI.getState !== 'function') {
            return [];
        }

        if (typeof window.SimulationAPI.refreshFromDom === 'function') {
            window.SimulationAPI.refreshFromDom();
        }

        const state = window.SimulationAPI.getState();
        if (!state) {
            return [];
        }

        const materials = mapLikeToMap(state.materials);
        const switches = mapLikeToMap(state.switches);
        const tree = mapLikeToMap(state.tree);
        const items = [];

        materials.forEach((material, typeId) => {
            const numericId = Number(typeId);
            if (!Number.isFinite(numericId)) {
                return;
            }
            const switchData = switches.get(numericId);
            const treeEntry = tree.get(numericId);
            const defaultMode = treeEntry && treeEntry.craftable ? 'prod' : 'buy';
            const mode = switchData ? switchData.state : defaultMode;
            const quantity = material ? Math.ceil(material.quantity || 0) : 0;

            items.push({
                type_id: numericId,
                mode: mode || 'prod',
                quantity: quantity,
            });
        });

        return items;
    }

    function gatherBlueprintEfficiencies() {
        const efficiencies = [];
        document.querySelectorAll('#tab-config tr[data-blueprint-type-id]').forEach((row) => {
            const typeId = Number(row.getAttribute('data-blueprint-type-id'));
            if (!Number.isFinite(typeId)) {
                return;
            }
            const meInput = row.querySelector('input[name^="me_"]');
            const teInput = row.querySelector('input[name^="te_"]');
            efficiencies.push({
                blueprint_type_id: typeId,
                material_efficiency: meInput ? Number(meInput.value) || 0 : 0,
                time_efficiency: teInput ? Number(teInput.value) || 0 : 0,
            });
        });
        return efficiencies;
    }

    function gatherCustomPrices() {
        const prices = [];

        const handleInput = (input, isSale) => {
            const typeId = Number(input.getAttribute('data-type-id'));
            if (!Number.isFinite(typeId)) {
                return;
            }
            const value = Number(input.value);
            const userModified = input.dataset.userModified === 'true';
            if (!userModified && !isSale) {
                return;
            }
            if (!Number.isFinite(value) || value <= 0) {
                return;
            }
            prices.push({
                item_type_id: typeId,
                unit_price: value,
                is_sale_price: !!isSale,
            });
        };

        document.querySelectorAll('input.real-price[data-type-id]').forEach((input) => handleInput(input, false));
        document.querySelectorAll('input.sale-price-unit[data-type-id]').forEach((input) => handleInput(input, true));

        return prices;
    }

    function refreshSaveSummary() {
        const runsInput = document.getElementById('runsInput');
        const runsValue = runsInput ? Number(runsInput.value) || 1 : 1;
        const runsSummary = document.getElementById('summaryRuns');
        if (runsSummary) {
            runsSummary.textContent = runsValue.toLocaleString();
        }

        const items = gatherProductionItems();
        const prodItems = items.filter((item) => item.mode === 'prod').length;
        const buyItems = items.filter((item) => item.mode === 'buy').length;
        const prodSummary = document.getElementById('summaryProdItems');
        const buySummary = document.getElementById('summaryBuyItems');
        if (prodSummary) {
            prodSummary.textContent = prodItems.toLocaleString();
        }
        if (buySummary) {
            buySummary.textContent = buyItems.toLocaleString();
        }

        const profitSummary = document.getElementById('summaryProfit');
        const profitSource = document.getElementById('financialSummaryProfit');
        if (profitSummary && profitSource) {
            profitSummary.textContent = profitSource.textContent || '0';
        }
    }

    async function persistProjectWorkspace(options = {}) {
        const { closeModal = false, showSuccessStatus = false, showErrorStatus = true } = options;

        if (!blueprintData.save_url) {
            if (showErrorStatus) {
                showSimulationStatus(__('Saving is not configured for this blueprint.'), 'warning');
            }
            return false;
        }

        const { payload, normalizedState, signature } = collectProjectWorkspacePayload();
        if (isProjectWorkspace && signature === lastSavedProjectStateSignature) {
            setProjectWorkspaceDirty(false);
            return true;
        }

        const response = await fetch(blueprintData.save_url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken() || '',
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            throw new Error(`Request failed with status ${response.status}`);
        }

        const data = await response.json();
        if (!data || !data.success) {
            throw new Error(data?.error || (isProjectWorkspace ? __('Unable to save table.') : __('Unable to save simulation.')));
        }

        if (closeModal) {
            const saveModal = document.getElementById('saveSimulationModal');
            if (saveModal && typeof bootstrap !== 'undefined' && bootstrap?.Modal) {
                const modalInstance = bootstrap.Modal.getInstance(saveModal) || (bootstrap.Modal.getOrCreateInstance ? bootstrap.Modal.getOrCreateInstance(saveModal) : null);
                if (modalInstance) {
                    modalInstance.hide();
                }
            }
        }

        blueprintData.workspace_state = normalizedState;
        lastSavedProjectStateSignature = signature;
        setProjectWorkspaceDirty(false);
        if (data.project_name) {
            blueprintData.name = data.project_name;
        }

        if (showSuccessStatus) {
            showSimulationStatus(isProjectWorkspace ? __('Table saved successfully.') : __('Simulation saved successfully.'), 'success');
        }

        cachedSimulations = null;
        return true;
    }

    async function saveSimulation(event) {
        if (event) {
            event.preventDefault();
        }

        const saveButton = document.getElementById('confirmSaveSimulation');
        if (saveButton) {
            saveButton.disabled = true;
        }

        try {
            await persistProjectWorkspace({ closeModal: true, showSuccessStatus: true });
        } catch (error) {
            console.error('[CraftBP] Failed to save simulation', error);
            showSimulationStatus(isProjectWorkspace ? __('Failed to save table.') : __('Failed to save simulation.'), 'danger');
        } finally {
            if (saveButton) {
                saveButton.disabled = false;
            }
        }
    }

    async function saveProjectWorkspaceFromHeader(event) {
        if (event) {
            event.preventDefault();
        }

        const saveButton = document.getElementById('saveSimulationBtn');
        if (saveButton) {
            saveButton.disabled = true;
        }

        try {
            await persistProjectWorkspace({ showSuccessStatus: true });
        } catch (error) {
            console.error('[CraftBP] Failed to save project workspace', error);
            showSimulationStatus(__('Failed to save table.'), 'danger');
        } finally {
            if (saveButton) {
                saveButton.disabled = false;
            }
        }
    }

    async function fetchSimulationsList() {
        if (cachedSimulations) {
            return cachedSimulations;
        }
        if (isFetchingSimulations) {
            return [];
        }
        if (!blueprintData.load_url) {
            return [];
        }

        try {
            isFetchingSimulations = true;
            const url = new URL(blueprintData.load_url, window.location.origin);
            url.searchParams.set('api', '1');

            const response = await fetch(url.toString(), {
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                },
            });
            if (!response.ok) {
                throw new Error(`Request failed with status ${response.status}`);
            }
            const data = await response.json();
            if (!data || !data.success) {
                throw new Error('Invalid simulations payload');
            }
            const currentBlueprintId = Number(blueprintData.bp_type_id || blueprintData.type_id);
            cachedSimulations = data.simulations.filter((sim) => Number(sim.blueprint_type_id) === currentBlueprintId);
            return cachedSimulations;
        } catch (error) {
            console.error('[CraftBP] Failed to load simulation list', error);
            showSimulationStatus(__('Unable to load saved simulations.'), 'danger');
            return [];
        } finally {
            isFetchingSimulations = false;
        }
    }

    function renderSimulationsList(simulations) {
        const container = document.getElementById('simulationsList');
        if (!container) {
            return;
        }

        if (!simulations.length) {
            container.innerHTML = '<div class="text-muted text-center py-4">' + __('No saved simulations for this blueprint yet.') + '</div>';
            return;
        }

        const list = document.createElement('div');
        list.className = 'list-group';

        const formatRunsLabel = (count) => {
            const safeCount = Number(count) || 0;
            const suffix = n__('run', 'runs', safeCount);
            return `${safeCount} ${suffix}`;
        };

        simulations.forEach((simulation) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-start';

            const title = simulation.simulation_name || simulation.display_name || `${__('Runs')} x${simulation.runs}`;
            const runsLabel = formatRunsLabel(simulation.runs);
            const subtitle = simulation.blueprint_name ? `${simulation.blueprint_name} · ${runsLabel}` : runsLabel;
            const profit = Number(simulation.estimated_profit || 0).toLocaleString();
            const updated = simulation.updated_at ? simulation.updated_at : '—';

            button.innerHTML = `
                <div class="me-3">
                    <div class="fw-semibold">${title}</div>
                    <div class="text-muted small">${subtitle}</div>
                </div>
                <div class="text-end">
                    <div class="badge bg-success-subtle text-success">+${profit} ISK</div>
                    <div class="text-muted small">${__('Updated')} ${updated}</div>
                </div>
            `;

            button.addEventListener('click', async () => {
                button.disabled = true;
                await loadSimulation(simulation);
                button.disabled = false;
            });

            list.appendChild(button);
        });

        container.innerHTML = '';
        container.appendChild(list);
    }

    function applySwitchState(typeId, mode) {
        const switchInput = document.querySelector(`input.mat-switch[data-type-id="${typeId}"]`);
        if (!switchInput) {
            return;
        }

        if (mode === 'useless') {
            switchInput.dataset.userState = 'useless';
            switchInput.dataset.fixedMode = 'useless';
            switchInput.checked = false;
            switchInput.disabled = true;
        } else {
            switchInput.dataset.userState = mode;
            switchInput.dataset.fixedMode = '';
            switchInput.disabled = false;
            switchInput.checked = mode !== 'buy';
        }
    }

    function applyBlueprintEfficiencies(efficiencies) {
        efficiencies.forEach((eff) => {
            const row = document.querySelector(`#tab-config tr[data-blueprint-type-id="${eff.blueprint_type_id}"]`);
            if (!row) {
                return;
            }
            const meInput = row.querySelector(`input[name="me_${eff.blueprint_type_id}"]`);
            const teInput = row.querySelector(`input[name="te_${eff.blueprint_type_id}"]`);
            if (meInput) {
                meInput.value = Number(eff.material_efficiency) || 0;
            }
            if (teInput) {
                teInput.value = Number(eff.time_efficiency) || 0;
            }
        });
    }

    function applyCustomPrices(customPrices) {
        customPrices.forEach((price) => {
            const selector = price.is_sale_price ? '.sale-price-unit' : '.real-price';
            const input = document.querySelector(`${selector}[data-type-id="${price.item_type_id}"]`);
            if (!input) {
                return;
            }
            input.value = Number(price.unit_price) || 0;
            if (window.CraftBP && typeof window.CraftBP.markPriceOverride === 'function') {
                window.CraftBP.markPriceOverride(input, true);
            } else {
                input.dataset.userModified = 'true';
                input.classList.add('is-manual');
            }

            if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function') {
                const priceType = price.is_sale_price ? 'sale' : 'real';
                window.SimulationAPI.setPrice(price.item_type_id, priceType, Number(price.unit_price) || 0);
            }
        });
    }

    function applySimulationConfig(config, simulationMeta) {
        const runsInput = document.getElementById('runsInput');
        if (runsInput && Number.isFinite(Number(config.runs))) {
            runsInput.value = Number(config.runs);
        }

        if (Array.isArray(config.items)) {
            config.items.forEach((item) => applySwitchState(item.type_id, item.mode));
        }

        if (typeof window.refreshTreeSwitchHierarchy === 'function') {
            window.refreshTreeSwitchHierarchy();
        }

        if (Array.isArray(config.blueprint_efficiencies)) {
            applyBlueprintEfficiencies(config.blueprint_efficiencies);
            if (!window.craftBPFlags) {
                window.craftBPFlags = {};
            }
            window.craftBPFlags.hasPendingMETEChanges = false;
        }

        if (Array.isArray(config.custom_prices)) {
            applyCustomPrices(config.custom_prices);
        }

        const simulationNameInput = document.getElementById('simulationName');
        if (simulationNameInput) {
            simulationNameInput.value = config.simulation_name || simulationMeta.simulation_name || '';
        }

        if (window.SimulationAPI && typeof window.SimulationAPI.refreshFromDom === 'function') {
            window.SimulationAPI.refreshFromDom();
        }

        if (window.CraftBP && typeof window.CraftBP.refreshTabs === 'function') {
            window.CraftBP.refreshTabs({ forceNeeded: true });
        }

        if (typeof window.CraftBPTabs?.updateAllTabs === 'function') {
            window.CraftBPTabs.updateAllTabs();
        }

        if (typeof recalcFinancials === 'function') {
            recalcFinancials();
        }

        refreshSaveSummary();

        const statusLabel = simulationMeta.simulation_name || simulationMeta.display_name || 'Simulation';
        showSimulationStatus(`${__('Loaded')} ${statusLabel}`, 'info');
    }

    async function loadSimulation(simulation) {
        if (!simulation || !blueprintData.load_config_url) {
            return;
        }

        try {
            const url = new URL(blueprintData.load_config_url, window.location.origin);
            url.searchParams.set('blueprint_type_id', simulation.blueprint_type_id);
            url.searchParams.set('runs', simulation.runs);

            const response = await fetch(url.toString(), {
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                },
            });

            if (!response.ok) {
                throw new Error(`Request failed with status ${response.status}`);
            }

            const config = await response.json();
            if (config && !config.error) {
                const loadModal = document.getElementById('loadSimulationModal');
                if (loadModal && typeof bootstrap !== 'undefined' && bootstrap?.Modal) {
                    const modalInstance = bootstrap.Modal.getInstance(loadModal) || (bootstrap.Modal.getOrCreateInstance ? bootstrap.Modal.getOrCreateInstance(loadModal) : null);
                    if (modalInstance) {
                        modalInstance.hide();
                    }
                }
                applySimulationConfig(config, simulation);
            } else {
                throw new Error(config?.error || 'Invalid simulation config');
            }
        } catch (error) {
            console.error('[CraftBP] Failed to load simulation config', error);
            showSimulationStatus(__('Failed to load simulation.'), 'danger');
        }
    }

    function attachEventHandlers() {
        const saveModal = document.getElementById('saveSimulationModal');
        if (saveModal) {
            saveModal.addEventListener('show.bs.modal', refreshSaveSummary);
        }

        const saveToolbarButton = document.getElementById('saveSimulationBtn');
        if (saveToolbarButton && isProjectWorkspace) {
            saveToolbarButton.addEventListener('click', saveProjectWorkspaceFromHeader);
        }

        const saveButton = document.getElementById('confirmSaveSimulation');
        if (saveButton) {
            saveButton.addEventListener('click', saveSimulation);
        }

        const loadModal = document.getElementById('loadSimulationModal');
        if (loadModal && !isProjectWorkspace) {
            loadModal.addEventListener('show.bs.modal', async () => {
                const container = document.getElementById('simulationsList');
                if (container) {
                    container.innerHTML = '<div class="text-center py-4 text-muted"><i class="fas fa-spinner fa-spin fa-2x mb-3"></i><p class="mb-0">' + __('Loading saved simulations…') + '</p></div>';
                }
                const simulations = await fetchSimulationsList();
                renderSimulationsList(simulations);
            });
        }
    }

    document.addEventListener('CraftBP:status', (event) => {
        const detail = event.detail || {};
        if (detail.message) {
            showSimulationStatus(detail.message, detail.variant || 'info');
        }
    });

    document.addEventListener('CraftBP:sessionStateChanged', (event) => {
        if (!isProjectWorkspace || !projectWorkspaceStateInitialized) {
            return;
        }
        const sessionState = normalizeWorkspaceStateForSession(event.detail?.state || {});
        const signature = buildWorkspaceSignature(sessionState);
        blueprintData.workspace_state = sessionState;
        setProjectWorkspaceDirty(signature !== lastSavedProjectStateSignature);
    });

    document.addEventListener('DOMContentLoaded', () => {
        if (window.CraftBPTabs && typeof window.CraftBPTabs.init === 'function') {
            window.CraftBPTabs.init();
        }
        const simulationNameInput = document.getElementById('simulationName');
        if (simulationNameInput) {
            simulationNameInput.value = (blueprintData.workspace_state && (blueprintData.workspace_state.simulationName || blueprintData.workspace_state.simulation_name))
                || blueprintData.name
                || '';
        }
        attachEventHandlers();
        if (isProjectWorkspace && blueprintData.workspace_state) {
            const normalizedState = normalizeWorkspaceStateForSession(blueprintData.workspace_state);
            blueprintData.workspace_state = normalizedState;
            if (window.CraftBP && typeof window.CraftBP.applySessionState === 'function') {
                window.CraftBP.applySessionState(normalizedState);
            } else {
                applySimulationConfig(blueprintData.workspace_state, {
                    simulation_name: normalizedState.simulationName || blueprintData.name || __('Table'),
                    display_name: blueprintData.name || __('Table'),
                });
            }
            lastSavedProjectStateSignature = buildWorkspaceSignature(normalizedState);
            setProjectWorkspaceDirty(false);
        }
        projectWorkspaceStateInitialized = true;
    });
})();
