'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {restore} = require('./baseline.cjs');
const {MARKER,publish,snapshot} = require('./report.cjs');
async function main(){
    const calls=[];
    let issues=[];
    const github={paginate:async()=>issues,rest:{issues:{listForRepo:()=>{},create:async p=>calls.push(['create',p]),update:async p=>calls.push(['update',p])}}};
    const context={repo:{owner:'example',repo:'test'},sha:'a'.repeat(40),ref:'refs/heads/main',runId:1};
    const failed={lane:'3.12-base',status:'confirmed',run_url:'https://github.com/example/test/actions/runs/1',candidate:{phase:'tests',failure_key:'same',versions:{django:'5.3'}},previous_versions:{django:'5.2'}};
    await publish({github,context,results:[{status:'healthy'}]});assert.equal(calls.length,0);
    await publish({github,context,results:[{status:'unverified'}]});assert.equal(calls.length,0);
    await publish({github,context,results:[failed]});assert.equal(calls[0][0],'create');
    issues=[{number:7,state:'open',body:calls[0][1].body,user:{login:'github-actions[bot]'}}];calls.length=0;
    await publish({github,context,results:[{...failed,run_url:'new-run'}]});assert.equal(calls.length,0,'Same failure must not notify weekly');
    await publish({github,context,results:[{status:'unverified'}]});assert.equal(calls.length,0);
    await publish({github,context,results:[{status:'healthy'}]});assert.equal(calls[0][1].state,'closed');
    calls.length=0;issues=[{number:9,state:'open',body:MARKER,user:{login:'someone-else'}}];
    await publish({github,context,results:[failed]});assert.equal(calls[0][0],'create','Do not edit human issues');
    assert.equal(snapshot(failed,context),null);
    const graph=snapshot({...failed,status:'healthy',spec:{runtime:['Django>=5'],extra:[],tests:['coverage']}},context);
    assert.equal(graph.manifests['pyproject.toml'].resolved.django.package_url,'pkg:pypi/django@5.3');
    assert.equal(graph.job.correlator,'dependency-watch-3.12-base');
    const directory=fs.mkdtempSync(path.join(os.tmpdir(),'indyhub-baseline-test-'));
    try {
        const downloaded=[];
        const api={rest:{actions:{listWorkflowRuns:async()=>({data:{workflow_runs:[
            {id:2,head_branch:'main',event:'workflow_dispatch',head_repository:{full_name:'attacker/fork'}},
            {id:3,head_branch:'main',event:'push',head_repository:{full_name:'example/test'}},
            {id:4,head_branch:'main',event:'schedule',head_repository:{full_name:'example/test'}}
        ]}}),listWorkflowRunArtifacts:()=>{},downloadArtifact:async p=>{downloaded.push(p.artifact_id);return {data:Buffer.from('baseline')};}}},paginate:async (_fn,p)=>{
            assert.equal(p.run_id,4,'Only trusted default-branch scheduled/manual runs');
            return [{id:42,name:'dependency-baseline-3.12-base',expired:false}];
        }};
        await restore({github:api,context:{...context,payload:{repository:{default_branch:'main'}}},lane:'3.12-base',target:path.join(directory,'baseline.zip')});
        assert.deepEqual(downloaded,[42]);
    } finally {fs.rmSync(directory,{recursive:true,force:true});}
    console.log('Dependency watch issue/snapshot tests passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
