# KaDshow exporter contract and remaining integration boundaries

Use the current client checkout when checking runtime compatibility. Paths below
are relative to `fe/client/src`; historical source line numbers are not an API.
The generic exporter and conversion helpers do not establish that every client
runtime supports a delivered asset.

| Convention | Consumer / required behaviour |
| --- | --- |
| Named empties and `parentId` | Preserve the hierarchy consumed by `Components/interactables/Environment.tsx` and the custom scene loader. |
| `lightmap_<stem>` and UV2 | The loader derives the KTX2 filename from the marker. Verify canonical naming, the converted file and its encoding metadata. RGBD alpha stores the divisor; it is not opacity. |
| `physicsObjects` and primitive-name tokens | `_types/physicsTypes.ts` constructs fixed bodies from proxies/transforms; successful serialization alone does not prove physics interaction. |
| `navMeshFloor` | Its descendants supply navigation/teleportation surfaces independently of collision proxies. |
| `optimise_chunk` | `Utils/Utility/mergeMeshes.ts` retains intentional merge boundaries. Keep few compatible runtime groups. |
| `alwaysSelectAsActiveMesh` | Use for the broad main area; keep compact dense cullable groups outside its ancestry. Do not set every group always active. |
| `hasenv` / `hasskyboxbasis` | Request `environment.env` / `cubemap.basis`; integrated conversion can add these markers to delivery JSON. |
| Shared PBR colour / packed RM | UV1 samples colour and packed properties; green roughness and blue metallic are data, not sRGB. Glass uses a separate material and no lightmap. |

## Included preparation and conversion support

The 3.3.31 package includes optional workflows for:

- Saved-scene preparation, physical baking, UV ownership, native lighting combination, receiver-response capture, and reusable review-camera views. These workflows are driven by the owning Codex skills and scene-specific recipes.
- Split direct/indirect lighting preview, AO grading, physical colour adjustment, and AgX lightmap preparation.
- KTX2 material and lightmap conversion, including `rgbd-v1`, divisor modes, optional region policies, and per-texture codec settings.
- Basis skybox and room ENV conversion.
- Direct spatial-light fields and simple or advanced RGBD environment-lighting volumes.

These are delivery tools with explicit inputs and validation receipts; they do not silently infer a complete asset delivery set from arbitrary tags or generate replacements for stale bake data.

## Integration boundaries

The generic exporter does not choose a KaDshow delivery set from arbitrary role
tags. Select intended visible meshes, helpers and required ancestors; exclude
retained detailed sources, bake-only contributors/lights and diagnostic cameras.
Use physical PBR materials for export and the accepted prepared lightmap.
Opening the export dialog does not regenerate stale maps or room captures.

Light-name tokens `diffuse` and `highlights` control runtime contributions;
`noenvironment` excludes environment meshes from both. They do not determine
Cycles bake participation. Do not introduce a runtime directional light merely
because another environment has one.

Object tags used by preparation are not automatically a supported runtime
metadata schema. Inspect the scalar writers and consumer before extending
object/mesh metadata or assuming arbitrary nested values survive. General
procedural material graphs still need supported image-based delivery materials.

Client compatibility, decoded texture correctness, GPU/runtime behavior, and
visual acceptance are separate evidence. Verify the stages required by the
target client and delivery task.
