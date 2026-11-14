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

    let payload = resolveBlueprintPayload();
    let marketGroupMap = payload.market_group_map || {};
    let structurePlannerPayload = payload.structure_planner || payload.structurePlanner || {};
    let rootProductTypeId = Number(payload.product_type_id || payload.productTypeId || 0);
    let rootProductOutputPerCycle = normalizeQuantity(payload.product_output_per_cycle || payload.productOutputPerCycle || 0);

    const materialsMap = new Map();
    const treeMap = new Map();
    const switchesMap = new Map();
    const pricesMap = new Map();
    const structureItemsMap = new Map();
    const structureAssignmentsMap = new Map();

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

    function readStructureItems() {
        const items = structurePlannerPayload.items;
        return Array.isArray(items) ? items : [];
    }

    function resolveRootProductTypeName() {
        if (!(rootProductTypeId > 0)) {
            return '';
        }

        const finalOutputs = Array.isArray(payload.final_outputs) ? payload.final_outputs : [];
        const outputEntry = finalOutputs.find((entry) => {
            const typeId = Number(readValue(entry, 'type_id', 'typeId'));
            return typeId === rootProductTypeId;
        });
        const outputName = readValue(outputEntry, 'type_name', 'typeName');
        if (typeof outputName === 'string' && outputName.trim()) {
            return outputName.trim();
        }

        const summary = payload.craft_cycles_summary || {};
        const summaryEntry = summary[String(rootProductTypeId)] || summary[rootProductTypeId] || null;
        const summaryName = readValue(summaryEntry, 'type_name', 'typeName');
        if (typeof summaryName === 'string' && summaryName.trim()) {
            return summaryName.trim();
        }

        return '';
    }

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

    function getFinalOutputQuantitiesByTypeId() {
        const quantities = new Map();
        const finalOutputs = Array.isArray(payload.final_outputs)
            ? payload.final_outputs
            : (Array.isArray(payload.finalOutputs) ? payload.finalOutputs : []);

        finalOutputs.forEach((entry) => {
            const typeId = Number(readValue(entry, 'type_id', 'typeId')) || 0;
            if (!(typeId > 0)) {
                return;
            }
            const quantity = normalizeQuantity(readValue(entry, 'quantity', 'qty'));
            if (!(quantity > 0)) {
                return;
            }
            quantities.set(typeId, (quantities.get(typeId) || 0) + quantity);
        });

        if (quantities.size === 0 && rootProductTypeId > 0) {
            const quantity = normalizeQuantity(payload.final_product_qty || payload.finalProductQty || 0);
            if (quantity > 0) {
                quantities.set(rootProductTypeId, quantity);
            }
        }

        return quantities;
    }

    function cloneNodeWithQuantity(node, quantity) {
        return Object.assign({}, node, { quantity: normalizeQuantity(quantity) });
    }

    function ingestTree(nodes, parentId = null) {
        if (!Array.isArray(nodes)) {
            return;
        }
        nodes.forEach((node) => {
            const typeId = Number(readValue(node, 'type_id', 'typeId'));
            if (!typeId || node.is_synthetic_final_output) {
                ingestTree(readChildren(node), parentId);
                return;
            }
            const typeName = readValue(node, 'type_name', 'typeName') || '';
            const quantity = normalizeQuantity(readValue(node, 'quantity', 'qty'));
            const inclusionModeRaw = String(
                readValue(node, 'project_inclusion_mode', 'projectInclusionMode') || ''
            ).trim().toLowerCase();
            const inclusionMode = (inclusionModeRaw === 'buy' || inclusionModeRaw === 'useless')
                ? inclusionModeRaw
                : (inclusionModeRaw === 'prod' || inclusionModeRaw === 'produce' ? 'prod' : '');

            if (!treeMap.has(typeId)) {
                treeMap.set(typeId, {
                    typeId,
                    typeName,
                    quantity: 0,
                    parentIds: new Set(),
                    children: new Set(),
                    craftable: false,
                    inclusionMode: ''
                });
            }
            const treeEntry = treeMap.get(typeId);
            treeEntry.quantity = Math.max(treeEntry.quantity, quantity);
            if (parentId) {
                treeEntry.parentIds.add(parentId);
            }
            // Capture the saved buy/prod choice carried by the project
            // workspace cache so the SimulationAPI initializes its switches
            // to the user's last saved decisions instead of defaulting every
            // craftable to 'prod' on page reload.
            if (inclusionMode && !treeEntry.inclusionMode) {
                treeEntry.inclusionMode = inclusionMode;
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

    function ingestStructurePlannerPayload(
        nextStructurePlannerPayload,
        preservedAssignments = new Map(),
        preservedOptionsByType = new Map()
    ) {
        structurePlannerPayload = (nextStructurePlannerPayload && typeof nextStructurePlannerPayload === 'object')
            ? nextStructurePlannerPayload
            : {};

        structureItemsMap.clear();
        structureAssignmentsMap.clear();
        const invalidStructures = Array.isArray(structurePlannerPayload.invalid_structures) ? structurePlannerPayload.invalid_structures : [];
        const invalidStructureIds = new Set(invalidStructures
            .filter((entry) => !entry.selection_only)
            .map((entry) => Number(entry.structure_id)));
        const invalidSelectionsByType = new Map();
        invalidStructures.filter((entry) => entry.selection_only).forEach((entry) => {
            (entry.selected_type_ids || []).forEach((typeId) => {
                const numericTypeId = Number(typeId);
                if (!invalidSelectionsByType.has(numericTypeId)) invalidSelectionsByType.set(numericTypeId, new Set());
                invalidSelectionsByType.get(numericTypeId).add(Number(entry.structure_id));
            });
        });
        const isInvalidForType = (typeId, structureId) => invalidStructureIds.has(structureId)
            || invalidSelectionsByType.get(typeId)?.has(structureId);

        readStructureItems().forEach((item) => {
            const typeId = Number(readValue(item, 'type_id', 'typeId'));
            if (!typeId) {
                return;
            }
            const options = Array.isArray(item.options) ? item.options : [];
            const optionsMap = new Map();
            options.forEach((option) => {
                const structureId = Number(readValue(option, 'structure_id', 'structureId'));
                if (!structureId || isInvalidForType(typeId, structureId)) {
                    return;
                }
                optionsMap.set(structureId, Object.assign({}, option, { structureId }));
            });
            const normalizedItem = Object.assign({}, item, {
                typeId,
                typeName: readValue(item, 'type_name', 'typeName') || '',
                selectedStructureId: Number(readValue(item, 'selected_structure_id', 'selectedStructureId')) || null,
                recommendedStructureId: Number(readValue(item, 'recommended_structure_id', 'recommendedStructureId')) || null,
                optionsMap
            });
            structureItemsMap.set(typeId, normalizedItem);

            let selectedStructureId = normalizedItem.selectedStructureId || normalizedItem.recommendedStructureId;
            if (!selectedStructureId && options.length > 0) {
                selectedStructureId = Number(readValue(options[0], 'structure_id', 'structureId')) || null;
            }

            const preservedStructureId = preservedAssignments.get(typeId);
            if (preservedStructureId) {
                if (optionsMap.has(preservedStructureId)) {
                    selectedStructureId = preservedStructureId;
                } else {
                    const preservedOption = preservedOptionsByType.get(typeId);
                    if (preservedOption && !isInvalidForType(typeId, preservedStructureId)) {
                        optionsMap.set(
                            preservedStructureId,
                            Object.assign({}, preservedOption, {
                                structureId: preservedStructureId,
                                structure_id: preservedStructureId,
                            })
                        );
                        selectedStructureId = preservedStructureId;
                    } else if (isInvalidForType(typeId, preservedStructureId)) {
                        selectedStructureId = preservedStructureId;
                    }
                }
            }

            if (selectedStructureId && (optionsMap.has(selectedStructureId) || isInvalidForType(typeId, selectedStructureId))) {
                structureAssignmentsMap.set(typeId, selectedStructureId);
            }
        });
        invalidStructures.forEach((entry) => {
            (entry.selected_type_ids || []).forEach((typeId) => {
                const numericTypeId = Number(typeId);
                const invalidStructureId = Number(entry.structure_id);
                const preservedStructureId = preservedAssignments.get(numericTypeId);
                if (!structureAssignmentsMap.has(numericTypeId)
                    && (!preservedStructureId || preservedStructureId === invalidStructureId)) {
                    structureAssignmentsMap.set(numericTypeId, invalidStructureId);
                }
            });
        });
    }

    function resetStateFromPayload(nextPayload, options) {
        const normalizedPayload = (nextPayload && typeof nextPayload === 'object') ? nextPayload : {};
        const config = Object.assign({
            preservePrices: true,
            preserveStructures: true,
            preserveSwitches: false,
        }, options || {});

        const preservedPrices = new Map();
        const preservedAssignments = new Map();
        const preservedStructureOptions = new Map();
        const preservedSwitches = new Map();

        if (config.preservePrices) {
            pricesMap.forEach((value, typeId) => {
                preservedPrices.set(Number(typeId), Object.assign({}, value));
            });
        }
        if (config.preserveStructures) {
            structureAssignmentsMap.forEach((value, typeId) => {
                const numericTypeId = Number(typeId);
                const numericStructureId = Number(value) || null;
                preservedAssignments.set(numericTypeId, numericStructureId);

                const structureItem = structureItemsMap.get(numericTypeId);
                const preservedOption = numericStructureId && structureItem && structureItem.optionsMap
                    ? structureItem.optionsMap.get(numericStructureId)
                    : null;
                if (preservedOption) {
                    preservedStructureOptions.set(numericTypeId, Object.assign({}, preservedOption));
                }
            });
        }
        if (config.preserveSwitches) {
            switchesMap.forEach((value, typeId) => {
                preservedSwitches.set(Number(typeId), Object.assign({}, value));
            });
        }

        payload = normalizedPayload;
        marketGroupMap = payload.market_group_map || {};
        const nextStructurePlannerPayload = payload.structure_planner || payload.structurePlanner || {};
        rootProductTypeId = Number(payload.product_type_id || payload.productTypeId || 0);
        rootProductOutputPerCycle = normalizeQuantity(payload.product_output_per_cycle || payload.productOutputPerCycle || 0);

        materialsMap.clear();
        treeMap.clear();
        switchesMap.clear();
        pricesMap.clear();
        structureItemsMap.clear();
        structureAssignmentsMap.clear();

        ingestTree(Array.isArray(payload.materials_tree) ? payload.materials_tree : []);
        ingestFlatMaterials(Array.isArray(payload.materials) ? payload.materials : []);
        ingestFlatMaterials(Array.isArray(payload.direct_materials) ? payload.direct_materials : []);
        ingestMaterialsByGroup(payload.materials_by_group || payload.materialsByGroup);
        ingestStructurePlannerPayload(
            nextStructurePlannerPayload,
            preservedAssignments,
            preservedStructureOptions
        );

        if (rootProductTypeId) {
            const rootProductName = resolveRootProductTypeName();
            if (!materialsMap.has(rootProductTypeId)) {
                materialsMap.set(rootProductTypeId, {
                    typeId: rootProductTypeId,
                    typeName: rootProductName,
                    quantity: normalizeQuantity(payload.final_product_qty || payload.finalProductQty || 0),
                    marketGroup: null,
                    groupId: null,
                    producedPerCycle: rootProductOutputPerCycle
                });
            }
            if (!treeMap.has(rootProductTypeId)) {
                treeMap.set(rootProductTypeId, {
                    typeId: rootProductTypeId,
                    typeName: rootProductName,
                    quantity: normalizeQuantity(payload.final_product_qty || payload.finalProductQty || 0),
                    parentIds: new Set(),
                    children: new Set(),
                    craftable: Array.isArray(payload.materials_tree) && payload.materials_tree.length > 0
                });
            }
        }

        treeMap.forEach((entry) => {
            if (!entry.craftable && entry.inclusionMode !== 'useless') {
                return;
            }

            const preserved = preservedSwitches.get(entry.typeId);
            // Initial state precedence:
            //   1. Preserved switch state from a previous ingest (set/refresh).
            //   2. project_inclusion_mode carried by the materials_tree node
            //      (this reflects the saved buy/prod decisions stored in the
            //      project workspace cache, so the user's choices survive
            //      reload).
            //   3. 'prod' default.
            let initialState = 'prod';
            if (preserved && preserved.state) {
                initialState = preserved.state;
            } else if (entry.inclusionMode === 'buy' || entry.inclusionMode === 'useless') {
                initialState = entry.inclusionMode;
            } else if (entry.inclusionMode === 'prod') {
                initialState = 'prod';
            }
            switchesMap.set(entry.typeId, {
                typeId: entry.typeId,
                typeName: entry.typeName,
                state: initialState
            });
        });

        materialsMap.forEach((_, typeId) => {
            const preserved = preservedPrices.get(typeId);
            pricesMap.set(typeId, preserved ? Object.assign({ fuzzwork: 0, real: 0, sale: 0 }, preserved) : { fuzzwork: 0, real: 0, sale: 0 });
        });

        if (payload.product_type_id && !pricesMap.has(payload.product_type_id)) {
            pricesMap.set(payload.product_type_id, { fuzzwork: 0, real: 0, sale: 0 });
        }

        configState.meLevel = payload.me || 0;
        configState.teLevel = payload.te || 0;
        metaState.changeCount += 1;
        metaState.lastUpdate = new Date().toISOString();
        markTabsDirty(['materials', 'tree', 'cycles', 'financial', 'needed', 'config']);
        ensureSimulationGlobals();
    }

    resetStateFromPayload(payload, {
        preservePrices: false,
        preserveStructures: false,
        preserveSwitches: false,
    });

    function ensureSimulationGlobals() {
        window.SimulationState = window.SimulationState || {};
        window.SimulationState.materials = materialsMap;
        window.SimulationState.tree = treeMap;
        window.SimulationState.switches = switchesMap;
        window.SimulationState.prices = pricesMap;
        window.SimulationState.tabs = tabsState;
        window.SimulationState.config = configState;
        window.SimulationState.meta = metaState;
        window.SimulationState.structureItems = structureItemsMap;
        window.SimulationState.structureAssignments = structureAssignmentsMap;
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
        markTabsDirty(['materials', 'tree', 'cycles', 'financial', 'needed']);
    }

    function resolveSwitchStateFromInput(input) {
        if (!input) {
            return 'prod';
        }

        const fixedMode = String(input.dataset.fixedMode || '').trim();
        const userState = String(input.dataset.userState || '').trim();
        const isLockedByParent = input.disabled && input.dataset.lockedByParent === 'true';

        if (fixedMode === 'useless' || userState === 'useless') {
            return 'useless';
        }

        if (isLockedByParent) {
            return userState === 'buy' ? 'buy' : 'prod';
        }

        if (!input.checked) {
            return 'buy';
        }

        return 'prod';
    }

    function deriveStateFromDom() {
        const switchInputs = [];
        const seenTypeIds = new Set();

        document.querySelectorAll('#decisionStrategyRows input.mat-switch[data-type-id]').forEach((input) => {
            const typeId = Number(input.getAttribute('data-type-id'));
            if (!typeId || seenTypeIds.has(typeId)) {
                return;
            }
            seenTypeIds.add(typeId);
            switchInputs.push(input);
        });

        document.querySelectorAll('#tab-tree summary input.mat-switch[data-type-id]').forEach((input) => {
            const typeId = Number(input.getAttribute('data-type-id'));
            if (!typeId || seenTypeIds.has(typeId)) {
                return;
            }
            seenTypeIds.add(typeId);
            switchInputs.push(input);
        });

        switchInputs.forEach((input) => {
            const typeId = Number(input.getAttribute('data-type-id'));
            if (!typeId) {
                return;
            }
            setSwitchState(typeId, resolveSwitchStateFromInput(input));
        });

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
            const numericValue = Number.isFinite(value) ? value : 0;
            if (priceType === 'fuzzwork') {
                setPrice(typeId, priceType, numericValue);
                return;
            }

            const isManualOverride = input.dataset.userModified === 'true';
            if (isManualOverride || numericValue > 0) {
                setPrice(typeId, priceType, numericValue);
            }
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

    function getStructureAssignment(typeId) {
        return structureAssignmentsMap.get(Number(typeId)) || null;
    }

    function getStructureItem(typeId) {
        return structureItemsMap.get(Number(typeId)) || null;
    }

    function getStructureOption(typeId, structureId) {
        const item = getStructureItem(typeId);
        if (!item || !item.optionsMap) {
            return null;
        }
        const resolvedStructureId = Number(structureId || getStructureAssignment(typeId) || 0);
        if (!resolvedStructureId) {
            return null;
        }
        return item.optionsMap.get(resolvedStructureId) || null;
    }

    function getStructureMaterialBonus(typeId) {
        const option = getStructureOption(typeId);
        if (!option) {
            return 0;
        }
        const bonus = Number(readValue(option, 'material_bonus_percent', 'materialBonusPercent')) || 0;
        return bonus > 0 ? bonus : 0;
    }

    function computeStructureAdjustedMaterialQuantity(child, fallbackQuantity, materialBonusPercent, runsOverride, originalRuns) {
        const quantity = normalizeQuantity(fallbackQuantity);
        const baseQuantity = Number(readValue(child, 'base_quantity_per_run', 'baseQuantityPerRun')) || 0;
        const savedRuns = normalizeQuantity(readValue(child, 'job_runs', 'jobRuns')) || normalizeQuantity(originalRuns);
        const jobRuns = runsOverride === undefined ? savedRuns : normalizeQuantity(runsOverride);
        const applicable = readValue(child, 'material_bonus_applicable', 'materialBonusApplicable');
        if (!(jobRuns > 0)) {
            return runsOverride === undefined ? quantity : 0;
        }
        if (baseQuantity > 0) {
            const blueprintME = Math.max(0, Math.min(100,
                Number(readValue(child, 'blueprint_material_efficiency', 'blueprintMaterialEfficiency')) || 0));
            const blueprintMultiplier = applicable === false ? 1 : 1 - blueprintME / 100;
            const structureMultiplier = applicable === false ? 1 : Math.max(0, 1 - materialBonusPercent / 100);
            // Reduce the entire job, then round once, with one material per run.
            return Math.max(jobRuns, Math.ceil(baseQuantity * jobRuns * blueprintMultiplier * structureMultiplier));
        }
        // Pre-recipe payloads do not contain raw ME/exemption data. Preserve their
        // saved inputs and reconstruct per job run, never per required output unit.
        const reconstructed = savedRuns > 0 ? quantity * jobRuns / savedRuns : quantity;
        const structureMultiplier = applicable === true ? Math.max(0, 1 - materialBonusPercent / 100) : 1;
        return Math.max(jobRuns, normalizeQuantity(reconstructed * structureMultiplier));
    }

    function getRecipeGraph() {
        const recipes = new Map();
        const rootNodes = Array.isArray(payload.materials_tree) ? payload.materials_tree : [];
        function remember(node) {
            const typeId = Number(readValue(node, 'type_id', 'typeId')) || 0;
            const children = readChildren(node);
            if (typeId > 0 && children.length > 0 && !node.is_synthetic_final_output && !recipes.has(typeId)) {
                const summary = payload.craft_cycles_summary?.[typeId] || {};
                const producedPerCycle = normalizeQuantity(readValue(node, 'produced_per_cycle', 'producedPerCycle'))
                    || normalizeQuantity(readValue(summary, 'produced_per_cycle', 'producedPerCycle'))
                    || (typeId === rootProductTypeId ? rootProductOutputPerCycle : 0) || 1;
                const originalRuns = normalizeQuantity(node.cycles)
                    || normalizeQuantity(readValue(children[0], 'job_runs', 'jobRuns'))
                    || Math.ceil(normalizeQuantity(node.quantity) / producedPerCycle) || 1;
                recipes.set(typeId, { node, children, producedPerCycle, originalRuns });
            }
            children.forEach(remember);
        }
        rootNodes.forEach(remember);
        const flattenSynthetic = (nodes) => nodes.flatMap((node) => node.is_synthetic_final_output ? flattenSynthetic(readChildren(node)) : [node]);
        const demandRoots = flattenSynthetic(rootNodes);
        const includesRoot = rootProductTypeId > 0 && demandRoots.some((node) => Number(readValue(node, 'type_id', 'typeId')) === rootProductTypeId);
        if (rootProductTypeId > 0 && rootNodes.length > 0 && !includesRoot) {
            const finalQuantities = getFinalOutputQuantitiesByTypeId();
            const producedPerCycle = rootProductOutputPerCycle || 1;
            const quantity = finalQuantities.get(rootProductTypeId)
                || normalizeQuantity(payload.final_product_qty || payload.finalProductQty)
                || normalizeQuantity(payload.runs) * producedPerCycle;
            recipes.set(rootProductTypeId, {
                node: { type_id: rootProductTypeId, quantity },
                children: rootNodes,
                producedPerCycle,
                originalRuns: normalizeQuantity(readValue(rootNodes[0], 'job_runs', 'jobRuns'))
                    || normalizeQuantity(payload.runs) || Math.ceil(quantity / producedPerCycle) || 1,
            });
        }
        return { recipes, rootNodes, demandRoots, includesRoot };
    }

    function getProductionInputs(typeId, runs, graph = getRecipeGraph()) {
        const recipe = graph.recipes.get(Number(typeId));
        if (!recipe) {
            return [];
        }
        const bonus = getStructureMaterialBonus(typeId);
        return recipe.children.map((child) => Object.assign({}, child, {
            quantity: computeStructureAdjustedMaterialQuantity(child, readValue(child, 'quantity', 'qty'), bonus, runs, recipe.originalRuns),
            job_runs: normalizeQuantity(runs),
        }));
    }

    function getProdStockAllocationMap() {
        const raw = typeof window.getCraftProdStockAllocationsForCurrentPlan === 'function'
            ? window.getCraftProdStockAllocationsForCurrentPlan()
            : {};
        const allocations = new Map();

        Object.entries(raw || {}).forEach(([typeId, quantity]) => {
            const numericTypeId = Number(typeId) || 0;
            const normalizedQuantity = normalizeQuantity(quantity);
            if (numericTypeId > 0 && normalizedQuantity > 0) {
                allocations.set(numericTypeId, normalizedQuantity);
            }
        });

        return allocations;
    }

    function consumeProdStockAllocation(allocations, typeId, quantity) {
        const numericTypeId = Number(typeId) || 0;
        const normalizedQuantity = normalizeQuantity(quantity);
        if (!(numericTypeId > 0) || !(normalizedQuantity > 0) || !(allocations instanceof Map)) {
            return normalizedQuantity;
        }

        const remainingAllocation = allocations.get(numericTypeId) || 0;
        if (!(remainingAllocation > 0)) {
            return normalizedQuantity;
        }

        const coveredQuantity = Math.min(normalizedQuantity, remainingAllocation);
        const nextAllocation = Math.max(0, remainingAllocation - coveredQuantity);
        if (nextAllocation > 0) {
            allocations.set(numericTypeId, nextAllocation);
        } else {
            allocations.delete(numericTypeId);
        }

        return Math.max(0, normalizedQuantity - coveredQuantity);
    }

    function computeDemandFromPayloadTree(options = {}) {
        const leafNeeds = new Map();
        const buyCraftables = new Map();
        const prodCraftables = new Map();
        const productionRuns = new Map();
        const requirements = new Map();
        const graph = getRecipeGraph();
        const { recipes, rootNodes, demandRoots, includesRoot } = graph;
        const usableTree = demandRoots.some((node) => Number(readValue(node, 'type_id', 'typeId')) > 0);
        const finalQuantities = getFinalOutputQuantitiesByTypeId();
        const allocations = options.stockAllocations === undefined ? getProdStockAllocationMap() : new Map(options.stockAllocations);
        const modeFor = (typeId) => options.decisions?.get(typeId) || getSwitchState(typeId) || 'prod';
        (options.skipStockForTypes || []).forEach((typeId) => allocations.delete(Number(typeId)));

        // A topological pass gathers every incoming requirement before rounding
        // a shared subproduction. Its integer jobs drive both inputs and cycles.
        const order = [];
        const visited = new Set();
        const visiting = new Set();
        function visit(typeId) {
            if (visited.has(typeId)) return;
            if (visiting.has(typeId)) throw new Error('Cyclic craft recipe for type ' + typeId);
            visiting.add(typeId);
            const recipe = recipes.get(typeId);
            if (recipe) recipe.children.forEach((child) => visit(Number(readValue(child, 'type_id', 'typeId'))));
            visiting.delete(typeId);
            visited.add(typeId);
            order.push(typeId);
        }
        if (Array.isArray(options.roots)) {
            options.roots.forEach((root) => {
                addToCounter(requirements, Number(root.typeId), root.quantity);
                visit(Number(root.typeId));
            });
        } else if (rootProductTypeId > 0 && !includesRoot && recipes.has(rootProductTypeId)) {
            addToCounter(requirements, rootProductTypeId, recipes.get(rootProductTypeId).node.quantity);
            visit(rootProductTypeId);
        } else {
            const seededFinalTypes = new Set();
            demandRoots.forEach((node) => {
                const typeId = Number(readValue(node, 'type_id', 'typeId'));
                if (finalQuantities.has(typeId)) {
                    if (!seededFinalTypes.has(typeId)) addToCounter(requirements, typeId, finalQuantities.get(typeId));
                    seededFinalTypes.add(typeId);
                } else {
                    addToCounter(requirements, typeId, readValue(node, 'quantity', 'qty'));
                }
                visit(typeId);
            });
        }
        order.reverse().forEach((typeId) => {
            const quantity = requirements.get(typeId) || 0;
            const mode = modeFor(typeId);
            if (!(quantity > 0) || mode === 'useless') return;
            const recipe = recipes.get(typeId);
            if (!recipe) {
                addToCounter(leafNeeds, typeId, quantity);
            } else if (mode === 'buy') {
                addToCounter(buyCraftables, typeId, quantity);
            } else {
                const effectiveQuantity = consumeProdStockAllocation(allocations, typeId, quantity);
                if (!(effectiveQuantity > 0)) return;
                addToCounter(prodCraftables, typeId, effectiveQuantity);
                const runs = Math.ceil(effectiveQuantity / recipe.producedPerCycle);
                productionRuns.set(typeId, runs);
                getProductionInputs(typeId, runs, graph).forEach((child) => {
                    addToCounter(requirements, Number(readValue(child, 'type_id', 'typeId')), child.quantity);
                });
            }
        });
        const outputPerCycle = new Map(Array.from(recipes, ([typeId, recipe]) => [typeId, recipe.producedPerCycle]));
        return { leafNeeds, buyCraftables, prodCraftables, productionRuns, requirements, outputPerCycle, usableTree };
    }

    function getCalculatedTree() {
        const graph = getRecipeGraph();
        const { recipes, rootNodes, includesRoot } = graph;
        const allocations = getProdStockAllocationMap();
        const finalQuantities = getFinalOutputQuantitiesByTypeId();
        const cumulativeQuantity = new Map();
        const seededFinalTypes = new Set();
        function project(node, quantity) {
            if (node.is_synthetic_final_output) {
                return Object.assign({}, node, { sub_materials: readChildren(node).map((child) => project(child, normalizeQuantity(readValue(child, 'quantity', 'qty')))) });
            }
            const typeId = Number(readValue(node, 'type_id', 'typeId'));
            const recipe = recipes.get(typeId);
            const result = cloneNodeWithQuantity(node, quantity);
            if (!recipe) return result;
            const mode = getSwitchState(typeId) || 'prod';
            const netQuantity = mode === 'prod' ? consumeProdStockAllocation(allocations, typeId, quantity) : 0;
            const previousQuantity = cumulativeQuantity.get(typeId) || 0;
            const nextQuantity = previousQuantity + netQuantity;
            cumulativeQuantity.set(typeId, nextQuantity);
            const previousRuns = Math.ceil(previousQuantity / recipe.producedPerCycle);
            const nextRuns = Math.ceil(nextQuantity / recipe.producedPerCycle);
            const previousInputs = new Map(getProductionInputs(typeId, previousRuns, graph).map((child) => [Number(readValue(child, 'type_id', 'typeId')), child.quantity]));
            const nextInputs = new Map(getProductionInputs(typeId, nextRuns, graph).map((child) => [Number(readValue(child, 'type_id', 'typeId')), child.quantity]));
            result.production_quantity = netQuantity;
            result.cycles = nextRuns - previousRuns;
            result.total_produced = result.cycles * recipe.producedPerCycle;
            result.sub_materials = readChildren(node).map((child) => {
                const childId = Number(readValue(child, 'type_id', 'typeId'));
                const childQuantity = Math.max(0, (nextInputs.get(childId) || 0) - (previousInputs.get(childId) || 0));
                return project(Object.assign({}, child, { job_runs: result.cycles }), childQuantity);
            });
            return result;
        }
        if (rootProductTypeId > 0 && !includesRoot && recipes.has(rootProductTypeId)) {
            const recipe = recipes.get(rootProductTypeId);
            const root = Object.assign({}, recipe.node, { sub_materials: rootNodes });
            return project(root, root.quantity).sub_materials;
        }
        return rootNodes.map((node) => {
            const typeId = Number(readValue(node, 'type_id', 'typeId'));
            let quantity = normalizeQuantity(readValue(node, 'quantity', 'qty'));
            if (!node.is_synthetic_final_output && finalQuantities.has(typeId)) {
                quantity = seededFinalTypes.has(typeId) ? 0 : finalQuantities.get(typeId);
                seededFinalTypes.add(typeId);
            }
            return project(node, quantity);
        });
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

        if (!demand.usableTree) {
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
            const typeName = (materialEntry && materialEntry.typeName)
                || (treeEntry && treeEntry.typeName)
                || (typeId === rootProductTypeId ? resolveRootProductTypeName() : '');
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
            if (!producedPerCycle && typeId === rootProductTypeId) {
                producedPerCycle = rootProductOutputPerCycle;
            }

            producedPerCycle = demand.outputPerCycle.get(Number(typeId)) || producedPerCycle;
            const cycles = demand.productionRuns.get(Number(typeId)) || 0;
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
            const groupCmp = String(groupA).localeCompare(String(groupB), window.getIndyHubLocale(), { sensitivity: 'base' });
            if (groupCmp !== 0) {
                return groupCmp;
            }
            return String(a.typeName).localeCompare(String(b.typeName), window.getIndyHubLocale(), { sensitivity: 'base' });
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
        replacePayload: (nextPayload, options) => {
            resetStateFromPayload(nextPayload, options);
            return payload;
        },
        replaceStructurePlanner: (nextStructurePlannerPayload, options) => {
            const config = Object.assign({ preserveAssignments: true }, options || {});
            const preservedAssignments = new Map();
            const preservedStructureOptions = new Map();
            if (config.preserveAssignments) {
                structureAssignmentsMap.forEach((value, typeId) => {
                    const numericTypeId = Number(typeId);
                    const numericStructureId = Number(value) || null;
                    preservedAssignments.set(numericTypeId, numericStructureId);

                    const structureItem = structureItemsMap.get(numericTypeId);
                    const preservedOption = numericStructureId && structureItem && structureItem.optionsMap
                        ? structureItem.optionsMap.get(numericStructureId)
                        : null;
                    if (preservedOption) {
                        preservedStructureOptions.set(numericTypeId, Object.assign({}, preservedOption));
                    }
                });
            }
            ingestStructurePlannerPayload(
                nextStructurePlannerPayload,
                preservedAssignments,
                preservedStructureOptions
            );
            markTabsDirty(['materials', 'tree', 'financial', 'needed', 'cycles']);
            return structurePlannerPayload;
        },
        setSwitchState,
        markSwitch: setSwitchState,
        getSwitchState: (typeId) => {
            const entry = switchesMap.get(Number(typeId));
            return entry ? entry.state : null;
        },
        getFinancialItems,
        computeDemand: computeDemandFromPayloadTree,
        getProductionInputs,
        getCalculatedTree,
        computeJobMaterialQuantity: computeStructureAdjustedMaterialQuantity,
        getAllMaterials,
        getNeededMaterials,
        getProductionCycles: buildProductionCycles,
        getPrice,
        setPrice,
        setConfig,
        getConfig,
        getStructurePlanner: () => structurePlannerPayload,
        getStructureItems: () => Array.from(structureItemsMap.values()).map((item) => {
            const selectedStructureId = getStructureAssignment(item.typeId);
            return Object.assign({}, item, {
                selectedStructureId,
                selected_structure_id: selectedStructureId,
                options: Array.from(item.optionsMap.values())
            });
        }),
        getStructureAssignment,
        getStructureOption,
        setStructureAssignment: (typeId, structureId) => {
            const numericTypeId = Number(typeId);
            const numericStructureId = Number(structureId);
            const item = getStructureItem(numericTypeId);
            if (!item || !item.optionsMap || !item.optionsMap.has(numericStructureId)) {
                return false;
            }
            structureAssignmentsMap.set(numericTypeId, numericStructureId);
            metaState.changeCount += 1;
            metaState.lastUpdate = new Date().toISOString();
            markTabsDirty(['materials', 'financial', 'needed', 'cycles']);
            return true;
        },
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
            meta: metaState,
            structureItems: structureItemsMap,
            structureAssignments: structureAssignmentsMap
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
        setSwitchState(typeId, resolveSwitchStateFromInput(target));
    });

    window.SimulationAPI = api;
    deriveStateFromDom();
    ensureSimulationGlobals();
})();
