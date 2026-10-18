# Blender 5.2 compatibility audit

This page preserves the original compatibility investigation as historical evidence; its baseline version and test date describe that investigation, not the current add-on release.

## Historical audit: 21 September 2026

Tested with Blender **5.2.2 LTS / Python 3.13.13** and the KaDshow Babylon **7.27.0** dependency. Fork baseline: `949d71f7a0d1e53aa768f3da0e6ba3c25022c99d`. The add-on was version **3.3.3** at that baseline.

### Changes recorded by that audit

- Replace removed Python `imp` imports with `importlib` in the add-on reload paths.
- Support old/current Principled names for subsurface, specular IOR level, coat, sheen and emission. Accept current colour-valued Sheen Tint.
- Recognise RGB-mode Separate Color nodes, preserving the bedroom's shared green-roughness/blue-metallic texture rather than triggering a material bake.
- Export evaluated loop triangles without a destructive bmesh triangulation round-trip. Read current corner normals, including sharp-edge and custom normals; release the evaluated temporary mesh correctly. This fixed actual normal changes on 20 bedroom objects. N-gons retain their evaluated triangulation/normal data and omit optional tangents because Blender cannot calculate tangents directly for n-gons. Tangent generation for triangle/quad meshes is retained.
- Serialize image data/colour interpretation through `gammaSpace`, so the packed RM map is explicitly linear.
- Escape JSON strings correctly and fix fractional Empty metadata serialization. Existing unsupported metadata types are still reported/omitted; this does not add a new custom-property schema.
- Return `CANCELLED` when the exporter reports a fatal/data error instead of advertising `FINISHED`.

This is a static-scene compatibility audit, not certification of every old feature. Armatures, action/shape-key animation, internal procedural baking, every shader feature and all Blender versions were not revalidated by that investigation. Socket compatibility does not guarantee identical Blender/Babylon BRDF appearance.

## Current package: 3.3.31

The current add-on ZIP is built with `python tools/package_addon.py`; install the versioned ZIP it creates and enable Babylon.js in Blender Preferences. The package retains the `babylon_js` module name. A Blender 5.2.2 factory-startup smoke confirmed add-on registration, the export operator, packaged helper imports and clean unregister; this is a startup check, not a full feature or delivery validation.

See the detailed current environment procedures in the owning Codex skills: `kadshow-blender-environments`, `kadshow-environment-export`, `kadshow-hdr-environment`, and `blender-texture-authoring`. `tests/test_blender_compat.py` remains an independent compatibility fixture. Historical bedroom experiment scripts are not current production recipes.
