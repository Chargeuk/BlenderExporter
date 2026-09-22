# Shared preparation restoration test — 22 September 2026

Implemented with BlenderExporter **3.3.14**, based on committed/pushed 297d310.
The shared implementation is included in the 3.3.14 source/package. The tests below identify the verified behaviour; inspect Git history for the published revision.

## Clean-input test

The temporary input contained only the final `.blend`, the two committed atlas
PNGs and `environment-build.json`. It contained **no EXRs**. The original Poly
Haven HDRI was downloaded and its SHA-256 matched the recorded source. No file
from a retired bedroom or test-scene folder was used to rebuild the environment.

The runner completed:

- Preflight and source/UV/material checks.
- 644 opaque receivers, 577 explicit bake contributors, glass/helpers excluded.
- DIRECT128 and INDIRECT2048 Cycles samples; 8 diffuse/12 total bounces.
- Separate receiving-metal cap0.8 for 37 brass objects; original transport materials retained.
- UV ownership: 3,378,528 covered pixels in a 2048-square atlas,
  1,841 sampled islands, zero ownership conflicts/unassigned covered pixels.
- Mirrored own-island denoising, interior copyback and final gap dilation for both passes.
- Native saved-node evaluation into the combined linear lightmap and island-ID regeneration.
- Enhanced-World baked-appearance HDR capture at4096×2048, plus a512-square thumbnail.
- ENV512 conversion, actual Babylon7.27 reload, finite irradiance and complete mip faces.
- A portable candidate scene, reopened successfully with no missing image files.

The full run took about **318 seconds** on this host,
including **240 seconds** for baking; download time is additional.
This is measured evidence, not a promised runtime on another machine.

## Preservation and repeatability

Geometry, material assignments and UVs matched the source audit exactly:
`7466735d46620d5aa4f9711581881691a727dcb6224d0e20047afcb0ae34554d`.
The accepted `.blend`, eight source images and eight runtime files stayed byte-identical.
The live Blender session was not changed. Blender itself, UVPackmaster and
SimpleBake were not patched.

A completed run resumed without rebaking. Deliberately changing a completed
report caused resume to reject it; restoring the report allowed reuse again.
`capture --reuse-lightmaps` performed only preflight and capture, with no bake.
A final preflight check additionally exercised NumPy/SciPy/Pillow, OIDN and real
EXR writing before an expensive bake. Four ownership regression cases passed:
separate ownership/interior preservation, overlap rejection, unassigned-pixel
rejection and fractional-alpha rejection.

The migrated standalone panorama-denoise helper was checked separately using a
constant linear HDR fixture. Its output remained finite RGB32,128×64 and above1
(range1.98625–2.00677 for input2). OIDN is not an identity transform, even on a flat
input. This optional helper is not used for the baked-emission capture.

## Numeric comparison to accepted masters

Mean absolute differences below use all RGB pixels, including filled padding.
They are measurements, not acceptance thresholds. Local maximum differences can
be considerably larger; inspect important chart edges and shadows visually.

| Candidate | Mean absolute RGB difference | Maximum channel difference |
| --- | ---: | ---: |
| masters/direct_final.exr | 0.00024529 | 0.304832 |
| masters/indirect_final.exr | 0.00002840 | 0.040214 |
| masters/combined.exr | 0.00073017 | 0.789661 |
| capture/capture.exr | 0.00008626 | 0.031039 |

The candidate thumbnail was inspected and shows the expected room, shadows,
materials and outdoor sky. It is a plausible restoration, **not a byte-identical
rebake or a newly accepted KaDshow delivery**. The inherited UV layout also has
181 subpixel/unsampled chart IDs, recorded in the ownership report;
restoration does not silently repack or enlarge them.

## Files and next use

- [Example configuration](../src/babylon_js/environment_tools/example.environment-build.json)
- [Maintained command guide](H:/code/babylonJs/BlenderExporter/docs/ENVIRONMENT_PREPARATION.md)
- [Detailed build report](C:/Users/d_a_s/Documents/Codex/2026-09-15/do-x20/environment-restoration/candidate03/build.json)
- [Candidate scene](C:/Users/d_a_s/Documents/Codex/2026-09-15/do-x20/environment-restoration/candidate03/snapshot/restored.blend)
- [Candidate thumbnail](C:/Users/d_a_s/Documents/Codex/2026-09-15/do-x20/environment-restoration/candidate03/thumbnail/thumbnail.jpg)

The temporary evidence links above are local test outputs, not required inputs
for another agent. Keep the source `.blend`, atlas PNGs, configuration and recipes.
Run the installed plugin tools to generate a fresh candidate when needed. Compare
and explicitly promote approved results; do not replace the accepted package
merely because file validation passed. KTX2 and Basis conversion remain the existing
exporter functions and were not rerun during this preparation-tool test.

## Saved-control recombination validation

The reuse path was exercised on the same saved bedroom source with existing
processed DIRECT, INDIRECT and island-ID masters and original bake ownership.
Only `preflight`, `reuse-check` and `combine` ran; no physical bake or denoising
stage ran. An absent previous combined image did not prevent recombination.

- Unchanged controls reproduced the prior combined EXR pixel-exactly.
- Separate saved copies at Direct Strength 0, 0.5 and 1 verified the native
  graph's affine direct contribution. The maximum owned-pixel midpoint error was
  0.0000038147 in linear RGB. Compare native evaluations rather than unfiltered
  source texels because the graph's image filtering participates in evaluation.
- All three reused masters remained byte-identical across the candidates.
- Six reuse tests and four ownership tests passed, including invalid arguments,
  catalogue changes, unknown labels, overlap and unassigned coverage failures.

The reuse check validates current UV catalogue, original ownership and matching
ID data. It does not infer unchanged physical lighting/materials from those data;
the caller must establish physical source correspondence before choosing reuse.
These are pipeline checks, not a new visual acceptance or export of the bedroom.

## Preview setup, packed images and accepted-map reuse

Native Blender tests verify new graph initialization, affine lighting math
(maximum error 4.77e-7), zero-radius bypass, within-island dark-patch correction
and no foreign-island leakage in the fixture. Fresh loading a replacement for a
packed image updated all 25 indirect samplers. Changed appearance controls are
rejected by the combination freshness guard; runtime metadata and editor layout
do not invalidate it. These checks are in `tests/test_preview_preparation.py`.

The initializer was compared with the saved bedroom's tuned graph: maximum
difference was **zero across 3,378,528 owned pixels**. The original bedroom
ownership was recovered and verified against current object/chart UV triangles
and accepted dilated IDs. Recombination using the actual accepted source maps
reproduced the accepted combined EXR's decoded pixels **exactly**. Enumeration
differs from a fresh sorted extraction; saved IDs are preserved rather than
renumbered. The source scene, source textures and eight runtime assets remained
unchanged. The environment now retains compressed original ownership and a
hash-based accepted-result record; this does not claim new visual acceptance.

## Source-resolution guard

`tests/test_preview_resolution.py` exercises a 32-to-64 source resize in native
Blender. Stale pixel offsets, inconsistent lighting/ownership dimensions and a
different configured source size are rejected. Failed updates preserve offset
values. After refreshing all 24 offsets, the graph's shader output matches a new
64-pixel initializer graph exactly (maximum decoded-pixel difference zero), with
all saved artistic/runtime controls retained. Colour-atlas dimensions remain
independent. Validation survives saving/reopening. Untagged custom graphs report
unverified status and refuse automatic migration. These are preparation checks,
not a new bake or visual acceptance of an existing environment.
