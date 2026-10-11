# Blender 5.2 compatibility and bedroom export

Compatibility evidence below describes the original Blender 5.2 investigation. Use the current package and linked manuals for environment preparation/export.

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

## Install and current environment workflow

Build using `python tools/package_addon.py`; install the versioned ZIP it reports
through Blender Preferences and enable Babylon.js. Verify the running add-on
after an update; the fork keeps the `babylon_js` package name.

Use `content/environments/grandBedroomDay/blender/README.md` and
`ACCEPTED_RESULT.json` for current accepted scene/package identities. Preserve
physical PBR source materials; an emission preview is unsuitable as the physical
export source. Include explicit delivery/helper/ancestor selection and
six-decimal geometry/UV precision.

See preparation (see the `kadshow-blender-environments` Codex skill), RGBD/KTX2 (see the `kadshow-environment-export` Codex skill),
lighting (see the `kadshow-environment-export` Codex skill), geometry (see the `kadshow-environment-export` Codex skill),
skybox (see the `kadshow-environment-export` Codex skill) and ENV (see the `kadshow-environment-export` Codex skill) for current settings.
`tests/test_blender_compat.py` remains an independent compatibility fixture.
Historical bedroom experiment scripts are not current production recipes.
