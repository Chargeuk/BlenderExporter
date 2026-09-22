# Optional tangent export

Exporter3.3.11 adds **World Properties > Babylon.js > Geometry > Export Tangents** (`bpy.context.scene.world.exportTangents`). The setting is saved in the blend file and respected by direct and staged/KTX exports.

The generic default remains True, preserving the previous behavior: calculate/export tangents for supported meshes with custom split normals and a UV layer. It does not force tangents onto every mesh. Set False for KaDshow environments to omit the tangent arrays, retaining triangle normals and UVs. This reduces file size and avoids a mixed tangent/no-tangent vertex layout splitting runtime batches. Keep needed normals; disabling tangents is not a reason to delete custom split normals.

Tangents provide an explicit UV-aligned surface basis for features such as normal mapping or anisotropic shading. The tested bedroom has neither feature; colour, roughness/metallic and lightmap inputs do not require them. Validate future material features before changing the KaDshow policy.

`tests/test_tangent_export.py` runs in background Blender with `--factory-startup --python-exit-code 1`. It covers the generic enabled default, opt-out in direct and staged exports, unchanged expanded triangle positions/normals/both UV channels, and a saved/reloaded setting. Evidence: `artifacts/tangents-3.3.11/result.json`.

The real-scene verification reduced12 runtime meshes to5 using current KaDshow merge code, with no tangent arrays and19,022 visible triangles. KaDshow naming policy: one main `MainArea_alwaysSelectAsActiveMesh_optimise_chunk`, sibling compact dense cullable groups, and separate unlightmapped glass. This is source-authoring policy, not automatic generic exporter regrouping. See the user's `kadshow-blender-environments/references/runtime-grouping.md` skill reference for implementation and review checks.
