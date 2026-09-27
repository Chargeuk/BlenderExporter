# AgX-compensated lightmap export

The export dialog defaults **Match Blender AgX using lightmap compensation** on.
It runs only when KTX conversion includes a KaDshow marker lightmap. Select RGBD
encoding. Disable the checkbox for unchanged legacy exports. The low-level
`ktx_options` API is backward-compatible: pass `lightmap_agx=True` explicitly.

## Process

1. Preserve the source linear master. If the scene associates a managed lighting
   preview, evaluate its current Direct/Indirect Strength, Shadow Lift and
   adaptive smoothing with white colour into staged combined linear lighting.
   Without a managed preview, use the explicitly selected linear EXR/HDR master.
2. Refresh surface colour into the same UV2 layout on evaluated copies of the
   exported lightmap descendants. Each material must have one Principled BSDF;
   its linked or constant Base Color is sampled, without metallic, roughness,
   lighting or emission. Shared instances, materials and live state are preserved.
   `bjs_runtime_material` resolves a scene's managed preview back to its PBR material.
   The authoring runner also creates `masters/albedo_uv.exr` and its JSON record
   during every physical bake. Export refreshes colour so later material edits
   cannot silently use stale albedo. This is a cheap emission-only reprojection.
3. Apply the installed Blender OCIO AgX transform to `colour * lighting`, with
   the current sRGB display, look, exposure and gamma. Custom display curves are
   rejected. Convert display RGB back with power 2.2 for Babylon's default display
   conversion, then divide by albedo per channel with a 1e-5 floor.
4. Fill unowned gaps from nearby owned pixels without mixing colours. Save a
   staged `_agx.exr` and provenance sidecar; never overwrite the physical master.
5. Resize in linear space, encode RGBD, flip once and compress UASTC/Zstd with no
   mips. The exported marker requests the new `_agx_rgbd.ktx2`. Reports include
   source/albedo/config/output hashes and effective view settings.

The UI path requires Blender with NumPy, OpenImageIO and PyOpenColorIO (verified
in Blender 5.2). Errors cancel delivery before publishing. Other exporters,
SimpleBake and UVPackmaster are not modified. The final indirect bake remains
2048 samples; colour reprojection uses one deterministic emission sample.

## Runtime control

`Baked Diffuse Preservation` is a runtime-only 0–1 socket on the saved split
lighting node (default 0 for backward compatibility). Export dialog defaults are
read from that socket; dialog overrides are serialized as
`metadata.kadshowLighting.bakedDiffusePreservation`. It does not drive the Blender
preview or invalidate lighting-map provenance. Upgrade a saved node without
resetting its settings:

```python
from babylon_js.lighting_controls import upgrade_runtime_controls
upgrade_runtime_controls(material)
```

At preservation 1, with extra lighting disabled and Babylon tone mapping off,
exposure/contrast 1, compensation targets Blender's baked AgX appearance. Lower
preservation intentionally allows PBR diffuse reduction; compensation does not
cancel the chosen slider. Reflections/additional lights are still applied later,
and reflection suppression reads the corrected map. This is material-dependent
appearance compensation, not physical illumination. Changing materials/controls
requires re-export. Skybox and ENV use their separate workflows; do not apply this
correction to an HDR scene-lighting capture.

Use original masters on repeat exports. Compensated source files with their
`.agx.json` sidecar are rejected to prevent double application. Keep that sidecar
if moving a generated corrected master. A renamed image without provenance
cannot be reliably recognised as already corrected.

## Verification

`tests/test_agx_export.py` exercises albedo coverage, material/scene restoration,
saved control migration/prefill, current-node combination, changed-colour refresh,
double-transform rejection, real KTX encoding and exported preservation metadata.
Run in factory-startup Blender with `--python-exit-code 1 --python TEST -- NEW_OUT`.
Do not claim a perfect renderer match: downsampling, texture filtering, near-black
division, colour compression and additional PBR contributions remain differences.

Deep Windows destination paths use a short temporary staging folder to avoid
encoder/image-writer path limits. Its retained location is in the KTX report.
