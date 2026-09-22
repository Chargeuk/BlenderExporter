# One HDRI, separate lighting and visible sky

Exporter 3.3.12 adds **KaDshow Environment Controls** to World properties.
The group contains ordinary Blender shader nodes and remains usable without
the exporter installed. Its tagged schema is version 1. Verified in Blender
5.2.2 on Windows; other Blender versions/platforms have not been exercised.

## Setup and controls

Add one Environment Texture to the World, choose its original external 2:1
HDRI, then use **World properties > Babylon.js > Add KaDshow Environment
Controls**. For safety, an existing graph is retained: explicitly connect the
group's **Lighting** output to World Output Surface and set the controls to
match the desired lighting. Reusing the button preserves existing values.
The source image can subsequently be changed in that same World panel.

| Control | Meaning |
| --- | --- |
| Rotation | Z rotation shared by lighting and skybox; displayed as an angle. |
| Lighting Strength | Overall multiplier for physical lighting and room capture. |
| Sky Lift | Hue-preserving boost that preferentially lifts darker sky values. Zero disables it. |
| Lift Rolloff | Positive denominator offset controlling the lift curve. |
| Visible Sky Strength | Independent visible-background brightness; 1 preserves original radiance. |

With source RGB `C`, Blender RGB-to-BW luminance `L`, sky lift `a`, and
rolloff `b`, the lighting branch evaluates
`C * (1 + a / (L + b)) * Lighting Strength`.
Visible Sky evaluates `C * Visible Sky Strength`, using the same rotation.
The implementation uses Blender's native luminance node, not a separate CPU
approximation of its coefficients. The current bedroom's values are rotation
−10°, lighting strength1.8, sky lift4, rolloff1, visible strength1. These are
scene-specific accepted values, not defaults for new environments.

Lighting stays connected in the authoring scene. To preview an original-brightness
background for a thumbnail, temporarily use Visible Sky in a separate render
copy; restore Lighting for physical baking and enhanced room captures. Changes
affect new renders/bakes; they cannot update an already saved lightmap or ENV.

## Export

With a connected managed group, **Export KaDshow skybox > Use KaDshow World
controls** defaults on. The exporter renders only Visible Sky in an isolated
temporary scene: canonical equirectangular camera `(pi/2, 0, 0)`, original image
dimensions, one Cycles sample, no denoising, RGB32 linear EXR. It prepares the
six faces and removes that intermediate EXR and its Blender data on success or
failure. No room geometry is included or changed. A selected manual image is
ignored while managed mode is on; turn it off to use the legacy image-only path.

Keep **Additional sky rotation** at0: World rotation is already evaluated.
Internally the canonical rendered panorama uses converter rotation360°.
Exposure0, Reinhard/sRGB conversion and1024-square faces remain the defaults.
Those display/size controls remain editable in the export dialog. Reports record
the original source hash, node values, evaluated panorama hash and resulting faces.

The raw outdoor HDR is deliberately not exported as the scene's room environment
for a managed World. **ENV export still uses World `bjs_environment_image` or an
explicit saved room capture.** Render that occupied-room capture with Lighting
when enhanced exterior brightness is intended. It cannot be replaced by the
outdoor source or by the World-only temporary sky panorama.

Do not silently rewrite arbitrary World graphs into this group. Resolve ambiguity
explicitly; multiple connected groups/unknown schema versions and linked control
inputs are rejected. Packed-only sources must be saved externally first.

## Validation

`tests/test_environment_controls.py` exercises brightness separation, native
rendering, idempotence, real staged Basis export, manual mode, source preservation,
temporary-data cleanup including render/consumer failure, and addon registration.
Existing `tests/test_skybox_blender.py` covers legacy projection, combined KTX
export and failure preservation. Both passed with3.3.12.

The bedroom migration rendered the old and new Lighting graphs identically.
Compared with its previously retained GPU-rendered panorama, new CPU-rendered
1024 faces differ by under0.01 of one8-bit step on average per face, maximum2
steps near bright detail. Thus appearance/orientation are preserved, but byte
identity of a newly encoded Basis file is not promised across render backends.
The accepted eight runtime files were retained byte-for-byte.
