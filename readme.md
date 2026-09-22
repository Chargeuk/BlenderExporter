# Blender to Babylon.js exporter

## Chargeuk fork: Blender 5.2, KTX2 and Basis skybox export

Version3.3.13 packages the [shared lightmap processor](docs/LIGHTMAP_PROCESSING.md), with an independent CLI and dependency checks. Environment projects keep their settings and recipes, not script copies.

Version3.3.12 adds native [World environment controls](docs/ENVIRONMENT_CONTROLS.md): one original HDRI, separate lighting/visible-sky brightness, and temporary skybox preparation.

Version3.3.11 adds a persistent [Export Tangents switch](docs/GEOMETRY_EXPORT.md). Disable it for KaDshow environments to reduce file size and keep merge-compatible vertex layouts.

Version3.3.10 adds [lightmap export sizes](docs/LIGHTMAP_RESOLUTION.md) and [saved lighting-node dialog defaults](docs/LIGHTING_PROFILE.md). Current PBR defaults are metallic1.0 / roughness0.8.


Version3.3.7 defaults PBR export multipliers to **metallic0.5 / roughness0.4**, matching the user's KaDshow comparison. Both are editable and preserve Blender source materials. See [material controls and tests](docs/MATERIAL_EXPORT.md).

Version 3.3.6 adds optional **Export KaDshow environment lighting**: a saved room EXR/HDR becomes a validated `environment.env`, with selectable face size defaulting to **512**, exposure/highlight controls and a delivery-only `hasenv` marker. See [ENV setup, capture contract and tests](docs/ENV_EXPORT.md). The local converter requires Node.js, the pinned Babylon helper and Chromium; it does not start a Blender render.

Version 3.3.5 added optional **Export KaDshow skybox** with selectable square face sizes, default **1024**, panorama conversion and validated `cubemap.basis` delivery. See [skybox settings and tests](docs/SKYBOX_EXPORT.md). It requires Basis Universal and works independently or alongside **Convert textures to KTX2**, introduced in 3.3.4. See [KTX2 setup, controls and tests](docs/KTX2_EXPORT.md). All three options are off by default; material conversion requires KTX-Software 4.4.2+.

The fork has been tested with Blender 5.2.2 LTS on a static KaDshow environment. See [Blender compatibility fixes](docs/BLENDER_52.md) and [the KaDshow import contract and remaining asset gaps](docs/KADSHOW_EXPORT_GAPS.md). Build the updated add-on with `python tools/package_addon.py`; the older root ZIP is historical.

**Note:** With the new Blender 4 release LTS schedule (X0, X1, X2, X3 LTS), the master branch of this repo will have the zip distribution file for the last LTS, currently 2.83 LTS.  Versions for the next cycle, if available, will be on the `dev` branch.

## Documentation
See the [exporters documentation](https://doc.babylonjs.com/extensions/Exporters) to:

- know [how to install](https://doc.babylonjs.com/extensions/Exporters/Blender) 
- learn the [features](https://doc.babylonjs.com/extensions/Exporters/Blender#installation)
- read some [tips](https://doc.babylonjs.com/extensions/Exporters/Blender_Tips)

If you think something missing in the Blender exporter documentation, please report it through github issues, or in this [forum thread](http://www.html5gamedevs.com/topic/36596-blender-exporter-doc-needs-feedback/).

## Changelog

Changelog can be [found here](https://github.com/BabylonJS/BlenderExporter/blob/master/changelog.md).


## KaDshow lighting profiles

Exporter 3.3.9 writes versioned runtime lighting settings on lightmap markers, defaults to metallic/roughness multipliers 1.0/0.8, and supports an explicit Original mode for older bakes. See [lighting profile and debug controls](docs/LIGHTING_PROFILE.md).
