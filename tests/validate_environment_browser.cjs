// Real WebGL texture decoding and rendering with the consuming app's extracted code.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require("H:/code/babylonJs/BlenderExporter/tools/env-converter/node_modules/playwright-core");
async function main(){
 const [folder,code,output]=process.argv.slice(2);fs.mkdirSync(output,{recursive:true}); const modelFiles=fs.readdirSync(folder).filter(n=>n.endsWith('.babylon'));if(modelFiles.length!==1)throw Error('Expected exactly one Babylon model');const modelName=modelFiles[0];
 const bundle=require.resolve("H:/code/babylonJs/BlenderExporter/tools/env-converter/node_modules/babylonjs/babylon.js");
 const server=http.createServer((req,res)=>{
  if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end('<style>body{margin:0}canvas{width:1200px;height:780px}</style><canvas width="1200" height="780"></canvas><script src="/babylon.js"></script><script src="/lodash.js"></script><script src="/app.js"></script>');return;}
  let file=req.url==='/babylon.js'?bundle:req.url==='/app.js'?code:req.url==='/lodash.js'?"H:/code/kadshowWeb/fe/client/node_modules/lodash/lodash.js":null;
  if(req.url==='/model-name.json'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(modelName));return;} if(req.url==='/expected-bounds.json')file=path.join(folder,'blender/evidence/source_comparison.json');
  if(req.url==='/export-manifest.json')file=path.join(folder,'blender/evidence/export_manifest.json');
  if(req.url.startsWith('/assets/')){const name=decodeURIComponent(req.url.slice(8));if(path.basename(name)===name)file=path.join(folder,name);}
  if(!file||!fs.existsSync(file)){res.statusCode=404;res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':'application/octet-stream');fs.createReadStream(file).pipe(res);
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
 try{
  browser=await chromium.launch({headless:true,args:['--enable-webgl','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:1200,height:780}}),messages=[],failures=[];
  page.on('console',m=>{console.log(m.type()+': '+m.text());if(['error','warning'].includes(m.type()))messages.push(m.text());});
  page.on('pageerror',e=>{failures.push(e.message);console.error(e.stack);});page.on('requestfailed',r=>{failures.push(r.url()+': '+r.failure().errorText);console.error(r.url(),r.failure());});
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  page.setDefaultTimeout(180000);
  const result=await page.evaluate(async()=>{
   const B=BABYLON,engine=new B.Engine(document.querySelector('canvas'),true,{preserveDrawingBuffer:true});
   const scene=new B.Scene(engine);scene.clearColor=new B.Color4(.1,.1,.1,1);
   const camera=new B.FreeCamera('review',new B.Vector3(4.8,1.7,-5.1),scene);camera.minZ=.05;camera.fov=1.25;camera.setTarget(new B.Vector3(-1,1.2,2.5));
   engine.runRenderLoop(()=>{try{scene.render();}catch(e){console.error('Render failed: '+e.stack);}});
   const raw=await(await fetch('/assets/'+await(await fetch('/model-name.json')).json())).text();
   const meshes=[],particles=[],skeletons=[],lights=[];
   if(!KadShowSceneLoader.ImportBabylonMeshesAndLights(null,scene,raw,'/assets/',meshes,particles,skeletons,lights,(m,e)=>{throw e||Error(m);}))throw Error('Parser failed');
   lights.forEach(applyCurrentLightChannels);
   const sourceManifest=await(await fetch('/export-manifest.json')).json();
   const textureErrors=[];
   console.log('Parsed room; loading lightmap');
   const lm=meshes.find(m=>m.name.startsWith('lightmap_'));
   if(!lm)throw Error('No KaDshow lightmap marker');
   const freshMaterials=[...new Set(lm.getChildMeshes().map(m=>m.material).filter(Boolean))];
   for(const m of freshMaterials)if(m.transparencyMode!==0||m.alpha!==1||m.needAlphaBlending()||m.needAlphaTesting())throw Error('Fresh-load opaque classification failed: '+m.name);
   const freshOpaqueClassification=freshMaterials.map(m=>({name:m.name,mode:m.transparencyMode,alpha:m.alpha,blending:m.needAlphaBlending(),testing:m.needAlphaTesting()}));
   const encoding=getLightmapEncoding(lm.metadata);
   const lightmap=await new Promise((resolve,reject)=>{const t=new B.Texture('/assets/'+encodeURIComponent(extractLightmapName(lm))+'.ktx2',scene,undefined,undefined,undefined,()=>resolve(t),(m,e)=>reject(Error(m)));setTimeout(()=>reject(Error('Lightmap timeout')),60000);});
   configureLightmapEncoding(lightmap,encoding,lm.metadata); lightmap.metadata={...lightmap.metadata,kadshowLighting:getLightingSettings(lm.metadata)};
   if(encoding==='rgbd-v1'&&lightmap.getInternalTexture()._useSRGBBuffer)throw Error('RGBD must use UNORM without hardware sRGB decode');
   lightmap.coordinatesIndex=1;
   const adjustment=getColourAdjustment(lm.metadata); let colourTexture=null;
   if(adjustment){colourTexture=await loadColourAdjustment('/assets/'+adjustment.texture,scene);lightmap.metadata={...lightmap.metadata,kadshowColourAdjustmentTexture:colourTexture,kadshowColourAdjustmentStrength:adjustment.strength};}

   const clones=new Map(),standardLookup=new Map(),assignmentLookup=new Map();
   for(const mesh of lm.getChildMeshes())assignLightmapToMeshMaterial(mesh,lightmap,assignmentLookup);
   for(const mesh of lm.getChildMeshes())convertToStandardMaterial(mesh,standardLookup,clones);
   for(const m of clones.values())if(m.transparencyMode!==0||m.alpha!==1)throw Error('Opaque client conversion changed classification');
   for(const name of ['physicsObjects','navMeshFloor']){const p=meshes.find(m=>m.name===name);if(p)for(const m of p.getChildMeshes())m.isVisible=false;}
   async function cube(url){return await new Promise((resolve,reject)=>{const t=B.CubeTexture.CreateFromPrefilteredData(url,scene);t.onLoadObservable.addOnce(()=>resolve(t));setTimeout(()=>reject(Error('Timeout '+url)),90000);});}
   console.log('Lightmap loaded; loading ENV');
   const env=await cube('/assets/environment.env');scene.environmentTexture=env;
   console.log('ENV loaded; loading Basis');
   const skyTexture=await cube('/assets/cubemap.basis');skyTexture.sphericalPolynomial=new B.SphericalPolynomial();skyTexture.gammaSpace=true;
   console.log('Basis loaded');
   const sky=scene.createDefaultSkybox(skyTexture,false,1000,0,false);
   await scene.whenReadyAsync();for(const m of clones.values())await m.forceCompilationAsync(lm.getChildMeshes().find(x=>x.material===m));
   for(let i=0;i<3;i++){scene.render();await new Promise(r=>requestAnimationFrame(r));}
   const expected=(await(await fetch('/expected-bounds.json')).json()).world_bounds;let boundsMaxError=0;
   for(const mesh of meshes)if(expected[mesh.name]){const world=mesh.computeWorldMatrix(true),positions=mesh.getVerticesData(B.VertexBuffer.PositionKind),lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(let k=0;k<positions.length;k+=3){const q=B.Vector3.TransformCoordinates(B.Vector3.FromArray(positions,k),world).asArray();for(let i=0;i<3;i++){lo[i]=Math.min(lo[i],q[i]);hi[i]=Math.max(hi[i],q[i]);}}for(const [key,actual] of [['min',lo],['max',hi]])for(let i=0;i<3;i++)boundsMaxError=Math.max(boundsMaxError,Math.abs(actual[i]-expected[mesh.name][key][i]));}
   // Native exporter rounds object rotations separately from six-decimal mesh coordinates; allow0.1mm world-space rounding.
   if(boundsMaxError>0.0001)throw Error('Exported world geometry bounds differ: '+boundsMaxError);
   const sourceTriangles=meshes.reduce((n,m)=>n+(m.material&&m.isVisible?m.getTotalIndices()/3:0),0);
   if(sourceTriangles!==sourceManifest.visible_triangles)throw Error('Imported visible triangle count differs from exact saved source');
   meshes.forEach(applyCurrentChunkMetadata);
   const delivery=meshes.filter(m=>m.material&&m.isVisible&&m.getTotalVertices()>2);
   // Environment.tsx:602 first merges compatible materials/layouts, then :652 merges chunks.
   const similar=mergeSimilarMeshes(delivery);
   const merged=mergeOptimisedChunkMeshes(similar);
   for(const m of merged)if(m.metadata?.alwaysSelectAsActiveMesh)m.alwaysSelectAsActiveMesh=true;
   for(const light of lights)if(light.name.toLowerCase().includes('noenvironment'))for(const m of merged){light._excludedMeshesIds.push(m.id);light.excludedMeshes.push(m);}
   const mergedTriangles=merged.reduce((n,m)=>n+m.getTotalIndices()/3,0);
   if(merged.length!==sourceManifest.expected_groups)throw Error('Unexpected visible merged groups, got'+merged.length);
   if(mergedTriangles!==sourceTriangles)throw Error('Merge triangle preservation failed');
   if(mergedTriangles!==sourceManifest.visible_triangles)throw Error('Merged count differs from exact saved source');
   if(!merged.find(m=>m.name.includes('MainArea'))?.alwaysSelectAsActiveMesh)throw Error('Main chunk not always active');
   if(lights.length!==1||!lights[0].name.includes('noenvironment')||merged.some(m=>lights[0].canAffectMesh(m)))throw Error('Runtime light environment exclusion failed');
   const detachedProbe=B.MeshBuilder.CreateBox('character recipient probe',{size:.1},scene);detachedProbe.isVisible=false;
   if(!lights[0].canAffectMesh(detachedProbe))throw Error('Runtime light cannot reach movable probe');detachedProbe.dispose();
   const mergeReport=merged.map(m=>({name:m.name,triangles:m.getTotalIndices()/3,alwaysActive:m.alwaysSelectAsActiveMesh,submeshes:m.subMeshes.length}));
   if(merged.some(m=>m.subMeshes.length!==1))throw Error('Expected one submesh per compatible material/chunk');
   if(merged.filter(m=>m.alwaysSelectAsActiveMesh).length!==sourceManifest.expected_always_active)throw Error('Always-active flag escaped main area');
   async function framePixels(){await scene.whenReadyAsync();for(let i=0;i<4;i++){scene.render();await new Promise(r=>requestAnimationFrame(r));}return await engine.readPixels(0,0,engine.getRenderWidth(),engine.getRenderHeight());}
   const difference=(a,b)=>{let maximum=0,changed=0;for(let i=0;i<a.length;i++){const d=Math.abs(a[i]-b[i]);maximum=Math.max(maximum,d);if(d)changed++;}return {maximum,changedChannels:changed};};
   lights[0].setEnabled(false);const envOff=await framePixels();lights[0].setEnabled(true);const envOn=await framePixels();const environmentToggle=difference(envOff,envOn);
   if(environmentToggle.maximum>1)throw Error('Character light changed excluded environment pixels');
   const recipient=B.MeshBuilder.CreateSphere('visible movable light probe',{diameter:1,segments:24},scene);recipient.position.set(2.4,1.1,-2.1);const recipientMaterial=new B.PBRMaterial('movable probe material',scene);recipientMaterial.albedoColor.set(.75,.75,.75);recipientMaterial.metallic=0;recipientMaterial.roughness=.55;recipient.material=recipientMaterial;
   lights[0].setEnabled(false);const probeOff=await framePixels();lights[0].setEnabled(true);const probeOn=await framePixels();const recipientToggle=difference(probeOff,probeOn);
   if(recipientToggle.maximum===0)throw Error('Movable object did not respond to character light');recipient.dispose();recipientMaterial.dispose();await framePixels();
   const textures=scene.textures.map(t=>({name:t.name,ready:t.isReady(),size:t.getSize(),gamma:t.gammaSpace}));
   if(textures.some(t=>!t.ready))throw Error('Texture not ready');
   window.review={scene,engine,camera,meshes:merged,sky,env,lightmap,lm,lights};
   return {status:'passed',babylon:B.Engine.Version,webgl:engine.webGLVersion,renderer:engine.getGlInfo(),importedNodes:meshes.length,lights:lights.length,
    visibleTriangles:mergedTriangles,boundsMaxError,boundsToleranceMetres:0.0001,mergeReport,freshOpaqueClassification,environmentToggle,recipientToggle,runtimeLightExclusion:true,textures,materialPlugin:'actual extracted PbrLightmapMaterialPlugin',
    colourAdjustment:adjustment, colourAdjustmentReady:colourTexture?.isReady()??null,lightmapEncoding:encoding,lightmapHardwareSrgb:lightmap.getInternalTexture()._useSRGBBuffer,
    runtimeMaterials:[...clones.values()].map(m=>({name:m.name,metallic:m.metallic,roughness:m.roughness,backFaceCulling:m.backFaceCulling,transparencyMode:m.transparencyMode,alphaBlending:m.needAlphaBlending()})),
    limits:'WebGL harness with actual app parser/plugin; React lifecycle and physics worker not exercised'};
  });
  await page.locator('canvas').screenshot({path:path.join(output,'room-arrival.png')});
  if(process.env.DIAGNOSE_LIGHTMAP){
   await page.evaluate(()=>{const r=review;const m=new BABYLON.StandardMaterial('diagnostic lightmap',r.scene);m.disableLighting=true;m.emissiveTexture=r.lightmap;for(const mesh of r.lm.getChildMeshes()){mesh._savedMaterial=mesh.material;mesh.material=m;}r.scene.render();});
   await page.waitForTimeout(500);await page.locator('canvas').screenshot({path:path.join(output,'diagnostic-lightmap.png')});
   await page.evaluate(()=>{for(const m of review.lm.getChildMeshes()){const uv=m.getVerticesData('uv2');if(uv){for(let i=1;i<uv.length;i+=2)uv[i]=1-uv[i];m.setVerticesData('uv2',uv);}}review.scene.render();});
   await page.waitForTimeout(500);await page.locator('canvas').screenshot({path:path.join(output,'diagnostic-lightmap-flipped.png')});
   await page.evaluate(()=>{for(const m of review.lm.getChildMeshes()){const uv=m.getVerticesData('uv2');if(uv){for(let i=1;i<uv.length;i+=2)uv[i]=1-uv[i];m.setVerticesData('uv2',uv);}m.material=m._savedMaterial;}review.scene.render();});
  }
  await page.evaluate(()=>{const r=review;r.camera.position.set(-4.6,1.65,4.9);r.camera.setTarget(new BABYLON.Vector3(1,1.1,-2));r.scene.render();});
  await page.locator('canvas').screenshot({path:path.join(output,'room-reverse.png')});
  for(const v of [{name:'pool',position:[-.8,1.55,-2.2],target:[-2.7222774,.6369188,-.085495],fov:.62199656},{name:'darts',position:[2.2,1.72,-2.35],target:[4.994731,1.47698,-1.286788],fov:.70898829},{name:'bar-details',position:[2.7,1.65,2.3],target:[2.226028,.82055,5.143829],fov:.67153096}]){
   await page.evaluate(v=>{const r=review;r.camera.position.set(...v.position);r.camera.setTarget(new BABYLON.Vector3(...v.target));r.camera.fov=v.fov;r.scene.render();},v);
   await page.locator('canvas').screenshot({path:path.join(output,'room-'+v.name+'.png')});
  }
  await page.evaluate(()=>{review.camera.fov=1.25;});
  if(process.env.REVIEW_CHAIR){
   const views=[['front',[-1.65,1.1,-1.5],[-2.8,.55,-.17]],['back',[-1.55,1.1,1.5],[-2.8,.6,-.17]]];
   for(const [name,position,target] of views){
    await page.evaluate(({position,target})=>{const r=review;r.camera.fov=2*Math.atan(.4);r.camera.position.set(...position);r.camera.setTarget(new BABYLON.Vector3(...target));r.scene.render();},{position,target});
    await page.locator('canvas').screenshot({path:path.join(output,'chair-'+name+'.png')});
   }
   await page.evaluate(()=>{review.camera.fov=1.25;});
  }
  if(process.env.REVIEW_EXTERIOR){
   const views=[['east',[11,1.8,0]],['west',[-11,1.8,0]],['north',[0,1.8,10]],['south',[0,1.8,-10]],['above',[0,13,0]],['below',[0,-10,0]]];
   for(const [name,position] of views){
    await page.evaluate(({name,position})=>{const r=review;r.sky.setEnabled(false);r.camera.position.set(...position);r.camera.upVector.set(0,['above','below'].includes(name)?0:1,['above','below'].includes(name)?1:0);r.camera.setTarget(new BABYLON.Vector3(0,1.8,0));r.scene.render();},{name,position});
    await page.locator('canvas').screenshot({path:path.join(output,'exterior-'+name+'.png')});
   }
   await page.evaluate(()=>{review.camera.upVector.set(0,1,0);});
   result.exteriorViews=views.map(([name])=>name);
  }
  const sourceViews=JSON.parse(fs.readFileSync(path.join(folder,'blender/evidence/review-cameras.json'),'utf8'));
  for(const v of sourceViews){
   await page.evaluate(v=>{const r=review;r.camera.position.set(...v.position);r.camera.setTarget(new BABYLON.Vector3(...v.target));r.camera.fov=v.fov;r.scene.render();},v);
   await page.waitForTimeout(150);await page.locator('canvas').screenshot({path:path.join(output,'source-'+v.name+'.png')});
   await page.evaluate(()=>{const r=review;r.scene.environmentTexture=null;r.lights.forEach(l=>l.setEnabled(false));r.scene.render();});
   await page.waitForTimeout(150);await page.locator('canvas').screenshot({path:path.join(output,'baked-only-'+v.name+'.png')});
   await page.evaluate(()=>{const r=review;r.scene.environmentTexture=r.env;r.lights.forEach(l=>l.setEnabled(true));r.scene.render();});
  }
  await page.evaluate(()=>{const r=review;for(const m of r.meshes)m.setEnabled(false);for(const light of r.scene.lights)light.setEnabled(false);r.sky.setEnabled(false);r.camera.position.set(0,1.5,-6);r.camera.setTarget(new BABYLON.Vector3(0,1,0));
   for(let i=0;i<3;i++){const s=BABYLON.MeshBuilder.CreateSphere('runtime probe '+i,{diameter:1.4,segments:32},r.scene);s.position.set((i-1)*1.8,1,0);const m=new BABYLON.PBRMaterial('probe '+i,r.scene);m.albedoColor.set(.7,.7,.7);m.metallic=i?1:0;m.roughness=i===1?.05:.65;s.material=m;}r.scene.render();});
  await page.waitForTimeout(1000);await page.locator('canvas').screenshot({path:path.join(output,'runtime-ibl-probes.png')});
  result.messages=messages;result.failures=failures;
  fs.writeFileSync(path.join(output,'browser-check.json'),JSON.stringify(result,null,2));
  if(failures.length||messages.some(m=>/shader error|unable to compile|error compiling/i.test(m)))throw Error('Runtime errors: '+JSON.stringify({messages,failures}));
  console.log(JSON.stringify(result,null,2));
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
