# Room HDR to KaDshow ENV (3.3.6)

**Export KaDshow environment lighting** converts a saved, linear 2:1 EXR/HDR room capture to `environment.env`. It adds an empty `hasenv` marker in the delivered JSON. It does not render Blender or substitute the outdoor World image for a room capture.

## Setup

The add-on ZIP contains the Python adapter. The WebGL converter is installed separately from this repository:

```powershell
cd H:\code\babylonJs\BlenderExporter\tools\env-converter
npm ci
npx playwright-core install chromium
node convert.cjs --check
```

Node.js must be on PATH. The helper pins Babylon **7.27.0**, matching the tested KaDshow consumer, and Playwright Core 1.56.1. Set the add-on preference **ENV converter script** to this folder's `convert.cjs`, or select the script in the export dialog. `BJS_ENV_CHROMIUM` can select an existing compatible Chromium executable. No browser window or hosted IBL service is needed; the helper serves its inputs only on loopback and closes the browser/server on completion. Integrated Linux Blender export has not been exercised.

## Controls

| Setting | Default / meaning |
| --- | --- |
| Export KaDshow environment lighting | Off; independent of KTX2 and Basis options |
| Room HDR capture | Explicit file, or World custom property `bjs_environment_image`, then image named `KaDshow_EnvironmentHDR` |
| ENV face size | **512**; selectable 128/256/512/1024 |
| ENV exposure | 0 stops; linear multiplier before filtering |
| Highlight gain | 1 (neutral); selectable up to 16 |
| Highlight threshold | 1 in exposed linear luminance; must be positive |

Highlight gain ramps smoothly from no boost at the threshold to full gain at twice the threshold. RGB channels receive the same multiplier. For example, gain 2 / threshold 0.15 boosts the brightest areas of a dim indoor capture; this is a scene-specific adjustment, not a universal preset. Preserve the original EXR. No display transform, PNG conversion, [0,1] clipping or AgX is applied to ENV radiance.

512 was selected from four of five inspected existing KaDshow ENV assets; the separate visible skybox defaults to 1024. These are independent choices.

## Source capture contract

Finish scene authoring, optimisation, UVs, baking and helper reviews first. Use original PBR materials with the real World and required bake lights, not colour-times-lightmap emission preview materials. Put a panoramic camera in clear, representative occupied space. Keep required transport contributors while excluding collision/navigation meshes and duplicate retained sources. Render a versioned 2:1 scene-linear EXR. A render-setting display exposure does not constitute an HDR radiance boost.

The tested capture camera uses Blender XYZ Euler `(pi/2, 0, 0)`, Z up, centre looking +Y; the exported scene maps Blender `(x,y,z)` to Babylon `(x,z,y)`. Arbitrarily rotated capture cameras require alignment checks. World-only captures can reproduce World mapping/strength for a matching visible skybox; the existing Basis converter uses **360 degrees** for this canonical rendered panorama, rather than its legacy 180-degree default for raw source panoramas. Check the actual application's orientation before visual acceptance.

Capture freshness is an authoring responsibility. The exporter records the source file hash; it cannot determine whether an EXR predates a scene edit. Store scene/material/lighting references with the capture and regenerate after changes that affect illumination or reflections.

## Conversion and evidence

1. Read linear EXR/HDR, preserve source hash, apply explicit exposure/highlight adjustment to a derived RGBE HDR.
2. Load into Babylon HDRCubeTexture, generate irradiance, and run HDRFiltering (256 samples) for roughness-dependent specular mip levels.
3. Write version-2 PNG-based `.env` with EnvironmentTextureTools. No newer diffuse-texture extension is required by the 7.27 consumer.
4. Reload the actual saved ENV in WebGL2; inspect nonempty radiance, finite irradiance coefficients, six faces at every mip, selected size and PNG payloads. Render diffuse/glossy/rough probes with no direct lights.
5. Publish only after validation. Existing package files are backed up; conversion failure retains previous delivery. No source `.blend` save occurs.

Evidence is under `.bjs-ktx/<model>/<timestamp>/environment`, with summary in `<model>.ktx-report.json`. The `.babylon` top-level World `environmentTexture*` fields are removed when this option is enabled, because KaDshow consumes `hasenv` plus `environment.env`. The source World is unchanged.

## Tests and limits

- `tests/test_environment_blender.py`: actual converter, HDR range/boost, defaults, explicit association, missing helper, marker ID collision, source preservation and failed conversion preserving old delivery.
- `tests/extract_kadshow_browser.mjs` and `tests/validate_kadshow_browser.cjs`: current application parser and PBR-lightmap plugin, actual WebGL texture decode and probe rendering. This is not the full React/physics lifecycle.
- Current bedroom: `content/environments/grandBedroomDay/blender/README.md` and `ACCEPTED_RESULT.json` identify the accepted source/package and verification limits. Use its enhanced-World baked-appearance capture contract; earlier diagnostic renders are not current delivery instructions.

Reference implementation: [Babylon environment texture tools, 7.27](https://github.com/BabylonJS/Babylon.js/blob/7.27.0/packages/dev/core/src/Misc/environmentTextureTools.ts), [PBR documentation](https://doc.babylonjs.com/features/featuresDeepDive/materials/using/masterPBR/), [IBL tool](https://www.babylonjs.com/tools/ibl/).
