# Optional KaDshow Basis skybox export (3.3.5)

Enable **Export KaDshow skybox** in File > Export > Babylon.js. The exporter reads a saved 2:1 spherical/equirectangular panorama, prepares six square PNG faces, compresses them into `cubemap.basis` with Basis Universal and adds the exported `hasskyboxbasis` marker used by KaDshow. The source scene and source panorama are not changed or saved.

## Controls

| Control | Default / behaviour |
| --- | --- |
| Export KaDshow skybox | Off. Independent of **Convert textures to KTX2**; enable either or both. |
| Basis executable | Empty searches PATH for a compatible `basisu`; explicit file path overrides it. Tested with 2.50.0. |
| Skybox panorama | Enabling the option suggests a unique connected external World Environment Texture image. Otherwise choose a saved PNG/JPEG/EXR/HDR panorama. |
| Skybox face size | **1024 x 1024**. Choose 256, 512, 1024, 2048 or 4096; applies to each of six faces. |
| Skybox rotation (degrees) | 180, matching the original panorama-to-cubemap website's default orientation convention. |
| Skybox exposure (stops) | 0. Explicit linear-light exposure applied before display conversion. |
| HDR skybox display | Reinhard: luminance-based highlight compression followed by sRGB encoding. Standard instead clips linear RGB to [0,1] before sRGB encoding. HDR inputs only. |
| Skybox ETC1S quality | 255. ETC1S compression level 2; no additional mipmaps; no alpha. |
| Skybox encoder threads | 8. |

The source suggestion finds an image, **not a rendered World**. Node-group internals are not searched. World Mapping nodes, World strength, scene exposure, AgX/Filmic and other World processing are not applied. Set skybox rotation/exposure explicitly and inspect the preview/result against the intended environment. A panorama with a different aspect ratio is rejected. Save packed-only/generated images to an external file first.

LDR input is treated as sRGB. Sampling and exposure are performed in linear light; HDR display choices are ignored for LDR input. HDR inputs retain their source file but the visible skybox output is LDR. The report includes post-display clipping. This does not produce a prefiltered HDR reflection map or `environment.env`.

Six-face projection uses standard cubemap directions, with horizontal panorama wrapping and clamped poles. Faces are written in `+X,-X,+Y,-Y,+Z,-Z` order. The old browser tool's orientation convention is retained; bilinear linear-light interpolation replaces its selectable browser interpolation. Material **Flip images vertically** has no effect on skybox faces. The selected face size is honoured even when it upsamples a small source; higher output resolution does not create missing detail.

Disable Inline textures for staged export. Skybox-only export does not require KTX-Software and leaves material texture formats unchanged. It still uses the shared `.bjs-ktx` staging and `<model>.ktx-report.json` report location.

## Output and scene structure

Deliver `cubemap.basis` beside the `.babylon` model. In the exported JSON only, a unique existing empty skybox marker is renamed to `hasskyboxbasis`, preserving its ID and parent relationships; if absent, a new empty mesh marker is added. Multiple skybox markers or a geometry-bearing skybox marker are rejected. No Empty is created in the live Blender scene.

KaDshow currently checks `hasskyboxbasis` and requests the fixed filename `cubemap.basis`. Multiple environments in one folder would share that filename: use a separate output folder per environment. The loader sets `gammaSpace=true`; new files explicitly mark sRGB instead of copying the unset sRGB bit of old files.

Staging retains `skybox/faces/{px,nx,py,ny,pz,nz}.png`, `settings.json`, the encoder command/log, `info.log` and `validate.log`. Encoding uses explicit `-basis -etc1s -tex_array -tex_type cubemap -srgb` arguments; Basis 2.50 otherwise defaults to KTX2 output. There is no intermediate KTX2 recompression step.

The final Basis file must pass the tool's decode/transcode validation and report six equal square ETC1S faces with one level each. Only then is the package published through the existing backup/rollback mechanism. A skybox preparation or encoding error leaves a previous delivered model/skybox untouched. See [KTX export staging and limits](KTX2_EXPORT.md).

## Existing asset baseline

Inspected directly with `basisu -info` on 2026-09-21:

| Environment | Six-face resolution | File bytes |
| --- | --- | ---: |
| rooftopBar | 1024 x 1024 | 693,592 |
| kitchenLoungeDay | 1024 x 1024 | 908,583 |
| kitchenLoungeNight | 1024 x 1024 | 757,751 |
| terraceDay | 2048 x 2048 | 3,562,496 |
| terraseNight | 2048 x 2048 | 1,225,645 |

All five use ETC1S, six faces, no alpha and one mip level. A 1024 face has one quarter the pixels of a 2048 face; compressed size also depends on the image. The 1024 default follows the majority of these existing environments. Exact old encoder settings cannot be recovered from this header information.

## API

```python
bpy.ops.export.bjs(
    filepath='/output/environment.babylon',
    export_selected=True,
    export_skybox=True,
    skybox_image='/source/panorama.exr',
    skybox_size='1024',
    skybox_rotation=180,
    skybox_exposure=0,
    skybox_tone_map='REINHARD',
    # Optional: convert_to_ktx2=True, with compatible ktx on PATH.
)
```

The existing `JsonExporter.execute(..., ktx_options=...)` staging API also accepts a `skybox` dictionary (`executable`, `image`, `size`, `rotation`, `exposure`, `tone_map`, `quality`, `threads`). Set `convert_materials=False` for skybox-only export. Existing callers that omit this flag continue to convert material textures.

## Validation and boundaries

- `tests/test_skybox_blender.py`: analytic direction-colour panorama checks for all faces, rotation, wrap seam and poles; size selector/default; HDR display controls; invalid inputs; real 256/512 compression; standalone skybox and combined KTX operator; existing/new marker behaviour; source/geometry/hierarchy preservation; failures preserving earlier output; add-on registration.
- `tests/export_skybox_hdri.py`: real source HDRI exported through the operator without setting a size, proving the **1024** default; validates all faces and preserves the source.
- Existing KTX Blender tests remain applicable and are run alongside the new feature.

These tests validate projection and package data. They do not establish full visual acceptance in the running KaDshow application, alignment with arbitrary Blender World graphs, or bit-identical compression to historical PVRTexTool files. Review orientation, horizon, seams and exposure in the target scene before accepting a new skybox.

Reference UI convention: [original panorama converter](https://jaxry.github.io/panorama-to-cubemap/). Encoder: [Basis Universal](https://github.com/BinomialLLC/basis_universal). The projection implementation is independent; no website code or browser runtime is bundled.
