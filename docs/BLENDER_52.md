# Blender 5.2 compatibility and bedroom export

This page records the initial 3.3.3 compatibility/export investigation. Version 3.3.4 adds [optional integrated KTX2 conversion](KTX2_EXPORT.md); its generated installer supersedes the 3.3.3 installer below. The initial unconverted artifact and its validation evidence remain unchanged.

Tested 2026-09-21 with Blender **5.2.2 LTS / Python 3.13.13** and the current KaDshow Babylon **7.27.0** dependency. Fork baseline: `949d71f7a0d1e53aa768f3da0e6ba3c25022c99d`. Local add-on version: **3.3.3**.

## Changes

- Replace removed Python `imp` imports with `importlib` in the add-on reload paths.
- Support old/current Principled names for subsurface, specular IOR level, coat, sheen and emission. Accept current colour-valued Sheen Tint.
- Recognise RGB-mode Separate Color nodes, preserving the bedroom's shared green-roughness/blue-metallic texture rather than triggering a material bake.
- Export evaluated loop triangles without a destructive bmesh triangulation round-trip. Read current corner normals, including sharp-edge and custom normals; release the evaluated temporary mesh correctly. This fixed actual normal changes on 20 bedroom objects. N-gons retain their evaluated triangulation/normal data and omit optional tangents because Blender cannot calculate tangents directly for n-gons. Tangent generation for triangle/quad meshes is retained.
- Serialize image data/colour interpretation through `gammaSpace`, so the packed RM map is explicitly linear.
- Escape JSON strings correctly and fix fractional Empty metadata serialization. Existing unsupported metadata types are still reported/omitted; this does not add a new custom-property schema.
- Return `CANCELLED` when the exporter reports a fatal/data error instead of advertising `FINISHED`.

This is verified static-scene compatibility, not certification of every old feature. Armatures, action/shape-key animation, internal procedural baking, every shader feature and all Blender versions have not been revalidated. Socket compatibility does not guarantee identical Blender/Babylon BRDF appearance.

## Install

Build using `python tools/package_addon.py`, then install `dist/Blender2Babylon-3.3.3-blender52.zip` through Blender Preferences → Add-ons → Install from Disk. Enable **Babylon.js**. The command appears under **File → Export → Babylon.js**. The ZIP retains the existing `babylon_js` module name and packages the repository source; the older root distribution ZIP is historical and has not been overwritten.

## Verified bedroom output

The local final export is in `artifacts/bedroom_final/`:

- `grandBedroomDay.babylon`: 633 visible meshes / 20,000 triangles, 29 helper meshes / 364 triangles and 11 empty hierarchy nodes; 673 imported nodes in Babylon's mesh array.
- One shared PBR material, a 2048-square colour PNG and a 512-square packed roughness/metallic PNG. Both original UV channels survive.
- 28 collision boxes under `physicsObjects`, one navigation surface under `navMeshFloor`, five `optimise_chunk` groups.
- `mirror8_final.exr`: latest mirrored-denoising source lightmap. KaDshow requests **mirror8_final.ktx2**, which has not been generated here.
- The stock exporter also copies the source World EXR. KaDshow's mesh/light import path does not use that top-level World texture as its environment map. See the gap report before uploading this package.
- `manifest.json`, `source_comparison.json`, `kadshow_import.json`: exact source identities, export selection and validation evidence.

Source: `H:/code/kadshowWeb/content/environments/grandBedroomDayRealisticV3/blender/bakeOrderInvestigation/isolationMirror/mirror_005_source_materials.blend`. This has the latest tested geometry and original PBR material assignments; the `_006_applied_comparison.blend` instead assigns a diagnostic colour-times-lightmap emission material and is unsuitable as the PBR export source.

`tests/export_bedroom.py` explicitly selects visible delivery meshes, collision/navigation helpers and their ancestors. It excludes retained high-resolution sources, bake-only contributors, bake-only light and review cameras. It changes the lightmap marker from the historical map stem to `lightmap_mirror8_final` **in memory only**, sets export geometry/UV precision to six decimals and verifies that the source file hash stays unchanged. It does not replace the previously accepted scene or map.

Do not blindly export every object in an authoring scene: the stock general exporter has no KaDshow role policy. Use the supplied bedroom script for this exact scene or an explicitly reviewed selection with ancestors for another scene. UI export precision defaults also differ from the tested six-decimal recipe.

## Reproduction

Use fresh output directories; failed/earlier trials are retained locally. Run from this repository. PowerShell examples:

```powershell
$blenderExe = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
$bedroomSource = 'H:\code\kadshowWeb\content\environments\grandBedroomDayRealisticV3\blender\bakeOrderInvestigation\isolationMirror\mirror_005_source_materials.blend'
& $blenderExe --background --factory-startup --python-exit-code 1 --python tests/test_blender_compat.py -- artifacts/compat_new
& $blenderExe --background --factory-startup $bedroomSource --python-exit-code 1 --python tests/export_bedroom.py -- artifacts/bedroom_new
& $blenderExe --background --factory-startup $bedroomSource --python-exit-code 1 --python tests/audit_bedroom.py -- artifacts/bedroom_new
```

For the actual current KaDshow parser, run in WSL:

```bash
node /mnt/h/code/babylonJs/BlenderExporter/tests/validate_kadshow_import.mjs \
  /home/d_a_s/code/kadshow/KaDshow_Web \
  /mnt/h/code/babylonJs/BlenderExporter/artifacts/bedroom_new
```

The parser test extracts the actual TypeScript class methods unchanged and runs them with KaDshow's installed Babylon dependency in a NullEngine. It verifies object identities, parenting, marker descendants, UV2 and PBR channel flags. It does **not** run the React environment lifecycle, decode textures on a GPU, start the physics worker, or establish in-app visual quality.

Final geometry audit: every visible object's exported position/UV corner corresponds to the evaluated source; triangle counts agree. Maximum normal component difference is **0.000000499947**, consistent with six-decimal serialization. The standalone fixture covers a modifier, n-gon/quad geometry, custom normals, two UV sets, packed RGB material, current Principled controls, quoted strings, fractional metadata and an add-on registration cycle.

Imported world-space bounds also match all 662 visible/helper meshes to within **0.000050025 scene units** (about 0.05 mm at this scene's metre scale). This compares the source's referenced faces, excluding unused vertices that are not exported. The test harness supplies same-realm JSON arrays to Babylon, matching browser execution; cross-realm arrays in the first harness attempt produced empty vertex buffers and were corrected before final validation. The final headless test verifies real populated geometry, not just declared index counts.

The final export has 31 expected warnings: creation of the texture directory, no exported active camera, and 29 helper meshes without materials. Those helpers are deliberately material-free. No data errors or unsupported-node baking were reported. The updated ZIP was installed and enabled in the user's Blender session; the empty live scene was left unchanged.

See [KaDshow contract and remaining gaps](KADSHOW_EXPORT_GAPS.md) for the decisions needed before a complete runtime-ready package.
