# Optional KTX2 texture export (3.3.4)

Version 3.3.5 also supports [optional Basis skybox export](SKYBOX_EXPORT.md), independently or together with KTX2 conversion. Statements below about skipping cube/World textures describe material conversion; the new explicit skybox option separately generates `cubemap.basis`. Reflection `.env` generation remains separate.

Enable **Convert textures to KTX2** in File > Export > Babylon.js to prepare PNGs, compress material textures, update the `.babylon` references and deliver the finished package. The option is **off by default**; ordinary export follows the existing path.

## Setup and controls

Install [Khronos KTX-Software](https://github.com/KhronosGroup/KTX-Software/releases). The exporter finds `ktx` using the current operating system's PATH and checks its reported version. Version 4.4.2 or newer is required. It does not download or install tools. If missing, incompatible or not executable, the checkbox is unavailable (an already checked option can still be switched off), with a setup message.

An optional **KTX executable** file path overrides PATH. Restart Blender after changing PATH, or set this override. On the development Windows machine, use `H:\code\texture-tools\KTX-Software-4.4.2\bin\ktx.exe`: an older Program Files KTX can otherwise take precedence. No personal executable path is embedded in the add-on.

- **Flip images vertically:** enabled by default, reproducing the pre-flipped Babylon baseline. Only prepared copies are flipped; mesh UVs and original images are unchanged. Disable when your pipeline already supplies correctly oriented images.
- **Material compression:** ETC1S (BasisLZ level 5/quality 255) by default, or UASTC quality 4 with Zstandard level 3.
- **Encoder threads:** defaults to eight.
- **Find KaDshow lightmap automatically:** fills an unassigned lightmap when a unique saved image can be identified; see below.
- **KaDshow lightmap image:** explicit override, including PNG or EXR. A single image/marker pair is supported in this dialog; use the shared standalone configuration for multiple lightmaps.
- **Lightmap marker:** exact `lightmap_...` name in the exported selection. Leave empty if only one is exported.

Inline/base64 textures are incompatible with this mode; disable Inline textures in the World settings. Basis Universal's separate `basisu` executable is not required. The add-on uses Blender's image reader and bundled numpy to write PNGs, with no Pillow/OpenCV installation needed in Blender.

## Automatic lightmap selection

The exporter checks these sources in order:

1. A **`bjs_lightmap_image` string custom property** on the exported `lightmap_...` node. Its value may be an image datablock name or an existing image file path, including Blender-relative `//...` paths. This is the most explicit reusable association.
2. A loaded image whose datablock name or source filename stem matches the part after `lightmap_`.
3. A saved image in an active material graph whose Vector input is explicitly driven by the mesh's **second UV layer**, directly or through intermediate nodes. Unconnected image nodes do not count. Node-group internals are not searched.
4. A unique saved image with `lightmap` or `SimpleBake` in its datablock name or filename.

UV2 is a coordinate set, not itself an image. Selection through UV2/keywords is an inference; inspect the filled field before export. The exporter never chooses a historical bake by timestamp or image-list order. Several candidates at the same priority, several markers without an explicit choice, or an invalid explicit custom property stop conversion with an explanation. If no candidate exists, the lightmap is omitted and a warning/detection note is recorded; ordinary material conversion can still complete. An explicitly selected image takes priority over inference. Turn automatic selection off to intentionally export only material textures.

When conversion is enabled, a unique candidate fills the image field. Headless export also resolves an empty field. Only images with existing external source files are eligible; save packed-only or unsaved baked images first. A `bjs_lightmap_image` property uses the exporter's normal scalar metadata handling, so its value can also appear in the exported node metadata.

The delivery node's name becomes `lightmap_<converted filename stem>` so KaDshow finds the matching KTX2. Its ID and child parent IDs remain unchanged. Source Blender nodes/images are not renamed or saved.

## Conversion policies

- Colour defaults to sRGB. Packed metallic/roughness, ambient occlusion, opacity and bump/normal slots use linear interpretation. Existing data-texture gamma flags are respected. Referenced output texture objects receive matching `gammaSpace` flags.
- Material textures have mipmaps. Bump/normal textures use UASTC. Lightmaps use UASTC with Zstandard and **no mipmaps**, matching the established baseline.
- Transparent alpha is preserved; fully opaque images can be stored as RGB. Legacy KaDshow lighting uses RGB and discards bake alpha. Opt-in RGBD uses RGBA with alpha as a brightness divisor.
- 16-bit PNGs use their high channel byte for the 8-bit prepared PNG, matching the existing standalone baseline (including the bedroom's packed RM source). Ordinary 8-bit PNG codes are preserved.
- In legacy mode, EXR/HDR source RGB is clipped to [0,1], optionally sRGB encoded, then quantized into an 8-bit PNG before compression. No exposure adjustment, AgX/Filmic, denoising, new dilation or HDR KTX codec is applied. The report records how many pixels had above-white values. Original HDR sources stay intact.
- Existing KTX2/DDS/Basis, cube/reflection/refraction and World environment textures are not recompressed. Unconverted local texture references are copied at their referenced relative paths. No `.env`/skybox generation is added.
- Shared material images are converted once. Conflicting colour/data uses, different images that flatten to the same export filename, and conflicting KTX2 output names fail rather than silently overwriting an image.

PNG row reversal and source values are tested; rendering in the actual target engine is still needed to accept orientation and appearance. The current UI supplies one flip setting and a material codec preset. For individual image overrides or multiple models/lightmaps, use the shared standalone manifest interface.

## Files and failure behaviour

The selected export directory receives the `.babylon` model, its converted KTX2 textures, export log and `<model>.ktx-report.json`. Unconverted referenced images retain a `source_textures/` path where necessary, for example the raw World HDRI. Converted source PNGs are not copied into that delivery folder.

Preserved staging is under `.bjs-ktx/<model>/<UTC timestamp>/` within the selected export directory:

- `source_export/`: unconverted export and copied images.
- `texture_conversion.json`: the generated conversion manifest.
- `textures/conversion_runs/`: PNG copies, encoder commands, KTX validation and hashes.
- `package/`: complete delivery set.
- `previous_delivery/`: backups of replaced output files.
- `FAILED.txt`: failure explanation, when applicable.

All conversions validate before publishing. Exporter/encoder failure leaves an existing delivered model/texture set untouched. Copy failures trigger restoration of the previous files. Unrelated files are preserved. This is not a simultaneous filesystem transaction for live viewers; do not serve the output directory while publishing. Large textures make export synchronous and can occupy Blender until conversion completes. Encoder stdout/stderr are recorded and Windows child console windows are hidden.

The implementation temporarily redirects the texture directory into contained staging and restores that setting. It does not save the open `.blend`. Normal exporter selection/camera/bake behaviour remains applicable: this option is **not** a general KaDshow delivery-selection preset. Explicitly select the correct delivery/helper nodes, excluding bake-only/retained source geometry.

## API and shared standalone implementation

The operator supports:

```python
bpy.ops.export.bjs(
    filepath='/output/scene.babylon',
    export_selected=True,
    convert_to_ktx2=True,
    ktx_executable='/path/to/ktx',
    ktx_flip_y=True,
    ktx_codec='basis-lz',
    ktx_auto_lightmap=True,
)
```

`JsonExporter.execute(..., ktx_options={...})` supports the same staged path for scripted exports. The options are `executable`, `flip_y`, `codec`, `threads`, `auto_lightmap`, `lightmap`, `lightmap_marker` and `lightmap_encoding`. Omit `ktx_options` entirely for ordinary export.

The manifest/mapping/encoder/validation code in `src/babylon_js/ktx_conversion.py` is shared with:

```text
python tools/convert_textures.py --config path/to/texture_conversion.json --plan
python tools/convert_textures.py --config path/to/texture_conversion.json
```

Standalone preparation requires Pillow/numpy and OpenCV for EXR/HDR; the Blender adapter supplies its own image preparation backend. A manifest can be based on the one retained by an integrated export. Its `models`, `textures`, `references`, `lightmap_markers`, output paths, flip, colour-space, alpha and compression settings are explicit. Full paths are matched, not basenames or arbitrary text. Unmatched references stay unchanged and appear in the plan.

## Validation evidence

Tested with Windows Blender 5.2.2 LTS and KTX 4.4.2. The path lookup/subprocess implementation supports native Windows and Linux tools; the integrated Blender path has not been exercised under Linux.

- `tests/test_ktx_core.py`: seven tests, including real three-texture/two-model conversion, shared/nested references, exact matching, alpha, missing/ambiguous mappings, output collisions, source overwrite prevention, encoder failure and delivery rollback.
- `tests/test_ktx_blender.py`: actual export operator and API; default off, PATH/override probing, exact PNG codes/alpha/flip, HDR preparation, lightmap conversion, automatic property/name/UV2/keyword inference and ambiguity, geometry preservation, restored settings, failure safety, inline incompatibility and registration cycle.
- `tests/test_blender_compat.py`: existing compatibility fixture still passes with conversion disabled.
- `tests/export_bedroom_ktx.py`: preserved bedroom source exported with three KTX2 textures, 633 visible meshes / **20,000 triangles**, 29 helpers; geometry, UVs, normals and parent relationships unchanged. An in-memory explicit lightmap association tests automatic detection without changing the saved source. This validates data and conversion, not a completed in-app KaDshow rendering review.

Build the installable add-on with `python tools/package_addon.py`. It reads the add-on version and produces `dist/Blender2Babylon-<version>-blender52.zip`.

## Opt-in RGBD lightmaps (3.3.8)

Choose **KaDshow lightmap encoding: RGBD HDR (requires updated KaDshow)** to retain lighting above 1. Legacy LDR remains the default. This requires the client RGBD feature; older clients cannot decode this new package correctly. Existing assets with no encoding metadata continue through the unchanged legacy shader.

Use a saved **linear EXR/HDR** lightmap, such as the accepted direct/indirect blend master. This step does not rebake or change Blender lighting controls. Values must be finite and in [0,255]; values outside the format range fail rather than being silently clipped.

- Operator: `ktx_lightmap_encoding='rgbd-v1'`; scripted `ktx_options`: `lightmap_encoding='rgbd-v1'`.
- The integrated exporter adds `_rgbd` to the lightmap filename. Every `lightmap_` token is removed from the filename stem because KaDshow removes every occurrence when deriving the URL. The marker has exactly one prefix, for example `lightmap_grandBedroom_v089_rgbd` -> `grandBedroom_v089_rgbd.ktx2`. IDs and parent relationships stay intact.
- The marker metadata contains `kadshowLightmapEncoding: "rgbd-v1"` and `kadshowLightmapMinDivisor`, the smallest source alpha divided by 255. Unrelated metadata is preserved.
- Preparation writes gamma-2.2 encoded RGB plus a linear divisor in alpha. KTX2 is **RGBA UNORM**, UASTC quality 4 / Zstandard 3, with **no mipmaps**. Preserve all four channels; do not mark the container sRGB, discard alpha or interpret it as transparency.
- For each linear RGB texel, `D = min(floor(max(255/max(RGB), 1)), 255)/255`; encoded RGB is `(RGB*D)^(1/2.2)`. Black uses D=1. Quantize RGBA to eight bits.
- Client decode is `pow(sample.rgb, 2.2) / max(sample.a, minimumDivisor)`. The source-derived lower bound protects against compression undershooting alpha to zero. If metadata omits the bound, the version-1 default is 1/255. Valid bounds are [1/255,1].
- Apply RGBD metadata to the lightmap texture before material assignment. Set `gammaSpace=false`, `hasAlpha=false`; do not enable automatic Babylon RGBD expansion. Decode once in the custom lightmap plugin. The format pins 2.2 explicitly, independent of engine exact-sRGB options.
- Select decoding per lightmap, not globally. Never infer it from filenames, image alpha, file extension or image brightness. Reject unknown encoding versions clearly.

This is lossy HDR encoding, not lossless storage. Alpha errors affect recovered brightness, especially at high-contrast boundaries. Validate the native compressed output against the linear master, including shadow error and peak values. Numeric recovery does not guarantee an exact Blender appearance: material response, exposure and tone mapping still matter.

Version 3.3.8 also explicitly serializes PBR transparency mode. Materials configured opaque export alpha=1, mode=0 and no albedo-alpha transparency. Explicit cutout/blended modes retain their intent; shared opaque/cutout atlases preserve alpha if any usage needs it.

Regression evidence: `tests/test_rgbd.py` covers the format, opt-in compatibility, per-map bounds and names; `tests/test_rgbd_blender.py` exports and transcodes a real Blender fixture; `tests/export_bedroom_rgbd.py` prepares the separate bedroom package. The browser harness extracts the actual KaDshow parser/plugin and renders both legacy and RGBD packages with native KTX2, Basis and ENV loaders. Its existing UBO warning also occurs on the legacy baseline; it is not a shader compile failure.
