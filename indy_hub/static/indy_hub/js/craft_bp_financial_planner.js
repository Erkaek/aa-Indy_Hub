function getFinancialGroupLabel(groupName) {
    const normalizedGroupName = String(groupName || '').trim();
    return normalizedGroupName || __('Other');
}

function normalizeFinancialInputGroupKey(groupName) {
    const normalizedGroupName = String(groupName || '').trim();
    return normalizedGroupName || '__other__';
}

function getFinancialGroupCollapseState() {
    if (!window.craftBPFinancialGroupCollapseState || typeof window.craftBPFinancialGroupCollapseState !== 'object') {
        window.craftBPFinancialGroupCollapseState = {
            input: {},
            output: {},
        };
    }
    if (!window.craftBPFinancialGroupCollapseState.input) {
        window.craftBPFinancialGroupCollapseState.input = {};
    }
    if (!window.craftBPFinancialGroupCollapseState.output) {
        window.craftBPFinancialGroupCollapseState.output = {};
    }
    return window.craftBPFinancialGroupCollapseState;
}

function isFinancialGroupCollapsed(kind, key) {
    const safeKind = kind === 'output' ? 'output' : 'input';
    const safeKey = String(key || '').trim();
    if (!safeKey) {
        return true;
    }
    const state = getFinancialGroupCollapseState();
    if (!Object.prototype.hasOwnProperty.call(state[safeKind], safeKey)) {
        return true;
    }
    return Boolean(state[safeKind]?.[safeKey]);
}

window.isFinancialGroupCollapsed = isFinancialGroupCollapsed;

function setFinancialGroupCollapsed(kind, key, collapsed) {
    const safeKind = kind === 'output' ? 'output' : 'input';
    const safeKey = String(key || '').trim();
    if (!safeKey) {
        return;
    }
    const state = getFinancialGroupCollapseState();
    state[safeKind][safeKey] = Boolean(collapsed);
}

function buildFinancialGroupRow(groupName) {
    const groupKey = normalizeFinancialInputGroupKey(groupName);
    const collapsed = isFinancialGroupCollapsed('input', groupKey);
    const row = document.createElement('tr');
    row.className = 'craft-financial-group-row';
    row.dataset.marketGroupRow = 'true';
    row.dataset.marketGroupKey = groupKey;
    row.dataset.groupKind = 'input';
    row.dataset.collapsed = collapsed ? 'true' : 'false';
    row.innerHTML = `
        <td colspan="6">
            <div class="d-flex align-items-center justify-content-between gap-3">
                <span class="d-inline-flex align-items-center gap-2">
                    <span class="financial-group-toggle" aria-hidden="true"><i class="fas fa-chevron-down"></i></span>
                    <span class="badge bg-secondary-subtle text-secondary-emphasis">${escapeHtml(getFinancialGroupLabel(groupName))}</span>
                </span>
                <span class="small text-muted financial-group-count">0 ${escapeHtml(__('lines'))}</span>
            </div>
        </td>
    `;
    return row;
}

function updateFinancialGroupRowsVisibility(tableBody) {
    if (!tableBody) {
        return;
    }

    let currentGroupRow = null;
    let currentVisibleCount = 0;
    let currentTotalCount = 0;

    const flushCurrentGroupRow = () => {
        if (!currentGroupRow) {
            return;
        }

        const countLabel = currentGroupRow.querySelector('.financial-group-count');
        if (countLabel) {
            if (currentTotalCount <= 0) {
                countLabel.textContent = __('No visible lines');
            } else if (currentVisibleCount === currentTotalCount) {
                countLabel.textContent = `${formatInteger(currentVisibleCount)} ${currentVisibleCount === 1 ? __('line') : __('lines')}`;
            } else {
                countLabel.textContent = `${formatInteger(currentVisibleCount)}/${formatInteger(currentTotalCount)} ${currentTotalCount === 1 ? __('line') : __('lines')}`;
            }
        }

        currentGroupRow.hidden = currentTotalCount === 0;
        currentGroupRow = null;
        currentVisibleCount = 0;
        currentTotalCount = 0;
    };

    Array.from(tableBody.children).forEach((row) => {
        if (row.dataset.marketGroupRow === 'true') {
            flushCurrentGroupRow();
            currentGroupRow = row;
            return;
        }

        if (row.id === 'financialRevenueSectionRow') {
            flushCurrentGroupRow();
            return;
        }

        if (!currentGroupRow) {
            return;
        }

        if (
            row.matches('tr[data-type-id]')
            && row.getAttribute('data-final-output') !== 'true'
            && !row.hidden
        ) {
            currentTotalCount += 1;
            if (!row.classList.contains('financial-group-collapsed-hidden')) {
                currentVisibleCount += 1;
            }
        }
    });

    flushCurrentGroupRow();
}

function syncFinancialGroupRows(tableBody) {
    if (!tableBody) {
        return;
    }

    tableBody.querySelectorAll('tr[data-market-group-row="true"]').forEach((row) => row.remove());

    const purchaseRows = Array.from(tableBody.querySelectorAll('tr[data-type-id]')).filter(
        (row) => row.getAttribute('data-final-output') !== 'true'
    );

    let previousGroupKey = null;
    purchaseRows.forEach((row) => {
        const currentGroupKey = normalizeFinancialInputGroupKey(row.dataset.marketGroup || '');
        if (currentGroupKey === previousGroupKey) {
            return;
        }

        tableBody.insertBefore(buildFinancialGroupRow(currentGroupKey), row);
        previousGroupKey = currentGroupKey;
    });

    updateFinancialGroupRowsVisibility(tableBody);
    applyFinancialGroupCollapseVisibility(tableBody);
    if (typeof window.updateFinancialOutputGroupRowsVisibility === 'function') {
        window.updateFinancialOutputGroupRowsVisibility(tableBody);
    }
}

function updateFinancialGroupSectionSummaries(tableBody = document.getElementById('financialItemsBody')) {
    if (!tableBody) {
        return;
    }
    const formatGroupSummary = (count) => count > 0
        ? `${formatInteger(count)} ${count === 1 ? __('group') : __('groups')}`
        : '';

    const inputSummaryEl = document.getElementById('financialInputGroupSummary');
    const outputSummaryEl = document.getElementById('financialOutputGroupSummary');

    const inputVisibleGroups = Array.from(tableBody.querySelectorAll('tr[data-market-group-row="true"]'))
        .filter((row) => !row.hidden).length;
    const outputVisibleGroups = Array.from(tableBody.querySelectorAll('tr[data-output-group-row="true"]'))
        .filter((row) => !row.hidden).length;

    if (inputSummaryEl) {
        inputSummaryEl.textContent = formatGroupSummary(inputVisibleGroups);
    }
    if (outputSummaryEl) {
        outputSummaryEl.textContent = formatGroupSummary(outputVisibleGroups);
    }
}

window.updateFinancialGroupSectionSummaries = updateFinancialGroupSectionSummaries;

function applyFinancialGroupCollapseVisibility(tableBody = document.getElementById('financialItemsBody')) {
    if (!tableBody) {
        return;
    }

    let activeInputGroupKey = '';
    let activeOutputGroupKey = '';
    let hideInputRows = false;
    let hideOutputRows = false;

    Array.from(tableBody.children).forEach((row) => {
        if (row.dataset.marketGroupRow === 'true') {
            activeInputGroupKey = String(row.dataset.marketGroupKey || '').trim();
            hideInputRows = isFinancialGroupCollapsed('input', activeInputGroupKey);
            row.dataset.collapsed = hideInputRows ? 'true' : 'false';
            return;
        }

        if (row.dataset.outputGroupRow === 'true') {
            activeOutputGroupKey = String(row.dataset.outputGroupKey || '').trim();
            hideOutputRows = isFinancialGroupCollapsed('output', activeOutputGroupKey);
            row.dataset.collapsed = hideOutputRows ? 'true' : 'false';
            return;
        }

        if (row.id === 'financialRevenueSectionRow') {
            activeInputGroupKey = '';
            hideInputRows = false;
        }

        if (row.matches('tr[data-type-id]') && row.getAttribute('data-final-output') !== 'true' && activeInputGroupKey) {
            row.classList.toggle('financial-group-collapsed-hidden', hideInputRows);
            return;
        }

        if (row.matches('tr[data-type-id][data-final-output="true"]') && activeOutputGroupKey) {
            row.classList.toggle('financial-group-collapsed-hidden', hideOutputRows);
            return;
        }

        row.classList.remove('financial-group-collapsed-hidden');
    });

    updateFinancialGroupRowsVisibility(tableBody);
    if (typeof window.updateFinancialOutputGroupRowsVisibility === 'function') {
        window.updateFinancialOutputGroupRowsVisibility(tableBody);
    }
    updateFinancialGroupSectionSummaries(tableBody);
}

window.applyFinancialGroupCollapseVisibility = applyFinancialGroupCollapseVisibility;

function ensureFinancialStockStateNode(row) {
    if (!row) {
        return null;
    }

    let note = row.querySelector('.financial-stock-state');
    if (note) {
        return note;
    }

    const metaLine = row.querySelector('.craft-planner-item-name-wrap .small.text-muted');
    if (!metaLine) {
        return null;
    }

    note = document.createElement('span');
    note.className = 'financial-stock-state d-none';
    metaLine.appendChild(note);
    return note;
}

function applyFinancialRowStockState(row, item) {
    if (!row || !item || row.getAttribute('data-final-output') === 'true' || typeof getCraftStockAllocationSummary !== 'function') {
        return;
    }

    const stockSummary = getCraftStockAllocationSummary(item.typeId, item.quantity);
    row.dataset.requiredQty = String(stockSummary.requiredQty);
    row.dataset.stockAllocatedQty = String(stockSummary.allocatedQty);
    row.dataset.buyRemainingQty = String(stockSummary.remainingQty);

    const note = ensureFinancialStockStateNode(row);
    if (note) {
        if (stockSummary.availableQty > 0) {
            const parts = [
                `${__('Stock')} ${formatInteger(stockSummary.availableQty)}`,
            ];
            if (stockSummary.allocatedQty > 0) {
                parts.push(`${__('Using')} ${formatInteger(stockSummary.allocatedQty)}`);
                parts.push(`${__('Buy')} ${formatInteger(stockSummary.remainingQty)}`);
            }
            note.textContent = parts.join(' · ');
            note.classList.remove('d-none');
        } else {
            note.textContent = '';
            note.classList.add('d-none');
        }
    }

    row.classList.toggle('craft-financial-row-stock-covered', stockSummary.remainingQty === 0 && stockSummary.requiredQty > 0);
}

function buildFinancialRow(item, pricesMap) {
    const row = document.createElement('tr');
    row.setAttribute('data-type-id', String(item.typeId));
    row.setAttribute('data-market-group', String(item.marketGroup || ''));
    row.setAttribute('data-row-kind', 'buy');

    row.innerHTML = `
        <td class="fw-semibold" data-manual-label="${escapeHtml(__('Manual'))}" data-fuzzwork-label="${escapeHtml(__('Fuzzwork'))}" data-missing-label="${escapeHtml(__('Missing'))}" data-buy-label="${escapeHtml(__('Buy input'))}" data-revenue-label="${escapeHtml(__('Revenue target'))}">
            <div class="d-flex align-items-start gap-2 craft-financial-item-shell">
                <img src="https://images.evetech.net/types/${item.typeId}/icon?size=32" alt="${escapeHtml(item.typeName)}" loading="lazy" decoding="async" fetchpriority="low" class="rounded" style="width:28px;height:28px;background:#f3f4f6;" onerror="this.style.display='none';">
                <span class="craft-planner-item-name-wrap">
                    <span class="d-flex flex-wrap align-items-center gap-2">
                        <span class="fw-bold craft-planner-item-name">${escapeHtml(item.typeName)}</span>
                        <span class="badge bg-secondary-subtle text-secondary-emphasis financial-row-kind">${escapeHtml(__('Buy input'))}</span>
                    </span>
                    <span class="d-flex flex-wrap align-items-center gap-2 small text-muted mt-1">
                        <span class="financial-market-group"${item.marketGroup ? '' : ' style="display:none"'}>${escapeHtml(item.marketGroup || '')}</span>
                            <span class="financial-stock-state d-none"></span>
                    </span>
                </span>
                <button type="button" class="btn btn-link btn-sm text-body-tertiary financial-row-reset" data-type-id="${item.typeId}" title="${escapeHtml(__('Reset this override'))}">
                    <i class="fas fa-rotate-left"></i>
                </button>
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
        <td class="text-end text-xs item-margin"><span class="badge bg-secondary-subtle text-secondary-emphasis financial-source-badge source-missing">${escapeHtml(__('Missing'))}</span></td>
    `;

    const fuzzInput = row.querySelector('.fuzzwork-price');
    const realInput = row.querySelector('.real-price');

    const priceEntry = pricesMap.get(item.typeId) || {};
    const fuzzPrice = Number(priceEntry.fuzzwork || 0);
    const realPrice = Number(priceEntry.real || 0);

    fuzzInput.value = fuzzPrice.toFixed(2);
    if (fuzzPrice <= 0) {
        fuzzInput.classList.add('bg-warning', 'border-warning');
        fuzzInput.setAttribute('title', __('Price not available (Fuzzwork)'));
    } else {
        fuzzInput.classList.remove('bg-warning', 'border-warning');
        fuzzInput.removeAttribute('title');
    }

    if (realPrice > 0) {
        realInput.value = realPrice.toFixed(2);
        updatePriceInputManualState(realInput, true);
    } else {
        realInput.value = '0.00';
        updatePriceInputManualState(realInput, false);
    }

    attachPriceInputListener(realInput);
    applyFinancialRowStockState(row, item);

    return { row, typeId: item.typeId, fuzzInput, realInput };
}

function updateFinancialRow(row, item) {
    row.setAttribute('data-type-id', String(item.typeId));
    row.setAttribute('data-market-group', String(item.marketGroup || ''));
    row.setAttribute('data-row-kind', row.getAttribute('data-final-output') === 'true' ? 'revenue' : 'buy');

    const nameNode = row.querySelector('.craft-planner-item-name');
    if (nameNode) {
        nameNode.textContent = item.typeName;
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

    const marketGroupNode = row.querySelector('.financial-market-group');
    if (marketGroupNode) {
        marketGroupNode.textContent = item.marketGroup || '';
        marketGroupNode.style.display = item.marketGroup ? '' : 'none';
    }

    applyFinancialRowStockState(row, item);
}

function getFinancialRowSourceState(row) {
    const cell = row.querySelector('td[data-manual-label]');
    const manualLabel = cell?.dataset.manualLabel || __('Manual');
    const fuzzworkLabel = cell?.dataset.fuzzworkLabel || __('Fuzzwork');
    const missingLabel = cell?.dataset.missingLabel || __('Missing');
    const isRevenue = row.getAttribute('data-final-output') === 'true';
    const overrideInput = row.querySelector(isRevenue ? '.sale-price-unit' : '.real-price');
    const fuzzworkInput = row.querySelector('.fuzzwork-price');
    const overrideValue = Number.parseFloat(overrideInput?.value) || 0;
    const referenceValue = Number.parseFloat(fuzzworkInput?.value) || 0;
    const isManual = Boolean(overrideInput && overrideInput.dataset.userModified === 'true' && overrideValue > 0);

    if (isManual) {
        return { key: 'manual', label: manualLabel };
    }

    if (isRevenue) {
        if (overrideValue > 0 || referenceValue > 0) {
            return { key: 'fuzzwork', label: fuzzworkLabel };
        }
        return { key: 'missing', label: missingLabel };
    }

    if (referenceValue > 0) {
        return { key: 'fuzzwork', label: fuzzworkLabel };
    }

    return { key: 'missing', label: missingLabel };
}

function updateFinancialRowPresentation(row) {
    if (!row || !row.hasAttribute('data-type-id')) {
        return;
    }

    const source = getFinancialRowSourceState(row);
    row.dataset.activeSource = source.key;

    const badge = row.querySelector('.financial-source-badge');
    if (badge) {
        badge.textContent = source.label;
        badge.classList.remove('source-manual', 'source-fuzzwork', 'source-missing');
        badge.classList.add(`source-${source.key}`);
    }

    const resetButton = row.querySelector('.financial-row-reset');
    if (resetButton) {
        resetButton.disabled = source.key !== 'manual';
    }
}

function resetFinancialRowManualOverride(row) {
    if (!row) {
        return;
    }

    const typeId = row.getAttribute('data-type-id');
    const saleInput = row.querySelector('.sale-price-unit');
    const realInput = row.querySelector('.real-price');

    // Both Override columns reset to 0 / empty; calculations fall back to the
    // Reference (fuzzwork) price via SimulationAPI / recalcFinancials.
    if (saleInput) {
        saleInput.value = '0';
        updatePriceInputManualState(saleInput, false);
        if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function' && typeId) {
            window.SimulationAPI.setPrice(typeId, 'sale', 0);
        }
    }

    if (realInput) {
        realInput.value = '0';
        updatePriceInputManualState(realInput, false);
        if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function' && typeId) {
            window.SimulationAPI.setPrice(typeId, 'real', 0);
        }
    }

    if (typeof recalcFinancials === 'function') {
        recalcFinancials();
    }

    persistCraftPageSessionState();
}

function applyFinancialPlannerFilters() {
    const tableBody = document.getElementById('financialItemsBody');
    if (!tableBody) {
        return;
    }

    const searchTerm = String(document.getElementById('financialSearchInput')?.value || '').trim().toLowerCase();
    const scope = String(document.getElementById('financialPlannerScopeFilter')?.value || 'all');
    const purchaseHeader = document.getElementById('financialPurchaseSectionRow');
    const revenueHeader = document.getElementById('financialRevenueSectionRow');
    const emptyState = document.getElementById('financialPlannerEmptyState');
    const missingAlert = document.getElementById('missingPricesAlert');
    const missingCount = document.getElementById('missingPricesCount');
    const missingMessage = document.getElementById('missingPricesMessage');

    let visibleLines = 0;
    const visibleGroups = new Set();
    let visibleManual = 0;
    let visibleMissing = 0;
    let visibleBuy = 0;
    let visibleRevenue = 0;
    let totalMissing = 0;

    tableBody.querySelectorAll('tr[data-type-id]').forEach((row) => {
        updateFinancialRowPresentation(row);

        const isRevenue = row.getAttribute('data-final-output') === 'true';
        const source = String(row.dataset.activeSource || 'missing');
        const typeName = String(row.querySelector('.craft-planner-item-name')?.textContent || '').toLowerCase();
        const marketGroup = String(row.dataset.marketGroup || '').toLowerCase();

        const matchesSearch = !searchTerm || typeName.includes(searchTerm) || marketGroup.includes(searchTerm);
        const matchesScope = (
            scope === 'all'
            || (scope === 'buy' && !isRevenue)
            || (scope === 'revenue' && isRevenue)
            || (scope === 'manual' && source === 'manual')
            || (scope === 'missing' && source === 'missing')
        );
        const visible = matchesSearch && matchesScope;
        row.hidden = !visible;

        if (source === 'missing') {
            totalMissing += 1;
        }

        if (!visible) {
            return;
        }

        visibleLines += 1;
        visibleGroups.add(getFinancialGroupLabel(row.dataset.marketGroup));
        if (source === 'manual') {
            visibleManual += 1;
        }
        if (source === 'missing') {
            visibleMissing += 1;
        }
        if (isRevenue) {
            visibleRevenue += 1;
        } else {
            visibleBuy += 1;
        }
    });

    updateFinancialGroupRowsVisibility(tableBody);
    if (typeof window.applyFinancialGroupCollapseVisibility === 'function') {
        window.applyFinancialGroupCollapseVisibility(tableBody);
    }

    if (purchaseHeader) {
        purchaseHeader.hidden = visibleBuy === 0;
    }
    if (revenueHeader) {
        revenueHeader.hidden = visibleRevenue === 0;
    }

    const setText = (id, value) => {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = formatInteger(value);
        }
    };
    setText('financialPlannerLineCount', visibleLines);
    setText('financialPlannerGroupCount', visibleGroups.size);
    setText('financialPlannerManualCount', visibleManual);
    setText('financialPlannerMissingCount', visibleMissing);

    if (emptyState) {
        emptyState.classList.toggle('d-none', visibleLines > 0);
    }

    if (missingCount) {
        missingCount.textContent = formatInteger(totalMissing);
    }
    if (missingMessage) {
        missingMessage.textContent = totalMissing === 1
            ? __('item is missing price data. Load Fuzzwork prices or enter it manually.')
            : __('items are missing price data. Load Fuzzwork prices or enter them manually.');
    }
    if (missingAlert) {
        missingAlert.hidden = totalMissing === 0;
        missingAlert.classList.toggle('d-none', totalMissing === 0);
        missingAlert.classList.toggle('d-flex', totalMissing > 0);
    }
}

function initializeFinancialPlannerChrome() {
    const searchInput = document.getElementById('financialSearchInput');
    const clearButton = document.getElementById('financialSearchClear');
    const scopeFilter = document.getElementById('financialPlannerScopeFilter');
    const tableBody = document.getElementById('financialItemsBody');

    if (searchInput && searchInput.dataset.boundPlannerFilter !== 'true') {
        searchInput.addEventListener('input', applyFinancialPlannerFilters);
        searchInput.dataset.boundPlannerFilter = 'true';
    }

    if (clearButton && clearButton.dataset.boundPlannerFilter !== 'true') {
        clearButton.addEventListener('click', () => {
            if (searchInput) {
                searchInput.value = '';
                searchInput.focus();
            }
            applyFinancialPlannerFilters();
        });
        clearButton.dataset.boundPlannerFilter = 'true';
    }

    if (scopeFilter && scopeFilter.dataset.boundPlannerFilter !== 'true') {
        scopeFilter.addEventListener('change', applyFinancialPlannerFilters);
        scopeFilter.dataset.boundPlannerFilter = 'true';
    }

    if (tableBody && tableBody.dataset.boundPlannerReset !== 'true') {
        tableBody.addEventListener('click', (event) => {
            const outputOverrideInput = event.target.closest('.financial-output-group-override');
            if (outputOverrideInput) {
                return;
            }

            const outputOverrideReset = event.target.closest('.financial-output-group-override-reset');
            if (outputOverrideReset) {
                const groupKey = String(outputOverrideReset.getAttribute('data-output-group-key') || '').trim();
                if (groupKey && typeof window.setOutputGroupRevenueOverride === 'function') {
                    window.setOutputGroupRevenueOverride(groupKey, 0);
                }
                return;
            }

            const button = event.target.closest('.financial-row-reset');
            if (!button) {
                const groupRow = event.target.closest('tr[data-market-group-row="true"], tr[data-output-group-row="true"]');
                if (!groupRow) {
                    return;
                }
                const isOutput = groupRow.dataset.outputGroupRow === 'true';
                const groupKey = isOutput
                    ? String(groupRow.dataset.outputGroupKey || '').trim()
                    : String(groupRow.dataset.marketGroupKey || '').trim();
                if (!groupKey) {
                    return;
                }
                const kind = isOutput ? 'output' : 'input';
                const collapsed = isFinancialGroupCollapsed(kind, groupKey);
                setFinancialGroupCollapsed(kind, groupKey, !collapsed);
                if (typeof window.applyFinancialGroupCollapseVisibility === 'function') {
                    window.applyFinancialGroupCollapseVisibility(tableBody);
                }
                return;
            }
            const row = button.closest('tr[data-type-id]');
            resetFinancialRowManualOverride(row);
        });
        tableBody.dataset.boundPlannerReset = 'true';
    }

    if (tableBody && tableBody.dataset.boundPlannerOutputOverride !== 'true') {
        tableBody.addEventListener('input', (event) => {
            const input = event.target.closest('.financial-output-group-override');
            if (!input) {
                return;
            }
            const groupKey = String(input.getAttribute('data-output-group-key') || '').trim();
            if (groupKey && typeof window.setOutputGroupRevenueOverride === 'function') {
                window.setOutputGroupRevenueOverride(groupKey, input.value, { syncInputs: false });
            }
        });

        tableBody.addEventListener('change', (event) => {
            const input = event.target.closest('.financial-output-group-override');
            if (!input) {
                return;
            }
            const groupKey = String(input.getAttribute('data-output-group-key') || '').trim();
            if (groupKey && typeof window.setOutputGroupRevenueOverride === 'function') {
                window.setOutputGroupRevenueOverride(groupKey, input.value);
            }
        });

        tableBody.dataset.boundPlannerOutputOverride = 'true';
    }
}

let CRAFT_DASHBOARD_ORDERING_CACHE = null;

function getDashboardMaterialsOrdering() {
    if (CRAFT_DASHBOARD_ORDERING_CACHE) {
        return CRAFT_DASHBOARD_ORDERING_CACHE;
    }

    const container = document.getElementById('materialsGroupsContainer');
    const fallbackGroupName = __('Other');

    const groupOrder = new Map();
    const itemOrder = new Map();

    if (!container) {
        return { groupOrder, itemOrder, fallbackGroupName };
    }

    const groupCards = Array.from(container.querySelectorAll('.craft-group-card'));
    if (groupCards.length > 0) {
        groupCards.forEach((card, groupIdx) => {
            const headerSpan = card.querySelector('.craft-group-header > span');
            let groupName = headerSpan && headerSpan.textContent ? headerSpan.textContent.trim() : '';
            if (!groupName) {
                groupName = fallbackGroupName;
            }
            if (!groupOrder.has(groupName)) {
                groupOrder.set(groupName, groupIdx);
            }

            const rows = Array.from(card.querySelectorAll('.craft-item-row[data-type-id]'));
            rows.forEach((row, itemIdx) => {
                const typeId = Number(row.getAttribute('data-type-id')) || 0;
                if (!typeId || itemOrder.has(typeId)) {
                    return;
                }
                itemOrder.set(typeId, { groupIdx, itemIdx });
            });
        });
    } else {
        const cards = Array.from(container.querySelectorAll('.card'));
        cards.forEach((card, groupIdx) => {
            const headerLabel = card.querySelector('.card-header span.fw-semibold');
            let groupName = headerLabel && headerLabel.textContent ? headerLabel.textContent.trim() : '';
            if (!groupName) {
                groupName = fallbackGroupName;
            }
            if (!groupOrder.has(groupName)) {
                groupOrder.set(groupName, groupIdx);
            }

            const rows = Array.from(card.querySelectorAll('tbody tr[data-type-id]'));
            rows.forEach((row, itemIdx) => {
                const typeId = Number(row.getAttribute('data-type-id')) || 0;
                if (!typeId || itemOrder.has(typeId)) {
                    return;
                }
                itemOrder.set(typeId, { groupIdx, itemIdx });
            });
        });
    }

    const result = { groupOrder, itemOrder, fallbackGroupName };
    if (groupOrder.size > 0 || itemOrder.size > 0) {
        CRAFT_DASHBOARD_ORDERING_CACHE = result;
    }
    return result;
}

function updateFinancialTabFromState() {
    const tableBody = document.getElementById('financialItemsBody');
    if (!tableBody || !window.SimulationAPI || typeof window.SimulationAPI.getFinancialItems !== 'function') {
        return Promise.resolve();
    }

    if (typeof updateFinalProductRowFromPayload === 'function') {
        updateFinalProductRowFromPayload(window.BLUEPRINT_DATA || {});
    }

    initializeFinancialPlannerChrome();

    const hasRenderedRows = !!tableBody.querySelector('tr[data-type-id]');
    const isFinancialDirty = window.SimulationAPI && typeof window.SimulationAPI.isTabDirty === 'function'
        ? window.SimulationAPI.isTabDirty('financial')
        : true;

    if (hasRenderedRows && !isFinancialDirty) {
        applyFinancialPlannerFilters();
        updateFinancialGroupSectionSummaries(tableBody);
        return Promise.resolve();
    }

    const pricesMap = getSimulationPricesMap();
    const sortedItems = typeof getCraftSourceRequirementRows === 'function'
        ? getCraftSourceRequirementRows()
        : [];

    const existingRows = new Map();
    tableBody.querySelectorAll('tr[data-type-id]').forEach((row) => {
        if (row.getAttribute('data-final-output') === 'true') {
            return;
        }
        const typeId = Number(row.getAttribute('data-type-id'));
        if (!typeId) {
            return;
        }
        existingRows.set(typeId, row);
    });

    tableBody.querySelectorAll('tr[data-market-group-row="true"]').forEach((row) => row.remove());

    const newRows = [];
    const firstFinalRow = getFinalOutputRows()[0] || null;
    const revenueSectionRow = document.getElementById('financialRevenueSectionRow');
    const materialInsertBefore = revenueSectionRow || firstFinalRow || null;

    sortedItems.forEach((item) => {
        let row = existingRows.get(item.typeId);
        if (row) {
            updateFinancialRow(row, item);
            tableBody.insertBefore(row, materialInsertBefore);
            existingRows.delete(item.typeId);
        } else {
            const buildResult = buildFinancialRow(item, pricesMap);
            row = buildResult.row;
            tableBody.insertBefore(row, materialInsertBefore);
            newRows.push(buildResult);
        }
    });

    existingRows.forEach((row) => row.remove());

    syncFinancialGroupRows(tableBody);
    if (typeof window.syncFinancialOutputGroupRows === 'function') {
        window.syncFinancialOutputGroupRows();
    }
    if (typeof window.applyFinancialGroupCollapseVisibility === 'function') {
        window.applyFinancialGroupCollapseVisibility(tableBody);
    }

    getFinalOutputRows().forEach((row) => {
        if (row.parentElement !== tableBody) {
            tableBody.appendChild(row);
        }
        if (typeof syncFinalOutputRowPriceState === 'function') {
            syncFinalOutputRowPriceState(row);
        }
    });

    const outputTypeIds = getFinalOutputRows()
        .map((row) => Number(row.getAttribute('data-type-id')) || 0)
        .filter((typeId) => typeId > 0)
        .filter((typeId) => {
            if (!window.SimulationAPI || typeof window.SimulationAPI.getPrice !== 'function') {
                return true;
            }
            const priceInfo = window.SimulationAPI.getPrice(typeId, true) || { value: 0, source: 'default' };
            return Number(priceInfo.value || 0) <= 0 || String(priceInfo.source || 'default') === 'default';
        });
    const fetchedTypeIds = [...new Set([
        ...newRows.map((entry) => entry.typeId),
        ...outputTypeIds,
    ])];

    if (fetchedTypeIds.length > 0) {
        return fetchAllPrices(fetchedTypeIds).then((prices) => {
            newRows.forEach(({ typeId, fuzzInput }) => {
                const priceValue = parseFloat(prices[typeId] ?? prices[String(typeId)]) || 0;
                fuzzInput.value = priceValue.toFixed(2);
                if (priceValue <= 0) {
                    fuzzInput.classList.add('bg-warning', 'border-warning');
                    fuzzInput.setAttribute('title', __('Price not available (Fuzzwork)'));
                } else {
                    fuzzInput.classList.remove('bg-warning', 'border-warning');
                    fuzzInput.removeAttribute('title');
                }
                if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function') {
                    window.SimulationAPI.setPrice(typeId, 'fuzzwork', priceValue);
                }
            });

            getFinalOutputRows().forEach((row) => {
                const typeId = Number(row.getAttribute('data-type-id')) || 0;
                if (!typeId) {
                    return;
                }
                const priceValue = parseFloat(prices[typeId] ?? prices[String(typeId)]) || 0;
                if (window.SimulationAPI && typeof window.SimulationAPI.setPrice === 'function') {
                    window.SimulationAPI.setPrice(typeId, 'fuzzwork', priceValue);
                }
                if (typeof syncFinalOutputRowPriceState === 'function') {
                    syncFinalOutputRowPriceState(row);
                }
            });

            if (typeof recalcFinancials === 'function') {
                recalcFinancials();
            }
            if (window.SimulationAPI && typeof window.SimulationAPI.markTabClean === 'function') {
                window.SimulationAPI.markTabClean('financial');
            }
        });
    }

    if (typeof recalcFinancials === 'function') {
        recalcFinancials();
    }

    if (window.SimulationAPI && typeof window.SimulationAPI.markTabClean === 'function') {
        window.SimulationAPI.markTabClean('financial');
    }

    applyFinancialPlannerFilters();
    updateFinancialGroupSectionSummaries(tableBody);
    return Promise.resolve();
}
