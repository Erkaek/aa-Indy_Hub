/* Verify browser tooling against an ephemeral local fixture, never the AA site.
 * Runtime dependencies live outside this repository; see CHROME-PRET.md.
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');

const runtimePath = process.env.INDY_HUB_BROWSER_CONFIG || '/tmp/indyhub-browser-tools/config.json';
const config = JSON.parse(fs.readFileSync(runtimePath, 'utf8'));
const {chromium} = require(config.playwrightModule);
const headed = process.argv.includes('--headed');
const allModes = process.argv.includes('--all-modes');
const modes = allModes ? [true, false] : [!headed];
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'indyhub-chrome-check-'));

function fixture() {
    return `<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vérification Chrome — Indy Hub</title>
<style>
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; font: 17px system-ui; background: #eef2f6; color: #17212d; }
main { max-width: 640px; margin: 30px auto; padding: 24px; border-radius: 12px; background: white; }
h1 { font-size: 26px; } label, input, output { display: block; width: 100%; }
input { margin: 10px 0; padding: 10px; font: inherit; }
button { margin: 8px 0; padding: 10px; font: inherit; max-width: 100%; }
output { margin-top: 16px; } small { color: #536477; }
</style><main><small>Page synthétique locale · aucune connexion à Alliance Auth</small>
<h1>Vérification Chrome</h1><form id="fixture-form">
<label for="fixture-name">Nom du scénario</label><input id="fixture-name" required autocomplete="off">
<button type="submit">Enregistrer</button></form>
<p>Valeur conservée : <strong id="saved-value">Aucune</strong></p>
<button id="simulate-failure">Simuler une erreur</button>
<button id="retry">Réessayer</button><output role="status">En attente</output></main>
<script>
const statusNode = document.querySelector('output');
const savedNode = document.getElementById('saved-value');
async function check(fail) {
    try {
    const response = await fetch('/api/value' + (fail ? '?fail=1' : ''));
    if (!response.ok) throw new Error('simulated');
    const data = await response.json();
    savedNode.textContent = data.value || 'Aucune';
    statusNode.textContent = 'Vérification réussie';
    } catch (_) { statusNode.textContent = 'Erreur simulée : données conservées'; }
}
document.getElementById('fixture-form').addEventListener('submit', async event => {
    event.preventDefault();
    const response = await fetch('/api/value', {method:'POST', headers:{'Content-Type':'application/json', 'X-Fixture-CSRF':'local-only'}, body:JSON.stringify({value:document.getElementById('fixture-name').value})});
    if (!response.ok) throw new Error('Unexpected fixture write failure');
    savedNode.textContent = (await response.json()).value;
    statusNode.textContent = 'Enregistré';
});
document.getElementById('simulate-failure').onclick = () => check(true);
document.getElementById('retry').onclick = () => check(false);
check(false);
</script></html>`;
}

async function verify(browser, origin, headless, state) {
    const mode = headless ? 'headless' : 'headed';
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const pageErrors = [];
    const requestFailures = [];
    const consoleErrors = [];
    let externalBlocked = 0;
    const unexpectedResponses = [];
    await context.route('**/*', route => {
        const requestURL = new URL(route.request().url());
        if (requestURL.origin === origin) return route.continue();
        externalBlocked += 1;
        return route.abort('blockedbyclient');
    });
    const page = await context.newPage();
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('requestfailed', request => requestFailures.push(request.url()));
    page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    page.on('response', response => {
        if (response.status() >= 400 && !response.url().endsWith('/api/value?fail=1')) unexpectedResponses.push(response.url());
    });
    try {
        await page.goto('chrome://sandbox');
        const sandboxText = await page.locator('body').innerText();
        fs.writeFileSync(path.join(output, `${mode}-sandbox.txt`), sandboxText);
        assert.match(sandboxText, /Seccomp-BPF sandbox\s+Yes/i, 'Chrome seccomp sandbox must be enabled');
        assert.match(sandboxText, /PID namespaces\s+Yes/i, 'Chrome PID namespace sandbox must be enabled');
        await page.goto(origin, {waitUntil: 'networkidle'});
        const status = page.getByRole('status');
        await assertText(status, 'Vérification réussie');
        const value = `Scénario ${mode} — Indy Hub`;
        await page.getByLabel('Nom du scénario').fill(value);
        await page.getByRole('button', {name: 'Enregistrer', exact: true}).click();
        await assertText(status, 'Enregistré');
        await assertText(page.locator('#saved-value'), value);
        await page.reload({waitUntil: 'networkidle'});
        await assertText(page.locator('#saved-value'), value);
        await page.getByRole('button', {name: 'Simuler une erreur'}).click();
        await assertText(status, 'Erreur simulée : données conservées');
        await assertText(page.locator('#saved-value'), value);
        await page.getByRole('button', {name: 'Réessayer'}).click();
        await assertText(status, 'Vérification réussie');
        await page.screenshot({path: path.join(output, `${mode}-desktop.png`), fullPage: true});
        await page.setViewportSize({width: 390, height: 844});
        await page.screenshot({path: path.join(output, `${mode}-mobile.png`), fullPage: true});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await context.addCookies([{name: 'fixture_session', value: 'synthetic-only', url: origin}]);
        const otherContext = await browser.newContext();
        try { assert.equal((await otherContext.cookies(origin)).length, 0); }
        finally { await otherContext.close(); }
        const blockedResult = await page.evaluate(async () => {
            try { await fetch('https://outside.invalid/fixture'); return false; }
            catch (_) { return true; }
        });
        assert.equal(blockedResult, true);
        assert.equal(externalBlocked, 1);
        assert.deepEqual(pageErrors, []);
        assert.deepEqual(unexpectedResponses, []);
        assert.deepEqual(requestFailures, ['https://outside.invalid/fixture']);
        assert(consoleErrors.every(message => /500|ERR_BLOCKED_BY_CLIENT/.test(message)), 'Unexpected browser console error');
        assert.equal(state.value, value);
        return {
            mode, browserVersion: browser.version(), sandboxEnabled: true,
            checks: ['local HTTP navigation', 'form POST', 'reload', 'controlled failure', 'retry', 'desktop/mobile', 'separate sessions', 'external network blocked'],
            pageErrors, unexpectedResponses, expectedConsoleErrors: consoleErrors,
            sandboxEvidence: `${mode}-sandbox.txt`, screenshots: [`${mode}-desktop.png`, `${mode}-mobile.png`]
        };
    } finally { await context.close(); }
}

async function assertText(locator, value) {
    await locator.page().waitForFunction(({selector, text}) => document.querySelector(selector)?.textContent === text,
        {selector: await locator.evaluate(node => node.id ? `#${node.id}` : 'output'), text: value});
}

(async () => {
    const state = {value: '', writes: 0};
    const server = http.createServer(async (request, response) => {
        if (request.url === '/' || request.url === '/favicon.ico') {
            response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'});
            return response.end(fixture());
        }
        if (request.url === '/api/value?fail=1') {
            response.writeHead(500, {'Content-Type': 'application/json'});
            return response.end(JSON.stringify({error: 'intentional fixture failure'}));
        }
        if (request.url === '/api/value') {
            if (request.method === 'POST') {
                assert.equal(request.headers['x-fixture-csrf'], 'local-only');
                let body = '';
                for await (const chunk of request) body += chunk;
                state.value = JSON.parse(body).value;
                state.writes += 1;
            }
            response.writeHead(200, {'Content-Type': 'application/json'});
            return response.end(JSON.stringify({value: state.value}));
        }
        response.writeHead(404);
        response.end();
    });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const origin = `http://127.0.0.1:${server.address().port}`;
    const results = [];
    try {
        for (const headless of modes) {
            if (!headless) assert(process.env.DISPLAY || process.env.WAYLAND_DISPLAY, 'No display available for headed Chrome');
            const browser = await chromium.launch({
                executablePath: config.browserExecutable, headless, chromiumSandbox: true,
                env: {...process.env, LD_LIBRARY_PATH: config.libraryPath + (process.env.LD_LIBRARY_PATH ? ':' + process.env.LD_LIBRARY_PATH : '')}
            });
            try { results.push(await verify(browser, origin, headless, state)); }
            finally { await browser.close(); }
        }
        assert.equal(state.writes, modes.length);
        const report = {
            outcome: 'PASS', checkedAt: new Date().toISOString(), fixtureOrigin: origin,
            playwrightVersion: require(path.join(config.playwrightModule, 'package.json')).version,
            browserExecutable: config.browserExecutable, modes: results,
            scope: 'Browser infrastructure only; no Indy Hub, ESI, OAuth, database, Redis or live broker contacted.'
        };
        fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n');
        console.log(JSON.stringify({outcome: 'PASS', report: path.join(output, 'report.json'), modes: results.map(result => result.mode)}));
    } finally { await new Promise(resolve => server.close(resolve)); }
})().catch(error => {
    fs.writeFileSync(path.join(output, 'failure.txt'), error.stack || String(error));
    console.error(`Browser self-test failed: ${error.message}\nEvidence: ${output}`);
    process.exitCode = 1;
});
