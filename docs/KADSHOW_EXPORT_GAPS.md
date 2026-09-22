# KaDshow exporter contract and scope

Use the current client checkout when checking runtime compatibility. Paths below
are relative to `fe/client/src`; historical source line numbers are not an API.

| Convention | Consumer / required behaviour |
| --- | --- |
| Named empties and `parentId` | Preserve the hierarchy consumed by `Components/interactables/Environment.tsx` and the custom scene loader. |
| `lightmap_<stem>` and UV2 | The loader derives the KTX2 filename from the marker. Verify canonical naming, the actual converted file and encoding metadata. |
| `physicsObjects` and primitive-name tokens | `_types/physicsTypes.ts` constructs fixed bodies from proxies/transforms; successful serialization alone does not prove physics interaction. |
| `navMeshFloor` | Its descendants supply navigation/teleportation surfaces independently of collision proxies. |
| `optimise_chunk` | `Utils/Utility/mergeMeshes.ts` retains intentional merge boundaries. Keep few compatible runtime groups. |
| `alwaysSelectAsActiveMesh` | Use for the broad main area; keep compact dense cullable groups outside its ancestry. Do not set every group always active. |
| `hasenv` / `hasskyboxbasis` | Request `environment.env` / `cubemap.basis`; integrated conversion can add these markers to delivery JSON. |
| Shared PBR colour / packed RM | UV1 samples colour and packed properties; green roughness and blue metallic are data, not sRGB. Glass uses a separate material and no lightmap. |

## Implemented preparation and conversion

- [Saved-scene preparation](ENVIRONMENT_PREPARATION.md): explicit roles, physical
  baking, original ownership, isolated processing, combination, HDR capture and
  thumbnails. The source remains editable and unchanged on disk.
- [Lighting preview](LIGHTING_CONTROLS.md): native saved adjustment graph,
  same-island smoothing and combination/capture freshness checks.
- [KTX2/RGBD](KTX2_EXPORT.md): source-preserving conversion, filename updates and
  explicit HDR encoding with a compatible client.
- [Basis skybox](SKYBOX_EXPORT.md): managed visible-sky evaluation and cubemap
  conversion, with rotation applied once.
- [Room ENV](ENV_EXPORT.md): conversion of an explicitly prepared room panorama.
- [Runtime lighting profile](LIGHTING_PROFILE.md) and [material multipliers](MATERIAL_EXPORT.md):
  editable export values with preserved source materials.
- [Geometry export](GEOMETRY_EXPORT.md): omit tangents for KaDshow; retain normals
  and required UV channels.

## Explicit boundaries

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

The compact accepted reference is `content/environments/grandBedroomDay`.
Its README, configuration and accepted-result record identify actual files and
verification limits. Parser checks, disk conversion, GPU decoding and user
acceptance are separate evidence; verify the stages required by the current task.
