#!/usr/bin/env node
// A local WebGL helper pinned to the consuming application's Babylon version.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright-core');
const VERSION = require('babylonjs/package.json').version;

async function main() {
  if (process.argv.includes('--check')) {
    const executable = process.env.BJS_ENV_CHROMIUM || chromium.executablePath();
    if (!fs.existsSync(executable)) throw Error('Chromium missing: set BJS_ENV_CHROMIUM or install Playwright Chromium');
    console.log(JSON.stringify({status:'ready',babylon:VERSION,chromium:executable}));
    return;
  }
  const configPath = process.argv[2];
  if (!configPath) throw Error('Usage: node convert.cjs config.json (or --check)');
  const config = JSON.parse(fs.readFileSync(configPath,'utf8'));
  if (![128,256,512,1024].includes(config.size)) throw Error('Unsupported ENV face size');
  if (!fs.existsSync(config.input)) throw Error('Input HDR missing');
  if (fs.existsSync(config.output)) throw Error('Output already exists; use a new staging path');
  const bundle = require.resolve('babylonjs/babylon.js');
  const server = http.createServer((req,res)=>{
    const files = {'/babylon.js':bundle,'/input.hdr':config.input,'/output.env':config.output};
    if(req.url === '/') {res.setHeader('Content-Type','text/html');res.end('<canvas id="c" width="900" height="450"></canvas><script src="/babylon.js"></script>');return;}
    const file = files[req.url];
    if(!file || !fs.existsSync(file)) {res.statusCode=404;res.end();return;}
    res.setHeader('Content-Type',req.url.endsWith('.js')?'application/javascript':'application/octet-stream');
    fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    browser = await chromium.launch({headless:true,executablePath:process.env.BJS_ENV_CHROMIUM || chromium.executablePath(),
      args:['--enable-webgl','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
    const page=await browser.newPage({viewport:{width:920,height:480}});
    const messages=[];
    page.on('console',m=>messages.push(m.type()+': '+m.text()));
    page.on('pageerror',e=>messages.push('pageerror: '+e.message));
    await page.exposeFunction('saveEnv',data=>fs.writeFileSync(config.output,Buffer.from(data,'base64')));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const result=await page.evaluate(async cfg=>{
      const B=BABYLON,canvas=document.querySelector('canvas');
      const engine=new B.Engine(canvas,false,{premultipliedAlpha:false,preserveDrawingBuffer:true,disableWebGL2Support:false});
      if(engine.webGLVersion!==2) throw Error('WebGL2 required for HDR prefiltering');
      const scene=new B.Scene(engine);
      const camera=new B.ArcRotateCamera('inspection',-Math.PI/2,Math.PI/2.2,8,B.Vector3.Zero(),scene);
      engine.runRenderLoop(()=>scene.render());
      const texture=await new Promise((resolve,reject)=>{
        const t=new B.HDRCubeTexture('/input.hdr',scene,cfg.size,false,true,false,false,
          ()=>resolve(t),(message,error)=>reject(Error(message+': '+error)));
      });
      const filter=new B.HDRFiltering(engine,{quality:cfg.filterSamples || 256});
      await filter.prefilter(texture);
      const data=await B.EnvironmentTextureTools.CreateEnvTextureAsync(texture,{imageType:'image/png'});
      const bytes=new Uint8Array(data);
      let text='';for(let i=0;i<bytes.length;i+=32768)text+=String.fromCharCode(...bytes.subarray(i,i+32768));
      await window.saveEnv(btoa(text));
      // Decode the actual saved ENV, rather than only validating the source cubemap.
      const loaded=await new Promise((resolve,reject)=>{
        const t=B.CubeTexture.CreateFromPrefilteredData('/output.env',scene);
        if(t.isReady())resolve(t);else t.onLoadObservable.addOnce(()=>resolve(t));
        setTimeout(()=>reject(Error('ENV reload timeout')),120000);
      });
      scene.environmentTexture=loaded;
      const materials=[];
      for(let i=0;i<5;i++) {
        const sphere=B.MeshBuilder.CreateSphere('IBL probe '+i,{diameter:1.25,segments:32},scene);
        sphere.position.x=(i-2)*1.55;
        const m=new B.PBRMaterial('roughness '+i,scene);
        m.albedoColor=new B.Color3(.7,.7,.7);m.metallic=i===0?0:1;m.roughness=i===0?.8:(i-1)/3;
        sphere.material=m;materials.push(m);
      }
      scene.clearColor=new B.Color4(.08,.08,.08,1);
      await scene.whenReadyAsync();
      for(let i=0;i<3;i++){scene.render();await new Promise(r=>requestAnimationFrame(r));}
      const info=B.EnvironmentTextureTools.GetEnvInfo(bytes);
      const values=Object.values(info.irradiance).flat();
      if(values.length!==27 || values.some(v=>!Number.isFinite(v)))throw Error('Invalid irradiance coefficients');
      const levels=Math.log2(cfg.size)+1;
      if(info.width!==cfg.size || info.specular.mipmaps.length!==6*levels)throw Error('Incorrect ENV mip layout');
      let nonzeroRGB=0;
      for(let face=0;face<6;face++){
        const sample=await loaded.readPixels(face,0);
        if(!sample || sample.length!==cfg.size*cfg.size*4)throw Error('Cannot read ENV face '+face);
        for(let i=0;i<sample.length;i+=4)for(let c=0;c<3;c++){
          if(!Number.isFinite(sample[i+c]))throw Error('Non-finite ENV radiance');
          if(sample[i+c]>0)nonzeroRGB++;
        }
      }
      if(!nonzeroRGB)throw Error('Reloaded ENV has no RGB radiance');
      return {status:'passed',babylon:B.Engine.Version,webgl:engine.webGLVersion,width:info.width,
        version:info.version,imageType:info.imageType,mipLevels:levels,faces:6,irradiance:info.irradiance,
        bytes:data.byteLength,reloaded:true,nonzeroRGB,validatedFaces:6,renderer:engine.getGlInfo(),lights:scene.lights.length};
    },config);
    if(config.preview) await page.locator('canvas').screenshot({path:config.preview});
    result.messages=messages;
    fs.writeFileSync(config.report,JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  } finally {
    if(browser)await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
}
main().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
