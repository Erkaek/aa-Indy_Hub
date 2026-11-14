/** Exercise real Django Craft Planner views on serve_craft_industry_fixture.py.
 * Only the disposable fixture manifest is accepted. CDN assets are served from
 * local copies, and every remaining external page request is blocked.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { launchBrowser, verifySandbox } = require('./test_craft_industry_browser.cjs');

async function main() {
    const manifestPath = process.env.INDY_HUB_CRAFT_MANIFEST;
    assert(manifestPath, 'Set INDY_HUB_CRAFT_MANIFEST to the disposable fixture manifest');
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    assert(manifest.database.startsWith('/tmp/indyhub-craft-browser-'), 'Use the disposable fixture database');
    const origin = new URL(manifest.base_url);
    assert.equal(origin.hostname, '127.0.0.1');
    const output = process.env.INDY_HUB_BROWSER_OUTPUT || '/tmp/indyhub-issue182/browser-workflow';
    const assets = process.env.INDY_HUB_BROWSER_ASSETS || '/tmp/indyhub-issue182/browser-assets';
    fs.mkdirSync(output, { recursive: true });
    const browser = await launchBrowser();
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    const errors = [];
    const workspaceSaves = [];
    const failedResponses = [];
    const externalBlocked = [];
    const passed = [];
    const files = new Map([
        ['https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js', process.env.INDY_HUB_BROWSER_JQUERY || manifest.jquery_path || path.join(assets, 'jquery.min.js')],
        ['https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js', path.join(assets, 'bootstrap.bundle.min.js')],
        ['https://cdnjs.cloudflare.com/ajax/libs/bootswatch/5.3.8/flatly/bootstrap.min.css', path.join(assets, 'bootstrap.min.css')],
        ['https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.6.0/css/all.min.css', path.join(assets, 'fontawesome.min.css')],
        ...['fa-solid-900.woff2', 'fa-regular-400.woff2', 'fa-brands-400.woff2'].map(name => [`https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.6.0/webfonts/${name}`, path.join(assets, name)]),
    ]);
    await context.route('**/*', route => {
        const url = route.request().url();
        if (new URL(url).origin === origin.origin) {
            if (new URL(url).pathname.includes('/fuzzwork-price/')) return route.fulfill({ contentType: 'application/json', body: '{}' });
            return route.continue();
        }
        const file = files.get(url);
        if (file) return route.fulfill({ contentType: url.endsWith('.css') ? 'text/css' : url.endsWith('.woff2') ? 'font/woff2' : 'application/javascript', body: fs.readFileSync(file), headers: { 'access-control-allow-origin': '*' } });
        externalBlocked.push(url); return route.abort('blockedbyclient');
    });
    await context.addCookies([manifest.cookie]);
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
        if (request.method() !== 'POST') return;
        try {
            const body = request.postDataJSON();
            if (body && Object.hasOwn(body, 'refresh_sde')) workspaceSaves.push(body);
        } catch (_) { /* Other form POSTs are outside this assertion. */ }
    });
    page.on('response', response => { if (response.status() >= 400) failedResponses.push({ url: response.url(), status: response.status() }); });
    const read = () => page.evaluate(() => ({
        items: Object.fromEntries(SimulationAPI.getFinancialItems().map(item => [item.typeId, item.quantity])),
        cycles: Object.fromEntries(SimulationAPI.getProductionCycles().map(item => [item.typeId, item.cycles])),
        choices: Object.fromEntries(Array.from(SimulationAPI.getState().switches, ([id, data]) => [id, data.state])),
        assignments: Object.fromEntries(SimulationAPI.getState().structureAssignments),
    }));
    const waitReady = async () => {
        await page.waitForFunction(() => window.SimulationAPI?.__crafted && window.BLUEPRINT_DATA?.materials_tree?.length && !window.craftBPFlags?.hasPendingWorkspaceRefresh);
        await page.locator('#bpTabs-loading').waitFor({ state: 'hidden' });
        await page.locator('#craftMainTabs').waitFor({ state: 'visible' });
        await page.waitForFunction(() => {
            const workspace = document.getElementById('craft-bp-workspace');
            const pane = document.querySelector('#craftMainTabsContent > .tab-pane.active.show');
            return workspace && pane && getComputedStyle(workspace).opacity === '1' && getComputedStyle(pane).opacity === '1';
        });
    };
    async function chooseStructure(typeId, structureId) {
        await page.locator('#structure-tab-btn').click();
        const select = page.locator(`.structure-assignment-select[data-type-id="${typeId}"]`);
        await select.waitFor();
        await page.waitForFunction(({ typeId, structureId }) => Array.from(document.querySelector(`.structure-assignment-select[data-type-id="${typeId}"]`)?.options || []).some(option => Number(option.value) === structureId), { typeId, structureId });
        await select.selectOption(String(structureId));
        await page.waitForFunction(({ typeId, structureId }) => SimulationAPI.getStructureAssignment(typeId) === structureId, { typeId, structureId });
    }
    async function save() {
        await page.locator('#saveSimulationBtn').click();
        await page.waitForFunction(() => !document.getElementById('saveSimulationBtn').disabled);
    }
    try {
        await verifySandbox(page, output);
        if (!process.env.INDY_HUB_CRAFT_FUEL_ONLY) {
        const confessor = manifest.projects.find(project => project.blueprint_id === 34318);
        const response = await page.goto(`${manifest.base_url}/indy_hub/craft/project/${confessor.ref}/?refresh_sde=yes`, { waitUntil: 'networkidle' });
        assert.equal(response.status(), 200);
        await waitReady();
        passed.push('authenticated Craft Planner opens real Confessor project after loading overlay closes');
        const firstSaveIndex = workspaceSaves.length;
        await save();
        await page.waitForFunction(() => !new URL(location.href).searchParams.has('refresh_sde'));
        assert.equal(workspaceSaves.slice(firstSaveIndex).filter(body => body.refresh_sde === true).length, 1);
        await page.locator('#stock-tab-btn').click(); await waitReady();
        await page.locator('#stockResetAllocationsBtn').click();
        await page.waitForFunction(() => Object.keys(getCraftStockAllocations()).length === 0);
        await page.locator('#run-optimized-tab-btn').click(); await waitReady();
        await page.locator('#set-tree-prod').click();
        const rootDecision = page.locator('#decisionStrategyRows input.mat-switch[data-type-id="34317"]');
        await rootDecision.setChecked(false);
        await page.waitForFunction(() => SimulationAPI.getSwitchState(34317) === 'buy');
        const ordinarySaveIndex = workspaceSaves.length;
        await save();
        assert(workspaceSaves.length > ordinarySaveIndex);
        assert.equal(workspaceSaves[workspaceSaves.length - 1].refresh_sde, false);
        passed.push('refresh_sde=yes is sent once on save; subsequent decision save uses refresh_sde=false');
        await rootDecision.setChecked(true);
        await page.locator('#set-tree-prod').click();
        await chooseStructure(34317, 2);
        const rigBonus = await page.evaluate(() => SimulationAPI.getStructureOption(34317));
        assert(Math.abs(rigBonus.material_bonus_percent - 5.9896) < 1e-8);
        assert(Math.abs(rigBonus.time_bonus_percent - 50.7) < 1e-8);
        passed.push('real Dogma/SDE Raitaru + 43854/43856 gives ME5.9896% / TE50.70%');
        await chooseStructure(34317, 1);
        const neutral = manifest.structures.find(structure => /neutral|no bonus|Astrahus/i.test(structure.name));
        assert(neutral, 'Fixture needs a manufacturing facility without material bonuses for the exact 198/792 scenario');
        await chooseStructure(29992, neutral.id);
        const engineBonus = await page.evaluate(() => SimulationAPI.getStructureOption(29992).material_bonus_percent);
        await page.locator('#run-optimized-tab-btn').click();
        await waitReady();
        await page.waitForFunction(() => document.querySelectorAll('#decisionStrategyRows input.mat-switch').length > 0);
        const topChildren = await page.evaluate(() => {
            const tree = BLUEPRINT_DATA.materials_tree;
            const root = tree.find(node => node.type_id === 34317);
            return (root?.sub_materials || tree).map(node => node.type_id).filter(id => id !== 29992);
        });
        for (const typeId of topChildren) {
            const input = page.locator(`#decisionStrategyRows input.mat-switch[data-type-id="${typeId}"]`);
            if (await input.count()) {
                await input.setChecked(false);
                await page.waitForFunction(id => SimulationAPI.getSwitchState(id) === 'buy', typeId);
            }
        }
        assert.equal(engineBonus, 0);
        const expected = 792;
        let result = await read();
        assert.equal(result.cycles[29992], 198);
        assert.equal(result.items[30018], expected);
        passed.push(`Buy/Produce decisions: 198 Optimized Nano-Engines / ${expected} Fused Nanomechanical Engines (own structure ME ${engineBonus}%)`);
        await page.locator('#plan-tab-btn').click();
        await waitReady();
        const treeRaw = page.locator('#tab-tree summary[data-type-id="30018"]');
        assert.equal(Number(await treeRaw.first().getAttribute('data-qty')), expected);
        passed.push('rendered production tree matches financial purchases and cycles');
        await page.locator('#expand-tree').click();
        await page.locator('#tab-tree').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, 'confessor-198-792.png'), fullPage: true });
        await page.locator('#stock-tab-btn').click();
        await waitReady();
        const engineStock = page.locator('.craft-stock-allocation-input[data-type-id="29992"]');
        await engineStock.waitFor();
        await engineStock.fill('50'); await engineStock.press('Enter');
        await page.waitForFunction(() => SimulationAPI.getProductionCycles().find(item => item.typeId === 29992)?.cycles === 148);
        result = await read();
        assert.equal(result.items[30018], Math.ceil(148 * 4 * (1 - engineBonus / 100)));
        passed.push('real cached stock allocation50 recomputes intermediate148runs and its recipe');
        await save();
        await page.reload({ waitUntil: 'networkidle' });
        await waitReady();
        result = await read();
        assert.equal(result.choices[29992], 'prod');
        topChildren.forEach(id => { if (result.choices[id]) assert.equal(result.choices[id], 'buy'); });
        assert.equal(result.assignments[34317], 1);
        assert.equal(result.cycles[29992], 148);
        passed.push('server save + reload preserves decisions, structure assignments and stock');
        await page.locator('#stock-tab-btn').click();
        const finalStock = page.locator('.craft-stock-allocation-input[data-type-id="34317"]');
        await finalStock.waitFor(); await finalStock.fill('100'); await finalStock.press('Enter');
        await page.waitForFunction(() => SimulationAPI.getProductionCycles().length === 0);
        result = await read(); assert.deepEqual(result.items, {}); assert.deepEqual(result.cycles, {});
        passed.push('full final-product stock produces zero cycles and zero purchases on real payload');
        await save();
        await page.reload({ waitUntil: 'networkidle' }); await waitReady();
        result = await read(); assert.deepEqual(result.items, {}); assert.deepEqual(result.cycles, {});
        passed.push('saved zero-demand workspace reloads without phantom direct-material purchases');
        await page.locator('#buy-tab-btn').click(); await waitReady();
        await page.locator('#financialItemsBody').waitFor({ state: 'visible' });
        await page.locator('#financialItemsBody').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, 'zero-demand.png'), fullPage: true });
        }
        const fuel = manifest.projects.find(project => project.blueprint_id === 4316);
        await page.goto(`${manifest.base_url}/indy_hub/craft/project/${fuel.ref}/`, { waitUntil: 'networkidle' }); await waitReady();
        const fuelType = await page.evaluate(() => BLUEPRINT_DATA.product_type_id);
        await page.locator('#configure-tab-btn').click();
        const fuelME = page.locator('input[name="me_4316"]');
        await fuelME.waitFor();
        if (await fuelME.inputValue() !== '0') {
            await fuelME.fill('0'); await fuelME.dispatchEvent('change');
            await page.locator('#plan-tab-btn').click(); await waitReady();
        }
        await chooseStructure(fuelType, 3);
        let fuelResult = await read(); assert.equal(fuelResult.items[17889], 4231); assert.equal(fuelResult.items[9848], 10);
        passed.push('real Hydrogen Fuel Block recipe: ME0 + applicable rig =4231isotopes /10Robotics');
        await page.locator('#bpTabs-loading').waitFor({ state: 'hidden' });
        await page.locator('#structurePlannerRows').scrollIntoViewIfNeeded();
        await page.waitForFunction(() => getComputedStyle(document.getElementById('structure-pane')).opacity === '1');
        await page.screenshot({ path: path.join(output, 'fuel-structure.png'), fullPage: true });
        const neutralFuel = manifest.structures.find(structure => /neutral|no bonus|Astrahus/i.test(structure.name));
        await chooseStructure(fuelType, neutralFuel.id);
        fuelResult = await read(); assert.equal(fuelResult.items[17889], 4500);
        passed.push('real Hydrogen Fuel Block ME0 without structure bonus =4500isotopes');
        await page.locator('#configure-tab-btn').click();
        const meInput = page.locator('input[name="me_4316"]');
        await meInput.waitFor(); await meInput.fill('10'); await meInput.dispatchEvent('change');
        await page.locator('#plan-tab-btn').click(); await waitReady();
        await page.waitForFunction(() => SimulationAPI.getFinancialItems().find(item => item.typeId === 17889)?.quantity === 4050);
        passed.push('real blueprint ME10 recalculation without structure bonus =4050isotopes');
        await chooseStructure(fuelType, 3);
        fuelResult = await read(); assert.equal(fuelResult.items[17889], 3808); assert.equal(fuelResult.items[9848], 10);
        passed.push('real blueprint ME10 plus Raitaru applicable rig =3808isotopes /10Robotics');
        await page.evaluate(() => ensureFullStructurePlannerLoaded());
        await page.evaluate((typeId) => {
            const original = SimulationAPI.getStructurePlanner();
            window.fixtureSupportedPlanner = original;
            const unsupported = JSON.parse(JSON.stringify(original));
            unsupported.summary = { ...unsupported.summary, has_full_options: true, hasFullOptions: true };
            unsupported.invalid_structures = [{ structure_id: 3, name: 'Fixture unsupported rig', error: 'Fixture unknown industrial effect', selected_type_ids: [typeId] }];
            unsupported.items.forEach(item => { item.options = item.options.filter(option => Number(option.structure_id || option.structureId) !== 3); });
            SimulationAPI.replaceStructurePlanner(unsupported);
            renderStructurePlanner({ force: true });
        }, fuelType);
        await page.locator('#craftSelectedStructureCalculationWarning').waitFor({ state: 'visible' });
        assert.equal(await page.evaluate(id => SimulationAPI.getStructureAssignment(id), fuelType), 3);
        assert.equal(await page.evaluate(id => SimulationAPI.getStructureOption(id), fuelType), null);
        await page.evaluate(() => {
            const invalidOnly = { ...SimulationAPI.getStructurePlanner(), items: [] };
            SimulationAPI.replaceStructurePlanner(invalidOnly);
            renderStructurePlanner({ force: true });
        });
        await page.locator('#craftSelectedStructureCalculationWarning').waitFor({ state: 'visible' });
        assert.equal(await page.evaluate(id => SimulationAPI.getStructureAssignment(id), fuelType), 3);
        await page.locator('#craftSelectedStructureCalculationWarning').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, 'selected-unsupported-structure.png'), fullPage: true });
        await page.evaluate(() => { SimulationAPI.replaceStructurePlanner(window.fixtureSupportedPlanner); renderStructurePlanner({ force: true }); });
        await page.locator('#craftSelectedStructureCalculationWarning').waitFor({ state: 'detached' });
        passed.push('synthetic unsupported-effect metadata, including an invalid-only replacement, displays a global warning and preserves the selected ID without stale bonus');
        const parameters = await page.evaluate(() => ({
            launchWindow: window.getCraftJobLaunchWindowSeconds(),
            sccPercent: BLUEPRINT_DATA.calculation_parameters.scc_surcharge_percent,
            defaultSalesTax: BLUEPRINT_DATA.market_fee_profiles.default_sales_tax_percent,
        }));
        assert.deepEqual(parameters, { launchWindow: 2592000, sccPercent: 4, defaultSalesTax: 7.5 });
        passed.push('server calculation parameters reach the authenticated browser without extra requests');
        await page.evaluate(() => {
            SimulationAPI.replaceStructurePlanner({
                items: [],
                invalid_items: [{ type_id: 42, type_name: '<Future reaction>', error: 'Unsupported reaction family <99999>' }],
            });
            renderStructurePlanner({ force: true });
        });
        const unknownFamilyNotice = page.locator('#craftSelectedStructureCalculationWarning');
        await unknownFamilyNotice.waitFor({ state: 'visible' });
        assert((await unknownFamilyNotice.innerText()).includes('Unsupported reaction family <99999>'));
        assert.equal(await unknownFamilyNotice.locator('script,img').count(), 0);
        await page.evaluate(() => { SimulationAPI.replaceStructurePlanner(window.fixtureSupportedPlanner); renderStructurePlanner({ force: true }); });
        await unknownFamilyNotice.waitFor({ state: 'detached' });
        passed.push('synthetic unknown-reaction diagnostics stay visible with no facility choice and are safely rendered');

        const future = manifest.future_reaction;
        assert(future, 'The disposable database needs the synthetic future reaction case');
        const futureProject = manifest.projects.find(project => project.blueprint_id === future.blueprint_type_id);
        const futureResponse = await page.goto(`${manifest.base_url}/indy_hub/craft/project/${futureProject.ref}/`, { waitUntil: 'networkidle' });
        assert.equal(futureResponse.status(), 200);
        await waitReady();
        const futurePlanner = await page.evaluate(() => BLUEPRINT_DATA.structure_planner);
        const futureDiagnostic = futurePlanner.invalid_items.find(item => item.type_id === future.product_type_id);
        assert(futureDiagnostic, 'The real planner must diagnose the unknown ORM reaction family');
        assert.equal(futureDiagnostic.group_id, future.group_id);
        assert.equal(futureDiagnostic.activity_id, 9);
        assert.match(futureDiagnostic.error, /reaction family/i);
        const futureItem = futurePlanner.items.find(item => item.type_id === future.product_type_id);
        assert(futureItem, 'The unknown product remains represented with its diagnostic');
        assert.deepEqual(futureItem.options, []);
        assert.equal(futureItem.service_category, null);
        assert.equal(futureItem.recommended_structure_id, null);
        assert(futurePlanner.structures.some(structure => /polyvalent refinery/i.test(structure.name)));
        assert(futurePlanner.structures.some(structure => /biochemical refinery/i.test(structure.name)));
        const actualFamilyNotice = page.locator('#craftSelectedStructureCalculationWarning');
        await actualFamilyNotice.waitFor({ state: 'visible' });
        assert((await actualFamilyNotice.innerText()).includes(futureDiagnostic.error));
        assert((await actualFamilyNotice.innerText()).includes('Synthetic future reaction <fixture>'));
        assert.equal(await actualFamilyNotice.locator('fixture,script,img').count(), 0);
        assert.equal(await page.evaluate(id => SimulationAPI.getStructureOption(id), future.product_type_id), null);
        passed.push('real synthetic ORM reaction -> backend invalid_items -> rendered payload -> escaped Chrome warning, with no invented service, facility or tax');
        await actualFamilyNotice.scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, 'future-reaction-orm-warning.png'), fullPage: true });

        await page.locator('#stock-tab-btn').click(); await waitReady();
        const futureStock = page.locator(`.craft-stock-allocation-input[data-type-id="${future.product_type_id}"]`);
        await futureStock.waitFor(); await futureStock.fill('3'); await futureStock.press('Enter');
        await page.waitForFunction(id => Number(getCraftStockAllocations()[id]) === 3, future.product_type_id);
        await page.locator('#run-optimized-tab-btn').click(); await waitReady();
        const futureDecision = page.locator(`#decisionStrategyRows input.mat-switch[data-type-id="${future.product_type_id}"]`);
        await futureDecision.waitFor(); await futureDecision.setChecked(false);
        await page.waitForFunction(id => SimulationAPI.getSwitchState(id) === 'buy', future.product_type_id);
        await save();
        await page.reload({ waitUntil: 'networkidle' }); await waitReady();
        const futureReloaded = await page.evaluate(id => ({
            choice: SimulationAPI.getSwitchState(id),
            stock: Number(getCraftStockAllocations()[id]),
            option: SimulationAPI.getStructureOption(id),
            diagnostics: BLUEPRINT_DATA.structure_planner.invalid_items,
            requirements: SimulationAPI.getFinancialItems().map(item => ({ typeId: item.typeId, quantity: item.quantity })),
            allocation: getCraftStockAllocationSummary(id, 10),
        }), future.product_type_id);
        assert.equal(futureReloaded.choice, 'buy');
        assert.equal(futureReloaded.stock, 3);
        assert.equal(futureReloaded.option, null);
        assert(futureReloaded.diagnostics.some(item => item.type_id === future.product_type_id));
        assert.deepEqual(futureReloaded.requirements, [{ typeId: future.product_type_id, quantity: 10 }]);
        assert.equal(futureReloaded.allocation.allocatedQty, 3);
        assert.equal(futureReloaded.allocation.remainingQty, 7);
        await page.locator('#buy-tab-btn').click(); await waitReady();
        const futureFinancialRow = page.locator(`#financialItemsBody tr[data-type-id="${future.product_type_id}"][data-row-kind="buy"]`);
        await futureFinancialRow.waitFor({ state: 'visible' });
        assert.equal(await futureFinancialRow.getAttribute('data-buy-remaining-qty'), '7');
        await page.locator('#craftSelectedStructureCalculationWarning').waitFor({ state: 'visible' });
        await page.screenshot({ path: path.join(output, 'future-reaction-stock-buy.png'), fullPage: true });
        passed.push('unknown reaction does not block independent stock allocation and Buy decision save/reload; seven remaining products are purchased without a facility fallback');
        assert.deepEqual(errors, []);
        assert.deepEqual(failedResponses, []);
        const ruleVersions = await page.evaluate(() => ({ rules: window.BLUEPRINT_DATA.industry_rules_version, calculation: window.BLUEPRINT_DATA.industry_calculation_version }));
        const expectedRules = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'indy_hub', 'data', 'industry', 'ccp_rules.json'), 'utf8')).rules_version;
        assert.equal(ruleVersions.rules, expectedRules, 'The fixture must run the current packaged rules');
        const report = { outcome: 'PASS', checkedAt: new Date().toISOString(), browserVersion: browser.version(), sandboxEnabled: true, manifest: manifestPath, ruleVersions, passed, count: passed.length, pageErrors: errors, failedResponses, workspaceSaveRefreshFlags: workspaceSaves.map(body => body.refresh_sde), externalBlocked: Array.from(new Set(externalBlocked)), limitations: ['Synthetic authenticated user, disposable SQLite/Redis, pinned offline SDE records; future reaction IDs are synthetic and are not CCP references.', 'Fuzzwork responses mocked to empty; no live prices, ESI, OAuth or worker execution.', 'Bootstrap/Bootswatch/FontAwesome served locally with original SRI; remote EVE images and Google font blocked.'] };
        fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n');
        console.log(JSON.stringify({ outcome: 'PASS', scenarios: passed.length, report: path.join(output, 'report.json') }));
    } catch (error) {
        await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
        fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({ error: error.stack, passed, errors, failedResponses, state: await read().catch(() => null) }, null, 2));
        throw error;
    } finally { await context.close(); await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
