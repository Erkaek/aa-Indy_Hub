/** Issue #182 arithmetic through real sandboxed Chromium and application JS.
 * This synthetic local fixture does not qualify the authenticated AA workflow.
 * Dependencies are external to the repo; INDY_HUB_BROWSER_CONFIG uses the same
 * format as check_indyhub_chrome.cjs. No external requests are permitted.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { basicPayload, material } = require('./test_craft_demand.js');
const jsRoot = path.join(__dirname, '..', 'indy_hub', 'static', 'indy_hub', 'js');
const simSource = fs.readFileSync(path.join(jsRoot, 'craft_bp_simulation_api.js'), 'utf8');
const coreSource = fs.readFileSync(path.join(jsRoot, 'craft_bp.js'), 'utf8');
const decisionStart = coreSource.indexOf('async function computeCraftDecisionAnalysis(');
const decisionEnd = coreSource.indexOf('\nfunction renderDecisionStrategyPanel(', decisionStart);
const decisionSource = coreSource.slice(decisionStart, decisionEnd);

function readConfig() {
    if (process.env.INDY_HUB_BROWSER_CONFIG) return JSON.parse(fs.readFileSync(process.env.INDY_HUB_BROWSER_CONFIG, 'utf8'));
    return {
        playwrightModule: process.env.INDY_HUB_PLAYWRIGHT_MODULE || '/tmp/indyhub-issue182/browser-tools/node_modules/playwright-core',
        browserExecutable: process.env.INDY_HUB_CHROME || '/home/aadev/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',
        libraryPath: process.env.INDY_HUB_BROWSER_LIBRARIES || '/tmp/indyhub-issue182/browser-libs/usr/lib/x86_64-linux-gnu',
    };
}
async function launchBrowser(config = readConfig()) {
    const { chromium } = require(config.playwrightModule);
    return chromium.launch({
        executablePath: config.browserExecutable,
        headless: !process.argv.includes('--headed'), chromiumSandbox: true,
        env: { ...process.env, LD_LIBRARY_PATH: config.libraryPath + (process.env.LD_LIBRARY_PATH ? ':' + process.env.LD_LIBRARY_PATH : '') },
    });
}
async function verifySandbox(page, output) {
    await page.goto('chrome://sandbox');
    const evidence = await page.locator('body').innerText();
    assert.match(evidence, /Seccomp-BPF sandbox\s+Yes/i);
    assert.match(evidence, /PID namespaces\s+Yes/i);
    fs.writeFileSync(path.join(output, 'sandbox.txt'), evidence);
}

function fixtureHtml() {
    return `<!doctype html><html><meta charset="utf-8"><title>Issue 182 isolated arithmetic</title>
<body><h1>Craft arithmetic fixture</h1><p>Synthetic context; application SimulationAPI and decision analysis.</p>
<label>Intermediate stock <input id="stock" type="number" value="0"></label>
<label>Final product stock <input id="finalStock" type="number" value="0"></label>
<label>Structure <select id="structure"><option value="10">10% ME</option><option value="11">No structure ME</option></select></label>
<label>Decision <select id="decision"><option value="prod">Produce</option><option value="buy">Buy</option><option value="useless">Useless</option></select></label>
<pre id="results"></pre><script>
window.BLUEPRINT_DATA = ${JSON.stringify(basicPayload())}; window._stock = {};
window.getCraftProdStockAllocationsForCurrentPlan = () => window._stock;
window.getIndyHubLocale = () => 'en'; window.__ = text => text;
</script><script src="/simulation.js"></script><script>
function getCurrentDecisionsFromSimulationOrDom() { return new Map(Array.from(SimulationAPI.getState().switches, ([id,record]) => [id,record.state])); }
function readMaterialsTreeChildren(node) { return node.sub_materials || []; }
function getDecisionBuyToleranceValue() { return 0; }
function getCraftDecisionBuyUnitPrice(id) { return SimulationAPI.getPrice(id, 'buy').value; }
function getCraftDecisionBuyUnitPriceOrInf(id) { return getCraftDecisionBuyUnitPrice(id) || Infinity; }
function getCraftDecisionSellUnitPrice(id) { return SimulationAPI.getPrice(id, 'sale').value; }
function render() { const api=SimulationAPI; document.getElementById('results').textContent=JSON.stringify({items:api.getFinancialItems(),cycles:api.getProductionCycles(),tree:api.getCalculatedTree()},null,2); }
window.loadFixture = (payload, stock={}) => { window.BLUEPRINT_DATA=payload; window._stock=stock; SimulationAPI.replacePayload(payload,{preservePrices:false,preserveStructures:false,preserveSwitches:false}); render(); };
document.getElementById('stock').oninput=e=>{window._stock[2]=Number(e.target.value);render();};
document.getElementById('finalStock').oninput=e=>{window._stock[1]=Number(e.target.value);render();};
document.getElementById('structure').onchange=e=>{SimulationAPI.setStructureAssignment(1,Number(e.target.value));render();};
document.getElementById('decision').onchange=e=>{SimulationAPI.setSwitchState(2,e.target.value);render();};
render();
</script><script src="/decision.js"></script></body></html>`;
}

async function runCalculationScenarios(browser, output) {
    const server = http.createServer((request, response) => {
        if (request.url === '/simulation.js' || request.url === '/decision.js') {
            response.writeHead(200, { 'Content-Type': 'application/javascript' });
            response.end(request.url === '/simulation.js' ? simSource : decisionSource);
        } else {
            response.writeHead(200, { 'Content-Type': 'text/html' }); response.end(fixtureHtml());
        }
    });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const origin = `http://127.0.0.1:${server.address().port}`;
    const context = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
    let externalBlocked = 0;
    const errors = [];
    await context.route('**/*', (route) => {
        if (new URL(route.request().url()).origin === origin) return route.continue();
        externalBlocked++; return route.abort('blockedbyclient');
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    const passed = [];
    const read = () => page.evaluate(() => ({
        items: Object.fromEntries(SimulationAPI.getFinancialItems().map(entry => [entry.typeId, entry.quantity])),
        cycles: Object.fromEntries(SimulationAPI.getProductionCycles().map(entry => [entry.typeId, entry.cycles])),
        tree: SimulationAPI.getCalculatedTree(),
    }));
    try {
        await verifySandbox(page, output);
        await page.goto(origin, { waitUntil: 'networkidle' });
        let value = await read();
        assert.equal(value.cycles[2], 90); assert.deepEqual(value.items, { 3: 900 });
        assert.equal(value.tree[0].sub_materials[0].quantity, 900); passed.push('parent ME 90 cycles / 900 inputs');
        await page.locator('#stock').fill('50');
        value = await read(); assert.equal(value.cycles[2], 40); assert.deepEqual(value.items, { 3: 400 }); passed.push('stock 40 cycles / 400 inputs');
        await page.selectOption('#decision', 'buy');
        assert.deepEqual((await read()).items, { 2: 90 }); passed.push('Buy encapsulates descendants');
        await page.selectOption('#decision', 'useless');
        assert.deepEqual((await read()).items, {}); passed.push('Useless removes demand');
        await page.selectOption('#decision', 'prod');
        await page.selectOption('#structure', '11');
        assert.deepEqual((await read()).items, { 3: 500 }); passed.push('structure change recomputes 50 cycles / 500 inputs');
        await page.locator('#finalStock').fill('1');
        value = await read(); assert.deepEqual(value.items, {}); assert.deepEqual(value.cycles, {}); passed.push('valid zero demand never falls back');
        await page.evaluate(() => loadFixture({ direct_materials: [{ type_id: 3, quantity: 1000 }] }));
        assert.deepEqual((await read()).items, { 3: 1000 }); passed.push('legacy fallback without tree');
        const confessor = basicPayload({ quantity: 200, bonus: 1 });
        confessor.product_type_id = 34317; confessor.final_product_qty = 100; confessor.runs = 100;
        confessor.materials_tree = [material(29992, 2, 100, [material(30018, 4, 200)])];
        confessor.structure_planner.items[0].type_id = 34317;
        await page.evaluate(payload => loadFixture(payload), confessor);
        value = await read(); assert.equal(value.cycles[29992], 198); assert.deepEqual(value.items, { 30018: 792 }); passed.push('Confessor branch 198 / 792');
        await page.evaluate(payload => { loadFixture(payload, { 2: 50 }); SimulationAPI.setPrice(2,'real',100); SimulationAPI.setPrice(3,'real',1); }, basicPayload());
        const analysis = await page.evaluate(() => computeCraftDecisionAnalysis({ toleranceISK: 0 }));
        const row = analysis.rows.find(entry => entry.typeId === 2);
        assert.equal(row.needed, 40); assert.equal(row.cycles, 40); assert.equal(row.prodTotal, 400); passed.push('Build vs Buy uses exact stock-adjusted recipe');
        assert.deepEqual(errors, []);
        await page.screenshot({ path: path.join(output, 'calculation-fixture.png'), fullPage: true });
        return { passed, count: passed.length, pageErrors: errors, externalBlocked, origin, scope: 'Actual calculation modules in synthetic local UI; authenticated AA workflow is separate.' };
    } finally {
        await context.close();
        await new Promise(resolve => server.close(resolve));
    }
}

async function main() {
    const output = process.env.INDY_HUB_BROWSER_OUTPUT || fs.mkdtempSync(path.join(os.tmpdir(), 'indyhub-craft-browser-'));
    fs.mkdirSync(output, { recursive: true });
    const browser = await launchBrowser();
    try {
        const result = await runCalculationScenarios(browser, output);
        const report = { outcome: 'PASS', checkedAt: new Date().toISOString(), browserVersion: browser.version(), sandboxEnabled: true, ...result };
        fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n');
        console.log(JSON.stringify({ outcome: 'PASS', scenarios: result.count, report: path.join(output, 'report.json') }));
    } finally { await browser.close(); }
}
module.exports = { launchBrowser, verifySandbox, runCalculationScenarios };
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
