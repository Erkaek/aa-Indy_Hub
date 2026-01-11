/**
 * Craft Blueprint Simulation API
 * Lightweight state manager built from the blueprint payload so UI tabs can hydrate reliably.
 */
(function () {
    // Avoid stomping on an existing fully featured implementation
    if (window.SimulationAPI && window.SimulationAPI.__crafted) {
        return;
    }

    var debugEnabled = (typeof window !== 'undefined' && window.INDY_HUB_DEBUG === true);
    function debugLog() {
        if (!debugEnabled || typeof console === 'undefined' || typeof console.debug !== 'function') {
            return;
        }
        console.debug.apply(console, arguments);
    }

    function debugWarn() {
        if (!debugEnabled || typeof console === 'undefined' || typeof console.warn !== 'function') {
            return;
        }
        console.warn.apply(console, arguments);
    }

    function resolveBlueprintPayload() {
        if (window.BLUEPRINT_DATA && typeof window.BLUEPRINT_DATA === 'object' && Object.keys(window.BLUEPRINT_DATA).length > 0) {
            return window.BLUEPRINT_DATA;
        }

        const payloadNode = document.getElementById('blueprint-payload');
        if (payloadNode) {
            try {
                const parsed = JSON.parse(payloadNode.textContent || '{}');
                window.BLUEPRINT_DATA = parsed;
                return parsed;
            } catch (error) {
                console.error('[SimulationAPI] Failed to parse blueprint payload JSON from script tag.', error);
            }
        }

        return window.BLUEPRINT_DATA || {};
    }

    const payload = resolveBlueprintPayload();
    const marketGroupMap = payload.market_group_map || {};

    const materialsMap = new Map();
    const treeMap = new Map();
    const switchesMap = new Map();
    const pricesMap = new Map();

    const tabsState = {
        materials: { dirty: true, lastUpdate: null },
        tree: { dirty: false, lastUpdate: Date.now() },
        cycles: { dirty: true, lastUpdate: null },
        financial: { dirty: true, lastUpdate: null },
        needed: { dirty: true, lastUpdate: null },
        config: { dirty: true, lastUpdate: null }
    };

    const configState = {
        meLevel: payload.me || 0,
        teLevel: payload.te || 0,
        taxRate: 0
    };

    const metaState = {
        changeCount: 0,
        lastUpdate: null
    };

    function readValue(source, primaryKey, secondaryKey) {
        if (!source || typeof source !== 'object') {
            return undefined;
        }
        if (Object.prototype.hasOwnProperty.call(source, primaryKey)) {
            return source[primaryKey];
        }
        if (secondaryKey && Object.prototype.hasOwnProperty.call(source, secondaryKey)) {
            return source[secondaryKey];
        }
        return undefined;
    }

    function readChildren(node) {
        const value = readValue(node, 'sub_materials', 'subMaterials');
        return Array.isArray(value) ? value : [];
    }

    function readMarketGroup(typeId) {
        const groupInfo = marketGroupMap[typeId];
        if (!groupInfo || typeof groupInfo !== 'object') {
            return { groupName: null, groupId: null };
        }
        return {
            groupName: Object.prototype.hasOwnProperty.call(groupInfo, 'group_name') ? groupInfo.group_name : null,
            groupId: Object.prototype.hasOwnProperty.call(groupInfo, 'group_id') ? groupInfo.group_id : null
        };
    }

    function normalizeQuantity(value) {
        const num = Number(value);
        if (!Number.isFinite(num)) {
            return 0;
        }
        if (num <= 0) {
            return 0;
        }
        return Math.ceil(num);
    }

    function ingestTree(nodes, parentId = null) {
        if (!Array.isArray(nodes)) {
            return;
        }
        nodes.forEach((node) => {
            const typeId = Number(readValue(node, 'type_id', 'typeId'));
            if (!typeId) {
                return;
            }
            const typeName = readValue(node, 'type_name', 'typeName') || '';
            const quantity = normalizeQuantity(readValue(node, 'quantity', 'qty'));

            if (!treeMap.has(typeId)) {
                treeMap.set(typeId, {
                    typeId,
                    typeName,
                    quantity: 0,
                    parentIds: new Set(),
                    children: new Set(),
                    craftable: false
                });
            }
            const treeEntry = treeMap.get(typeId);
            treeEntry.quantity = Math.max(treeEntry.quantity, quantity);
            if (parentId) {
                treeEntry.parentIds.add(parentId);
            }

            const marketGroupInfo = readMarketGroup(typeId);

            if (!materialsMap.has(typeId)) {
                materialsMap.set(typeId, {
                    typeId,
                    typeName,
                    quantity,
                    marketGroup: marketGroupInfo.groupName,
                    groupId: marketGroupInfo.groupId
                });
            } else {
                const materialEntry = materialsMap.get(typeId);
                materialEntry.quantity = Math.max(materialEntry.quantity, quantity);
                if (!materialEntry.typeName && typeName) {
                    materialEntry.typeName = typeName;
                }
            }

            const children = readChildren(node);
            if (children.length > 0) {
                treeEntry.craftable = true;
                children.forEach((child) => {
                    const childId = Number(readValue(child, 'type_id', 'typeId'));
                    if (childId) {
                        treeEntry.children.add(childId);
                    }
                });
                ingestTree(children, typeId);
            }
        });
    }

    function ingestFlatMaterials(items) {
        if (!Array.isArray(items)) {
            return;
        }
        items.forEach((item) => {
            const typeId = Number(readValue(item, 'type_id', 'typeId'));
            if (!typeId) {
                return;
            }
            const typeName = readValue(item, 'type_name', 'typeName') || '';
            const quantity = normalizeQuantity(readValue(item, 'quantity', 'qty'));
            const marketGroupInfo = readMarketGroup(typeId);

            if (!materialsMap.has(typeId)) {
                materialsMap.set(typeId, {
                    typeId,
                    typeName,
                    quantity,
                    marketGroup: marketGroupInfo.groupName,
                    groupId: marketGroupInfo.groupId
                });
            } else {
                const materialEntry = materialsMap.get(typeId);
                materialEntry.quantity = Math.max(materialEntry.quantity, quantity);
                if (!materialEntry.typeName && typeName) {
                    materialEntry.typeName = typeName;
                }
            }

            if (!treeMap.has(typeId)) {
                treeMap.set(typeId, {
                    typeId,
                    typeName,
                    quantity,
                    parentIds: new Set(),
                    children: new Set(),
                    craftable: false
                });
            }
        });
    }

    function ingestMaterialsByGroup(groupedMaterials) {
        if (!groupedMaterials || typeof groupedMaterials !== 'object') {
            return;
        }
        Object.values(groupedMaterials).forEach((group) => {
            if (!group || !Array.isArray(group.items)) {
                return;
            }
            group.items.forEach((item) => {
                const typeId = Number(readValue(item, 'type_id', 'typeId'));
                if (!typeId) {
                    return;
                }
                const typeName = readValue(item, 'type_name', 'typeName') || '';
                const quantity = normalizeQuantity(readValue(item, 'quantity', 'qty'));

                if (!materialsMap.has(typeId)) {
                    materialsMap.set(typeId, {
                        typeId,
                        typeName,
                        quantity,
                        marketGroup: group.group_name || group.groupName || null,
                        groupId: group.group_id || group.groupId || null
                    });
                } else {
                    const materialEntry = materialsMap.get(typeId);
                    materialEntry.quantity = Math.max(materialEntry.quantity, quantity);
                    if (!materialEntry.typeName && typeName) {
                        materialEntry.typeName = typeName;
                    }
                    if (!materialEntry.marketGroup && (group.group_name || group.groupName)) {
                        materialEntry.marketGroup = group.group_name || group.groupName;
                    }
                    if (!materialEntry.groupId && (group.group_id || group.groupId)) {
                        materialEntry.groupId = group.group_id || group.groupId;
                    }
                }

                if (!treeMap.has(typeId)) {
                    treeMap.set(typeId, {
                        typeId,
                        typeName,
                        quantity,
                        parentIds: new Set(),
                        children: new Set(),
                        craftable: false
                    });
                }
            });
        });
    }

    ingestTree(Array.isArray(payload.materials_tree) ? payload.materials_tree : []);
    ingestFlatMaterials(Array.isArray(payload.materials) ? payload.materials : []);
    ingestFlatMaterials(Array.isArray(payload.direct_materials) ? payload.direct_materials : []);
    ingestMaterialsByGroup(payload.materials_by_group || payload.materialsByGroup);

    treeMap.forEach((entry) => {
        if (entry.craftable) {
            switchesMap.set(entry.typeId, {
                typeId: entry.typeId,
                typeName: entry.typeName,
                state: 'prod'
            });
        }
    });

    materialsMap.forEach((_, typeId) => {
        pricesMap.set(typeId, { fuzzwork: 0, real: 0, sale: 0 });
    });

    if (payload.product_type_id && !pricesMap.has(payload.product_type_id)) {
        pricesMap.set(payload.product_type_id, { fuzzwork: 0, real: 0, sale: 0 });
    }

    function ensureSimulationGlobals() {
        window.SimulationState = window.SimulationState || {};
        window.SimulationState.materials = materialsMap;
        window.SimulationState.tree = treeMap;
        window.SimulationState.switches = switchesMap;
        window.SimulationState.prices = pricesMap;
        window.SimulationState.tabs = tabsState;
        window.SimulationState.config = configState;
        window.SimulationState.meta = metaState;
    }

    ensureSimulationGlobals();

    function markTabsDirty(tabNames) {
        tabNames.forEach((name) => {
            if (!tabsState[name]) {
                tabsState[name] = { dirty: true, lastUpdate: null };
            } else {
                tabsState[name].dirty = true;
            }
        });
    }

    function markTabClean(tabName) {
        if (!tabsState[tabName]) {
            tabsState[tabName] = { dirty: false, lastUpdate: Date.now() };
        } else {
            tabsState[tabName].dirty = false;
            tabsState[tabName].lastUpdate = Date.now();
        }
    }

    function setSwitchState(typeId, state) {
        const numericId = Number(typeId);
        if (!numericId) {
            return;
        }
        if (!switchesMap.has(numericId)) {
            const material = materialsMap.get(numericId);
            switchesMap.set(numericId, {
                typeId: numericId,
                typeName: material ? material.typeName : '',
                state: state
            });
        } else {
            switchesMap.get(numericId).state = state;
        }
        metaState.changeCount += 1;
        metaState.lastUpdate = new Date().toISOString();
        markTabsDirty(['materials', 'financial', 'needed']);
    }

    function deriveStateFromDom() {
        const treeTab = document.getElementById('tab-tree');
        if (treeTab) {
            treeTab.querySelectorAll('summary input.mat-switch').forEach((input) => {
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
                setSwitchState(typeId, state);
            });
        }

        // Keep pricesMap in sync with the visible UI so optimizer and financials
        // use the same price values.
        // - fuzzwork-price: fetched market price
        // - real-price: buy cost per unit (manual override allowed)
        // - sale-price-unit: sell revenue per unit (manual override allowed)
        const priceInputs = document.querySelectorAll(
            'input.fuzzwork-price[data-type-id], input.real-price[data-type-id], input.sale-price-unit[data-type-id]'
        );
        priceInputs.forEach((input) => {
            const typeId = Number(input.getAttribute('data-type-id'));
            if (!typeId) {
                return;
            }

            let priceType = null;
            if (input.classList.contains('fuzzwork-price')) {
                priceType = 'fuzzwork';
            } else if (input.classList.contains('sale-price-unit')) {
                priceType = 'sale';
            } else {
                priceType = 'real';
            }

            const value = parseFloat(input.value);
            setPrice(typeId, priceType, Number.isFinite(value) ? value : 0);
        });
    }

    function materialToDto(entry) {
        return {
            typeId: entry.typeId,
            type_id: entry.typeId,
            name: entry.typeName,
            typeName: entry.typeName,
            type_name: entry.typeName,
            quantity: Math.ceil(entry.quantity),
            marketGroup: entry.marketGroup,
            market_group: entry.marketGroup,
            groupId: entry.groupId
        };
    }

    // NOTE: The production tree is a DAG in practice (shared children / shared leaf materials).
    // Any logic that treats parentage as a single chain (e.g. "has a BUY ancestor") will
    // incorrectly exclude shared materials when only one branch is bought.
    // We therefore aggregate demand by traversing the original payload.materials_tree (which
    // keeps duplicated occurrences per parent) and applying switch rules per path.

    function addToCounter(map, typeId, qty) {
        const numericId = Number(typeId);
        const amount = normalizeQuantity(qty);
        if (!numericId || amount <= 0) {
            return;
        }
        map.set(numericId, (map.get(numericId) || 0) + amount);
    }

    function getSwitchState(typeId) {
        const entry = switchesMap.get(Number(typeId));
        return entry ? entry.state : null;
    }

    function computeDemandFromPayloadTree() {
        const leafNeeds = new Map();
        const buyCraftables = new Map();
        const prodCraftables = new Map();

        const walk = (nodes, blockedByBuyAncestor = false) => {
            if (!Array.isArray(nodes) || nodes.length === 0) {
                return;
            }
            nodes.forEach((node) => {
                const typeId = Number(readValue(node, 'type_id', 'typeId'));
                if (!typeId) {
                    return;
                }
                if (blockedByBuyAncestor) {
                    // A bought ancestor encapsulates this subtree; do not count children separately.
                    return;
                }

                const qty = normalizeQuantity(readValue(node, 'quantity', 'qty'));
                const children = readChildren(node);
                const craftable = children.length > 0;

                const state = craftable ? (getSwitchState(typeId) || 'prod') : (getSwitchState(typeId) || 'prod');
                if (state === 'useless') {
                    return;
                }

                if (craftable) {
                    if (state === 'buy') {
                        addToCounter(buyCraftables, typeId, qty);
                        // Do not traverse children.
                        return;
                    }
                    // Produced craftable: we need its inputs.
                    addToCounter(prodCraftables, typeId, qty);
                    walk(children, false);
                    return;
                }

                // Leaf material: always a buy input (unless explicitly marked useless).
                addToCounter(leafNeeds, typeId, qty);
            });
        };

        walk(Array.isArray(payload.materials_tree) ? payload.materials_tree : [], false);
        return { leafNeeds, buyCraftables, prodCraftables };
    }

    function getFinancialItems() {
        const items = new Map();
        debugLog('[SimulationAPI] Computing financial items from payload tree traversal.');

        const demand = computeDemandFromPayloadTree();

        const addItem = (typeId, qty) => {
            const materialEntry = materialsMap.get(typeId) || treeMap.get(typeId) || { typeId, typeName: '', quantity: 0 };
            const dto = materialToDto(materialEntry);
            dto.quantity = normalizeQuantity(qty);
            items.set(Number(typeId), dto);
        };

        demand.leafNeeds.forEach((qty, typeId) => addItem(typeId, qty));
        demand.buyCraftables.forEach((qty, typeId) => addItem(typeId, qty));

        if (items.size === 0) {
            const fallbackMaterials = Array.isArray(payload.direct_materials)
                ? payload.direct_materials
                : Array.isArray(payload.materials)
                    ? payload.materials
                    : [];

            debugWarn('[SimulationAPI] No financial items derived from tree/materials map - falling back to direct materials. Count:', fallbackMaterials.length);

            fallbackMaterials.forEach((material) => {
                const typeId = Number(readValue(material, 'type_id', 'typeId'));
                if (!typeId) {
                    return;
                }
                const dto = materialToDto({
                    typeId,
                    typeName: readValue(material, 'type_name', 'typeName') || '',
                    quantity: normalizeQuantity(readValue(material, 'quantity', 'qty')),
                    marketGroup: null,
                    groupId: null
                });
                dto.quantity = dto.quantity || 0;
                debugLog('[SimulationAPI] Adding from direct materials fallback:', typeId, dto.typeName, 'quantity', dto.quantity);
                items.set(typeId, dto);
            });

            if (items.size === 0 && payload.materials_by_group) {
                debugWarn('[SimulationAPI] Direct materials fallback empty, using materials_by_group');
                Object.values(payload.materials_by_group).forEach((group) => {
                    if (!group || !Array.isArray(group.items)) {
                        return;
                    }
                    group.items.forEach((material) => {
                        const typeId = Number(readValue(material, 'type_id', 'typeId'));
                        if (!typeId) {
                            return;
                        }
                        const dto = materialToDto({
                            typeId,
                            typeName: readValue(material, 'type_name', 'typeName') || '',
                            quantity: normalizeQuantity(readValue(material, 'quantity', 'qty')),
                            marketGroup: group.group_name || group.groupName || null,
                            groupId: group.group_id || group.groupId || null
                        });
                        dto.quantity = dto.quantity || 0;
                        debugLog('[SimulationAPI] Adding from materials_by_group fallback:', typeId, dto.typeName, 'quantity', dto.quantity);
                        items.set(typeId, dto);
                    });
                });
            }
        }

        const result = Array.from(items.values());
        debugLog('[SimulationAPI] Financial items result count:', result.length);
        return result;
    }

    function getAllMaterials() {
        return Array.from(materialsMap.values()).map(materialToDto);
    }

    function getNeededMaterials() {
        // Mirrors financial items: everything that must be bought (leaf inputs + craftables switched to BUY).
        // Critically, this is path-aware for shared materials.
        return getFinancialItems();
    }

    function buildProductionCycles() {
        const results = [];

        const demand = computeDemandFromPayloadTree();
        demand.prodCraftables.forEach((qtyNeeded, typeId) => {
            const switchData = switchesMap.get(Number(typeId));
            const state = switchData ? switchData.state : 'prod';
            if (state === 'buy' || state === 'useless') {
                return;
            }

            const treeEntry = treeMap.get(Number(typeId));
            const materialEntry = materialsMap.get(Number(typeId));
            const typeName = (materialEntry && materialEntry.typeName) || (treeEntry && treeEntry.typeName) || '';
            const marketGroupInfo = readMarketGroup(typeId);
            const marketGroup = marketGroupInfo && marketGroupInfo.groupName ? marketGroupInfo.groupName : null;

            const totalNeeded = normalizeQuantity(qtyNeeded);

            // produced_per_cycle is stable per blueprint output; pull from materialsMap first.
            // Fallback to payload craft_cycles_summary if present.
            let producedPerCycle = normalizeQuantity(readValue(materialEntry, 'produced_per_cycle', 'producedPerCycle') || 0);
            if (!producedPerCycle) {
                const entry = payload.craft_cycles_summary && (payload.craft_cycles_summary[String(typeId)] || payload.craft_cycles_summary[typeId]);
                producedPerCycle = normalizeQuantity(entry ? (entry.produced_per_cycle || entry.producedPerCycle || 0) : 0);
            }

            const cycles = producedPerCycle > 0 ? Math.ceil(totalNeeded / producedPerCycle) : 0;
            const totalProduced = producedPerCycle * cycles;
            const surplus = Math.max(totalProduced - totalNeeded, 0);

            results.push({
                typeId: Number(typeId),
                typeName,
                marketGroup,
                totalNeeded,
                producedPerCycle,
                cycles,
                totalProduced,
                surplus
            });
        });

        const fallbackGroupName = 'Other';
        results.sort((a, b) => {
            const groupA = a.marketGroup || fallbackGroupName;
            const groupB = b.marketGroup || fallbackGroupName;
            const groupCmp = String(groupA).localeCompare(String(groupB), undefined, { sensitivity: 'base' });
            if (groupCmp !== 0) {
                return groupCmp;
            }
            return String(a.typeName).localeCompare(String(b.typeName), undefined, { sensitivity: 'base' });
        });
        return results;
    }

    function getPrice(typeId, preference) {
        const numericId = Number(typeId);
        if (!pricesMap.has(numericId)) {
            return { value: 0, source: 'default' };
        }
        const record = pricesMap.get(numericId);

        // Optional preference:
        // - 'buy': prioritize real > fuzzwork (never sale)
        // - 'sale': prioritize sale > fuzzwork > real
        if (preference === 'buy') {
            if (record.real > 0) {
                return { value: record.real, source: 'real' };
            }
            if (record.fuzzwork > 0) {
                return { value: record.fuzzwork, source: 'fuzzwork' };
            }
            return { value: 0, source: 'default' };
        }
        if (preference === 'sale') {
            if (record.sale > 0) {
                return { value: record.sale, source: 'sale' };
            }
            if (record.fuzzwork > 0) {
                return { value: record.fuzzwork, source: 'fuzzwork' };
            }
            if (record.real > 0) {
                return { value: record.real, source: 'real' };
            }
            return { value: 0, source: 'default' };
        }

        if (record.real > 0) {
            return { value: record.real, source: 'real' };
        }
        if (record.fuzzwork > 0) {
            return { value: record.fuzzwork, source: 'fuzzwork' };
        }
        if (record.sale > 0) {
            return { value: record.sale, source: 'sale' };
        }
        return { value: 0, source: 'default' };
    }

    function setPrice(typeId, priceType, value) {
        const numericId = Number(typeId);
        if (!numericId) {
            return;
        }
        if (!pricesMap.has(numericId)) {
            pricesMap.set(numericId, { fuzzwork: 0, real: 0, sale: 0 });
        }
        const record = pricesMap.get(numericId);
        record[priceType] = Number(value) || 0;
        markTabsDirty(['financial']);
    }

    function setConfig(key, value) {
        configState[key] = value;
        metaState.changeCount += 1;
        metaState.lastUpdate = new Date().toISOString();
        markTabsDirty(['config']);
    }

    function getConfig() {
        return {
            meLevel: configState.meLevel,
            teLevel: configState.teLevel,
            taxRate: configState.taxRate,
            changeCount: metaState.changeCount,
            lastUpdate: metaState.lastUpdate
        };
    }

    const api = {
        __crafted: true,
        refreshFromDom: deriveStateFromDom,
        initializeSwitchStates: deriveStateFromDom,
        initializeDefaultSwitchStates: deriveStateFromDom,
        setSwitchState,
        markSwitch: setSwitchState,
        getSwitchState: (typeId) => {
            const entry = switchesMap.get(Number(typeId));
            return entry ? entry.state : null;
        },
        getFinancialItems,
        getAllMaterials,
        getNeededMaterials,
    getProductionCycles: buildProductionCycles,
        getPrice,
        setPrice,
        setConfig,
        getConfig,
        markTabsDirty,
        markTabDirty: (tabName) => markTabsDirty([tabName]),
        markTabsDirtyBulk: markTabsDirty,
        markTabClean,
        markAllTabsDirty: () => markTabsDirty(Object.keys(tabsState)),
        isTabDirty: (tabName) => (tabsState[tabName] ? !!tabsState[tabName].dirty : true),
        getMaterialCount: () => materialsMap.size,
        getTreeItemCount: () => treeMap.size,
        incrementChangeCount: () => {
            metaState.changeCount += 1;
            metaState.lastUpdate = new Date().toISOString();
        },
        getState: () => ({
            materials: materialsMap,
            tree: treeMap,
            switches: switchesMap,
            prices: pricesMap,
            tabs: tabsState,
            config: configState,
            meta: metaState
        })
    };

    document.addEventListener('change', (event) => {
        const target = event.target;
        if (!target || !target.classList) {
            return;
        }
        if (!target.classList.contains('mat-switch')) {
            return;
        }
        const typeId = Number(target.getAttribute('data-type-id'));
        if (!typeId) {
            return;
        }
        let state = 'prod';
        if (target.disabled) {
            state = 'useless';
        } else if (!target.checked) {
            state = 'buy';
        }
        setSwitchState(typeId, state);
    });

    window.SimulationAPI = api;
    deriveStateFromDom();
    ensureSimulationGlobals();
})();
