# Saved KaDshow lighting preview

The Blender Python initializer creates the standard colour × adjusted-lighting
emission preview for a new scene. It does not replace physical source materials,
bake anything, unwrap geometry or overwrite an existing tuned preview.

```python
from babylon_js.lighting_controls import create_preview_material

# Loaded bpy image datablocks; placeholders at final dimensions are allowed
# before the first bake. Colour: sRGB; direct/indirect: Linear Rec.709;
# IDs: full float, Non-Color. Lighting/ID image dimensions must agree.
material, controls = create_preview_material(
    colour_image, direct_image, indirect_image, island_id_image,
    uv1='UVMap', uv2='SimpleBake')
```

Load images with `bpy.data.images.load(path, check_existing=False)`, or create
named float placeholders with `bpy.data.images.new(..., float_buffer=True)`.
Set the intended colour spaces explicitly. Save placeholders externally at the
configured paths before preparation if required. Preparation replaces mapped
images with completed candidate files and remaps all users, including packed
images and nested samplers. Physical baking still uses the original PBR material;
assign the emission preview only to disposable preview copies or a preserved
preview scene. The group itself needs no Python callback after saving.

The initializer refuses an existing material name or scene control association.
For an existing scene, preserve its graphs and settings. To deliberately create
an alternative, first preserve the source and explicitly clear the scene
`bjs_lighting_controls_material` association, then choose a new material name.

## Calculation and defaults

- Direct and Indirect Strength default to 1; Shadow Lift and Adaptive Smoothing
  default to 0 (bypass). Do not inherit another room's artistic tuning.
- Adjusted indirect is `strength * (lift + (1-lift) * indirect)`.
- The 5×5 binomial filter rejects neighbours with different ownership IDs,
  renormalizes remaining weights and corrects qualifying dark deviations after
  lift/strength. Patch Size is in pixels. Zero radius bypasses correction.
- Dark Difference defaults to 0.02, radius to 3; smoothing supports 0–2 including
  deliberate overdrive. HDR values are not clamped. This is an artistic preview
  filter; it can soften actual contact shadows.
- Colour uses UV1 with Repeat wrapping, lighting/IDs use UV2 with Extend.
  Neighbour and ID samples use Closest; the centre light sample is Linear.
- Runtime-only Shadow Suppression / Fully Lit Threshold / Highlight Preservation
  default to 2 / 1 / 1. The group tag and scene association let the exporter read
  these as dialog defaults; they do not affect this Blender preview.

The caller supplies the ownership ID image generated with the matching maps.
Colour is multiplied once; emission strength is 1. This preview does not include
full PBR specular response. The preparation runner evaluates the saved graph into
an illumination-only master for export, preserving the editable graph.

## Freshness and validation

Combination emits `masters/combined.provenance.json`: shader/image identity,
combined-image hash and source/ownership provenance. Promote it with its master
and set `combined_provenance` in the project config. Without that field, reuse
looks for the combined EXR path plus `.provenance.json`.

Baked capture/thumbnail reject missing or stale provenance. Runtime metadata
and editor layout do not affect the identity; appearance controls, shader
properties, wiring and image bytes do. This check does not prove that processed
maps still represent current physical transport. Validate geometry/UV/material/
lighting correspondence separately. Physical-PBR capture does not use this guard.

`tests/test_preview_preparation.py` exercises native Blender shader output,
25-sampler packed-image replacement, same-island isolation, smoothing bypasses,
runtime metadata independence and stale-combination rejection. Run with
`blender --background --factory-startup --python-exit-code 1 --python TEST -- NEW_OUTPUT`.
