# Blender to Babylon.js exporter

## Chargeuk fork: version 3.3.48

This fork maintains the Babylon.js Blender exporter and packages workflows for Blender 5.2. The add-on retains the scene and material exporter and adds optional texture delivery and environment-lighting tools.

Current fork features include:

- KTX2 conversion for material textures and lightmaps, with `rgbd-v1` lightmap encoding, per-map codec settings, divisor modes, and optional region-specific encoding.
- Optional Basis skybox and Babylon ENV conversion for prepared panoramas.
- Split direct/indirect lightmap preview and AO grading, physical colour adjustment, and AgX-compensated lightmap preparation.
- Saved-scene environment helpers for preparation, baking and ownership checks, receiver response, camera capture (including uncapped native physical-diffuse references), spatial lighting, and simple or advanced environment-lighting volumes.
- Reusable Python workflow tools included in the installable add-on package, including offline paired Simple/Advanced RGBD volume delivery validation with a native OpenImageIO reader.
- Source-bound solid/culling diagnostics with explicit evaluated delivery membership and recorded face omissions, plus UV chart-spacing and signed-border measurements.
- Offline Babylon geometry inspection and native merge emulation with explicit structural limits; these reports do not replace application or rendered acceptance.
- Opt-in `separate-source-v1` export assembly prepares delivery assets in a disposable scene, then validates and adds the lighting volume from the untouched saved source.
- Optional per-World and per-export-dialog environment-lighting defaults, serialized as a versioned KaDshow profile only for exports containing `hasenv`.

These options are purpose-built for prepared environments. Their presence does not make arbitrary KaDshow scenes or client runtime packages automatically compatible; see [the KaDshow integration contract and boundaries](docs/KADSHOW_EXPORT_GAPS.md).

## Build and install

Build the versioned Blender 5.2 add-on ZIP with:

```powershell
python tools/package_addon.py
```

The script writes `dist/Blender2Babylon-<version>-blender52.zip`. In Blender, install that ZIP from Preferences > Add-ons > Install from Disk, then enable **Babylon.js**. After replacing an installed add-on, re-enable it or restart Blender before relying on the new code.

The optional room HDR-to-ENV converter needs Node.js 18+ and a compatible existing Chromium/Chrome browser. After installing or upgrading the add-on, run **Set up / repair ENV converter** in the add-on Preferences, or run the installed `workflow_tools/env_converter/setup.py` with external Python 3.8+. Setup installs the locked converter dependencies beside the selected converter and checks WebGL2; it does not download a browser unless explicitly allowed. The setup guide in the `blender-live-workflow` skill documents headless commands and overrides. Converter dependencies and local browser settings live beside the installed add-on and are excluded from the ZIP.

## Workflow references

The detailed environment procedures and settings live in their owning Codex skills: `kadshow-blender-environments`, `kadshow-environment-export`, `kadshow-hdr-environment`, and `blender-texture-authoring`. The repository includes a [historical Blender 5.2 compatibility audit](docs/BLENDER_52.md) and [client integration boundaries](docs/KADSHOW_EXPORT_GAPS.md).

For upstream exporter concepts and the Blender installation interface, see the [Babylon.js Exporters documentation](https://doc.babylonjs.com/extensions/Exporters) and its [Blender exporter page](https://doc.babylonjs.com/extensions/Exporters/Blender).

## Changelog

See the [fork changelog](changelog.md).
