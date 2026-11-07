/* Run against an isolated local instance with a synthetic superuser cookie.
 * INDY_HUB_BROWSER_SESSION must reference a JSON cookie object; never commit it.
 * Playwright can be installed separately and selected with INDY_HUB_PLAYWRIGHT_MODULE.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const config = JSON.parse(fs.readFileSync(process.env.INDY_HUB_BROWSER_CONFIG || '/tmp/indyhub-browser-tools/config.json', 'utf8'));
const {chromium} = require(process.env.INDY_HUB_PLAYWRIGHT_MODULE || config.playwrightModule);

(async function () {
    const sessionPath = process.env.INDY_HUB_BROWSER_SESSION;
    assert(sessionPath, 'Set INDY_HUB_BROWSER_SESSION to the temporary cookie file');
    assert(process.env.INDY_HUB_BROWSER_ASSET_MANIFEST, 'Set INDY_HUB_BROWSER_ASSET_MANIFEST to prepared local CDN assets');
    const output = process.env.INDY_HUB_BROWSER_OUTPUT || '/tmp/indyhub-admin-preview';
    fs.mkdirSync(output, {recursive: true});
    const base = process.env.INDY_HUB_BASE_URL;
    assert(base, 'Set INDY_HUB_BASE_URL to an isolated test instance');
    const origin = new URL(base);
    assert(['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname), 'Use a local isolated test instance');
    const url = new URL('/indy_hub/settings/admin-users/', base).href;
    const browser = await chromium.launch({
        executablePath: config.browserExecutable, headless: true, chromiumSandbox: true,
        env: {...process.env, LD_LIBRARY_PATH: config.libraryPath + (process.env.LD_LIBRARY_PATH ? ':' + process.env.LD_LIBRARY_PATH : '')}
    });
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 1100}});
        const assets = JSON.parse(fs.readFileSync(process.env.INDY_HUB_BROWSER_ASSET_MANIFEST, 'utf8'));
        await context.route('**/*', route => {
            const url = route.request().url();
            if (new URL(url).origin === origin.origin) return route.continue();
            const asset = assets[url];
            if (asset) {
                const contentType = url.endsWith('.css') ? 'text/css' : url.endsWith('.woff2') ? 'font/woff2' : 'application/javascript';
                return route.fulfill({status: 200, contentType, body: fs.readFileSync(asset.path), headers: {'access-control-allow-origin': '*'}});
            }
            return route.abort('blockedbyclient');
        });
        await context.addCookies([JSON.parse(fs.readFileSync(sessionPath, 'utf8'))]);
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        const response = await page.goto(url, {waitUntil: 'domcontentloaded'});
        assert.equal(response.status(), 200);
        await page.locator('#adminUsersSummary .admin-users-summary-card strong').first().waitFor();
        const rows = page.locator('.admin-users-data-row');
        assert(await rows.count() > 0, 'The local database must contain a visible user');
        const username = await page.locator('.admin-users-user-trigger').first().innerText();
        await page.evaluate(() => { window.consoleTestTable = document.getElementById('adminUsersUsersTable'); });
        assert.equal(await page.locator('#adminUsersPage').evaluate(node => node.scrollWidth > node.clientWidth), false);
        await page.screenshot({path: path.join(output, 'desktop.png')});

        // A failed read must keep the previous table and provide a retry.
        const failedQuery = '__console_read_failure__';
        const fragmentMatch = requestURL => requestURL.pathname.endsWith('/settings/admin-users/') && requestURL.searchParams.get('fragment') === '1';
        await page.route(fragmentMatch, route => new URL(route.request().url()).searchParams.get('q') === failedQuery ? route.abort('failed') : route.continue());
        await page.locator('#adminUsersSearch').fill(failedQuery);
        await page.locator('#adminUsersNotice button').waitFor();
        assert.equal(await page.evaluate(() => window.consoleTestTable === document.getElementById('adminUsersUsersTable')), true);
        await page.unroute(fragmentMatch);
        await page.locator('#adminUsersNotice button').click();
        await page.waitForFunction(() => document.getElementById('adminUsersResults').getAttribute('aria-busy') === 'false' && !document.getElementById('adminUsersNotice').textContent);
        assert.equal(await rows.count(), 0);

        // A delayed old result must never replace newer search input/results.
        await page.route(fragmentMatch, async route => {
            if (new URL(route.request().url()).searchParams.get('q') === 'old-delayed-query') {
                const result = await route.fetch();
                await new Promise(resolve => setTimeout(resolve, 900));
                try { await route.fulfill({response: result}); } catch (_) { /* cancelled */ }
            } else { await route.continue(); }
        });
        await page.locator('#adminUsersSearch').fill('old-delayed-query');
        await page.waitForTimeout(450);
        await page.locator('#adminUsersSearch').fill(username);
        await page.waitForFunction(name => new URL(window.location.href).searchParams.get('q') === name && document.getElementById('adminUsersResults').getAttribute('aria-busy') === 'false', username);
        await page.waitForTimeout(1000);
        assert.equal(await page.locator('#adminUsersSearch').inputValue(), username);
        assert(await rows.count() > 0);
        await page.unroute(fragmentMatch);
        await page.goBack();
        await page.waitForFunction(() => new URL(window.location.href).searchParams.get('q') === '__console_read_failure__' && document.getElementById('adminUsersResults').getAttribute('aria-busy') === 'false');
        assert.equal(await rows.count(), 0);
        await page.goForward();
        await page.waitForFunction(name => document.getElementById('adminUsersSearch').value === name && document.getElementById('adminUsersResults').getAttribute('aria-busy') === 'false', username);

        await page.locator('.js-user-select').first().check();
        assert(await page.locator('#adminUsersBulkBar').isVisible());
        await page.locator('#adminUsersClearSelection').click();
        assert.equal(await page.locator('#adminUsersBulkBar').isVisible(), false);
        await page.locator('.admin-users-user-trigger').first().click();
        await page.locator('.admin-users-detail').waitFor();
        await page.waitForFunction(() => getComputedStyle(document.getElementById('adminUsersDetailModal')).opacity === '1');
        await page.screenshot({path: path.join(output, 'diagnostics.png')});
        await page.locator('#adminUsersDetailModal .btn-close').click();
        await page.locator('#adminUsersDetailModal').waitFor({state: 'hidden'});

        // Read-only smoke: CSV honors the current query and has no token secrets.
        const exported = await context.request.get(new URL(await page.locator('#adminUsersExport').getAttribute('href'), base).href);
        assert.equal(exported.status(), 200);
        assert((await exported.text()).includes('Username'));
        await page.setViewportSize({width: 390, height: 844});
        await page.waitForTimeout(500);
        await page.locator('.nav-padding.col').evaluate(node => { node.scrollTop = 0; });
        assert(await page.locator('#adminUsersPage').evaluate(node => node.clientWidth) > 300);
        await page.screenshot({path: path.join(output, 'mobile.png')});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
        await page.locator('#adminUsersResults').scrollIntoViewIfNeeded();
        await page.screenshot({path: path.join(output, 'mobile-results.png')});
        assert.deepEqual(errors, []);
        console.log(`Chrome ${browser.version()} headless, sandbox enabled. Admin console browser checks passed: AJAX failure/retry, stale reads, history, diagnostics, selection, CSV and mobile.`);
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
