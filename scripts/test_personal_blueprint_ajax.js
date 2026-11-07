/* Regression checks for background blueprint updates and stale requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const requests = [];
const timers = new Map();
let timerId = 0;
const classes = new Set();
const status = {
    textContent: '',
    dataset: {errorMessage: 'Retrying', updatedMessage: 'Updated', syncMessage: 'Syncing', updatingMessage: 'Loading'},
    classList: {
        toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
        contains(name) { return classes.has(name); }
    }
};
const results = {innerHTML: 'cached', addEventListener() {}, setAttribute() {}};
const owner = {
    value: '', options: [{cloneNode() { return this; }}],
    replaceChildren(...options) { this.options = options; }
};
const buttons = [{disabled: false}];
const nodes = {
    blueprintResults: results, blueprintFilters: {addEventListener() {}, elements: []},
    blueprintLiveStatus: status, ownerFilter: owner, exportBtn: {addEventListener() {}}
};
const document = {
    hidden: false,
    getElementById(id) { return nodes[id]; },
    addEventListener() {},
    querySelector() { return {value: 'csrf'}; },
    querySelectorAll() { return buttons; }
};
const window = {
    location: {href: 'https://auth.test/indy_hub/personnal-bp/?search=Widget&page=2'},
    addEventListener() {},
    history: {
        pushState(_state, _title, url) { window.location.href = String(url); },
        replaceState(_state, _title, url) { window.location.href = String(url); }
    }
};
const context = vm.createContext({
    document, window, URL, URLSearchParams, AbortController,
    Option: class { constructor(text, value) { this.textContent = text; this.value = value; } },
    setTimeout(callback, delay) { timers.set(++timerId, {callback, delay}); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) {
        return new Promise((resolve, reject) => { requests.push({url: new URL(url), options, resolve, reject}); });
    }
});
vm.runInContext(fs.readFileSync('indy_hub/static/indy_hub/js/personal_bp_list.js', 'utf8') + '\nwindow.List = PersonalBlueprintList;', context);
const list = new window.List();
const response = (html, running = false) => ({
    ok: true, redirected: false,
    async json() { return {html, owner_options: [], refresh_running: running}; }
});
const settle = () => new Promise(resolve => setImmediate(resolve));

(async () => {
    // The first background read keeps the current filters and visible local data.
    assert.equal(results.innerHTML, 'cached');
    assert.equal(requests[0].url.searchParams.get('search'), 'Widget');
    assert.equal(requests[0].url.searchParams.get('page'), '2');
    assert.equal(requests[0].url.searchParams.get('fragment'), '1');
    requests[0].resolve(response('first snapshot'));
    await settle();
    assert.equal(results.innerHTML, 'first snapshot');
    assert.ok([...timers.values()].some(timer => timer.delay === 30000));

    // A slow prior response must never overwrite a newer filter/navigation result.
    const oldLoad = list.load(new URL(window.location.href));
    const oldRequest = requests.at(-1);
    const newURL = new URL('https://auth.test/indy_hub/personnal-bp/?search=New');
    const newLoad = list.load(newURL, true);
    assert.equal(oldRequest.options.signal.aborted, true);
    requests.at(-1).resolve(response('new snapshot', true));
    await newLoad;
    oldRequest.resolve(response('stale snapshot'));
    await oldLoad;
    assert.equal(results.innerHTML, 'new snapshot');
    assert.equal(new URL(window.location.href).searchParams.get('search'), 'New');
    assert.ok([...timers.values()].some(timer => timer.delay === 5000));

    // Failed background reads leave results usable, then clear the error on recovery.
    const failedLoad = list.load(list.dataURL());
    requests.at(-1).reject(new Error('network unavailable'));
    await failedLoad;
    assert.equal(results.innerHTML, 'new snapshot');
    assert.equal(status.textContent, 'Retrying');
    const recovery = list.load(list.dataURL());
    requests.at(-1).resolve(response('recovered snapshot'));
    await recovery;
    assert.equal(results.innerHTML, 'recovered snapshot');
    assert.equal(classes.has('text-danger'), false);

    // Manual sync uses a CSRF-protected POST and never navigates away from the data.
    const beforeURL = window.location.href;
    const refresh = list.refresh();
    const manual = requests.at(-1);
    assert.equal(manual.options.method, 'POST');
    assert.equal(manual.options.headers['X-CSRFToken'], 'csrf');
    assert.equal(manual.options.body.get('action'), 'refresh');
    assert.equal(buttons[0].disabled, true);
    manual.resolve({ok: true, async json() { return {message: 'Queued', refresh_running: true}; }});
    await refresh;
    assert.equal(window.location.href, beforeURL);
    assert.equal(buttons[0].disabled, false);
    assert.equal(results.innerHTML, 'recovered snapshot');
    document.hidden = true;
    list.schedule();
    assert.equal(timers.size, 0);
    // Jobs share the controller and update slots alongside the filtered result rows.
    document.hidden = false;
    window.location.href = 'https://auth.test/indy_hub/personnal-jobs/?force_refresh=1';
    nodes.jobResults = results;
    nodes.jobFilters = nodes.blueprintFilters;
    nodes.jobLiveStatus = status;
    const panel = {id: 'slotOverviewPanel', classList: {add(name) { assert.equal(name, 'show'); }}};
    nodes.jobSlots = {dataset: {}, innerHTML: 'cached slots', querySelector(selector) {
        return selector === '.collapse.show' || selector === '#slotOverviewPanel' ? panel : null;
    }};
    let updates = 0;
    const jobs = new window.List({resultsId: 'jobResults', formId: 'jobFilters', statusId: 'jobLiveStatus', onUpdate() { updates++; }});
    assert.equal(requests.at(-1).url.searchParams.has('force_refresh'), false);
    requests.at(-1).resolve({ok: true, async json() {return {html: 'updated jobs', extra_html: {jobSlots: 'updated slots'}, owner_options: [], refresh_running: false}; }});
    await settle();
    assert.equal(results.innerHTML, 'updated jobs');
    assert.equal(nodes.jobSlots.innerHTML, 'updated slots');
    assert.equal(updates, 1);
    const failedJobs = jobs.load(jobs.dataURL());
    requests.at(-1).reject(new Error('unavailable'));
    await failedJobs;
    assert.equal(results.innerHTML, 'updated jobs');
    assert.equal(nodes.jobSlots.innerHTML, 'updated slots');
    console.log('Blueprint and job AJAX regression checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
