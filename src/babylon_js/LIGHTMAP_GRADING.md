# AO and editable lightmap grading (3.3.17)

All grading operates on scene-linear illumination **before** multiplying material colour. The result remains one lightmap; AO is an authoring dependency, not an extra runtime texture or material. Physical PBR sources stay separate from the emission preview.

## Create or upgrade

New `lighting_controls.create_preview_material(..., ao=optional_image)` includes grading. For a saved recognized split-lighting material:

```python
from babylon_js.lightmap_grading import upgrade
controls = upgrade(material, ao_image, uv='SimpleBake')
```

Or use F3 **Babylon: Add or Upgrade Lightmap Grading** on the scene-associated preview. Upgrading is explicit, idempotent and preserves existing controls/wiring. Unknown layouts are refused before mutation; do not delete a tuned graph to force migration. Use a separate material candidate and prove native output equivalence when manually migrating a custom graph. A repeated upgrade may bind a new AO image without resetting artistic settings.

The original outer group gains AO controls and Illumination/Direct Lighting/Indirect Lighting colour outputs. Enter it to find **Direct Grade**, **Indirect Grade** and **Final Grade**. Enter a grade to edit the RGB Curves node. The Shader output remains compatible with existing previews and export tools.

## Order and controls

`existing direct/indirect adjustments → per-pass grade → per-pass AO attenuation → sum → final grade → material colour → nonnegative emission`

Each grade begins with **Adjustment Strength 0**, guaranteeing the existing appearance. Set it to 1 to use the following controls, or blend a smaller amount. **Bypass 1** temporarily returns the original input without erasing settings.

| Control | Meaning / neutral |
| --- | --- |
| Exposure | Lighting gain in stops; 0 neutral, +1 doubles it. |
| Contrast / Contrast Pivot | Luminance power contrast; 1 / 1 neutral. The pivot is in linear lighting units, not display grey. |
| Tint | RGB channel multipliers; white neutral. |
| Saturation | Luminance-relative chroma; 1 neutral. |
| Black Level / White Level | Advanced input levels; 0 / 1 neutral. White must exceed black; denominator has a small positive floor. These remap values without imposing an HDR ceiling. |
| Gamma | Advanced luminance-power adjustment; 1 neutral. It overlaps contrast behaviour and is not an image encoding flag. |
| Curves Strength | Optional native RGB curves; 0 bypass. Edit points inside the grade. Curve extrapolation can affect HDR strongly. |
| AO Strength / Contrast | Attenuation amount 0–1 / scalar shaping power (1 neutral). Strength starts at 0. |
| AO Direct / Indirect Influence | Where attenuation applies. Defaults 0 / 1. Direct may be enabled artistically; compare contact depth against the reference. |

An unbound AO image is explicitly bypassed. AO distance is a **bake parameter**, not a live slider. Native Bright/Contrast is included disconnected as an advanced alternative: connecting it is a deliberate custom-graph edit, not an additional default transform.

For positive RGB and gamma 1: `Y=dot(RGB,[.2126,.7152,.0722])`; `RGBout=RGB*(Y/pivot)^(contrast-1)`. Implementation uses the magnitude of signed luminance with a tiny nonzero floor, preserving signed intermediate contributions and channel ratios rather than clipping a negative indirect term before summation. Gamma divides the exponent's contrast parameter. Final output floors negative RGB only; values above 1 survive. Inspect finite values/range after aggressive grading. No numerical transform can invent missing sampled lighting detail.

AO attenuation per pass is `1 - strength * influence * (1 - AO^contrast)`. It can deliberately deepen shadows already present in the diffuse bake. Judge its effect and avoid treating duplicate darkening as physically measured transport.

## Baking and reuse

See [environment preparation](environment_tools/ENVIRONMENT_PREPARATION.md). The AO shader uses scene geometry, self-occlusion and neighbouring blockers; the scalar output is baked through EMIT on the same evaluated UVs/normals. Receiver colour and metallic do not multiply AO. Exclude glass, helpers and duplicated retained sources; include the declared bake contributors.

Use zero-margin initialized targets with `use_clear=False`: Blender's emission clear fills unowned alpha. Check exact ownership against the lighting bake, then nearest-dilate gaps. No HDR radiance denoiser is applied to AO. The pub trial used distance 0.5 scene units, 128 Cycles samples and 64 AO-node samples; its 2048² scalar map was usable in room and close-up views without denoising. These are starting settings to review, not a universal production guarantee. Final indirect remains 2048 fixed samples.

## Output consistency and provenance

Managed combination and AgX export use the same native CPU emission evaluator with colour replaced by white. This avoids observed CPU/GPU sampling differences at sharp atlas transitions; physical lighting and room captures can still use the configured GPU. Keep unclipped linear masters. AgX compensation is applied once using UV-space albedo, then resized/encoded to RGBD/KTX2. Baked room captures use the graded linear emission graph; never the AgX-compensated delivery lightmap. Physical-PBR capture remains separate.

Preview identity includes image bytes, graph wiring, controls, RGB CurveMapping settings/points/handles and ColorRamp settings/stops. Curve edits invalidate combination and dependent baked captures. Runtime metadata and editor layout do not. Unsupported external script/IES/point-density state is rejected rather than certified. Geometry/UV/normals/blocker fingerprints guard AO reuse; physical materials and lighting correspondence still need the original bake provenance.

Source resolution changes require AO, D/I and ownership at matching dimensions; delivery-only resizing does not. Preserve AO and the original ownership catalogue/labels in restoration recipes. Never interpret padded pixels as original coverage.

## Verified checks

Native tests cover signed shadow lift, disabled/bypassed identity, HDR contrast, AO attenuation, gamma/curves, curve-only provenance changes, runtime metadata independence, save/reopen and emission ownership. Existing preview and AgX/KTX regressions cover the integration. The pub neutral upgrade had maximum float error 0 at 2048²; its AO coverage matched 3,319,315 owned pixels exactly. Numerical checks and harness loading do not replace the user's final visual acceptance.
