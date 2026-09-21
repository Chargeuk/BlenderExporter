# Blender to Babylon.js exporter

## Chargeuk fork: Blender 5.2, KTX2 and Basis skybox export

Version 3.3.5 adds optional **Export KaDshow skybox** with selectable square face sizes, default **1024**, panorama conversion and validated `cubemap.basis` delivery. See [skybox settings and tests](docs/SKYBOX_EXPORT.md). It requires Basis Universal and works independently or alongside **Convert textures to KTX2**, introduced in 3.3.4. See [KTX2 setup, controls and tests](docs/KTX2_EXPORT.md). Both options are off by default; material conversion requires KTX-Software 4.4.2+.

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
