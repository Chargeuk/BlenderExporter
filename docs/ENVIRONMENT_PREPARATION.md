# Saved-scene environment preparation

The plugin owns the reusable implementation. Each environment keeps only its
`environment-build.json`, saved `.blend`, source atlases, asset credits and
restoration READMEs. Do not copy these Python tools into an environment folder.

This runner prepares an **already authored** scene. It does not model geometry,
unwrap or pack UVs, approve visual quality or replace physical materials. Create a new preview with
[`lighting_controls.create_preview_material`](LIGHTING_CONTROLS.md); preserve an existing tuned graph.
UVPackmaster/SimpleBake remain separate installed Blender products; neither is
modified or required by this runner's native Cycles bake operation.

## Run

Use external Python with NumPy/SciPy/Pillow, Blender, an OIDN library and an
image-writing Python with NumPy/OpenImageIO. See the adjacent
[`../src/babylon_js/lightmap_tools/LIGHTMAP_PROCESSING.md`](../src/babylon_js/lightmap_tools/LIGHTMAP_PROCESSING.md)
for dependency checks. Call the file directly, not `python -m babylon_js...`.

```powershell
& $python "$addon/environment_tools/cli.py" restore `
  --config "$environment/blender/environment-build.json" `
  --output "$candidate" --blender "$blender" `
  --image-python "$imagePython" --oidn-library "$oidn"
```

Output must be a separate candidate directory outside the source folder. Source
files and the live Blender session are not changed. Background Blender processes
open the saved `.blend`; save wanted live settings before running.

Commands stop after the named stage, running all preceding stages unless resume/reuse skips them:

| Command | Result |
| --- | --- |
| `preflight` | Verify source files, source materials, UV bounds, role selections, managed World and preview material |
| `bake` | Separate zero-margin diffuse DIRECT/INDIRECT arrays; evaluated receiver normals preserved; local receiving-metal cap patches |
| `ownership` | Extract island ownership from UV triangles and actual bake coverage; merge receiver-only patches |
| `process` | Mirrored own-island OIDN context, interior copyback, final gap dilation; direct/indirect EXRs and island IDs |
| `combine` | Evaluate the saved preview shader with colour replaced by white, then dilate gaps; unclipped linear lighting EXR |
| `capture` | Canonical equirectangular room render, with explicit baked/physical mode and World branch |
| `env` | Optional existing exporter conversion of the saved capture to ENV, including Babylon reload validation |
| `thumbnail` | Baked or physical camera render with configured framing |
| `snapshot` | Portable candidate `.blend` with relative image, lightmap-marker and room-capture paths |
| `validate` / `restore` | Complete the pipeline and validate regenerated image dimensions/range and source integrity |

`build.json` records source/tool hashes, stage timing and output hashes;
`progress.json`, per-stage logs and the bake's own progress identify current work.
Use `--resume` to verify and skip completed stages with identical inputs. Changed
source/config/tool inputs require a new candidate directory. An interrupted stage
is restarted; successful preceding stages are retained. Resume does not mean
resuming partway through a Cycles bake.

For capture-only work on an existing accepted scene, add `--reuse-lightmaps` and
use `capture` as the command. The four configured masters are verified and copied;
physical baking/ownership/processing/combination are skipped. Baked capture also
requires the matching combined-provenance sidecar and verifies current shader/image
identity before rendering; stale controls require recombination. This is explicit
reuse, not an assertion that those images are fresh after scene changes. Use a
cheap capture configuration first when judging a new camera or capture setup.
`thumbnail --reuse-lightmaps` similarly avoids a physical rebake.

### Recombine after adjusting saved lighting controls

For control-only changes, reuse the accepted processed DIRECT/INDIRECT/ID masters
and the **original zero-margin ownership** from their bake:

```powershell
& $python "$addon/environment_tools/cli.py" combine `
  --config "$environment/blender/environment-build.json" --output "$candidate" `
  --blender "$blender" --image-python "$imagePython" --oidn-library "$oidn" `
  --reuse-lightmaps --ownership-labels "$matchingBuild/ownership/labels.npy" `
  --island-catalogue "$matchingBuild/bake/islands.json"
```

This runs preflight, ownership/UV correspondence checks and native shader
combination. It does **not** bake or denoise and does not require the configured
old combined EXR to exist. Current receiver chart identities and UV triangles must match the supplied
bake catalogue; enumeration can differ, but saved ownership IDs are preserved, and the reused ID image must match ordinary dilation of the
original labels. `--ownership-labels` accepts NPY or a lossless NPZ containing only `labels`.
Preserve these provenance inputs alongside accepted maps;
a fully dilated ID image cannot recover original chart interiors by itself.

To continue through capture or later stages with these new controls, add
`--recombine` to `--reuse-lightmaps` and supply the same ownership arguments.
Without `--recombine`, capture reuse deliberately keeps the configured combined
image, and capture rejects a mismatching preview. Plain `combine` without reuse runs preceding physical bake dependencies.

Reuse is appropriate only when physical geometry/materials/lighting still match
the source of the masters. The checks prove UV/ownership correspondence, not
unchanged physical transport: verify that against the bake provenance. Changed
physical inputs require the appropriate bake. Save live settings first and use a
new candidate directory after source/config changes; do not weaken resume hashes.

Pass `--env-converter /path/to/tools/env-converter/convert.cjs` to include ENV
generation in `restore`; Node and that helper's pinned dependencies must already
be available. Without this option the runner delivers the HDR source, leaving
runtime conversion to a later export. `env` itself requires the option. The
`env` configuration object accepts the exporter's size/exposure/highlight settings.

## Configuration

See [`example.environment-build.json`](../src/babylon_js/environment_tools/example.environment-build.json). Paths are
relative to the configuration file, except executable/library paths supplied on
the command line. `images` maps the saved image file paths, not Blender datablock
names. These bindings allow missing generated images to be relinked to candidates.
`source_images` lists committed dependencies whose content hashes invalidate a run.
`downloads` specifies unchanged source assets and mandatory SHA-256 verification.
Downloaded HDRIs retain their source brightness; native World controls supply the
lighting adjustments and visible-sky rotation.

Selectors accept explicit `names`, one `property` plus `values`, or a `properties`
dictionary of property-to-allowed-values pairs (all conditions must match).
`type` defaults to `MESH`. Use `type: LIGHT` for property-based bake-light selection.
An empty `bake_lights.names` means HDRI-only lighting. Contributors must be explicit:
the saved export visibility often hides geometry that should still cast bake shadows.
Retained modelling sources, runtime helpers and glass are not diffuse receivers.

The receiver's physical source material must contain a Principled shader. Metallic
cap selections must be receiver subsets. The cap is applied to temporary receiving
materials only; it is not a scene-wide material edit. UVs must fit the square atlas
without overlapping chart interiors. Charts too small to own pixels are reported;
the runner cannot invent lighting for them. Review and enlarge significant ones.

Final indirect baking requires at least **2048 samples**. Cheaper trials must say
`quality: preview`. DIRECT and INDIRECT sampling are independently configurable.
The existing accepted example uses DIRECT128/INDIRECT2048, not 2048 direct samples.
Original baked pixels are never replaced by padding pixels during processing.

The preview material must be an authored emission preview using the saved controls,
the configured colour image and direct/indirect/ownership images. Its native shader
is evaluated by Blender; the runner does not reimplement its smoothing arithmetic.
Changing the atlas resolution may also require updating pixel-radius constants in
that authored shader. The tool preserves the graph rather than guessing changes.

For new KaDshow environments, explicitly configure capture `mode: baked` and
`world_output: Lighting` to match the accepted adjusted lightmap and enhanced
World illumination. Preserve a different existing project contract when present.
Capture `mode: physical` is an explicit alternative using source PBR materials and contributors. `baked`
uses the saved colour Ã— adjusted-lighting preview and excludes analytic lights,
glass and contributors. Physical captures need appropriate render sampling and
separate visual assessment. Select `world_output: Lighting` for enhanced lighting
or `Visible Sky` for its separately controlled brightness. Thumbnail glass is
included. Do not confuse the scene lighting capture with the outdoor skybox.

For optional physical-panorama denoising, the plugin also carries
`denoise_panorama.py`. Run factory-startup background Blender with `--python` and
arguments `-- RAW_EXR NEW_DENOISED_EXR`. It preserves a raw master, uses 64 pixels
of horizontal wrap context and crops that context away. This is separate from
island denoising and from the baked-emission capture, which needs no second denoise.
Supply absolute paths to this standalone Blender helper.

## Review, promotion and export

Candidates are deliberately marked `accepted: false`. Validate application of
the lightmap, thin charts, shadows, six-direction camera clearance, the panorama
seam and thumbnail framing before copying approved results into source textures.
Restoration is not a promise of byte-identical Cycles results across versions or
hardware. Preserve accepted originals until the replacement has been reviewed.

The four lightmap masters live in `masters/`; scene lighting capture is `capture/capture.exr`
and thumbnail is `thumbnail/thumbnail.jpg`. Promote `masters/combined.exr` under
the environment's configured final lightmap filename. Update the lightmap marker
and World `bjs_environment_image` when paths change. Runtime RGBD/KTX2, Basis and
ENV conversion remain the existing exporter stages; see
[ENV export manual](https://github.com/Chargeuk/BlenderExporter/blob/feature/rgbd-lightmaps/docs/ENV_EXPORT.md) in the repository. Installed
add-ons also require the separately configured Node/Babylon ENV converter.

For inspection, open `snapshot/restored.blend`. The candidate includes copies of
declared source atlases plus its downloaded HDRI, so that snapshot does not depend
on the temporary input folder. Keep the whole candidate directory together.

## Combination freshness and disk buffers

Combination writes `masters/combined.provenance.json`; promote it with the accepted
map and original ownership. Set `combined_provenance` to its relative path in the
config (otherwise the combined EXR path plus `.provenance.json` is used). Plain
baked capture/thumbnail reuse rejects missing or mismatching provenance. Changes
to appearance controls, image bytes, shader properties or wiring require
recombination. Runtime metadata controls and node-editor layout do not. This
guard establishes map/preview correspondence, not physical bake freshness or
visual acceptance. A physical-PBR capture does not require preview provenance.

Mapped images are fresh-loaded and all users remapped, including nested samplers
and packed images. The source file remains unchanged because work runs in a
disposable background process. Required external assets must be declared; a
packed-only or unsaved preview image cannot establish external-file provenance.

The runner preserves original island IDs during reuse even if scene enumeration
changes; actual object/chart UV triangles must still match exactly. It never
reconstructs original ownership from the fully dilated ID image.

## Supported surface scope

Baked capture assigns one `preview_material` to every receiver slot and uses one UV1 colour atlas. Physical source materials may differ, but extra colour atlases, independent visible emission and alpha-cutout foliage are not automatically preserved. Specify and validate a representative preparation/capture/export route before extending beyond this scope. Glass follows its explicit separate role. A material budget exception does not make the common preview support that surface.

Managed initializer graphs have a source-resolution guard (see the lighting-controls manual). After replacing all source lighting/ID images at a new size, explicitly refresh offsets with `update_preview_resolution`, update config and save before running. Preflight reports custom graphs as unverified; inspect their offsets manually. Runtime-only resizing does not change source offsets.

For lighting variants, use separate saved scenes/configurations, map paths, provenance and runtime destinations; share only explicitly unchanged source assets. Shared geometry/UV/material edits require revalidating every affected variant. For open or mixed spaces use a representative clear capture point and review the sky/ground/enclosure balance; this remains a scene capture, not a substitution of the source HDRI.
