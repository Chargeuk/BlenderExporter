// Real WebGL texture decoding and rendering with the consuming app's extracted code.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require('../tools/env-converter/node_modules/playwright-core');
async function main(){
 const [folder,code,output]=process.argv.slice(2);fs.mkdirSync(output,{recursive:true});
 const bundle=require.resolve('../tools/env-converter/node_modules/babylonjs/babylon.js');
 const server=http.createServer((req,res)=>{
  if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end('<style>body{margin:0}canvas{width:1280px;height:800px}</style><canvas width="1280" height="800"></canvas><script src="/babylon.js"></script><script src="/app.js"></script>');return;}
  let file=req.url==='/babylon.js'?bundle:req.url==='/app.js'?code:null;
  if(req.url.startsWith('/assets/')){const name=decodeURIComponent(req.url.slice(8));if(path.basename(name)===name)file=path.join(folder,name);}
  if(!file||!fs.existsSync(file)){res.statusCode=404;res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':'application/octet-stream');fs.createReadStream(file).pipe(res);
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
 try{
  browser=await chromium.launch({headless:true,args:['--enable-webgl','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:1280,height:800}}),messages=[],failures=[];
  page.on('console',m=>{console.log(m.type()+': '+m.text());if(['error','warning'].includes(m.type()))messages.push(m.text());});
  page.on('pageerror',e=>{failures.push(e.message);console.error(e.stack);});page.on('requestfailed',r=>{failures.push(r.url()+': '+r.failure().errorText);console.error(r.url(),r.failure());});
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  page.setDefaultTimeout(180000);
  const result=await page.evaluate(async()=>{
   const B=BABYLON,engine=new B.Engine(document.querySelector('canvas'),true,{preserveDrawingBuffer:true});
   const scene=new B.Scene(engine);scene.clearColor=new B.Color4(.1,.1,.1,1);
   const camera=new B.FreeCamera('review',new B.Vector3(-3.04,1.6,-3.18),scene);camera.minZ=.05;camera.fov=1.25;camera.setTarget(new B.Vector3(0,1.1,1.5));
   engine.runRenderLoop(()=>{try{scene.render();}catch(e){console.error('Render failed: '+e.stack);}});
   const raw=await(await fetch('/assets/grandBedroomDay.babylon')).text();
   const meshes=[],particles=[],skeletons=[],lights=[];
   if(!KadShowSceneLoader.ImportBabylonMeshesAndLights(null,scene,raw,'/assets/',meshes,particles,skeletons,lights,(m,e)=>{throw e||Error(m);}))throw Error('Parser failed');
   const textureErrors=[];
   console.log('Parsed room; loading lightmap');
   const lm=meshes.find(m=>m.name.startsWith('lightmap_'));
   if(!lm)throw Error('No KaDshow lightmap marker');
   const lightmap=await new Promise((resolve,reject)=>{const t=new B.Texture('/assets/'+encodeURIComponent(lm.name.slice(9))+'.ktx2',scene,undefined,undefined,undefined,()=>resolve(t),(m,e)=>reject(Error(m)));setTimeout(()=>reject(Error('Lightmap timeout')),60000);});
   lightmap.coordinatesIndex=1;
   const clones=new Map();
   for(const mesh of lm.getChildMeshes())if(mesh.material){
    const old=mesh.material;
    if(!clones.has(old)){
     const m=old.clone(old.name+' app lightmap');const plugin=new PbrLightmapMaterialPlugin(m);
     plugin.pbrLightmapTexture=lightmap;m.lightmapTexture=null;plugin.isEnabled=true;
     if(m.metallicTexture)m.metallicTexture.gammaSpace=false;clones.set(old,m);
    }mesh.material=clones.get(old);
   }
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
   const textures=scene.textures.map(t=>({name:t.name,ready:t.isReady(),size:t.getSize(),gamma:t.gammaSpace}));
   if(textures.some(t=>!t.ready))throw Error('Texture not ready');
   window.review={scene,engine,camera,meshes,sky,env,lightmap,lm};
   return {status:'passed',babylon:B.Engine.Version,webgl:engine.webGLVersion,renderer:engine.getGlInfo(),importedNodes:meshes.length,lights:lights.length,
    visibleTriangles:lm.getChildMeshes().reduce((n,m)=>n+m.getTotalIndices()/3,0),textures,materialPlugin:'actual extracted PbrLightmapMaterialPlugin',
    runtimeMaterials:[...clones.values()].map(m=>({name:m.name,metallic:m.metallic,roughness:m.roughness,backFaceCulling:m.backFaceCulling})),
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
  await page.evaluate(()=>{const r=review;r.camera.position.set(3,1.65,2.9);r.camera.setTarget(new BABYLON.Vector3(-.5,1,-1));r.scene.render();});
  await page.locator('canvas').screenshot({path:path.join(output,'room-reverse.png')});
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
  await page.evaluate(()=>{const r=review;for(const m of r.meshes)m.setEnabled(false);r.sky.setEnabled(false);r.camera.position.set(0,1.5,-6);r.camera.setTarget(new BABYLON.Vector3(0,1,0));
   for(let i=0;i<3;i++){const s=BABYLON.MeshBuilder.CreateSphere('runtime probe '+i,{diameter:1.4,segments:32},r.scene);s.position.set((i-1)*1.8,1,0);const m=new BABYLON.PBRMaterial('probe '+i,r.scene);m.albedoColor.set(.7,.7,.7);m.metallic=i?1:0;m.roughness=i===1?.05:.65;s.material=m;}r.scene.render();});
  await page.waitForTimeout(1000);await page.locator('canvas').screenshot({path:path.join(output,'runtime-ibl-probes.png')});
  result.messages=messages;result.failures=failures;
  fs.writeFileSync(path.join(output,'browser-check.json'),JSON.stringify(result,null,2));
  if(failures.length||messages.some(m=>/shader error|unable to compile|error compiling/i.test(m)))throw Error('Runtime errors: '+JSON.stringify({messages,failures}));
  console.log(JSON.stringify(result,null,2));
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
