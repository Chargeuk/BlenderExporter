/** CPU NullEngine validation against the actual target client's Babylon package. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';

const args = Object.fromEntries(Array.from({ length: (process.argv.length - 2) / 2 }, (_, i) =>
  [process.argv[2 + i * 2], process.argv[3 + i * 2]]));
for (const key of ['--babylon-core', '--fixture', '--output']) assert(args[key], key);
const root = path.resolve(args['--babylon-core']);
const output = path.resolve(args['--output']);
assert(!fs.existsSync(output), 'Fresh receipt required');
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const packageInfo = JSON.parse(fs.readFileSync(path.join(root, 'package.json')));
assert.equal(packageInfo.version, '7.27.0', 'Use actual KaDshow client 7.27.0');
const { NullEngine, Scene, PBRMaterial, StandardMaterial, MeshBuilder, FreeCamera, Vector3 } =
  await import(pathToFileURL(path.join(root, 'index.js')).href);
const payload = JSON.parse(fs.readFileSync(args['--fixture']));
const byName = Object.fromEntries(payload.materials.map(m => [m.name, m]));
const engine = new NullEngine({ renderWidth: 32, renderHeight: 32, textureSize: 32 });
const scene = new Scene(engine);
const camera = new FreeCamera('camera', new Vector3(0, 0, -4), scene);
camera.setTarget(Vector3.Zero());
scene.activeCamera = camera;
// Manual material binding still needs the normal frame's real camera matrices.
// A camera constructor alone does not initialize Scene's cached matrices.
scene.updateTransformMatrix(true);
for (const matrix of [scene.getViewMatrix(), scene.getProjectionMatrix(), scene.getTransformMatrix()]) {
  assert(matrix && Number.isFinite(matrix.updateFlag), 'Normal frame camera matrix is uninitialized');
  assert(matrix.m.every(Number.isFinite), 'Camera matrix contains nonfinite values');
}
const close = (a, b, label) => assert(Math.abs(a - b) < 1e-7, `${label}: ${a} != ${b}`);
const rows = [];
try {
  for (const [name, ior] of [['water', 1.333], ['default', 1.5], ['glass', 1.45], ['sss', 1.333], ['sss_default', 1.5]]) {
    assert(byName[name], `Missing real Blender export fixture ${name}`);
    const material = PBRMaterial.Parse(byName[name], scene, '');
    close(material.indexOfRefraction, ior, name + ' public IOR');
    close(material.subSurface.indexOfRefraction, ior, name + ' nested IOR');
    close(material.alpha, name === 'water' ? .86 : name === 'glass' ? .12 : name === 'sss' ? .7 : name === 'default' ? 1 : .86, name + ' alpha');
    const isSSS = name.startsWith('sss');
    assert.equal(material.subSurface.isTranslucencyEnabled, isSSS);
    assert.equal(material.subSurface.isRefractionEnabled, false);
    assert.equal(material.subSurface.isScatteringEnabled, false);
    if (isSSS) {
      close(material.subSurface.translucencyIntensity, name === 'sss' ? .25 : .2, name + ' translucency');
      if (name === 'sss') for (const [key, value] of [['r', .2], ['g', .4], ['b', .6]]) close(material.subSurface.tintColor[key], value, 'SSS tint ' + key);
    }
    const f0 = ((material.subSurface.indexOfRefraction - 1) / (material.subSurface.indexOfRefraction + 1)) ** 2;
    const row = { name, IOR: material.indexOfRefraction, derived_dielectric_F0: f0,
      refraction: material.subSurface.isRefractionEnabled, translucency: material.subSurface.isTranslucencyEnabled,
      scattering: material.subSurface.isScatteringEnabled, alpha: material.alpha };
    // Observe the real PBR binding calculation, rather than merely restating its equation.
    // NullEngine performs CPU-only shader/material setup; no browser/WebGL/GPU is used.
    if (!isSSS) {
      const mesh = MeshBuilder.CreateBox(name + '_probe', { size: 1 }, scene);
      mesh.material = material;
      const updates = [];
      const buffer = material._uniformBuffer;
      const original = buffer.updateColor4;
      buffer.updateColor4 = function (uniform, colour, alpha, ...rest) {
        if (uniform === 'vMetallicReflectanceFactors') updates.push([colour.r, colour.g, colour.b, alpha]);
        return original.call(this, uniform, colour, alpha, ...rest);
      };
      let timer;
      try {
        await Promise.race([
          material.forceCompilationAsync(mesh),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('NullEngine compilation timeout: ' + name)), 15000); })
        ]);
        assert(material.isReadyForSubMesh(mesh, mesh.subMeshes[0]), 'Material not ready: ' + name);
        material.bindForSubMesh(mesh.computeWorldMatrix(true), mesh, mesh.subMeshes[0]);
        assert(updates.length > 0, 'PBR binding did not write dielectric reflectance: ' + name);
        const observed = updates.at(-1);
        for (let c = 0; c < 3; ++c) close(observed[c], f0, name + ' actual bound F0');
        close(observed[3], 1, name + ' default F90/F0 factor');
        row.actual_bound_metallic_reflectance_factors = observed;
      } finally {
        clearTimeout(timer);
        buffer.updateColor4 = original;
        mesh.dispose();
      }
    }
    rows.push(row);
    material.dispose();
  }
  close(rows.find(r => r.name === 'default').derived_dielectric_F0, .04, 'default F0');
  const waterF0 = rows.find(r => r.name === 'water').derived_dielectric_F0;
  assert(waterF0 > .0203 && waterF0 < .0204, 'Water F0 must be approximately .02037');
  const standard = StandardMaterial.Parse(byName.standard, scene, '');
  close(standard.indexOfRefraction, byName.standard.indexOfRefraction, 'Legacy Standard parsed reciprocal IOR');
  standard.dispose();
  const receipt = { passed: true, client_core_root: root, Babylon_version: packageInfo.version,
    fixture: path.resolve(args['--fixture']), fixture_sha256: hash(args['--fixture']),
    method: 'Actual PBRMaterial.Parse plus actual PBR bindForSubMesh UniformBuffer observation under CPU NullEngine. Exported JSON is not modified.',
    rendered_GPU: false, rows, Standard_unchanged: true,
    camera_frame_setup: 'Explicit active FreeCamera and Scene.updateTransformMatrix(true), finite actual view/projection/transform matrices before manual PBR bind.',
    client_sources: Object.fromEntries(['package.json', 'scene.js', 'Materials/material.js', 'Materials/PBR/pbrMaterial.js', 'Materials/PBR/pbrBaseMaterial.js'].map(p => [p, hash(path.join(root, p))])) };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(receipt, null, 2));
  console.log(JSON.stringify({ passed: true, parsed_materials: rows.length + 1, actual_F0_bindings: 3, receipt: output }));
} finally {
  scene.dispose();
  engine.dispose();
}
