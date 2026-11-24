/* Publish only confirmed failures. Successful/uncertain runs never open issues. */
'use strict';
const crypto = require('node:crypto');
const MARKER = '<!-- indyhub-dependency-watch:v1 -->';
const TITLE = 'Dependency compatibility: confirmed regressions';
function render(results) {
    const failures = results.filter(row => row.status === 'confirmed');
    if (!failures.length) return null;
    const sections = failures.map(row => {
        const old = row.previous_versions || {};
        const current = row.candidate.versions || {};
        const changes = [...new Set([...Object.keys(old), ...Object.keys(current)])].sort()
            .filter(name => old[name] !== current[name])
            .map(name => `| ${name} | ${old[name] || 'absent'} | ${current[name] || 'absent'} |`);
        return `### ${row.lane}\n\nPhase: \`${row.candidate.phase}\`. The previous versions pass on the same source; the candidate fails twice with matching failure evidence.\n\n[Logs and complete inventories](${row.run_url})\n\n| Package | Previous | Candidate |\n| --- | --- | --- |\n${changes.slice(0,60).join('\n') || '| Resolution failed | See install log | No complete candidate inventory |'}\n\nUp to 60 changed packages are shown; artifacts contain the complete inventory. Changed packages are suspects, not an automatic attribution of the root cause.`;
    });
    return `${MARKER}\n${sections.join('\n\n')}\n\nAction: inspect the failing test/resolver evidence, then adapt Indy Hub or adjust its supported dependency constraint. No update is deployed automatically.\n`;
}
async function publish({ github, context, results }) {
    const body = render(results);
    const issues = await github.paginate(github.rest.issues.listForRepo, { ...context.repo, state: 'all', per_page: 100 });
    const existing = issues.filter(row => !row.pull_request && row.user?.login === 'github-actions[bot]' && row.body?.startsWith(MARKER)).sort((a,b) => b.number-a.number)[0];
    if (!body) {
        // An uncertain or missing lane must never close a confirmed incident.
        if (existing?.state === 'open' && results.length && results.every(row => row.status === 'healthy')) {
            await github.rest.issues.update({ ...context.repo, issue_number: existing.number, state: 'closed', state_reason: 'completed' });
        }
        return;
    }
    const digest = crypto.createHash('sha256').update(JSON.stringify(results.filter(row => row.status === 'confirmed').map(row => [row.lane, row.candidate.failure_key, row.candidate.versions]).sort())).digest('hex');
    const stamp = `<!-- incident:${digest} -->`;
    if (existing?.state === 'open' && existing.body.includes(stamp)) return;
    const stampedBody = `${body}\n${stamp}`;
    if (existing) {
        if (existing.body !== body || existing.state !== 'open') {
            await github.rest.issues.update({ ...context.repo, issue_number: existing.number, title: TITLE, body: stampedBody, state: 'open' });
        }
    } else {
        await github.rest.issues.create({ ...context.repo, title: TITLE, body: stampedBody });
    }
}
function snapshot(row, context) {
    if (row.status !== 'healthy' || !row.spec || !row.candidate.versions) return null;
    const direct = new Set([...row.spec.runtime, ...row.spec.extra].map(req => req.match(/^[A-Za-z0-9_.-]+/)[0].toLowerCase().replace(/_/g,'-')));
    const tests = new Set([...row.spec.tests, ...(row.spec.build || [])].map(req => req.match(/^[A-Za-z0-9_.-]+/)[0].toLowerCase().replace(/_/g,'-')));
    const resolved = Object.fromEntries(Object.entries(row.candidate.versions).map(([name, version]) => [name, {
        package_url: `pkg:pypi/${encodeURIComponent(name)}@${encodeURIComponent(version)}`,
        relationship: direct.has(name) || tests.has(name) ? 'direct' : 'indirect',
        scope: tests.has(name) ? 'development' : 'runtime',
    }]));
    return {version:0, sha:context.sha, ref:context.ref,
        job:{correlator:`dependency-watch-${row.lane}`,id:String(context.runId)},
        detector:{name:'indyhub-dependency-watch',version:'1.0.0',url:`https://github.com/${context.repo.owner}/${context.repo.repo}`},
        scanned:new Date().toISOString(), manifests:{'pyproject.toml':{name:'pyproject.toml',file:{source_location:'pyproject.toml'},resolved}}};
}
module.exports = { MARKER, render, publish, snapshot };
