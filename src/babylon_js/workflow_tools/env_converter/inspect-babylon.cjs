#!/usr/bin/env node
// Offline structural inspection and documented native merge emulation, never application execution.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const B=require('babylonjs');
const VERSION=require('babylonjs/package.json').version;
const hash=data=>crypto.createHash('sha256').update(data).digest('hex');
const fields={positions:3,normals:3,tangents:4,uvs:2,uvs2:2,uvs3:2,uvs4:2,uvs5:2,uvs6:2,
  colors:4,matricesIndices:4,matricesWeights:4,matricesIndicesExtra:4,matricesWeightsExtra:4};
const layoutFields=Object.keys(fields).filter(f=>f!=='positions');
const identity=node=>({id:node.id,name:node.name,uniqueId:node.uniqueId,class:node.getClassName()});
const sourceIdentity=node=>({id:node.id,name:node.name,uniqueId:node.uniqueId});
function fail(message){throw Error(message);}
function finiteArray(value,label,length){
  if(!Array.isArray(value)&&!ArrayBuffer.isView(value))fail(label+' must be a numeric array');
  if(length!==undefined&&value.length!==length)fail(label+' has incorrect length');
  for(let i=0;i<value.length;i++)if(typeof value[i]!=='number'||!Number.isFinite(value[i]))fail(label+' contains nonfinite/nonnumeric value at '+i);
}
function geometryAudit(data,label){
  const present=Object.fromEntries([...Object.keys(fields),'indices'].map(k=>[k,Object.hasOwn(data,k)]));
  const positions=data.positions||[];finiteArray(positions,label+'.positions');
  if(positions.length%3)fail(label+' positions length is not divisible by 3');
  const vertices=positions.length/3,indices=data.indices||[];
  if(vertices&&(!present.indices||!indices.length))fail(label+' has vertices but missing/empty indices');
  finiteArray(indices,label+'.indices');if(indices.length%3)fail(label+' index count is not divisible by 3');
  for(let i=0;i<indices.length;i++)if(!Number.isInteger(indices[i])||indices[i]<0||indices[i]>=vertices)fail(label+' invalid index at '+i+': '+indices[i]);
  const attributes={};
  for(const [field,width] of Object.entries(fields)){
    const values=data[field];
    if(values!==undefined&&values!==null){
      finiteArray(values,label+'.'+field);
      const allowed=field==='colors'?[3,4]:field==='matricesIndices'||field==='matricesIndicesExtra'?[1,4]:[width];
      if(values.length&&!allowed.some(n=>values.length===vertices*n))fail(label+' '+field+' length does not match vertex count');
      attributes[field]={present:true,length:values.length,finite:true,sha256:hash(JSON.stringify(Array.from(values)))};
    }else attributes[field]={present:present[field],length:0,finite:true};
  }
  return {vertices,indices:indices.length,triangles:indices.length/3,indexFieldPresent:present.indices,
    validIndices:true,finitePositions:true,positionsFieldPresent:present.positions,tangentFieldPresent:present.tangents,
    uvFields:Object.fromEntries(['uvs','uvs2','uvs3','uvs4','uvs5','uvs6'].map(f=>[f,attributes[f]])),attributes,
    indicesSha256:hash(JSON.stringify(Array.from(indices)))};
}
function checkSubMeshes(subs,vertices,indices,label){
  for(const [i,sub] of (subs||[]).entries()){
    for(const key of ['materialIndex','verticesStart','verticesCount','indexStart','indexCount'])
      if(!Number.isInteger(sub[key])||sub[key]<0)fail(label+' subMesh '+i+' invalid '+key);
    if(sub.verticesStart+sub.verticesCount>vertices||sub.indexStart+sub.indexCount>indices||sub.indexCount%3)
      fail(label+' subMesh '+i+' exceeds geometry or has incomplete triangles');
  }
}
function preflight(raw){
  if(!raw||typeof raw!=='object'||Array.isArray(raw))fail('Input is not a Babylon scene object');
  for(const section of ['meshes','transformNodes','cameras','lights','materials','multiMaterials'])
    if(raw[section]!==undefined&&!Array.isArray(raw[section]))fail(section+' must be an array');
  const meshes=raw.meshes||[],nodes=[...meshes,...(raw.transformNodes||[]),...(raw.cameras||[]),...(raw.lights||[])];
  const nodeIds=new Map(),parentKeys=new Map();
  for(const node of nodes){
    if(node.id===undefined)fail('Structural node missing id: '+node.name);
    const id=String(node.id);if(nodeIds.has(id))fail('Ambiguous duplicate node id '+id);nodeIds.set(id,node);
    for(const key of [node.id,node.uniqueId].filter(v=>v!==undefined)){
      const text=String(key);if(parentKeys.has(text)&&parentKeys.get(text)!==node)fail('Ambiguous parent identifier '+text);parentKeys.set(text,node);
    }
    for(const [field,width] of Object.entries({position:3,rotation:3,scaling:3,rotationQuaternion:4,pivotMatrix:16,localMatrix:16}))
      if(node[field]!==undefined&&node[field]!==null)finiteArray(node[field],id+'.'+field,width);
    if(node.delayLoadingFile||node._binaryInfo)fail('External/deferred geometry is unsupported: '+id);
  }
  for(const node of nodes){
    const visited=new Set([node]);let current=node;
    while(current.parentId!==undefined&&current.parentId!==null){
      const parent=parentKeys.get(String(current.parentId));if(!parent)fail('Unresolved parent '+current.parentId+' on '+current.id);
      if(visited.has(parent))fail('Parent cycle on '+node.id);visited.add(parent);current=parent;
    }
  }
  const geometryMap=new Map(),geometries=[];
  for(const [kind,entries] of Object.entries(raw.geometries||{})){
    if(kind!=='vertexData'&&entries&&entries.length)fail('Unsupported non-vertexData geometry section '+kind);
    if(kind!=='vertexData')continue;
    for(const geometry of entries||[]){
      if(geometry.delayLoadingFile||geometry._binaryInfo)fail('External/deferred geometry is unsupported: '+geometry.id);
      if(geometry.id===undefined||geometryMap.has(String(geometry.id)))fail('Missing/duplicate geometry id');
      geometryMap.set(String(geometry.id),geometry);geometries.push({id:geometry.id,...geometryAudit(geometry,'geometry '+geometry.id)});
    }
  }
  const materialMap=new Map();
  for(const material of [...(raw.materials||[]),...(raw.multiMaterials||[])]){
    if(material.id===undefined||materialMap.has(String(material.id)))fail('Missing/duplicate material id');materialMap.set(String(material.id),material);
    if(material.customType&&!['BABYLON.StandardMaterial','BABYLON.PBRMaterial','BABYLON.PBRMetallicRoughnessMaterial','BABYLON.PBRSpecularGlossinessMaterial'].includes(material.customType))
      fail('Unsupported custom material class '+material.customType);
  }
  for(const material of raw.multiMaterials||[])for(const id of material.materials||[])
    if(id!==null&&!materialMap.has(String(id)))fail('Unresolved submaterial '+id);
  const audited=meshes.map(mesh=>{
    let geometry=mesh;
    if(mesh.geometryId!==undefined&&mesh.geometryId!==null){
      geometry=geometryMap.get(String(mesh.geometryId));if(!geometry)fail('Unresolved geometry '+mesh.geometryId+' on '+mesh.id);
      if(mesh.positions&&mesh.positions.length)fail('Ambiguous inline and referenced geometry on '+mesh.id);
    }
    if(mesh.materialId!==undefined&&mesh.materialId!==null&&!materialMap.has(String(mesh.materialId)))fail('Unresolved material '+mesh.materialId+' on '+mesh.id);
    const audit=geometryAudit(geometry,'mesh '+mesh.id);checkSubMeshes(mesh.subMeshes,audit.vertices,audit.indices,'mesh '+mesh.id);
    for(const instance of mesh.instances||[])for(const field of ['position','rotation','scaling','rotationQuaternion'])
      if(instance[field])finiteArray(instance[field],'instance '+instance.id+'.'+field,field==='rotationQuaternion'?4:3);
    return {id:mesh.id,name:mesh.name,parentId:mesh.parentId??null,geometryId:mesh.geometryId??null,materialId:mesh.materialId??null,
      instanceCount:(mesh.instances||[]).length,subMeshes:(mesh.subMeshes||[]).length,...audit};
  });
  return {nodes,nodeIds,parentKeys,geometryMap,meshes:audited,geometries,
    counts:{meshes:meshes.length,transformNodes:(raw.transformNodes||[]).length,cameras:(raw.cameras||[]).length,
      vertices:audited.reduce((s,m)=>s+m.vertices,0),indices:audited.reduce((s,m)=>s+m.indices,0),triangles:audited.reduce((s,m)=>s+m.triangles,0),
      subMeshes:audited.reduce((s,m)=>s+m.subMeshes,0),materials:(raw.materials||[]).length,multiMaterials:(raw.multiMaterials||[]).length,
      lights:(raw.lights||[]).length,instances:audited.reduce((s,m)=>s+m.instanceCount,0)}};
}
function structuralCopy(raw){
  const data=JSON.parse(JSON.stringify(raw)),omissions=[];
  function remove(object,key,prefix){if(Object.hasOwn(object,key)){
    const value=object[key];omissions.push({path:prefix+key,count:Array.isArray(value)?value.length:1});delete object[key];
  }}
  for(const key of ['environmentTexture','createDefaultSkybox','reflectionProbes','postProcesses','particleSystems','spriteManagers',
    'sounds','lensFlareSystems','shadowGenerators','proceduralTextures','animationGroups','animations','actions','autoAnimate','autoAnimateFrom','autoAnimateTo','autoAnimateLoop'])remove(data,key,'$.');
  for(const section of ['meshes','transformNodes','cameras','lights','materials','skeletons'])for(const [i,node] of (data[section]||[]).entries()){
    for(const key of ['animations','actions','autoAnimate','autoAnimateFrom','autoAnimateTo','autoAnimateLoop'])remove(node,key,'$.'+section+'['+i+'].');
    for(const [j,instance] of (node.instances||[]).entries())remove(instance,'animations','$.meshes['+i+'].instances['+j+'].');
  }
  const changedFields=[];
  if(data.useDelayedTextureLoading!==true)changedFields.push({path:'$.useDelayedTextureLoading',previous:data.useDelayedTextureLoading??null,value:true});
  data.useDelayedTextureLoading=true;
  return {data,omissions,changedFields};
}
function selector(node,selection){
  if(!selection||typeof selection!=='object'||Array.isArray(selection)||Object.keys(selection).length!==1||!['id','name'].includes(Object.keys(selection)[0]))fail('Selector must have exactly one id or name');
  return selection.id!==undefined?String(node.id)===String(selection.id):node.name===selection.name;
}
function resolveOne(nodes,selection,label){const matches=nodes.filter(n=>selector(n,selection));if(matches.length!==1)fail(label+' resolved '+matches.length+' nodes');return matches[0];}
function ancestorChain(node){const result=[],seen=new Set();for(let current=node.parent;current;current=current.parent){if(seen.has(current))fail('Native parent cycle');seen.add(current);result.push(identity(current));}return result;}
function bounds(mesh){
  const positions=mesh.getVerticesData(B.VertexBuffer.PositionKind)||[];if(!positions.length)return null;
  const matrix=mesh.computeWorldMatrix(true),min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
  for(let i=0;i<positions.length;i+=3){const point=B.Vector3.TransformCoordinates(new B.Vector3(positions[i],positions[i+1],positions[i+2]),matrix).asArray();
    for(let c=0;c<3;c++){if(!Number.isFinite(point[c]))fail('Nonfinite world bounds '+mesh.id);min[c]=Math.min(min[c],point[c]);max[c]=Math.max(max[c],point[c]);}}
  return {minimum:min,maximum:max};
}
function unionBounds(items){const all=items.filter(Boolean);if(!all.length)return null;return {minimum:[0,1,2].map(c=>Math.min(...all.map(b=>b.minimum[c]))),maximum:[0,1,2].map(c=>Math.max(...all.map(b=>b.maximum[c])))};}
function nearBounds(a,b){if(!a||!b)return a===b;return ['minimum','maximum'].every(k=>a[k].every((v,c)=>Math.abs(v-b[k][c])<=1e-5*Math.max(1,Math.abs(v),Math.abs(b[k][c]))));}
function materialIdentity(material){return material?{...identity(material),subMaterials:material instanceof B.MultiMaterial?material.subMaterials.map(m=>m?identity(m):null):undefined}:null;}
function nativeGeometry(mesh){const data=B.VertexData.ExtractFromMesh(mesh,false,true);const audit=geometryAudit(data,'native mesh '+mesh.id);return {data,audit};}
function snapshot(mesh,role,chunk){const {data,audit}=nativeGeometry(mesh);return {...identity(mesh),role,chunk:chunk?.key??null,chunkRoot:chunk?identity(chunk.node):null,
  parent:mesh.parent?identity(mesh.parent):null,ancestors:ancestorChain(mesh),material:materialIdentity(mesh.material),bounds:bounds(mesh),
  subMeshes:(mesh.subMeshes||[]).map(s=>({materialIndex:s.materialIndex,verticesStart:s.verticesStart,verticesCount:s.verticesCount,indexStart:s.indexStart,indexCount:s.indexCount})),
  visible:mesh.isVisible,enabled:mesh.isEnabled(),isInstance:mesh.isAnInstance,sourceInstanceCount:mesh.instances?.length??0,thinInstanceCount:mesh.thinInstanceCount??0,
  layout:Object.fromEntries(layoutFields.map(f=>[f,Boolean(data[f]?.length)])),...audit};}
function totals(meshes){return {meshes:meshes.length,vertices:meshes.reduce((s,m)=>s+m.getTotalVertices(),0),indices:meshes.reduce((s,m)=>s+m.getTotalIndices(),0),
  triangles:meshes.reduce((s,m)=>s+m.getTotalIndices()/3,0),subMeshes:meshes.reduce((s,m)=>s+(m.subMeshes?.length??0),0),
  bounds:unionBounds(meshes.map(bounds)),materials:[...new Set(meshes.map(m=>m.material))].map(materialIdentity)};}
function compareGeometry(mesh,rawGeometry){
  const native=mesh.getVerticesData(B.VertexBuffer.PositionKind)||[],raw=rawGeometry.positions||[];
  if(native.length!==raw.length)fail('Native position count differs on '+mesh.id);
  let exact=true,float32Compatible=true,maxAbsoluteDifference=0;
  for(let i=0;i<raw.length;i++){if(native[i]!==raw[i])exact=false;if(native[i]!==raw[i]&&native[i]!==Math.fround(raw[i]))float32Compatible=false;maxAbsoluteDifference=Math.max(maxAbsoluteDifference,Math.abs(native[i]-raw[i]));}
  if(!float32Compatible)fail('Native positions changed beyond float32 storage on '+mesh.id);
  const nativeIndices=Array.from(mesh.getIndices()||[]),rawIndices=rawGeometry.indices||[];
  if(nativeIndices.length!==rawIndices.length||nativeIndices.some((n,i)=>n!==rawIndices[i]))fail('Native indices differ on '+mesh.id);
  return {positionSourceValuesExact:exact,float32Compatible,maxAbsoluteDifference,indexSourceValuesExact:true};
}
function inspectNative(raw,pre,config,report){
  const engine=new B.NullEngine({renderWidth:1,renderHeight:1,textureSize:1}),scene=new B.Scene(engine),ioAttempts=[],restore=[];
  const guard=name=>(...args)=>{const target=args.find(a=>typeof a==='string');ioAttempts.push({api:name,target:target??null});fail('Offline inspector blocked file/network access: '+name);};
  function replace(object,key,name){const previous=object[key];object[key]=guard(name);restore.push(()=>{object[key]=previous;});}
  replace(engine,'_loadFile','engine._loadFile');replace(scene,'_loadFile','scene._loadFile');
  for(const key of ['LoadFile','LoadFileAsync','LoadImage'])replace(B.Tools,key,'Tools.'+key);
  replace(globalThis,'fetch','global.fetch');replace(globalThis,'XMLHttpRequest','global.XMLHttpRequest');
  try{
    scene.useDelayedTextureLoading=true;scene.autoClear=false;
    const copy=structuralCopy(raw),text=JSON.stringify(copy.data);
    report.structuralLoad={copySha256:hash(text),copyBytes:Buffer.byteLength(text),omissions:copy.omissions,changedFields:copy.changedFields,
      delayedTextureLoading:true,ioAttempts,loader:'SceneLoader.GetPluginForExtension(.babylon).loadAssetContainer',
      limits:'Native structural copy only. Images/ENV omitted or delayed; texture-dependent opacity/appearance, decode and rendering are not validated.'};
    let loaderError;
    const container=B.SceneLoader.GetPluginForExtension('.babylon').loadAssetContainer(scene,text,'offline:///',(message,error)=>{loaderError={message,error:String(error||'')};});
    if(loaderError)fail('Native loader failed: '+JSON.stringify(loaderError));if(ioAttempts.length)fail('Native loader attempted external I/O');
    if(!container)fail('Native loader returned no AssetContainer');container.addAllToScene();
    const meshes=[...container.meshes],nodes=[...meshes,...container.transformNodes,...container.cameras,...container.lights];
    if(meshes.filter(m=>!m.isAnInstance).length!==(raw.meshes||[]).length)fail('Raw/native base mesh count mismatch');
    for(const [section,assets] of [['transformNodes',container.transformNodes],['cameras',container.cameras],['lights',container.lights],['materials',container.materials],['multiMaterials',container.multiMaterials]])
      if(assets.length!==(raw[section]||[]).length)fail('Raw/native '+section+' count mismatch');
    for(const node of nodes.filter(n=>!n.isAnInstance)){
      const source=pre.nodeIds.get(String(node.id));if(!source)fail('Native structural node missing raw source '+node.id);
      const expectedParent=source.parentId===undefined||source.parentId===null?null:pre.parentKeys.get(String(source.parentId));
      if((node.parent?.id??null)!==(expectedParent?.id??null))fail('Native structural parent mismatch '+node.id);
    }
    report.structuralLoad.parentRelationshipsVerified=true;
    const groups=[],byRoot=new Map();
    for(const entry of config.groups||[]){
      const node=resolveOne(nodes,entry.root,'Group root '+entry.key);
      if(typeof entry.key!=='string'||!entry.key||groups.some(g=>g.key===entry.key)||byRoot.has(node))fail('Missing/duplicate group key/root');
      const group={key:entry.key,node,expected:entry,discovered:'configuration'};groups.push(group);byRoot.set(node,group);
    }
    for(const node of nodes)if(typeof node.name==='string'&&node.name.includes('optimise_chunk')&&!byRoot.has(node)){
      const key='marker:'+String(node.id);if(groups.some(g=>g.key===key))fail('Ambiguous derived group key '+key);
      const group={key,node,expected:{},discovered:'literal optimise_chunk marker'};groups.push(group);byRoot.set(node,group);
    }
    const helperSet=new Set((config.helpers||[]).map(s=>resolveOne(meshes,s,'Helper')));
    const explicitRuntime=config.runtimeMeshes!==undefined?new Set(config.runtimeMeshes.map(s=>resolveOne(meshes,s,'Runtime mesh'))):null;
    if(explicitRuntime)for(const helper of helperSet)if(explicitRuntime.has(helper))fail('Mesh is both helper and runtime: '+helper.id);
    const roles=new Map(),chunks=new Map();
    for(const mesh of meshes){
      let group=null;for(let node=mesh;node;node=node.parent)if(byRoot.has(node)){group=byRoot.get(node);break;}
      chunks.set(mesh,group);
      const role=helperSet.has(mesh)?'helper':explicitRuntime?(explicitRuntime.has(mesh)?'runtime':'preserved-other'):(mesh.getTotalVertices()>0?'runtime':'helper');roles.set(mesh,role);
      if(group){mesh.metadata={...(mesh.metadata||{}),optimise_chunk:group.node.name};}
    }
    const loaded=meshes.map(mesh=>{
      const snap=snapshot(mesh,roles.get(mesh),chunks.get(mesh));
      if(!mesh.isAnInstance){const source=pre.nodeIds.get(String(mesh.id));if(!source)fail('Native mesh missing raw source '+mesh.id);
        const geometry=source.geometryId!==undefined&&source.geometryId!==null?pre.geometryMap.get(String(source.geometryId)):source;
        snap.rawComparison=compareGeometry(mesh,geometry);
        if(source.parentId!==undefined&&source.parentId!==null){const parent=pre.parentKeys.get(String(source.parentId));
          if(!mesh.parent||String(mesh.parent.id)!==String(parent.id))fail('Native parent mismatch '+mesh.id);}}
      if(config.requireTangentsAbsent&&snap.tangentFieldPresent&&snap.attributes.tangents.length)fail('Serialized/loaded tangents disallowed by configuration: '+mesh.id);
      checkSubMeshes(snap.subMeshes,snap.vertices,snap.indices,'native '+mesh.id);return snap;
    });
    report.native={counts:{...totals(meshes),transformNodes:container.transformNodes.length,cameras:container.cameras.length,
      materials:container.materials.length,multiMaterials:container.multiMaterials.length,lights:container.lights.length,
      baseMeshes:meshes.filter(m=>!m.isAnInstance).length,instances:meshes.filter(m=>m.isAnInstance).length},meshes:loaded,
      roleBasis:explicitRuntime?'explicit runtime/helper selectors':'inferred nonempty geometry and configured helpers; convenience, not authoritative role proof',
      roleTotals:Object.fromEntries(['runtime','helper','preserved-other'].map(role=>[role,totals(meshes.filter(m=>roles.get(m)===role))]))};
    report.groups=groups.map(group=>{
      const members=meshes.filter(m=>chunks.get(m)===group),runtime=members.filter(m=>roles.get(m)==='runtime');
      const receipt={key:group.key,root:identity(group.node),discovered:group.discovered,memberIds:members.map(m=>m.id),runtimeMemberIds:runtime.map(m=>m.id),
        helperMemberIds:members.filter(m=>roles.get(m)==='helper').map(m=>m.id),runtime:totals(runtime),helper:totals(members.filter(m=>roles.get(m)==='helper')),expectationsPassed:true};
      for(const [field,actual] of [['expectedMemberIds',runtime.map(m=>String(m.id))],['expectedMemberNames',runtime.map(m=>m.name)]])if(group.expected[field]!==undefined){
        const expected=group.expected[field];if(!Array.isArray(expected)||new Set(expected.map(String)).size!==expected.length)fail('Invalid '+field+' for '+group.key);
        const missing=expected.map(String).filter(v=>!actual.includes(v)),extra=actual.filter(v=>!expected.map(String).includes(v));
        if(missing.length||(!group.expected.allowAdditionalMembers&&extra.length))fail('Group membership mismatch '+group.key+': '+JSON.stringify({missing,extra}));
      }
      return receipt;
    });
    const candidates=meshes.filter(m=>roles.get(m)==='runtime'&&m instanceof B.Mesh&&!m.isAnInstance&&m.getTotalVertices()>2&&!m.instances.length&&!m.hasThinInstances);
    const preserved=meshes.filter(m=>!candidates.includes(m));
    report.mergeEmulation={status:'running',applicationExecuted:false,renderingExecuted:false,sourceDisposal:false,
      policy:'Native offline emulation: material-object + root-identity + VertexData field-presence buckets; then same-root multi-material merge. Unrooted buckets remain separate in stage 2.',
      stages:[],excluded:preserved.map(m=>({...identity(m),role:roles.get(m),reason:roles.get(m)!=='runtime'?'non-runtime role':m.isAnInstance?'instance':m.getTotalVertices()<=2?'at most two vertices':'source/thin instances'})),
      before:totals(candidates),preservedBefore:totals(preserved)};
    const detach=[];
    for(const mesh of candidates){const before=bounds(mesh);mesh.unfreezeWorldMatrix();mesh.setParent(null);const after=bounds(mesh);
      const unchanged=nearBounds(before,after);detach.push({mesh:sourceIdentity(mesh),before,after,worldBoundsPreserved:unchanged});
      if(!unchanged)fail('Native setParent(null) did not preserve world bounds: '+mesh.id);}
    report.mergeEmulation.detach=detach;
    const buckets=new Map();
    for(const mesh of candidates){const data=B.VertexData.ExtractFromMesh(mesh,false,true),layout=layoutFields.map(f=>Boolean(data[f]?.length));
      const key=JSON.stringify([mesh.material?.uniqueId??null,chunks.get(mesh)?.key??null,layout]);if(!buckets.has(key))buckets.set(key,[]);buckets.get(key).push(mesh);}
    const merge=(inputs,stage,multi)=>{
      const before=totals(inputs),sourceHashes=inputs.map(m=>nativeGeometry(m).audit.attributes.positions.sha256);
      let merged;
      try{merged=B.Mesh.MergeMeshes(inputs,false,true,undefined,false,multi);}catch(error){report.mergeEmulation.stages.push({stage,inputs:inputs.map(sourceIdentity),before,failure:String(error)});throw error;}
      if(!merged){report.mergeEmulation.stages.push({stage,inputs:inputs.map(sourceIdentity),before,failure:'Native MergeMeshes returned null'});fail('Native merge returned null at '+stage);}
      const after=totals([merged]),audit=nativeGeometry(merged).audit;
      if(before.triangles!==after.triangles||before.vertices!==after.vertices||!nearBounds(before.bounds,after.bounds))fail('Merge altered triangle/vertex totals or world bounds at '+stage);
      const intact=inputs.every((m,i)=>!m.isDisposed()&&nativeGeometry(m).audit.attributes.positions.sha256===sourceHashes[i]);if(!intact)fail('Merge mutated/disposed a source at '+stage);
      report.mergeEmulation.stages.push({stage,multiMultiMaterials:multi,inputs:inputs.map(sourceIdentity),before,after,output:identity(merged),
        sourcesUndisposedAndPositionsUnchanged:intact,outputIndexAudit:{valid:audit.validIndices,finitePositions:audit.finitePositions},
        outputSubMeshes:snapshot(merged,'runtime',chunks.get(inputs[0])).subMeshes});
      chunks.set(merged,chunks.get(inputs[0]));return merged;
    };
    const first=[...buckets.values()].map(bucket=>merge(bucket,'material-and-layout',false));
    report.mergeEmulation.afterMaterialStage=totals(first);
    const secondBuckets=new Map(),unrooted=[];
    for(const mesh of first){const group=chunks.get(mesh);if(!group){unrooted.push(mesh);continue;}if(!secondBuckets.has(group.key))secondBuckets.set(group.key,[]);secondBuckets.get(group.key).push(mesh);}
    const final=[...unrooted,...[...secondBuckets.values()].map(bucket=>merge(bucket,'same-chunk-multimaterial',true))];
    report.mergeEmulation.after=totals(final);report.mergeEmulation.groups=groups.map(group=>({key:group.key,before:totals(candidates.filter(m=>chunks.get(m)===group)),after:totals(final.filter(m=>chunks.get(m)===group))}));
    report.mergeEmulation.preservedAfter=totals(preserved);
    if(!nearBounds(report.mergeEmulation.preservedBefore.bounds,report.mergeEmulation.preservedAfter.bounds)||report.mergeEmulation.preservedBefore.triangles!==report.mergeEmulation.preservedAfter.triangles)fail('Preserved helper/other geometry changed');
    report.mergeEmulation.originalSourcesStillUndisposed=candidates.every(m=>!m.isDisposed());
    report.mergeEmulation.status='passed';
    if(ioAttempts.length)fail('External I/O attempted during inspection');
  }finally{for(const reset of restore.reverse())reset();scene.dispose();engine.dispose();}
}
function parseArgs(){
  const args=process.argv.slice(2),result={};
  for(let i=0;i<args.length;i+=2){if(!['--input','--output','--groups'].includes(args[i])||!args[i+1]||result[args[i].slice(2)])fail('Usage: node inspect-babylon.cjs --input SCENE.babylon --output FRESH.json [--groups SCENE_GROUPS.json]');result[args[i].slice(2)]=path.resolve(args[i+1]);}
  if(!result.input||!result.output)fail('--input and --output are required');
  for(const source of [result.input,result.groups].filter(Boolean))if(source.toLowerCase()===result.output.toLowerCase())fail('Output must differ from input/configuration');
  if(fs.existsSync(result.output))fail('Output already exists; use a fresh path');return result;
}
function main(){
  const args=parseArgs(),report={status:'failed',schemaVersion:1,babylon:VERSION,mode:'offline-native-structural-copy-and-merge-emulation',
    input:{path:args.input},failures:[],limits:['No application execution.','No texture decode, GPU render, visual/material acceptance, navigation, physics or measured culling savings.']};
  try{
    const bytes=fs.readFileSync(args.input);report.input.bytes=bytes.length;report.input.sha256=hash(bytes);
    const raw=JSON.parse(bytes.toString('utf8')),config=args.groups?JSON.parse(fs.readFileSync(args.groups,'utf8')):{};
    report.configuration=args.groups?{path:args.groups,sha256:hash(fs.readFileSync(args.groups)),data:config}:{data:{}};
    if(config.runtimeMeshes!==undefined&&!Array.isArray(config.runtimeMeshes))fail('runtimeMeshes must be an array');
    const pre=preflight(raw);report.raw={counts:pre.counts,meshes:pre.meshes,geometries:pre.geometries,
      materials:[...(raw.materials||[]),...(raw.multiMaterials||[])].map(m=>({id:m.id,name:m.name,type:m.customType||'BABYLON.StandardMaterial',
        alpha:m.alpha??null,transparencyMode:m.transparencyMode??null,backFaceCulling:m.backFaceCulling??null,
        useAlphaFromAlbedoTexture:m.useAlphaFromAlbedoTexture??null,albedoTextureHasAlpha:m.albedoTexture?.hasAlpha??null,
        subMaterialIds:m.materials??null})),
      nodes:pre.nodes.map(n=>({id:n.id,name:n.name,parentId:n.parentId??null,position:n.position??null,rotation:n.rotation??null,
        rotationQuaternion:n.rotationQuaternion??null,scaling:n.scaling??null,pivotMatrix:n.pivotMatrix??null,localMatrix:n.localMatrix??null}))};
    inspectNative(raw,pre,config,report);report.status='passed';
  }catch(error){report.failures.push(String(error.stack||error));if(report.mergeEmulation?.status==='running')report.mergeEmulation.status='failed';process.exitCode=1;}
  fs.writeFileSync(args.output,JSON.stringify(report,null,2),{flag:'wx'});
  console.log(JSON.stringify({status:report.status,report:args.output,inputSha256:report.input.sha256,failures:report.failures.length}));
}
try{main();}catch(error){console.error(error.stack||error);process.exitCode=1;}