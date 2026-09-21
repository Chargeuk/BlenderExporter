# KaDshow exporter contract and remaining gaps

Update for 3.3.5: [optional skybox export](SKYBOX_EXPORT.md) now generates `cubemap.basis` from a panorama, with a selectable face size defaulting to 1024, and creates/updates the delivery-only `hasskyboxbasis` marker. The historical missing-skybox rows below are superseded for exports using this option. Reflection `.env` generation and in-app visual acceptance remain separate.

Update for 3.3.4: [optional KTX2 conversion](KTX2_EXPORT.md) now prepares/converts material images and an explicitly selected or unambiguously identified KaDshow lightmap, updates delivery references, and validates before publishing. The report below describes the initial 3.3.3 artifact. Environment/skybox generation, runtime light authoring, a general KaDshow delivery-selection preset and in-app visual acceptance remain separate work.

Analysis date: 2026-09-21. Read-only source: WSL `/home/d_a_s/code/kadshow/KaDshow_Web`, commit `7191be0b0931e81f60863407bb4590d396685525`. KaDshow was not modified. `code_info` tools were unavailable, so scoped source reads and searches were used. Exporter baseline/fixes and evidence are in [BLENDER_52.md](BLENDER_52.md).

## What already survives the patched export

Paths below are relative to KaDshow's `fe/client/src`.

| Contract | Consumer | Verified export result |
| --- | --- | --- |
| Named empty nodes in `meshes`, with `parentId` | `Utils/babylonjs/kadShowSceneLoader.ts:1012` custom parser | All 673 nodes parse using the actual installed parser/Babylon 7.27.0. Empty markers remain Mesh nodes, so the environment's `result.meshes` searches can find them. |
| `lightmap_<stem>` parent and UV2 | `Components/interactables/Environment.tsx:186`, `:199`, `:806` | Marker is `lightmap_mirror8_final`, with all 633 visible descendants. Both UV streams are present. The loader constructs `<stem>.ktx2`; it does not discover the baked image from a Blender shader. |
| `physicsObjects` and primitive names | `Environment.tsx:356`; `_types/physicsTypes.ts:48` and `:105` | 28 direct child proxies survive, including their `_Box` names. The application builds fixed bodies from names, world/local transforms and bounds, then removes proxy rendering meshes. No custom JSON physics object is required for this path. The parser check does not prove worker/collision behaviour. |
| `navMeshFloor` | `Environment.tsx:266` | One child navigation mesh survives. The named-parent path works without `metadata.ground`; the alternate metadata path looks for any metadata value equal to `ground`. With neither, the loader creates a default 50-unit plane. |
| `optimise_chunk` | `Environment.tsx:837`; `Utils/Utility/mergeMeshes.ts:6` | Five distinct parent groups survive, with 315/49/24/29/216 visible meshes across architecture/sleeping/sitting/working/storage. KaDshow generates chunk metadata from names before merging. |
| `alwaysSelectAsActiveMesh` | `Environment.tsx:855` | The exporter preserves such names if present. This bedroom has none; that is a deliberate available behaviour, not a missing exporter feature. Do not add it everywhere by default. |
| PBR colour and packed RM, UV channel 1 | `Environment.tsx:75`; material parser | One PBR material; green roughness, blue metallic, no roughness from alpha. The packed image now exports `gammaSpace:false`. Source PNG files copy successfully. |

The old terrace-night and kitchen-night `.babylon` files were also inspected. **Neither uses mesh metadata keys** for these environment conventions. Both contain name markers for lightmaps, physics, navigation, skybox/environment and optimisation. This supports retaining the naming contract; it does not establish precisely what the lost exporter modifications did.

## Missing or incomplete for this bedroom

| Item | Current result | Decision still needed |
| --- | --- | --- |
| Runtime lightmap file | The mirrored EXR is supplied, but the loader explicitly requests `mirror8_final.ktx2`. | Add a verified conversion step with correct linear encoding, orientation, compression and mip handling. Do not rename EXR to KTX2. The exporter should identify the asset, while conversion can remain a separate tool. |
| Reflection environment | Source has no `hasenv` marker or matching `environment.env`. Stock top-level `environmentTexture` contains the raw World EXR, but the custom mesh/light import path does not apply it as the KaDshow reflection map. | Produce the prefiltered `.env` from the intended HDRI and add the marker as part of an explicit environment export preparation step. Preserve rotation/lighting correspondence. |
| Skybox | No `hasskyboxbasis` marker / `cubemap.basis`, or alternative skybox marker / six JPEG cube faces. | Choose the existing Basis route or six-face route, generate the companion images and add the matching marker. These are source/package omissions, not names lost by the exporter. `Environment.tsx:899–934` uses fixed companion filenames. |
| Runtime directional light | No runtime light was authored in this scene. The only light is an explicitly bake-only area light and was excluded. | Decide whether this environment should use a runtime light and its contribution. The kitchen has `overhead_diffuse_highlights_noenvironment` (directional, intensity 1.4); terrace-night has no light. Do not copy the kitchen intensity blindly. |
| Delivery selection and stale lightmap name | The generic exporter does not interpret `role` or SimpleBake data, and the source root still used the historical processed map name. | The supplied script fixes selection and map naming in memory for this export. Decide on a maintained KaDshow export preset/manifest rather than relying on exporting every object or the currently displayed diagnostic material. |
| Compressed base/RM textures | PNG colour and RM files export correctly; old environment packages typically reference KTX2 instead. | Compression is a follow-up optimisation, not a hard filename requirement for these material textures. Keep both texture references and converted files in sync. The lightmap is different: its KTX2 extension is hardcoded by KaDshow. |

Light naming behaviour is already handled by the consumer: `diffuse` and `highlights` enable their respective contributions (`Environment.tsx:794`); `noenvironment` excludes merged environment meshes from **both** light contributions (`:679`). `simplifyOnly` depends on simplified materials; conversion is currently disabled (`enableOptimisedConversion=false`), so it is not a suitable new default. These tokens do not automatically exclude a light from a Blender bake.

## Custom-property limitations to consider separately

- Empty scalar properties are exported to `metadata` from `Object.items()`; mesh scalar properties come from **Mesh datablock** properties (`bpyMesh.data.items()`), not Object custom properties. Bedroom `role` and many investigation tags are object properties and are therefore not carried on exported mesh metadata. They are used by the explicit preparation script; the current environment name-based paths do not require them at runtime.
- Booleans, arrays and nested groups are not supported by the existing scalar metadata writers. Strings, integers and floats are supported; quoted strings and Empty fractional serialization were repaired. No automatic object/data merge or precedence policy has been invented in this phase.
- Native Blender rigid-body fields use the upstream Babylon physics serialization, which is a separate path from KaDshow's named proxy/worker system. Restoring arbitrary old `metadata.physics` fields would not by itself connect to the environment's current `createPhysics` implementation.
- A general material graph can still contain unsupported nodes. The bedroom's RGB Separate Color graph is tested. No claim is made that arbitrary procedural materials, node groups or every advanced Principled feature will reproduce identically.

## Recommended next decisions

1. Keep the existing name/hierarchy contract. Add a small explicit KaDshow export-preparation preset if desired: delivery/helper manifest, correct lightmap stem, optional environment/skybox markers and runtime-light selection.
2. Prove companion texture/HDR conversion separately, starting from the preserved source images and HDRI. This is the main blocker to loading the new bedroom with its intended baked lighting.
3. Once those assets exist, load the package in KaDshow and review lighting/materials, collision behaviour, navigation and chunk culling. Current proof is parser/data compatibility, not a completed in-app test.
4. Only extend custom metadata after identifying a specific consumer and defining object-versus-datablock precedence and supported types. The inspected legacy environments do not require that extension to preserve their existing conventions.

No converter, KaDshow code change, marker invention, runtime light or custom metadata schema was added merely to conceal these remaining decisions.
