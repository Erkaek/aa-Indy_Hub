/** Offline regressions for issue #182: real SimulationAPI, no copied arithmetic. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const jsRoot = path.join(__dirname, '..', 'indy_hub', 'static', 'indy_hub', 'js');
const simulationSource = fs.readFileSync(path.join(jsRoot, 'craft_bp_simulation_api.js'), 'utf8');
const coreSource = fs.readFileSync(path.join(jsRoot, 'craft_bp.js'), 'utf8');

function material(typeId, base, runs, children = [], output = 1, me = 0, applicable = true) {
    return {
        type_id: typeId, type_name: `Item ${typeId}`,
        quantity: Math.max(runs, Math.ceil(base * runs * (applicable ? 1 - me / 100 : 1))),
        base_quantity_per_run: base, job_runs: runs,
        blueprint_material_efficiency: me, material_bonus_applicable: applicable,
        produced_per_cycle: children.length ? output : null,
        cycles: children.length ? Math.ceil(base * runs / output) : null,
        sub_materials: children,
    };
}

function basicPayload({ stock = {}, output = 1, quantity = 100, bonus = 10 } = {}) {
    const childRuns = Math.ceil(quantity / output);
    return {
        product_type_id: 1, product_output_per_cycle: 1,
        final_product_qty: 1, runs: 1,
        materials_tree: [material(2, quantity, 1, [material(3, 10, childRuns)], output)],
        direct_materials: [{ type_id: 3, type_name: 'Raw', quantity: childRuns * 10 }],
        structure_planner: { items: [{ type_id: 1, selected_structure_id: 10, options: [
            { structure_id: 10, material_bonus_percent: bonus },
            { structure_id: 11, material_bonus_percent: 0 },
        ] }] },
        _stock: stock,
    };
}

function createHarness(payload, stock = payload._stock || {}) {
    const window = {
        BLUEPRINT_DATA: payload,
        getCraftProdStockAllocationsForCurrentPlan: () => stock,
        getIndyHubLocale: () => 'en',
    };
    const document = { getElementById: () => null, querySelectorAll: () => [], addEventListener() {} };
    const context = vm.createContext({ window, document, console, Map, Set, Date, Promise, __: (text) => text });
    vm.runInContext(simulationSource, context);
    return { api: window.SimulationAPI, context, window, stock };
}

function quantities(api) {
    return Object.fromEntries(api.getFinancialItems().map((entry) => [entry.typeId, entry.quantity]));
}
function cycles(api) {
    return Object.fromEntries(api.getProductionCycles().map((entry) => [entry.typeId, entry.cycles]));
}

async function decisionAnalysis(harness) {
    const context = harness.context;
    context.CRAFT_BP = { productTypeId: harness.window.BLUEPRINT_DATA.product_type_id || 0 };
    context.getCurrentDecisionsFromSimulationOrDom = () => new Map(
        Array.from(harness.api.getState().switches, ([typeId, record]) => [typeId, record.state]));
    context.readMaterialsTreeChildren = (node) => node.sub_materials || [];
    context.getDecisionBuyToleranceValue = () => 0;
    context.getCraftDecisionBuyUnitPrice = (typeId) => harness.api.getPrice(typeId, 'buy').value;
    context.getCraftDecisionBuyUnitPriceOrInf = (typeId) => context.getCraftDecisionBuyUnitPrice(typeId) || Infinity;
    context.getCraftDecisionSellUnitPrice = (typeId) => harness.api.getPrice(typeId, 'sale').value;
    const start = coreSource.indexOf('async function computeCraftDecisionAnalysis(');
    const end = coreSource.indexOf('\nfunction renderDecisionStrategyPanel(', start);
    vm.runInContext(coreSource.slice(start, end), context);
    return context.computeCraftDecisionAnalysis({ toleranceISK: 0 });
}

const cases = [];
function test(name, run) { cases.push({ name, run }); }

test('parent ME recomputes intermediate jobs: 90 / 900', () => {
    const { api } = createHarness(basicPayload());
    assert.equal(cycles(api)[2], 90);
    assert.deepEqual(quantities(api), { 3: 900 });
    const tree = api.getCalculatedTree();
    assert.equal(tree[0].quantity, 90);
    assert.equal(tree[0].cycles, 90);
    assert.equal(tree[0].sub_materials[0].quantity, 900);
    assert.equal(tree[0].sub_materials[0].job_runs, 90);
});

test('partial intermediate stock recomputes jobs: 40 / 400', () => {
    const { api } = createHarness(basicPayload({ stock: { 2: 50 } }));
    assert.equal(cycles(api)[2], 40);
    assert.deepEqual(quantities(api), { 3: 400 });
    assert.equal(api.getCalculatedTree()[0].sub_materials[0].quantity, 400);
});

test('full intermediate stock gives no descendant inputs', () => {
    const { api } = createHarness(basicPayload({ stock: { 2: 100 } }));
    assert.equal(cycles(api)[2], undefined);
    assert.deepEqual(quantities(api), {});
});

test('full final output stock stays zero despite direct_materials', () => {
    const { api } = createHarness(basicPayload({ stock: { 1: 1 } }));
    assert.deepEqual(cycles(api), {});
    assert.deepEqual(quantities(api), {});
    assert.equal(api.getCalculatedTree()[0].quantity, 0);
});

test('missing tree retains legacy direct_materials fallback', () => {
    const { api } = createHarness({ direct_materials: [{ type_id: 3, quantity: 1000 }] });
    assert.deepEqual(quantities(api), { 3: 1000 });
});

test('unusable tree retains grouped legacy fallback', () => {
    const { api } = createHarness({ materials_tree: [{}], materials_by_group: { raw: { items: [{ type_id: 3, quantity: 1000 }] } } });
    assert.deepEqual(quantities(api), { 3: 1000 });
});

test('batch output above one rounds integer jobs before recipe inputs', () => {
    const { api } = createHarness(basicPayload({ output: 7, stock: { 2: 50 } }));
    assert.equal(cycles(api)[2], 6);
    assert.deepEqual(quantities(api), { 3: 60 });
});

test('structure and blueprint ME round once across an entire job', () => {
    const payload = basicPayload({ quantity: 9, bonus: 0 });
    payload.materials_tree[0].sub_materials = [material(3, 2, 9, [], 1, 10)];
    payload.structure_planner.items.push({ type_id: 2, selected_structure_id: 20, options: [{ structure_id: 20, material_bonus_percent: 10 }] });
    const { api } = createHarness(payload);
    assert.deepEqual(quantities(api), { 3: 15 });
});

test('each material has a minimum of one unit per run', () => {
    const payload = basicPayload({ quantity: 100 });
    payload.materials_tree[0].sub_materials = [material(3, 1, 100, [], 1, 10)];
    payload.structure_planner.items.push({ type_id: 2, selected_structure_id: 20, options: [{ structure_id: 20, material_bonus_percent: 50 }] });
    const { api } = createHarness(payload);
    assert.deepEqual(quantities(api), { 3: 90 });
});

test('ME exemptions prevent both blueprint and structure reduction', () => {
    const payload = basicPayload({ bonus: 50 });
    payload.materials_tree[0].sub_materials = [material(3, 10, 100, [], 1, 10, false)];
    payload.structure_planner.items.push({ type_id: 2, selected_structure_id: 20, options: [{ structure_id: 20, material_bonus_percent: 50 }] });
    const { api } = createHarness(payload);
    assert.deepEqual(quantities(api), { 3: 500 });
});

test('structure changes recompute from raw data without compounding bonuses', () => {
    const payload = basicPayload();
    const original = JSON.stringify(payload);
    const { api } = createHarness(payload);
    assert.deepEqual(quantities(api), { 3: 900 });
    api.setStructureAssignment(1, 11);
    assert.deepEqual(quantities(api), { 3: 1000 });
    api.setStructureAssignment(1, 10);
    assert.deepEqual(quantities(api), { 3: 900 });
    assert.equal(JSON.stringify(payload), original);
});

test('Buy / Produce / Useless changes update purchases and cycles', () => {
    const { api } = createHarness(basicPayload());
    api.setSwitchState(2, 'buy');
    assert.deepEqual(quantities(api), { 2: 90 });
    assert.equal(cycles(api)[2], undefined);
    api.setSwitchState(2, 'useless');
    assert.deepEqual(quantities(api), {});
    api.setSwitchState(2, 'prod');
    assert.deepEqual(quantities(api), { 3: 900 });
});

test('a Buy final product encapsulates all of its inputs', () => {
    const { api } = createHarness(basicPayload());
    api.setSwitchState(1, 'buy');
    assert.deepEqual(quantities(api), { 1: 1 });
    assert.deepEqual(cycles(api), {});
});

test('Confessor 34318 recipe branch requires 198 engines / 792 raw engines', () => {
    const payload = basicPayload({ quantity: 200, bonus: 1 });
    payload.product_type_id = 34317;
    payload.final_product_qty = 100;
    payload.runs = 100;
    payload.materials_tree = [material(29992, 2, 100, [material(30018, 4, 200)])];
    payload.structure_planner.items[0].type_id = 34317;
    const { api } = createHarness(payload);
    assert.equal(cycles(api)[29992], 198);
    assert.deepEqual(quantities(api), { 30018: 792 });
});

test('all production levels recompute integer jobs', () => {
    const payload = basicPayload();
    payload.materials_tree[0].sub_materials = [material(3, 3, 100, [material(4, 7, 150)], 2)];
    const { api } = createHarness(payload);
    assert.equal(cycles(api)[2], 90);
    assert.equal(cycles(api)[3], 135);
    assert.deepEqual(quantities(api), { 4: 945 });
});

test('shared intermediate requirements combine before batch rounding', () => {
    const shared = () => material(4, 1, 1, [material(5, 10, 1)], 10);
    const payload = { materials_tree: [
        { type_id: 2, type_name: 'Left', quantity: 1, produced_per_cycle: 1, sub_materials: [shared()] },
        { type_id: 3, type_name: 'Right', quantity: 1, produced_per_cycle: 1, sub_materials: [shared()] },
    ] };
    const { api } = createHarness(payload);
    assert.equal(cycles(api)[4], 1);
    assert.deepEqual(quantities(api), { 5: 10 });
    const tree = api.getCalculatedTree();
    assert.equal(tree.reduce((sum, node) => sum + node.sub_materials[0].sub_materials[0].quantity, 0), 10);
    api.setSwitchState(2, 'buy');
    assert.deepEqual(quantities(api), { 2: 1, 5: 10 });
    api.setSwitchState(3, 'buy');
    assert.deepEqual(quantities(api), { 2: 1, 3: 1 });
});

test('shared stock is consumed exactly once across paths', () => {
    const shared = () => material(4, 1, 1, [material(5, 10, 1)]);
    const payload = { materials_tree: [
        { type_id: 2, quantity: 1, sub_materials: [shared()] },
        { type_id: 3, quantity: 1, sub_materials: [shared()] },
    ] };
    const { api } = createHarness(payload, { 4: 1 });
    assert.equal(cycles(api)[4], 1);
    assert.deepEqual(quantities(api), { 5: 10 });
});

test('root nodes, final_outputs and saved decisions hydrate consistently', () => {
    const payload = { final_outputs: [{ type_id: 2, quantity: 100 }], materials_tree: [
        { type_id: 2, quantity: 20, produced_per_cycle: 1, project_inclusion_mode: 'buy', sub_materials: [material(3, 10, 20)] },
    ] };
    const { api } = createHarness(payload);
    assert.deepEqual(quantities(api), { 2: 100 });
    api.setSwitchState(2, 'prod');
    assert.deepEqual(quantities(api), { 3: 1000 });
});

test('legacy recipe rows reconstruct using saved runs and batch sizes', () => {
    const payload = basicPayload({ output: 7, stock: { 2: 50 } });
    const child = payload.materials_tree[0].sub_materials[0];
    delete child.base_quantity_per_run;
    assert.deepEqual(quantities(createHarness(payload).api), { 3: 60 });
});

test('fuel blocks preserve blueprint ME, rig ME and per-run minimum', () => {
    for (const [me, bonus, expected] of [[0, 0, 4500], [10, 0, 4050], [0, 5.9896, 4231], [10, 5.9896, 3808]]) {
        const payload = {
            product_type_id: 4312, product_output_per_cycle: 40, final_product_qty: 400, runs: 10,
            materials_tree: [material(17889, 450, 10, [], 1, me), material(9848, 1, 10, [], 1, me)],
            structure_planner: { items: [{ type_id: 4312, selected_structure_id: 10, options: [{ structure_id: 10, material_bonus_percent: bonus }] }] },
        };
        const { api } = createHarness(payload);
        assert.deepEqual(quantities(api), { 17889: expected, 9848: 10 });
    }
});

test('EFT synthetic fit wrappers never become hull recipes or take rig bonuses', () => {
    const payload = { final_outputs: [{ type_id: 2, quantity: 2 }, { type_id: 4, quantity: 4 }], materials_tree: [{
        type_id: 2, type_name: 'Two fits', quantity: 2, cycles: 2, produced_per_cycle: 1,
        is_synthetic_final_output: true,
        sub_materials: [
            { type_id: 2, quantity: 2, produced_per_cycle: 1, sub_materials: [material(3, 10, 2)] },
            { type_id: 4, quantity: 4, produced_per_cycle: 1, sub_materials: [material(5, 10, 4)] },
        ],
    }], structure_planner: { items: [{ type_id: 2, selected_structure_id: 10, options: [{ structure_id: 10, material_bonus_percent: 10 }] }] } };
    const { api } = createHarness(payload);
    assert.deepEqual(cycles(api), { 2: 2, 4: 4 });
    assert.deepEqual(quantities(api), { 3: 18, 5: 40 });
    const tree = api.getCalculatedTree();
    assert.equal(tree[0].quantity, 2);
    assert.equal(tree[0].sub_materials[0].quantity, 2);
    assert.equal(tree[0].sub_materials[0].sub_materials[0].quantity, 18);
});

test('shared descendants at different depths gather demand before expansion', () => {
    const shared = (runs) => material(4, 1, runs, [material(5, 10, runs)]);
    const payload = { materials_tree: [{ type_id: 2, quantity: 2, produced_per_cycle: 1, sub_materials: [
        shared(2), material(3, 1, 2, [shared(2)]),
    ] }] };
    const { api } = createHarness(payload, { 4: 1 });
    assert.equal(cycles(api)[4], 3);
    assert.deepEqual(quantities(api), { 5: 30 });
});

test('500 seeded DAGs match an independent integer oracle and their projected leaves', () => {
    let seed = 512;
    const random = (limit) => {
        seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
        return seed % limit;
    };
    for (let sample = 0; sample < 500; sample++) {
        const recipes = new Map();
        const decisions = new Map();
        const stock = {};
        for (let typeId = 1; typeId <= 8; typeId++) {
            const children = [];
            for (let childId = typeId + 1; childId <= 8; childId++) {
                if (random(3) === 0) children.push({ typeId: childId, base: 1 + random(9) });
            }
            recipes.set(typeId, { output: 1 + random(6), children });
            decisions.set(typeId, ['prod', 'prod', 'buy', 'useless'][random(4)]);
            stock[typeId] = random(6);
        }
        const roots = [{ typeId: 1, quantity: 1 + random(13) }, { typeId: 2, quantity: 1 + random(13) }];
        const buildNode = (typeId, quantity) => {
            const recipe = recipes.get(typeId);
            const runs = Math.ceil(quantity / recipe.output);
            return {
                type_id: typeId, quantity, cycles: runs, produced_per_cycle: recipe.output,
                sub_materials: recipe.children.map((child) => ({
                    ...buildNode(child.typeId, child.base * runs),
                    base_quantity_per_run: child.base, job_runs: runs,
                    material_bonus_applicable: true, blueprint_material_efficiency: 0,
                })),
            };
        };
        const { api } = createHarness({ materials_tree: roots.map((root) => buildNode(root.typeId, root.quantity)) }, stock);
        decisions.forEach((mode, typeId) => api.setSwitchState(typeId, mode));

        // This graph's edges always go to higher IDs, so a simple numeric pass
        // supplies an oracle without using the implementation's graph traversal.
        const requirements = new Map(roots.map((root) => [root.typeId, root.quantity]));
        const expectedPurchases = new Map();
        const expectedRuns = new Map();
        for (let typeId = 1; typeId <= 8; typeId++) {
            const quantity = requirements.get(typeId) || 0;
            const recipe = recipes.get(typeId);
            const mode = decisions.get(typeId);
            if (quantity <= 0 || mode === 'useless') continue;
            if (recipe.children.length === 0 || mode === 'buy') {
                expectedPurchases.set(typeId, quantity);
                continue;
            }
            const net = Math.max(0, quantity - stock[typeId]);
            if (!net) continue;
            const runs = Math.ceil(net / recipe.output);
            expectedRuns.set(typeId, runs);
            recipe.children.forEach((child) => {
                requirements.set(child.typeId, (requirements.get(child.typeId) || 0) + runs * child.base);
            });
        }
        assert.deepEqual(new Map(api.getFinancialItems().map((item) => [item.typeId, item.quantity])), expectedPurchases, `purchases sample ${sample}`);
        assert.deepEqual(new Map(api.getProductionCycles().map((item) => [item.typeId, item.cycles])), expectedRuns, `cycles sample ${sample}`);
        const projectedPurchases = new Map();
        const collect = (node) => {
            if (decisions.get(node.type_id) === 'useless') return;
            if (node.sub_materials.length === 0 || decisions.get(node.type_id) === 'buy') {
                if (node.quantity > 0) projectedPurchases.set(node.type_id, (projectedPurchases.get(node.type_id) || 0) + node.quantity);
                return;
            }
            node.sub_materials.forEach(collect);
        };
        api.getCalculatedTree().forEach(collect);
        assert.deepEqual(projectedPurchases, expectedPurchases, `tree sample ${sample}`);
    }
});

test('payload reload preserves choices and recomputes from the replacement recipe', () => {
    const payload = basicPayload();
    const { api } = createHarness(payload);
    api.setSwitchState(2, 'buy');
    api.setStructureAssignment(1, 11);
    api.replacePayload(JSON.parse(JSON.stringify(payload)), { preserveSwitches: true });
    assert.deepEqual(quantities(api), { 2: 100 });
    api.setSwitchState(2, 'prod');
    assert.deepEqual(quantities(api), { 3: 1000 });
    api.setStructureAssignment(1, 10);
    assert.deepEqual(quantities(api), { 3: 900 });
});

test('synthetic fit without hull still hydrates child decisions and names', () => {
    const { api } = createHarness({ materials_tree: [{ type_id: null, is_synthetic_final_output: true, quantity: 1, sub_materials: [
        { type_id: 2, type_name: 'Module', quantity: 3, project_inclusion_mode: 'buy', sub_materials: [material(3, 10, 3)] },
    ] }] });
    assert.equal(api.getSwitchState(2), 'buy');
    assert.equal(api.getFinancialItems()[0].typeName, 'Module');
    assert.deepEqual(quantities(api), { 2: 3 });
});

test('saved Useless decisions apply to uncraftable final products', () => {
    const { api } = createHarness({ materials_tree: [{ type_id: 2, quantity: 5, project_inclusion_mode: 'useless', sub_materials: [] }] });
    assert.deepEqual(quantities(api), {});
});

test('Build vs Buy uses the same integer recipe, ME and stock calculation', async () => {
    const harness = createHarness(basicPayload({ stock: { 2: 50 } }));
    harness.api.setPrice(2, 'real', 100);
    harness.api.setPrice(3, 'real', 1);
    const analysis = await decisionAnalysis(harness);
    const row = analysis.rows.find((entry) => entry.typeId === 2);
    assert.equal(row.needed, 40);
    assert.equal(row.cycles, 40);
    assert.equal(row.prodTotal, 400);
});

test('Build vs Buy discovers profitable descendants below currently bought parents', async () => {
    const payload = { materials_tree: [{ type_id: 2, type_name: 'Root', quantity: 10, produced_per_cycle: 1, sub_materials: [
        material(3, 1, 10, [material(4, 1, 10)]),
    ] }] };
    const harness = createHarness(payload);
    harness.api.setSwitchState(2, 'buy');
    harness.api.setSwitchState(3, 'buy');
    harness.api.setPrice(2, 'real', 50);
    harness.api.setPrice(3, 'real', 100);
    harness.api.setPrice(4, 'real', 1);
    const analysis = await decisionAnalysis(harness);
    const root = analysis.rows.find(row => row.typeId === 2);
    assert.equal(root.prodTotal, 10);
    assert.equal(root.recommendedMode, 'prod');
});

test('Build vs Buy never replaces a cheaper shared batch with individually cheaper purchases', async () => {
    const shared = () => material(4, 1, 1, [material(5, 100, 1)], 10);
    const payload = { materials_tree: [
        { type_id: 2, type_name: 'Left', quantity: 1, produced_per_cycle: 1, sub_materials: [shared()] },
        { type_id: 3, type_name: 'Right', quantity: 1, produced_per_cycle: 1, sub_materials: [shared()] },
    ] };
    const harness = createHarness(payload);
    for (const [typeId, price] of [[2, 60], [3, 60], [4, 1000], [5, 1]]) harness.api.setPrice(typeId, 'real', price);
    const analysis = await decisionAnalysis(harness);
    const cost = (decisions) => {
        const demand = harness.api.computeDemand({ decisions });
        return [...demand.leafNeeds, ...demand.buyCraftables].reduce(
            (total, [typeId, quantity]) => total + quantity * harness.api.getPrice(typeId, 'buy').value, 0);
    };
    assert.equal(cost(analysis.currentDecisions), 100);
    assert.equal(cost(new Map([[2, 'buy'], [3, 'buy']])), 120);
    assert.equal(cost(analysis.recommendedDecisions), 100);
    assert.equal(analysis.recommendedDecisions.get(2), 'prod');
    assert.equal(analysis.recommendedDecisions.get(3), 'prod');
});

test('Build vs Buy compares complete shared plans after stock and integer job rounding', async () => {
    const shared = () => material(4, 6, 1, [material(5, 100, 1)], 10);
    const payload = { materials_tree: [
        { type_id: 2, type_name: 'Left', quantity: 1, produced_per_cycle: 1, sub_materials: [shared()] },
        { type_id: 3, type_name: 'Right', quantity: 1, produced_per_cycle: 1, sub_materials: [shared()] },
    ] };
    const harness = createHarness(payload, { 4: 2 });
    for (const [typeId, price] of [[2, 60], [3, 60], [4, 1000], [5, 1]]) harness.api.setPrice(typeId, 'real', price);
    const analysis = await decisionAnalysis(harness);
    const recommendedDemand = harness.api.computeDemand({ decisions: analysis.recommendedDecisions });
    assert.equal(recommendedDemand.productionRuns.get(4), 1);
    assert.deepEqual(Object.fromEntries(recommendedDemand.leafNeeds), { 5: 100 });
    assert.equal(recommendedDemand.buyCraftables.size, 0);
});

test('invalid selected structures retain the choice without stale or replacement bonuses', () => {
    const harness = createHarness(basicPayload());
    assert.deepEqual(quantities(harness.api), { 3: 900 });
    harness.api.replaceStructurePlanner({
        items: [{ type_id: 1, selected_structure_id: 10, options: [{ structure_id: 11, material_bonus_percent: 5 }] }],
        invalid_structures: [{ structure_id: 10, name: 'Unsupported rig', error: 'Unknown source', selected_type_ids: [1] }],
    });
    assert.equal(harness.api.getStructureAssignment(1), 10);
    assert.equal(harness.api.getStructureOption(1), null);
    assert.deepEqual(quantities(harness.api), { 3: 1000 });
    assert.equal(harness.api.setStructureAssignment(1, 11), true);
    assert.deepEqual(quantities(harness.api), { 3: 950 });
});

test('invalid-only planner keeps saved assignment IDs for later correction', () => {
    const payload = basicPayload();
    payload.structure_planner = { items: [], invalid_structures: [{ structure_id: 10, name: 'Unsupported', error: 'Unknown source', selected_type_ids: [1] }] };
    const { api } = createHarness(payload);
    assert.equal(api.getStructureAssignment(1), 10);
    assert.equal(api.getStructureOption(1), null);
    assert.deepEqual(quantities(api), { 3: 1000 });
});

test('invalid-only planner replacement retains an already selected unsupported facility', () => {
    const { api } = createHarness(basicPayload());
    api.replaceStructurePlanner({
        items: [],
        invalid_structures: [{ structure_id: 10, name: 'Unsupported', error: 'Unknown source', selected_type_ids: [1] }],
    });
    assert.equal(api.getStructureAssignment(1), 10);
    assert.equal(api.getStructureOption(1), null);
    assert.deepEqual(quantities(api), { 3: 1000 });
    api.replacePayload({ ...basicPayload(), structure_planner: api.getStructurePlanner() });
    assert.equal(api.getStructureAssignment(1), 10);
});

test('old invalid selection metadata does not override a newer supported assignment', () => {
    const { api } = createHarness(basicPayload());
    api.setStructureAssignment(1, 11);
    api.replaceStructurePlanner({
        items: [{ type_id: 1, options: [{ structure_id: 11, material_bonus_percent: 0 }] }],
        invalid_structures: [{ structure_id: 10, name: 'Unsupported', error: 'Unknown source', selected_type_ids: [1] }],
    });
    assert.equal(api.getStructureAssignment(1), 11);
});

test('an ineligible service invalidates only its selected product and preserves a newer valid choice', () => {
    const payload = basicPayload();
    payload.structure_planner = {
        items: [
            { type_id: 1, selected_structure_id: 10, options: [
                { structure_id: 10, material_bonus_percent: 10 },
                { structure_id: 11, material_bonus_percent: 0 },
            ] },
            { type_id: 2, selected_structure_id: 10, options: [{ structure_id: 10, material_bonus_percent: 8 }] },
        ],
        invalid_structures: [{ structure_id: 10, name: 'Reaction facility', error: 'No manufacturing service', selected_type_ids: [1], selection_only: true }],
    };
    const harness = createHarness(payload);
    const { api } = harness;
    assert.equal(api.getStructureAssignment(1), 10);
    assert.equal(api.getStructureOption(1), null);
    assert.equal(api.getStructureAssignment(2), 10);
    assert.equal(api.getStructureOption(2).material_bonus_percent, 8);
    assert.deepEqual(quantities(api), { 3: 920 });

    let notice = null;
    const anchor = { parentNode: { insertBefore(node) { notice = node; } } };
    harness.context.document.getElementById = (id) => id === 'craftMainTabs' ? anchor : id === 'craftSelectedStructureCalculationWarning' ? notice : null;
    harness.context.document.createElement = () => ({ remove() { notice = null; } });
    harness.context.mapLikeToMap = (value) => value;
    harness.context.escapeHtml = String;
    const start = coreSource.indexOf('function buildStructureCalculationWarning(');
    const end = coreSource.indexOf('\nfunction renderStructurePlanner(', start);
    vm.runInContext(coreSource.slice(start, end), harness.context);
    harness.context.updateSelectedStructureCalculationWarning();
    assert(notice.innerHTML.includes('No manufacturing service'));

    // A lazy response still describes the former type-1 selection. Type 2 may
    // keep using structure 10, without retaining type 1's obsolete warning.
    api.setStructureAssignment(1, 11);
    api.replaceStructurePlanner(payload.structure_planner);
    assert.equal(api.getStructureAssignment(1), 11);
    assert.equal(api.getStructureOption(1).material_bonus_percent, 0);
    assert.equal(api.getStructureOption(2).material_bonus_percent, 8);
    harness.context.updateSelectedStructureCalculationWarning();
    assert.equal(notice, null);
});

test('structure warnings escape names and diagnostic messages', () => {
    const harness = createHarness(basicPayload());
    harness.context.escapeHtml = (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
    const start = coreSource.indexOf('function buildStructureCalculationWarning(');
    const end = coreSource.indexOf('\nfunction renderStructurePlanner(', start);
    vm.runInContext(coreSource.slice(start, end), harness.context);
    const warning = harness.context.buildStructureCalculationWarning({ invalid_structures: [{ name: '<img src=x>', error: '<script>oops</script>' }] });
    assert(warning.includes('&lt;img src=x&gt;'));
    assert(warning.includes('&lt;script&gt;oops&lt;/script&gt;'));
    assert(!warning.includes('<script>'));
    assert.equal(harness.context.buildStructureCalculationWarning({}), '');
});

test('selected invalid structures warn globally until the user chooses a supported facility', () => {
    const harness = createHarness(basicPayload());
    harness.api.replaceStructurePlanner({
        items: [{ type_id: 1, selected_structure_id: 10, options: [{ structure_id: 11, material_bonus_percent: 0 }] }],
        invalid_structures: [{ structure_id: 10, name: 'Unsupported', error: 'Unknown source', selected_type_ids: [1] }],
    });
    let notice = null;
    const anchor = { parentNode: { insertBefore(node) { notice = node; } } };
    harness.context.document.getElementById = (id) => id === 'craftMainTabs' ? anchor : id === 'craftSelectedStructureCalculationWarning' ? notice : null;
    harness.context.document.createElement = () => ({ remove() { notice = null; } });
    harness.context.mapLikeToMap = (value) => value;
    harness.context.escapeHtml = String;
    const start = coreSource.indexOf('function buildStructureCalculationWarning(');
    const end = coreSource.indexOf('\nfunction renderStructurePlanner(', start);
    vm.runInContext(coreSource.slice(start, end), harness.context);
    harness.context.updateSelectedStructureCalculationWarning();
    assert(notice.innerHTML.includes('Unsupported'));
    harness.api.setStructureAssignment(1, 11);
    harness.context.updateSelectedStructureCalculationWarning();
    assert.equal(notice, null);
});

test('an explicitly invalid structure never exposes a stale bonus even if its option remains in JSON', () => {
    const payload = basicPayload();
    payload.structure_planner.invalid_structures = [{ structure_id: 10, name: 'Unsupported', error: 'Unknown source', selected_type_ids: [1] }];
    const { api } = createHarness(payload);
    assert.equal(api.getStructureAssignment(1), 10);
    assert.equal(api.getStructureOption(1), null);
    assert.deepEqual(quantities(api), { 3: 1000 });
});

test('server launch window drives job limits and retains historical payload compatibility', () => {
    const { context, window } = createHarness({ calculation_parameters: { job_launch_window_seconds: 3600 } });
    const pageSource = fs.readFileSync(path.join(jsRoot, 'craft_bp_page.js'), 'utf8');
    const helperStart = pageSource.indexOf('window.getCraftJobLaunchWindowSeconds = function');
    const helperEnd = pageSource.indexOf('\n    };', helperStart) + '\n    };'.length;
    assert(helperStart >= 0 && helperEnd > helperStart);
    vm.runInContext(pageSource.slice(helperStart, helperEnd), context);
    const metricsStart = coreSource.indexOf('function getEveJobLaunchMetrics(');
    const metricsEnd = coreSource.indexOf('\nconst structureMotherSystemState', metricsStart);
    vm.runInContext(coreSource.slice(metricsStart, metricsEnd), context);
    assert.equal(context.getEveJobLaunchMetrics(1800, 5).maxRunsPerJob, 2);
    assert.equal(context.getEveJobLaunchMetrics(1800, 5).jobsRequired, 3);
    window.BLUEPRINT_DATA.calculation_parameters.job_launch_window_seconds = 7200;
    assert.equal(context.getEveJobLaunchMetrics(1800, 5).maxRunsPerJob, 4);
    delete window.BLUEPRINT_DATA.calculation_parameters;
    assert.equal(window.getCraftJobLaunchWindowSeconds(), 2592000);
});

test('unknown reaction diagnostics remain visible without a selected structure and are escaped', () => {
    const harness = createHarness(basicPayload());
    harness.api.replaceStructurePlanner({ items: [], invalid_items: [{ type_id: 42, type_name: '<future reaction>', error: '<unsupported family>' }] });
    let notice = null;
    const anchor = { parentNode: { insertBefore(node) { notice = node; } } };
    harness.context.document.getElementById = (id) => id === 'craftMainTabs' ? anchor : id === 'craftSelectedStructureCalculationWarning' ? notice : null;
    harness.context.document.createElement = () => ({ remove() { notice = null; } });
    harness.context.mapLikeToMap = (value) => value;
    harness.context.escapeHtml = (value) => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;');
    const start = coreSource.indexOf('function buildStructureCalculationWarning(');
    const end = coreSource.indexOf('\nfunction renderStructurePlanner(', start);
    vm.runInContext(coreSource.slice(start, end), harness.context);
    harness.context.updateSelectedStructureCalculationWarning();
    assert(notice.innerHTML.includes('&lt;unsupported family&gt;'));
    assert(!notice.innerHTML.includes('<future reaction>'));
});

async function main() {
    let passed = 0;
    for (const entry of cases) {
        try {
            await entry.run();
            passed++;
            console.log(`PASS ${entry.name}`);
        } catch (error) {
            console.error(`FAIL ${entry.name}`);
            throw error;
        }
    }
    console.log(`Craft demand regressions: ${passed} passed, 0 failed`);
}

module.exports = { basicPayload, createHarness, material, decisionAnalysis };
if (require.main === module) main().catch((error) => { console.error(error); process.exitCode = 1; });
