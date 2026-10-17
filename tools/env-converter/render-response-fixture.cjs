#!/usr/bin/env node
// Focused synthetic data/check for convert.cjs; no scene or application dependencies.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

function writeFixture(directory) {
  const root=path.resolve(directory);
  if(fs.existsSync(root))throw Error('Use a new fixture directory; existing evidence is preserved');
  fs.mkdirSync(root,{recursive:true});
  const width=64,height=32;
  const header=Buffer.from(`#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y ${height} +X ${width}\n`,'ascii');
  for(const [name,rgbe] of [['uniform',[128,64,32,128]],['black',[0,0,0,0]]]) {
    // Radiance scanline RLE: each channel is one 64-pixel run. Uniform linear RGB=.5,.25,.125.
    const rows=[];
    for(let y=0;y<height;y++)rows.push(Buffer.from([2,2,0,width,...rgbe.flatMap(v=>[128+width,v])]));
    const input=path.join(root,name+'.hdr');
    fs.writeFileSync(input,Buffer.concat([header,...rows]));
    fs.writeFileSync(path.join(root,name+'.json'),JSON.stringify({input,output:path.join(root,name+'.env'),
      report:path.join(root,name+'-report.json'),preview:path.join(root,name+'-preview.png'),
      size:128,filterSamples:64},null,2));
  }
  console.log(JSON.stringify({directory:root,positive:'uniform.json',negative:'black.json',
    expectedNegative:'Nonzero exit: Reloaded ENV has no RGB radiance'}));
}

function checkReport(filename) {
  const report=JSON.parse(fs.readFileSync(filename,'utf8'));
  assert.equal(report.status,'passed');assert.equal(report.reloaded,true);
  assert.equal(report.validatedFaces,6);assert(report.nonzeroRGB>0);
  const response=report.renderResponse;
  assert.equal(response.passed,true);assert.equal(response.savedEnvOnVersusDisabled,true);
  assert.equal(response.envRestoredForPreview,true);
  assert.equal(response.noDirectLights.asserted,true);assert.equal(response.noDirectLights.sceneLightCount,0);
  assert.equal(response.noDirectLights.directIntensity,0);assert.equal(response.noDirectLights.emissiveIntensity,0);
  assert.equal(response.frame.format,'RGBA8');assert.equal(response.frame.measuredChannels,'RGB only');
  assert.equal(response.frame.alphaIgnored,true);assert.equal(response.frame.backgroundExcluded,true);
  assert.equal(response.display.exposure,1);assert.equal(response.display.contrast,1);
  for(const setting of ['toneMappingEnabled','colorGradingEnabled','vignetteEnabled','ditheringEnabled','applyByPostProcess'])
    assert.equal(response.display[setting],false);
  assert.equal(response.probes.length,2);
  for(const [position,kind,metallic] of [[0,'dielectric-diffuse-dominant',0],[1,'metallic-only-specular',1]]) {
    const probe=response.probes[position];
    assert.equal(probe.kind,kind);assert.equal(probe.metallic,metallic);assert.equal(probe.pureDiffuseIsolation,false);
    assert.equal(probe.mask.shape,'circle');assert.equal(probe.mask.radiusPixels,12);
    assert(probe.mask.pixels>=100);assert.equal(probe.mask.geometryValidatedPixels,probe.mask.pixels);
    for(const vector of [probe.envOn.meanRgb8,probe.envDisabled.meanRgb8,probe.contribution.meanPositiveDeltaRgb8]) {
      assert.equal(vector.length,3);assert(vector.every(v=>Number.isFinite(v)&&v>=0&&v<=255));
    }
    assert(probe.envOn.nonzeroRGBChannels>0);assert(probe.envOn.maximumChannelRgb8>0);
    assert(probe.envDisabled.maximumChannelRgb8<=1);
    assert.equal(probe.contribution.passed,true);
    assert(Number.isFinite(probe.contribution.meanAcrossRGB8)&&probe.contribution.meanAcrossRGB8>=.25);
    assert(probe.contribution.maximumPositiveChannelDeltaRgb8>=2);
    assert(probe.contribution.contributingPixels>=Math.max(4,Math.ceil(probe.mask.pixels*.01)));
    // The constant positive fixture should give a substantial response, independent of acceptance thresholds.
    assert(probe.envOn.meanRgb8.some(v=>v>10),'Uniform fixture failed to light '+kind);
    assert(probe.contribution.meanPositiveDeltaRgb8.some(v=>v>10),'Uniform fixture failed ENV comparison '+kind);
  }
  console.log(JSON.stringify({status:'passed',report:path.resolve(filename),validatedResponseProbes:2}));
}

const [mode,target]=process.argv.slice(2);
if(!target || !['write','check'].includes(mode))throw Error('Usage: node render-response-fixture.cjs write NEW_DIRECTORY | check REPORT_JSON');
if(mode==='write')writeFixture(target);else checkReport(target);