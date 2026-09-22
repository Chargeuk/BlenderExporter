# KaDshow exported lighting profile (exporter 3.3.9)

New exports carry `metadata.kadshowLighting` on each `lightmap_` node. This survives staged KTX2 conversion and the KaDshow mesh-only loader. It is separate from `kadshowLightmapEncoding`: legacy and RGBD maps can use either lighting mode. Existing files without this field always select Original, even when they already use RGBD.

```json
{
  "version": 1,
  "mode": "shadow-aware",
  "bakedIntensity": 1,
  "reflectionIntensity": 1,
  "shadowSuppression": 2,
  "fullyLitThreshold": 1,
  "highlightPreservation": 1
}
```

These are the new exporter defaults. The export dialog exposes every value and the mode (`original`, `experimental`, `shadow-aware`). Choose **Original (older bakes)** when re-exporting an old scene whose lighting has not been recalculated. Merely installing the exporter or updated client does not modify old assets. New profile metadata requires the matching KaDshow client; older clients ignore it.

The PBR export multiplier defaults are now metallic **1.0**, roughness **0.8**. They are exported as normal material properties, not added to the lighting profile or multiplied a second time in the shader. With a metallic/roughness atlas these values scale the sampled channels. Scalar-only materials retain source values scaled by these factors. Blender source materials are not modified. Existing exports retain their own material values.

## Shader controls

- Baked and reflection strength: 0â€“4.
- Shadow suppression: 0â€“8; 0 disables suppression, 1 is the base curve, higher values darken more.
- Fully lit threshold: 0.01â€“8, in decoded linear lightmap luminance. Above this threshold reflections are not suppressed. Raising it includes more areas in suppression.
- Highlight preservation: 0â€“1; preserves a limited fraction of bright reflections, with modest metallic dependence.
- Reflection suppression is capped at a multiplier of 1. HDR lightmaps do not amplify environment reflections above their normal PBR result.

The client rejects unknown profile versions/modes and invalid finite ranges, warns and uses Original for that marker rather than breaking loading. Missing numeric fields in a valid v1 profile use the v1 defaults. The exporter validates before writing and rejects invalid settings.

## Debug and production

KaDshow uses asset settings in both builds. The Lighting panel is included in development or an explicitly compiled debug client (`REACT_APP_LIGHTING_DEBUG=true`). Ordinary production builds compile it out and do not subscribe to debug overrides. There is no URL, runtime configuration or local-storage switch that enables the controls in a release.

Debug adjustments are per loaded environment instance, not global. Use the Environment dropdown to choose one. **Reset to file settings** discards that instance's temporary overrides and restores its exported mode and values. These adjustments are local to the tab and are not automatically written back to the `.babylon` file. To keep a tuning change, use the corresponding exporter controls and export again.

From the WSL KaDshow repository:

```sh
# Normal production build, no lighting UI
KADSHOW_CLIENT_DOCKER_BUILD_SCRIPT=build-docker-rgbd npm run build:summary:docker:client
# Explicit local debug build, with lighting UI
KADSHOW_CLIENT_DOCKER_BUILD_SCRIPT=build-docker-lighting-debug npm run build:summary:docker:client
KADSHOW_CLIENT_START_IMAGE=kadshow-client:lighting-debug npm run start:summary:client
```

## Evidence

Real Blender tests: `tests/test_lighting_export.py` covers direct/staged exports, defaults, operator overrides, Original selection, metadata preservation and invalid-input rejection. `tests/test_material_export.py` checks source preservation and packed/scalar material multipliers. Client tests cover schema, unmarked/RGBD-only legacy fallback, isolated overrides, reset and plugin lifecycle. Actual-code WebGL evidence is in `C:/Users/d_a_s/Documents/Codex/2026-09-15/do-x20/lighting-comparison/scoped-debug01` and `scoped-production01`.

The accepted bedroom delivery `H:/code/kadshowWeb/content/environments/grandBedroomDayRealisticV3/rgbd-test/grandBedroomDay.babylon` was updated with this profile and the 1.0/0.8 material values. Its old model and a change report are under `blender/appearanceCorrections/lightingProfile_20260922_104114`. Geometry, UVs and all companion files are unchanged. Upload the updated model; reuse its existing KTX2, Basis, ENV and thumbnail files.


## Saved Blender lighting-node defaults (3.3.10)

An authoring scene can set `scene['bjs_lighting_controls_material']` to the material containing its direct/indirect lighting group. Tag that one group-node instance with `node['bjs_lighting_controls'] = 1`. Give it unlinked float inputs `Shadow Suppression`, `Fully Lit Threshold`, and `Highlight Preservation`.

When opening the export dialog, its `invoke` method reads those three values once into the editable operator fields. Subsequent dialog edits win. Execute, check and draw do not reread the node. The `.babylon` profile always comes from the final exporter settings; nothing writes dialog overrides back into Blender's node. Opening the dialog again reads the saved node values again. The inputs describe the runtime shader and do not change Blender's emission preview or physical bake.

Scripted/background callers bypass `invoke`: pass `lighting_options` explicitly. They may call `scene_lighting_profile(context)` to start from the saved node, then override the returned dictionary. Without an association the normal exporter defaults and existing scripted arguments work as before. Missing, duplicate, linked, nonfinite and out-of-range associated controls are rejected when prefilling. An explicit headless export does not depend on the association.

The metadata remains `kadshowLighting`, so KaDshow uses its existing versioned profile parser. Older files without the metadata retain the original shader.
