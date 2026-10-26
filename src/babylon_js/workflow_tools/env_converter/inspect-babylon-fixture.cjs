#!/usr/bin/env node
// Focused synthetic structural/merge data and independent receipt checks. No application imports.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const sha=data=>crypto.createHash('sha256').update(data).digest('hex');
function triangle(id,parentId,materialId,position){return {id,name:id,parentId,materialId,position,rotation:[0,0,0],scaling:[1,1,1],isVisible:true,isEnabled:true,
  positions:[0,0,0,1,0,0,0,1,0],normals:[0,0,1,0,0,1,0,0,1],uvs:[0,0,1,0,0,1],indices:[0,1,2],
  subMeshes:[{materialIndex:0,verticesStart:0,verticesCount:3,indexStart:0,indexCount:3}]};}
function write(directory){
  const root=path.resolve(directory);if(fs.existsSync(root))throw Error('Use a new fixture directory');fs.mkdirSync(root,{recursive:true});
  const scene={producer:{name:'offline inspector fixture'},useDelayedTextureLoading:false,
    environmentTexture:'https://invalid.example/never-fetch.env',createDefaultSkybox:true,
    materials:[{id:'red',name:'red',diffuse:[1,0,0],alpha:1,backFaceCulling:true,
      diffuseTexture:{name:'https://invalid.example/never-fetch.png',hasAlpha:false}},
      {id:'blue',name:'blue',diffuse:[0,0,1],alpha:1,backFaceCulling:true}],
    transformNodes:[{id:'A',name:'groupA_optimise_chunk',position:[10,0,0],rotation:[0,0,Math.PI/2],scaling:[2,2,2]},
      {id:'B',name:'groupB_optimise_chunk',position:[-5,0,0],rotation:[0,0,0],scaling:[1,1,1]}],
    meshes:[triangle('a1','A','red',[0,0,0]),triangle('a2','A','red',[2,0,0]),triangle('a3','A','blue',[4,0,0]),
      triangle('b1','B','red',[0,0,0]),triangle('helper','A','red',[0,0,1])],
    lights:[{id:'light',name:'light',type:1,direction:[0,-1,0],position:[0,5,0],intensity:1}],
    cameras:[{id:'camera',name:'camera',type:'FreeCamera',position:[0,0,-5],rotation:[0,0,0],fov:.8,minZ:.1,maxZ:100}]};
  const config={groups:[{key:'A',root:{id:'A'},expectedMemberIds:['a1','a2','a3']},
    {key:'B',root:{name:'groupB_optimise_chunk'},expectedMemberNames:['b1']}],helpers:[{id:'helper'}],
    runtimeMeshes:['a1','a2','a3','b1'].map(id=>({id})),requireTangentsAbsent:true};
  const positive=JSON.stringify(scene);fs.writeFileSync(path.join(root,'positive.babylon'),positive);
  fs.writeFileSync(path.join(root,'groups.json'),JSON.stringify(config,null,2));
  const bad=JSON.parse(positive);bad.meshes[0].indices=[0,1,9];fs.writeFileSync(path.join(root,'bad-index.babylon'),JSON.stringify(bad));
  // JSON's numeric grammar permits 1e400; JSON.parse yields Infinity, which inspection must reject.
  fs.writeFileSync(path.join(root,'nonfinite.babylon'),positive.replace('"positions":[0,','"positions":[1e400,'));
  const missingParent=JSON.parse(positive);missingParent.meshes[0].parentId='missing';fs.writeFileSync(path.join(root,'missing-parent.babylon'),JSON.stringify(missingParent));
  console.log(JSON.stringify({directory:root,positive:'positive.babylon',groups:'groups.json',negative:['bad-index.babylon','nonfinite.babylon','missing-parent.babylon']}));
}
function near(actual,expected){assert.equal(actual.length,expected.length);for(let i=0;i<actual.length;i++)assert(Math.abs(actual[i]-expected[i])<1e-5,JSON.stringify({actual,expected}));}
function check(reportPath){
  const receipt=JSON.parse(fs.readFileSync(reportPath,'utf8'));assert.equal(receipt.status,'passed');assert.equal(receipt.babylon,'7.27.0');
  assert.equal(receipt.mode,'offline-native-structural-copy-and-merge-emulation');
  assert.equal(receipt.input.sha256,sha(fs.readFileSync(receipt.input.path)));
  assert.equal(receipt.raw.counts.meshes,5);assert.equal(receipt.raw.counts.triangles,5);assert.equal(receipt.raw.counts.vertices,15);
  assert.equal(receipt.native.counts.baseMeshes,5);assert.equal(receipt.native.counts.lights,1);assert.equal(receipt.native.counts.cameras,1);
  assert.equal(receipt.native.roleTotals.runtime.triangles,4);assert.equal(receipt.native.roleTotals.helper.triangles,1);
  assert.equal(receipt.native.roleBasis,'explicit runtime/helper selectors');
  assert.equal(receipt.structuralLoad.ioAttempts.length,0);assert.equal(receipt.structuralLoad.delayedTextureLoading,true);
  assert(receipt.structuralLoad.omissions.some(o=>o.path==='$.environmentTexture'&&o.count===1));
  assert(receipt.structuralLoad.copySha256!==receipt.input.sha256);
  const merge=receipt.mergeEmulation;assert.equal(merge.status,'passed');assert.equal(merge.applicationExecuted,false);assert.equal(merge.renderingExecuted,false);
  assert.equal(merge.sourceDisposal,false);assert.equal(merge.originalSourcesStillUndisposed,true);
  for(const stage of ['before','afterMaterialStage','after']){assert.equal(merge[stage].triangles,4);assert.equal(merge[stage].vertices,12);}
  assert.equal(merge.before.meshes,4);assert.equal(merge.afterMaterialStage.meshes,3);assert.equal(merge.after.meshes,2);assert.equal(merge.after.subMeshes,3);
  assert.equal(merge.preservedBefore.triangles,1);assert.equal(merge.preservedAfter.triangles,1);
  assert(merge.excluded.some(m=>m.id==='helper'&&m.role==='helper'));
  assert.equal(merge.detach.length,4);assert(merge.detach.every(d=>d.worldBoundsPreserved));
  const a1=merge.detach.find(d=>d.mesh.id==='a1');near(a1.before.minimum,[8,0,0]);near(a1.before.maximum,[10,2,0]);
  const b1=merge.detach.find(d=>d.mesh.id==='b1');near(b1.before.minimum,[-5,0,0]);near(b1.before.maximum,[-4,1,0]);
  const a=merge.groups.find(g=>g.key==='A'),b=merge.groups.find(g=>g.key==='B');
  assert.equal(a.after.triangles,3);assert.equal(a.after.meshes,1);assert.equal(a.after.subMeshes,2);
  assert.equal(b.after.triangles,1);assert.equal(b.after.meshes,1);assert.equal(b.after.subMeshes,1);
  const sameMaterial=merge.stages.find(s=>s.stage==='material-and-layout'&&s.inputs.length===2);
  assert(sameMaterial);assert.deepEqual(sameMaterial.inputs.map(n=>n.id).sort(),['a1','a2']);
  assert(merge.stages.every(s=>s.sourcesUndisposedAndPositionsUnchanged&&s.outputIndexAudit.valid&&s.outputIndexAudit.finitePositions));
  const multimaterial=merge.stages.find(s=>s.stage==='same-chunk-multimaterial'&&s.before.triangles===3);
  assert.deepEqual(multimaterial.after.materials[0].subMaterials.map(m=>m.id).sort(),['blue','red']);
  assert(receipt.native.meshes.filter(m=>!m.isInstance).every(m=>m.rawComparison.indexSourceValuesExact&&m.rawComparison.float32Compatible));
  console.log(JSON.stringify({status:'passed',report:path.resolve(reportPath),checks:'counts, roles, two roots, material merge/submeshes, transforms/bounds, source preservation and I/O guards'}));
}
function checkFailure(filename,expected){const receipt=JSON.parse(fs.readFileSync(filename,'utf8'));assert.equal(receipt.status,'failed');assert(receipt.failures.some(message=>message.includes(expected)));console.log(JSON.stringify({status:'passed',expectedFailure:expected,report:path.resolve(filename)}));}
const [mode,target,expected]=process.argv.slice(2);
if(mode==='write'&&target)write(target);else if(mode==='check'&&target)check(target);else if(mode==='check-failure'&&target&&expected)checkFailure(target,expected);
else throw Error('Usage: node inspect-babylon-fixture.cjs write NEW_DIRECTORY | check REPORT | check-failure REPORT EXPECTED_TEXT');