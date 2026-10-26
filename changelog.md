# Blender2Babylon add-on changelog

## Blender exporter 3.3.47 (26 October 2026)

- Combines the preserved local 3.3.46 workflows with the published 3.3.37 feature line, retaining native physical-diffuse and appearance diagnostics, ordered material restoration, deferred volume attachment, decoded-volume validation and intrinsic palette processing.
- Retains remote solid/culling and UV-spacing diagnostics, compact Babylon output, lighting defaults, source-bound volume assembly and current Simple/Advanced producer policies.
- Preserves both glass RGB retention and explicit alpha overrides, both decoded-volume CLI routes, and the existing separate receipt contracts for alternate volume export workflows. Package metadata and the add-on version are aligned at 3.3.47.

## Blender exporter 3.3.44 (22 October 2026)

- Adds opt-in512RGBA appearance-only colour finishing over immutable native transport, with a separate copied-map/combined proof and a single-derivative export gate. Physical source/ownership hashes remain original; legacy reuse/export guards and encoding algorithms are unchanged.

## Blender exporter 3.3.43 (22 October 2026)

- Adds an explicit unaccepted pre-HDR native diagnostic mode to the existing export preparation entry point. It verifies original native provenance and real exporter registration, preserves ordered physical slots, and rejects environment/capture fallback. Legacy export guards and encoding algorithms are unchanged.

## Blender exporter 3.3.41 (22 October 2026)

- Adds opt-in measured UV inter-chart and tile-border clearance to the existing read-only UV auditor, with closest-point witnesses, explicit numerical tolerance, and an AABB search/candidate cap. Existing audit defaults are unchanged.

## Blender exporter 3.3.36 (19 October 2026)

- Adds offline paired Simple/Advanced decoded-volume delivery validation with native OpenImageIO EXR/PNG reading. It binds the converted model, capture masters, receipts, source dependencies and KTX2 outputs; checks base-level RGBA8 decoding, RGBD comparisons and Advanced directional banks. This does not perform Blender recapture or KaDshow runtime acceptance.

## Blender exporter 3.3.37 (26 October 2026)

- Packages source-bound opaque-gray solid/culling diagnostics with explicit evaluated delivery membership, camera and face-omission guards, and a working direct-file Blender CLI bootstrap. These disposable views never save the source or claim PBR/runtime acceptance.
- Packages the offline Babylon structural inspector and native merge-emulation CLI with geometry, hierarchy, membership and blocked-I/O checks; its reports distinguish structural prediction from application execution.
- Adds dependency-free UV chart-spacing and signed tile-border measurements with finite-input validation, witnessed minimum distances and no implicit gap pass threshold.
- Preserves portable candidate-manifest paths and validates legacy separator aliases; binds managed preview samplers to complete current lighting masters without remapping unrelated image users; supports Simple-only volume capture without computing or writing Advanced data.
- Retains compact model JSON, existing lighting-default schemas and default capture behavior, with expanded helper, Blender fixture and package coverage.

## Blender exporter 3.3.36 (25 October 2026)

- Writes compact Babylon model JSON in direct export, KTX conversion and metadata rewrites, and separate-source volume assembly. Removes layout whitespace without changing geometry, lighting, numeric precision or JSON values; diagnostic reports and manifests retain their readable formatting.

## Blender exporter 3.3.35 (25 October 2026)

- Adds opt-in `separate-source-v1` volume export assembly. Preparation runs in a disposable scene context; a separate strict Stage B opens the untouched saved source and calls the guarded volume producer, allowing only the intended volume metadata delta. This preserves saved-source association and geometry checks while enabling volume production after prepared-scene export. The route requires the existing volume producer policies and helper versions declared by its recipe; a version label alone does not establish readiness.
- Adds native volume audit tooling that compares the prepared RGBA8 volume with the complete linear master before KTX conversion, reports typed assembly stages separately, and keeps frozen voxel-first quality screens distinct from current encoded-filter-first shader math. Failed quality screens remain failures; no previous-scene exceptions are inherited.


## Blender exporter 3.3.34 (25 October 2026)

- Adds opt-in saved-World and export-dialog defaults for KaDshow environment lighting. Exports containing `hasenv` may include `kadshowEnvironmentLightingDefaults` with version 1, mode `off`, `simple`, or `advanced`, and multiplier 0–10; omitted scripted settings inherit the World opt-in; an explicit unchecked dialog setting or `null` disables it. Existing export behavior remains the default.



## Blender exporter 3.3.35 (19 October 2026)

- Adds an opt-in native uncapped physical-diffuse camera reference path. It retains linear float32 EXR and native diffuse passes, derives RGB display PNGs without a second render, and records exact saved-source and restoration receipts. This is a diagnostic reference, not a capped-bake equivalence or runtime lightmap.

## Blender exporter 3.3.34 (19 October 2026)

- Adds opt-in native Workbench geometry diagnostics with exact saved-source selection, explicit camera/shading/culling jobs, per-view visibility and camera receipts, and the existing receiver-normal guard. Physical and baked capture APIs are unchanged.

## Blender exporter 3.3.33 (18 October 2026)

- Adds explicit local setup and readiness checks for the optional Babylon ENV converter. Locked dependencies are installed only on request, and downloading Playwright Chromium requires a separate opt-in.

## Blender exporter 3.3.32 (18 October 2026)

- Adds an explicit `--skip-trial` option for capture jobs whose user waives the normal coarse-grid review. It records a signature-bound skipped status without claiming a passed trial; default trial gating and all source and clearance checks remain.

## Blender exporter 3.3.31 (18 October 2026)

This cumulative fork update records changes since the last documented fork entry, 3.3.8.

- Extends RGBD lightmap delivery with per-pixel or image-wide divisor modes, optional region policies, and per-texture codec selection while preserving the explicit `rgbd-v1` metadata contract.
- Adds AgX lightmap preparation and split-lighting preview controls, including separate AO grading and physical colour adjustment workflows.
- Packages saved-scene environment helpers for preparation and native baking, ownership and freshness checks, receiver response capture, and camera review.
- Adds optional direct spatial-light export and simple or advanced RGBD environment-lighting volumes alongside KTX2, Basis skybox, and ENV delivery.
- Includes reusable workflow tools in the installable Blender add-on package. Build the versioned Blender 5.2 ZIP with `python tools/package_addon.py`.

## Blender exporter 3.3.8 (22 September 2026)

- Opt-in RGBD HDR lightmaps, retaining legacy defaults; explicit encoding metadata and a compression-safe minimum divisor.
- Canonical KaDshow lightmap filenames to prevent repeated-prefix URL mismatches.
- Explicit opaque PBR serialization and shared opaque/cutout alpha preservation.
- Numeric, real Blender/KTX and Chromium/WebGL regression checks. See [the current KaDshow integration boundaries](docs/KADSHOW_EXPORT_GAPS.md).

## Blender 3.3.2 ##
* 31 August 2023

* When export completes without errors or warnings, add a 'Export Completed' message to display.
* Do not add a stray comma to the JSON for meshes and nodes if custom property not one of the types that can be handled {mwagner-webdev}

## Blender 3.3.1 ##
* 22 February 2023

* Custom mesh properties which are either string, float, or integer written as metadata.

## Blender 3.3.0 ##
* 31 October 2022

* No major changes or enhancements, just responding to those things required for Blender 3.3 

## Blender 2.93.5 ##
* 08 January 2022

* Changed to local position & scale when getting animation for things which are parented
* Fixed "ShadowGenerator errors on nodes without castShadow attribute" by griga

## Blender 2.93.4 ##
* 17 August 2021

* Added check box to Export Dialog box to only export the current selection.  Stated use case is to export objects in individual files via script. From @ghempton.

## Blender 2.93.3 ##
* 23 July 2021

* When converting a local matrix into a rotation, change the order from 'XYZ' to 'YXZ'

## Blender 2.93.2 ##
* 14 July 2021

* Fixed exception when there are shapekeys, but no animation

## Blender 2.93.1 ##
* 14 June 2021

* Fixed a typo in Diffuse Node
* Problem baking without a UV


## Blender 2.93.0 ##
* 31 May 2021

* Changes for API changes in Blender 2.93
* Shape Key animation added
* Switched the default of pickable to False for meshes
* For Shadows, added Cascaded type, autoZBounds & min & max

* For Materials:
    * Allow for having a value for Albedo / diffuse or emission when there is also a texture for same
    * The emission color is never defaulted on nor explicitly black, when there is an emissive texture
    * Added a custom overload property, where a color AND a texture can be specified for Albedo & emissive with the same fields
    * Added custom properties of 2 Sided lighting, disable lights, invert X or Y normals, & Object Space Normal mapping
    * Support for Parallax and it's scale bias, & occlusion
    * Added PBR specific custom properties for Horizon Occlusion, Radiance Occlusion, Irradiance In Fragment, Radiance Over Alpha, Normals Forward, & Specular Anti-Aliasing
    * Added custom property to allow mixing STD materials in a PBR export via a STD material override
    * Discontinued Check Only Once custom property

## Blender 2.80.1 ##
* 18 January 2021

Preserving of vertex groups by https://github.com/mvanacker, see PR https://github.com/BabylonJS/BlenderExporter/pull/44.

## Blender 2.80.0 ##
* 29 December 2020

Renaming of Blender build zip to now be in `Blender2Babylon-2.80x` format, where the
version of Blender it was made for is now in the name.  This is the exact same
as version 6.46 for the current file.  The deprecated files are renamed as follows:

- `Blender2Babylon-5.6` is now `Blender2Babylon-2.79x`
- `io_export_babylon.py` is now `Blender2Babylon-2.75x`

## Blender 6.4.6 ##
* 08 December 2020

* Increase maximum # of decimals of precision to 8.

## Blender 6.4.5 ##
* 10 October 2020

* Fix Problem in Armature introduced in 6.4.2

## Blender 6.4.4 ##
* 08 September 2020

* Allow exported Arcrotate camera to be panned (Right click / drag).

## Blender 6.4.3 ##
* 08 July 2020

* Fixed exporting of the Blender HDR texture being used as the enviromnent.

## Blender 6.4.2 ##
* 02 July 2020

* The original animation assigned to an armature is return once animation has completed, by https://github.com/ghempton, see PR https://github.com/BabylonJS/BlenderExporter/pull/25.

## Blender 6.4.1 ##
* 09 June 2020

* Changed with how Blender 2.83.0 now reports versions.  Exporter now works both ways.

## Blender 6.4.0 ##
* 01 June 2020

* Fixed exception when an empty material slot, just ignored now.
* Fixed non-sharing of materials when part of a multi-material.
* Added optional .csv file for mesh exporting statistics (turned on in World properties).
* Changed logging of the # of indexes to the more meaningful # of triangle exported.

## Blender 6.3.1 ##
* 11 March 2020

* Fixed 'This collection Only' option for lights, when empties are also in the scene.

## Blender 6.3.0 ##
* 10 February 2020

* Add the option 'preserve Z-up and right-handed' in the world tab.

## Blender 6.2.4 ##
* 05 January 2020

* Fixed material when not using Nodes
* Changed how Location, Rotation, & Scale are implemented for Blender 2.81.  Inputs can now be other nodes.  Still works for Blender 2.80, but the as other inputs part.

## Blender 6.2.1 ##
* 24 September 2019

* added direction onto tangents when doing split normals
* changed only exporting currently assigned animation to be the default
* fixed Glossy node from erroring when an unsupported node is assigned as input to Roughness

## Blender Exporter 6.2.0 ##
* 21 July 2019

* bug fix for meshes with armatures
* added lights for specific collections
* added pbr transparency mode & alpha cut off
* (florianfelix) removed deprecated messages from console

## Blender Exporter Version 6.1.1
* 12 May 2019

* Support for exporting to either BJS 4.0 or 4.1 regarding names of properties, which changed names
* Make PBR materials the default

## Blender Exporter Version 6.1.0
* 11 May 2019
* Added support for Subsurface, Anisotropic, Clear Coat, & sheen from Principled node for PBR
* Added tangents when also using custom split normals
* Added several generic .env files for when PBR is output
* Made changes reflecting the final API for Blender 2.80

## Blender Exporter Version 6.0.0
*24 January 2019

* Supports Blender 2.80 redesign
* Removed Internal render materials support
* Relocated Game Engine render properties used
* Moved all exporter level custom properties from scene tab to world tab
* Changes to world tab:
	* Added properties from scene tab
	* Added Sky Box / Environment Textures section
	* Added `Use PBR` checkbox
* Changes to mesh tab / proccesing:
	* Relocated Billboard Mode from Game Engine to here
	* Relocated most of material section to new panel in Materials tab
	* Remaining materials stuff now in 'Baking Settings' section.  Added `Force Baking` checkbox to avoid multi-materials.
    * Blender's mixed flat / smooth shading now supported, or custom split normals if used.
    * Custom properties `Picking` & `Disabled` are now using Outliner Icons instead.
    * Alpha now supported in vertex colors
* Changes for lights tab / proccessing:
    * Added `PBR intensity mode` custom property.  When `Automatic` or not PBR, `intensity` scaled 0-1 from Blender's `Energy`, where 10 is 1.  Otherwise `Energy` passed, unmodified.
    * `Range` property now supported using Blender property `Radius`
    * Hemi light type is no longer supported.  To get a BJS hemi light use area type, & specify `Size X` for `range`.
* Added new custom properties panel for Materials:
	* Relocated `Back Face Culling` checkbox from Game Engine to here
	* Relocated `Check Ready Only` Once checkbox from Mesh tab to here
	* Relocated `Max Simultaneous Lights` from Mesh tab to here
	* Relocated Name Space from Mesh tab to here (might be in TOB only, since JSON files cannot share materials)
* Mesh baking can be reduced to only the texture channels required, keeping other image texture based channels (not for multi-material meshes)
* Nodes based renders (Cycles & eevee) not always just baked.  See chart for properties / textures & where values are from.  Properties are only assigned when no texture input to socket.

|  STD Property / Tex | PBR Property / Tex | From Nodes-Socket
| --- | --- | --- |
| diffuseColor / diffuseTexture | albedoColor / albedoTexture |Diffuse BSDF - Color, Principled BSDF - Base Color |
| ambientColor / ambientTexture | ambientColor / useAmbientOcclusionFromMetallicTextureRed  | Ambient Occlusion - Color |
| emissiveColor / emissiveTexture | emissiveColor / emissiveTexture | Emission - Color |
| specularColor / specularTexture | reflectivityColor / reflectivityTexture | Glossy BSDF - Color, Principled BSDF - Specular |
| specularPower (inverted & 0 - 128) | roughness / useRoughnessFromMetallicTextureGreen | Glossy BSDF - Roughness, Principled BSDF - Roughness |
| indexOfRefraction / refractionTexture | indexOfRefraction / refractionTexture | Refraction BSDF - IOR, Frensel - IOR, Principled BSDF - IOR / Refraction BSDF - Color, Principled BSDF - IOR|
| -- | metallic / metallicTexture | Principled - Specular |
| -- | emissiveIntensity | Emission - Strength|
| alpha / opacityTexture | alpha / opacityTexture |Diffuse BSDF - Color, Transparency BSDF - Color, Principled BSDF - Base Color |
| bumpTexture | bumpTexture | Normal Map - Color, Principled BSDF - Normal |

* Certain nodes are allowed, and are either ignored or just passed thru
	* Mix Shader, used mostly for non-principled trees
	* Separate RGB, for metallic textures wt roughness / AO
	* Frensel, when not PBR
* glTF legacy nodes (glTF Metallic Roughness or glTF Specular Glossiness) produce an error saying to switch to standard Blender nodes or use glTF exporter
* Texture / UV parameters are optional Nodes, when input to a texture node (ignored when must be baked, baking uses them though)
	* Mapping node for (translation to offset), (rotation to ang), (scale to scale)
	* Texture Coordinate & UVMap nodes for coordinatesIndex
* When a material channel cannot really be represented by mapping, then it will be baked.  Examples:
	* A Noise or other procedureal texture to Principled BSDF - Normal, then a bump texture will be baked
	* Any node which is not explicitly supported or ignored

## Blender Exporter Version 5.6.4

*17 July 2018

* Fix typo for exporting only visible layers

* Copy tags also to instances

## Blender Exporter Version 5.6.3

*01 June 2018

* Fix exporter settings panel (in Properties > Scene tab)

* Remove active layers export, replace with 3 options (All, Selected objects, and visible Layers)

* Show message in exporter settings panel to redirect user to Scene tab

* Fix bl_idname to allow setting of a shortcut

## Blender Exporter Version 5.6.2

*23 March 2018

The custom property, textureDir, originally for [Tower of Babel](https://github.com/BabylonJS/Extensions/tree/master/QueuedInterpolation/Blender) to
indicate the directory to write images, is now joined into the name
field of the texture in the *.babylon* file.

If the field does not end with `/`, then one will be added between the
directory & file name.

The field is relative to the *.babylon* file.  For this to work probably
implies that the *.babylon* is in the same directory as the html file.
Still it now allows images to be in a separate directory.

Have not tested where the the *.babylon* is in a different directory from
html file.

## Blender Exporter Version 5.6.1

*07 March 2018

- zip file now has manifest option code

## Blender Exporter Version 5.6

*02 March 2018

Essentially, this is an obsolete feature dump.  This in preparation for Blender 2.80 which could cause many changes.  Things removed:
-  Remove the checking for, and splitting up of meshes with more than 64k vertices.
-  Flat shading both for the mesh and the entire scene.  This can be replaced with a Split Edges modifier.
-  Generate ES6 instead of ES3 (Tower of Babel variant).

There were a few additions:
-  Separate scene level controls for the # of decimals of (Default):
    - positions / shape keys (4)
    - normals (3)
    - UVS (3)
    - vertex colors (3)
    - matrix weights (2)
-  Pickable property for meshes
-  Redesign of checking for un-applied transforms on meshes with armatures

Due to arguments changes to some functions, you will need to restart Blender if you had a prior version.
