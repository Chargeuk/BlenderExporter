// Execute the actual KaDshow parser methods, extracted without rewriting their
// bodies, against its installed Babylon dependency in a headless NullEngine.
// Usage in WSL: node this.mjs KADSHOW_REPO EXPORT_DIRECTORY
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import crypto from 'node:crypto';
const [repo,out] = process.argv.slice(2);
const ts = (await import(pathToFileURL(path.join(repo,'fe/client/node_modules/typescript/lib/typescript.js')))).default;
const B = await import(pathToFileURL(path.join(repo,'fe/client/node_modules/@babylonjs/core/index.js')));
const loaderPath=path.join(repo,'fe/client/src/Utils/babylonjs/kadShowSceneLoader.ts');
const text=fs.readFileSync(loaderPath,'utf8');
const source=ts.createSourceFile('loader.ts',text,ts.ScriptTarget.Latest,true);
const declaration=source.statements.find(n=>ts.isClassDeclaration(n)&&n.name.text==='KadShowSceneLoader');
const names=['tempIndexContainer','tempMaterialIndexContainer','isDescendantOf','parseMaterialByPredicate','findMaterial','findParent','logOperation','loadDetailLevels','ImportBabylonMeshesAndLights'];
const members=declaration.members.filter(n=>names.includes(n.name?.getText(source)));
if(members.length!==names.length) throw new Error('KaDshow parser structure changed; inspect before adapting');
const code='class KadShowSceneLoader {\n'+members.map(n=>n.getText(source)).join('\n')+'\n}\nglobalThis.TestLoader=KadShowSceneLoader;';
const js=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
// Babylon uses instanceof Array for vertex buffers. JSON must produce arrays
// in the same realm as the imported Babylon modules, as it does in the app.
const context=vm.createContext({...B,console,JSON});
vm.runInContext(js,context);
const engine=new B.NullEngine();
const scene=new B.Scene(engine);
// Structural/material parsing only. No browser texture decode or GPU claims.
scene.useDelayedTextureLoading=true;
const meshes=[], particles=[], skeletons=[],lights=[];
const raw=fs.readFileSync(path.join(out,'grandBedroomDay.babylon'),'utf8');
const json=JSON.parse(raw);
const ok=context.TestLoader.ImportBabylonMeshesAndLights(null,scene,raw,'./',meshes,particles,skeletons,lights,(message,error)=>{throw error||new Error(message)});
if(!ok) throw new Error('KaDshow parser failed');
const manifest=JSON.parse(fs.readFileSync(path.join(out,'manifest.json'),'utf8'));
const expected=new Set(manifest.export_names);
const received=new Set(meshes.map(m=>m.name));
if(expected.size!==received.size||[...expected].some(x=>!received.has(x))) throw new Error('Missing or unexpected imported objects');
const byName=new Map(meshes.map(m=>[m.name,m]));
const sourceComparison=JSON.parse(fs.readFileSync(path.join(out,'source_comparison.json'),'utf8'));
let maxWorldBoundsError=0;
for(const [name,expectedBounds] of Object.entries(sourceComparison.world_bounds)){
 const mesh=byName.get(name);mesh.refreshBoundingInfo();mesh.computeWorldMatrix(true);
 const bounds=mesh.getBoundingInfo().boundingBox;
 for(const [expected,actual] of [[expectedBounds.min,bounds.minimumWorld.asArray()],[expectedBounds.max,bounds.maximumWorld.asArray()]]){
  const error=Math.max(...expected.map((v,i)=>Math.abs(v-actual[i])));
  maxWorldBoundsError=Math.max(maxWorldBoundsError,error);
  if(error>.001)throw new Error(`World bounds changed for ${name}: ${error}`);
 }
}
let visibleTriangles=0;
for(const name of manifest.visible_names){
 const mesh=byName.get(name);
 if(!mesh.isVisible||!mesh.isEnabled()) throw new Error(`Invisible delivery: ${name}`);
 if(!mesh.isVerticesDataPresent(B.VertexBuffer.UV2Kind)) throw new Error(`UV2 absent: ${name}`);
 mesh.computeWorldMatrix(true);
 visibleTriangles+=mesh.getTotalIndices()/3;
}
if(visibleTriangles!==20000)throw new Error(`Triangle mismatch ${visibleTriangles}`);
for(const entry of json.meshes){
 if(entry.parentId&&byName.get(entry.name).parent?.id!==entry.parentId)throw new Error(`Lost parent ${entry.name}`);
}
const lm=meshes.find(m=>m.name.startsWith('lightmap_'));
if(lm.getChildMeshes().length!==636) {
 // Three additional empty chair orientation parents are also Mesh nodes.
 const owned=lm.getChildMeshes().filter(m=>manifest.visible_names.includes(m.name));
 if(owned.length!==633)throw new Error('Lightmap hierarchy incomplete');
}
const phy=byName.get('physicsObjects').getChildMeshes(true);
const nav=byName.get('navMeshFloor').getChildMeshes();
if(!phy.length||!nav.length)throw new Error('Missing physics/navigation hierarchy');
const chunks=meshes.filter(m=>m.name.toLowerCase().includes('optimise_chunk'));
if(chunks.length!==5)throw new Error('Missing optimisation groups');
const material=byName.get(manifest.visible_names[0]).material;
if(!(material instanceof B.PBRMaterial)||!material.albedoTexture||!material.metallicTexture)throw new Error('PBR material missing');
if(!material.useMetallnessFromMetallicTextureBlue||!material.useRoughnessFromMetallicTextureGreen||material.useRoughnessFromMetallicTextureAlpha)throw new Error('Packed channel mapping wrong');
const report={status:'passed',babylonVersion:B.Engine.Version,loader:loaderPath,
 loaderSha256:crypto.createHash('sha256').update(text).digest('hex'),
 parserMethods:names,importedMeshes:meshes.length,visibleTriangles,maxWorldBoundsError,physicsDirectChildren:phy.length,navigationMeshes:nav.length,
 optimisationGroups:chunks.map(m=>({name:m.name,visibleDescendants:m.getChildMeshes().filter(c=>manifest.visible_names.includes(c.name)).length})),
 material:{name:material.name,albedo:material.albedoTexture.name,metallic:material.metallicTexture.name},
 lightmapRequested:lm.name.slice(9)+'.ktx2',lights:lights.length,
 limits:'Actual custom parser with Babylon NullEngine; no React environment lifecycle, texture decoding, GPU rendering, physics worker or runtime visual acceptance tested.'};
fs.writeFileSync(path.join(out,'kadshow_import.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify(report,null,2));
scene.dispose();engine.dispose();
