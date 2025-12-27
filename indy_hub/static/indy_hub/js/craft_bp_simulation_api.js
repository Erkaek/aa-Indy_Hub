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
        if (!treeTab) {
            return;
        }
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

    const buyAncestorCache = new Map();

    function hasBuyingAncestor(typeId, visited = new Set()) {
        const numericId = Number(typeId);
        if (!numericId || visited.has(numericId)) {
            return false;
        }
        if (buyAncestorCache.has(numericId)) {
            return buyAncestorCache.get(numericId);
        }

        const treeEntry = treeMap.get(numericId);
        if (!treeEntry || !treeEntry.parentIds || treeEntry.parentIds.size === 0) {
            buyAncestorCache.set(numericId, false);
            return false;
        }

        visited.add(numericId);
        let hasBuy = false;
        treeEntry.parentIds.forEach((parentId) => {
            if (hasBuy) {
                return;
            }
            const parentNumeric = Number(parentId);
            const parentSwitch = switchesMap.get(parentNumeric);
            if (parentSwitch && parentSwitch.state === 'buy') {
                hasBuy = true;
                return;
            }
            if (parentNumeric && hasBuyingAncestor(parentNumeric, visited)) {
                hasBuy = true;
            }
        });
        visited.delete(numericId);

        buyAncestorCache.set(numericId, hasBuy);
        return hasBuy;
    }

    function getFinancialItems() {
        buyAncestorCache.clear();
        const items = new Map();

        debugLog('[SimulationAPI] treeMap size:', treeMap.size, 'switches size:', switchesMap.size);

        treeMap.forEach((treeEntry, typeId) => {
            const switchData = switchesMap.get(typeId);
            const state = switchData ? switchData.state : 'prod';

            if (state === 'useless') {
                return;
            }

            if (hasBuyingAncestor(typeId)) {
                debugLog('[SimulationAPI] Skipping', typeId, treeEntry?.typeName, 'because ancestor is BUY');
                return;
            }

            const isLeaf = !treeEntry || treeEntry.children.size === 0 || !treeEntry.craftable;
            const shouldInclude = isLeaf || state === 'buy';

            if (!shouldInclude) {
                return;
            }

            debugLog('[SimulationAPI] Including from treeMap:', typeId, treeEntry.typeName, 'isLeaf?', isLeaf, 'state', state);

            const materialEntry = materialsMap.get(typeId) || {
                typeId,
                typeName: treeEntry.typeName,
                quantity: treeEntry.quantity,
                marketGroup: null,
                groupId: null
            };

            const dto = materialToDto(materialEntry);
            dto.quantity = Math.max(dto.quantity || 0, treeEntry.quantity || 0);
            items.set(typeId, dto);
        });

        materialsMap.forEach((entry, typeId) => {
            if (!items.has(typeId)) {
                const treeEntry = treeMap.get(typeId);
                const switchData = switchesMap.get(typeId);
                const state = switchData ? switchData.state : 'prod';
                const craftable = treeEntry ? treeEntry.craftable : false;
                if (!hasBuyingAncestor(typeId) && state !== 'useless' && (!craftable || state === 'buy')) {
                    debugLog('[SimulationAPI] Adding from materialsMap fallback:', typeId, entry.typeName, 'craftable?', craftable, 'state', state);
                    items.set(typeId, materialToDto(entry));
                }
            }
        });

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
        // Currently mirrors financial items – can be refined later if needed
        return getFinancialItems();
    }

    function buildProductionCycles() {
        const results = [];

        treeMap.forEach((treeEntry, typeId) => {
            if (!treeEntry || !treeEntry.craftable) {
                return;
            }

            const switchData = switchesMap.get(typeId);
            const state = switchData ? switchData.state : 'prod';
            if (state === 'buy' || state === 'useless') {
                return;
            }

            const materialEntry = materialsMap.get(typeId);
            const totalNeeded = normalizeQuantity(materialEntry ? materialEntry.quantity : treeEntry.quantity);
            const producedPerCycle = normalizeQuantity(readValue(materialEntry, 'produced_per_cycle', 'producedPerCycle') || treeEntry.quantity || 0);

            const cycles = producedPerCycle > 0 ? Math.ceil(totalNeeded / producedPerCycle) : 0;
            const totalProduced = producedPerCycle * cycles;
            const surplus = Math.max(totalProduced - totalNeeded, 0);

            results.push({
                typeId,
                typeName: treeEntry.typeName,
                totalNeeded,
                producedPerCycle,
                cycles,
                totalProduced,
                surplus
            });
        });

        results.sort((a, b) => a.typeName.localeCompare(b.typeName));
        return results;
    }

    function getPrice(typeId) {
        const numericId = Number(typeId);
        if (!pricesMap.has(numericId)) {
            return { value: 0, source: 'default' };
        }
        const record = pricesMap.get(numericId);
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
