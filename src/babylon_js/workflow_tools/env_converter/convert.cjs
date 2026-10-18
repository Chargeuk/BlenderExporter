#!/usr/bin/env node
// A local WebGL helper pinned to the consuming application's Babylon version.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright-core');
const VERSION = require('babylonjs/package.json').version;
const PLAYWRIGHT_VERSION = require('playwright-core/package.json').version;
const { browserPath, launchOptions } = require('./runtime.cjs');

async function main() {
  const args = process.argv.slice(2);
  const browserIndex = args.indexOf('--browser');
  if (browserIndex !== -1 && (!args[browserIndex+1] || args[browserIndex+1].startsWith('--')))
    throw Error('--browser requires an executable path');
  if (VERSION !== '7.27.0' || PLAYWRIGHT_VERSION !== '1.56.1')
    throw Error('ENV converter dependencies differ from the pinned versions; run setup.py');
  const executable = browserPath(chromium, browserIndex === -1 ? '' : args[browserIndex+1]);
  if (args.includes('--check')) {
    const result = {status:'ready',babylon:VERSION,playwright:PLAYWRIGHT_VERSION,chromium:executable};
    if (args.includes('--launch')) {
      const browser = await chromium.launch(launchOptions(executable));
      try {
        const page = await browser.newPage();
        if (!await page.evaluate(()=>!!document.createElement('canvas').getContext('webgl2')))
          throw Error('ENV converter requires a browser with WebGL2');
        result.webgl = 2;
        result.browserVersion = browser.version();
      } finally { await browser.close(); }
    }
    console.log(JSON.stringify(result));
    return;
  }
  const configPath = args[0];
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
    browser = await chromium.launch(launchOptions(executable));
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
      const materials=[],spheres=[];
      for(let i=0;i<5;i++) {
        const sphere=B.MeshBuilder.CreateSphere('IBL probe '+i,{diameter:1.25,segments:32},scene);
        sphere.position.x=(i-2)*1.55;
        const m=new B.PBRMaterial('roughness '+i,scene);
        m.albedoColor=new B.Color3(.7,.7,.7);m.metallic=i===0?0:1;m.roughness=i===0?.8:(i-1)/3;
        // Keep probe energy limited to IBL; metallic=1 removes the PBR diffuse albedo.
        m.directIntensity=0;m.emissiveIntensity=0;m.environmentIntensity=1;
        m.ambientColor=B.Color3.Black();m.emissiveColor=B.Color3.Black();
        sphere.material=m;materials.push(m);spheres.push(sphere);
      }
      scene.clearColor=new B.Color4(.08,.08,.08,1);
      scene.ambientColor=B.Color3.Black();scene.environmentIntensity=1;loaded.level=1;
      engine.setHardwareScalingLevel(1);
      const display=scene.imageProcessingConfiguration;
      display.exposure=1;display.contrast=1;display.toneMappingEnabled=false;
      display.colorGradingEnabled=false;display.vignetteEnabled=false;display.ditheringEnabled=false;
      display.applyByPostProcess=false;
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
      // Measure the saved texture's actual PBR response against the same frame with ENV absent.
      // Central, fixed-radius masks avoid the silhouettes and the nonblack preview background.
      engine.stopRenderLoop();
      const frameWidth=engine.getRenderWidth(),frameHeight=engine.getRenderHeight(),maskRadius=12;
      const probeIndices=[0,4]; // Dielectric roughness .8 and broad metallic-only specular roughness 1.
      const masks=probeIndices.map(index=>{
        const sphere=spheres[index];
        const projected=B.Vector3.Project(sphere.getAbsolutePosition(),B.Matrix.Identity(),
          scene.getTransformMatrix(),camera.viewport.toGlobal(frameWidth,frameHeight));
        if(![projected.x,projected.y,projected.z].every(Number.isFinite) || projected.z<=0 || projected.z>=1)
          throw Error('Invalid projected IBL probe '+index);
        const cx=Math.round(projected.x),cy=Math.round(projected.y),pixels=[];
        if(cx-maskRadius<0 || cy-maskRadius<0 || cx+maskRadius>=frameWidth || cy+maskRadius>=frameHeight)
          throw Error('IBL probe mask outside framebuffer '+index);
        for(let y=cy-maskRadius;y<=cy+maskRadius;y++)for(let x=cx-maskRadius;x<=cx+maskRadius;x++){
          if((x+.5-cx)**2+(y+.5-cy)**2>maskRadius**2)continue;
          const hit=scene.pick(x+.5,y+.5);
          if(!hit || !hit.hit || hit.pickedMesh!==sphere)throw Error('IBL mask includes background/other mesh '+index);
          // Vector3.Project/picking use top-left coordinates; WebGL readPixels starts at bottom-left.
          pixels.push(((frameHeight-1-y)*frameWidth+x)*4);
        }
        if(pixels.length<100)throw Error('Empty/undersized IBL probe mask '+index);
        return {index,pixels,center:[cx,cy],radius:maskRadius,geometryValidatedPixels:pixels.length};
      });
      const assertNoDirectLights=()=>{
        if(scene.lights.length!==0 || materials.some(m=>m.directIntensity!==0 || m.emissiveIntensity!==0))
          throw Error('IBL probes require zero direct lights and zero direct/emissive intensity');
      };
      const renderProbeFrame=async environment=>{
        assertNoDirectLights();scene.environmentTexture=environment;
        // Changing ENV recompiles PBR defines. Wait for the new shaders before reading the frame.
        scene.render();await scene.whenReadyAsync();
        for(let i=0;i<3;i++){scene.render();await new Promise(r=>requestAnimationFrame(r));}
        const pixels=await engine.readPixels(0,0,frameWidth,frameHeight,true,true);
        if(!(pixels instanceof Uint8Array) || pixels.length!==frameWidth*frameHeight*4)
          throw Error('Invalid IBL framebuffer readback');
        assertNoDirectLights();return pixels;
      };
      let envOn,envDisabled;
      try {
        envOn=await renderProbeFrame(loaded);
        envDisabled=await renderProbeFrame(null);
      } finally {
        // The existing five-sphere screenshot must retain the actual saved ENV.
        await renderProbeFrame(loaded);
      }
      const thresholds={minimumChannelDeltaRgb8:2,minimumContributingPixelFraction:.01,
        minimumMeanPositiveDeltaRgb8:.25,maximumBaselineChannelRgb8:1};
      const probes=masks.map(mask=>{
        const onSum=[0,0,0],offSum=[0,0,0],positiveDeltaSum=[0,0,0];
        let onMax=0,offMax=0,nonzeroOnRGB=0,contributingPixels=0,maxPositiveDelta=0;
        for(const offset of mask.pixels){
          let pixelDelta=0;
          for(let c=0;c<3;c++){
            const on=envOn[offset+c],off=envDisabled[offset+c];
            if(!Number.isFinite(on) || !Number.isFinite(off))throw Error('Non-finite IBL probe RGB');
            onSum[c]+=on;offSum[c]+=off;onMax=Math.max(onMax,on);offMax=Math.max(offMax,off);
            if(on>0)nonzeroOnRGB++;
            const delta=Math.max(0,on-off);positiveDeltaSum[c]+=delta;pixelDelta=Math.max(pixelDelta,delta);
          }
          maxPositiveDelta=Math.max(maxPositiveDelta,pixelDelta);
          if(pixelDelta>=thresholds.minimumChannelDeltaRgb8)contributingPixels++;
        }
        const count=mask.pixels.length,minimumContributingPixels=Math.max(4,Math.ceil(count*thresholds.minimumContributingPixelFraction));
        const meanPositiveDeltaRGB=positiveDeltaSum.map(v=>v/count);
        const meanPositiveDelta=meanPositiveDeltaRGB.reduce((a,b)=>a+b,0)/3;
        if(!nonzeroOnRGB || offMax>thresholds.maximumBaselineChannelRgb8 ||
          contributingPixels<minimumContributingPixels || meanPositiveDelta<thresholds.minimumMeanPositiveDeltaRgb8)
          throw Error('Missing/invalid saved-ENV contribution on '+(mask.index===0?'dielectric diffuse-dominant':'metallic-only specular')+
            ' probe: '+JSON.stringify({nonzeroOnRGB,offMax,contributingPixels,minimumContributingPixels,meanPositiveDelta}));
        const material=materials[mask.index];
        return {index:mask.index,kind:mask.index===0?'dielectric-diffuse-dominant':'metallic-only-specular',
          pureDiffuseIsolation:false,metallic:material.metallic,roughness:material.roughness,albedoRGB:material.albedoColor.asArray(),
          mask:{shape:'circle',centerTopLeft:mask.center,radiusPixels:mask.radius,pixels:count,
            geometryValidatedPixels:mask.geometryValidatedPixels},
          envOn:{meanRgb8:onSum.map(v=>v/count),maximumChannelRgb8:onMax,nonzeroRGBChannels:nonzeroOnRGB},
          envDisabled:{meanRgb8:offSum.map(v=>v/count),maximumChannelRgb8:offMax},
          contribution:{meanPositiveDeltaRgb8:meanPositiveDeltaRGB,meanAcrossRGB8:meanPositiveDelta,
            maximumPositiveChannelDeltaRgb8:maxPositiveDelta,contributingPixels,minimumContributingPixels,passed:true}};
      });
      const renderResponse={passed:true,savedEnvOnVersusDisabled:true,envRestoredForPreview:scene.environmentTexture===loaded,
        noDirectLights:{asserted:true,sceneLightCount:scene.lights.length,directIntensity:0,emissiveIntensity:0},
        frame:{width:frameWidth,height:frameHeight,format:'RGBA8',measuredChannels:'RGB only',alphaIgnored:true,backgroundExcluded:true},
        camera:{alpha:camera.alpha,beta:camera.beta,radius:camera.radius,target:camera.target.asArray(),fov:camera.fov},
        display:{exposure:display.exposure,contrast:display.contrast,toneMappingEnabled:display.toneMappingEnabled,
          colorGradingEnabled:display.colorGradingEnabled,vignetteEnabled:display.vignetteEnabled,
          ditheringEnabled:display.ditheringEnabled,applyByPostProcess:display.applyByPostProcess},
        environmentIntensity:scene.environmentIntensity,thresholds,probes,
        limitations:'RGBA8 display response, not linear radiometry. Dielectric includes its normal Fresnel specular term; it is diffuse-dominant, not pure diffuse isolation.'};
      return {status:'passed',babylon:B.Engine.Version,webgl:engine.webGLVersion,width:info.width,
        version:info.version,imageType:info.imageType,mipLevels:levels,faces:6,irradiance:info.irradiance,
        bytes:data.byteLength,reloaded:true,nonzeroRGB,validatedFaces:6,renderer:engine.getGlInfo(),lights:scene.lights.length,renderResponse};
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
