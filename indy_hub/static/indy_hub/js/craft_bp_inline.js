/**
 * Craft Blueprint inline controller
 * Bridges page-specific UI (modals, summaries) with SimulationAPI/stateful helpers.
 */
(function () {
    const blueprintData = window.BLUEPRINT_DATA || {};
    let cachedSimulations = null;
    let isFetchingSimulations = false;

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

    function showSimulationStatus(message, variant = 'info') {
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
        }
        const timeoutId = window.setTimeout(() => {
            badge.classList.add('d-none');
        }, 5000);
        badge.dataset.timeoutId = timeoutId;
    }

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

    async function saveSimulation(event) {
        if (event) {
            event.preventDefault();
        }

        if (!blueprintData.save_url) {
            showSimulationStatus('Saving is not configured for this blueprint.', 'warning');
            return;
        }

        const runsInput = document.getElementById('runsInput');
        const simulationNameInput = document.getElementById('simulationName');

        const payload = {
            blueprint_type_id: blueprintData.bp_type_id || blueprintData.type_id,
            blueprint_name: blueprintData.name || document.querySelector('.blueprint-hero .hero-title')?.textContent?.trim() || document.querySelector('.blueprint-header h1')?.textContent?.trim() || 'Blueprint',
            runs: runsInput ? Number(runsInput.value) || 1 : 1,
            simulation_name: simulationNameInput ? simulationNameInput.value.trim() : '',
            active_tab: getActiveTabId(),
            items: gatherProductionItems(),
            blueprint_efficiencies: gatherBlueprintEfficiencies(),
            custom_prices: gatherCustomPrices(),
            estimated_cost: parseISK(document.getElementById('financialSummaryCost')?.textContent),
            estimated_revenue: parseISK(document.getElementById('financialSummaryRevenue')?.textContent),
            estimated_profit: parseISK(document.getElementById('financialSummaryProfit')?.textContent),
        };

        try {
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
            if (data && data.success) {
                showSimulationStatus('Simulation saved successfully.', 'success');
                cachedSimulations = null; // force refresh next time
            } else {
                throw new Error(data?.error || 'Unable to save simulation.');
            }
        } catch (error) {
            console.error('[CraftBP] Failed to save simulation', error);
            showSimulationStatus('Failed to save simulation.', 'danger');
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
            showSimulationStatus('Unable to load saved simulations.', 'danger');
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
            container.innerHTML = '<div class="text-muted text-center py-4">No saved simulations for this blueprint yet.</div>';
            return;
        }

        const list = document.createElement('div');
        list.className = 'list-group';

        simulations.forEach((simulation) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-start';

            const title = simulation.simulation_name || simulation.display_name || `Runs x${simulation.runs}`;
            const subtitle = simulation.blueprint_name ? `${simulation.blueprint_name} · ${simulation.runs} runs` : `${simulation.runs} runs`;
            const profit = Number(simulation.estimated_profit || 0).toLocaleString();
            const updated = simulation.updated_at ? simulation.updated_at : '—';

            button.innerHTML = `
                <div class="me-3">
                    <div class="fw-semibold">${title}</div>
                    <div class="text-muted small">${subtitle}</div>
                </div>
                <div class="text-end">
                    <div class="badge bg-success-subtle text-success">+${profit} ISK</div>
                    <div class="text-muted small">Updated ${updated}</div>
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
        showSimulationStatus(`Loaded ${statusLabel}`, 'info');
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
            showSimulationStatus('Failed to load simulation.', 'danger');
        }
    }

    function attachEventHandlers() {
        const saveModal = document.getElementById('saveSimulationModal');
        if (saveModal) {
            saveModal.addEventListener('show.bs.modal', refreshSaveSummary);
        }

        const saveButton = document.getElementById('confirmSaveSimulation');
        if (saveButton) {
            saveButton.addEventListener('click', saveSimulation);
        }

        const loadModal = document.getElementById('loadSimulationModal');
        if (loadModal) {
            loadModal.addEventListener('show.bs.modal', async () => {
                const container = document.getElementById('simulationsList');
                if (container) {
                    container.innerHTML = '<div class="text-center py-4 text-muted"><i class="fas fa-spinner fa-spin fa-2x mb-3"></i><p class="mb-0">Loading saved simulations…</p></div>';
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

    document.addEventListener('DOMContentLoaded', () => {
        if (window.CraftBPTabs && typeof window.CraftBPTabs.init === 'function') {
            window.CraftBPTabs.init();
        }
        attachEventHandlers();
    });
})();
