/* Restore only baselines published by this workflow on the default branch. */
'use strict';
const fs = require('node:fs');
async function restore({github,context,lane,target}) {
    const {data} = await github.rest.actions.listWorkflowRuns({...context.repo, workflow_id:'dependency-watch.yml', branch:context.payload.repository.default_branch, status:'completed', per_page:20});
    for (const run of data.workflow_runs) {
        if (run.id === context.runId || run.head_branch !== context.payload.repository.default_branch || run.head_repository?.full_name !== `${context.repo.owner}/${context.repo.repo}` || !['schedule','workflow_dispatch'].includes(run.event)) continue;
        const artifacts = await github.paginate(github.rest.actions.listWorkflowRunArtifacts,{...context.repo,run_id:run.id,per_page:100});
        const artifact = artifacts.find(row => row.name === `dependency-baseline-${lane}` && !row.expired);
        if (!artifact) continue;
        const response = await github.rest.actions.downloadArtifact({...context.repo,artifact_id:artifact.id,archive_format:'zip'});
        const bytes=Buffer.from(response.data);
        if(bytes.length>8*1024*1024) throw new Error('Baseline archive is unexpectedly large');
        fs.writeFileSync(target,bytes);
        return;
    }
}
module.exports={restore};
