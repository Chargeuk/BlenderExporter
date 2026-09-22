# Current defaults (3.3.9)

Metallic **1.0**, roughness **0.8**, with editable overrides. These supersede the earlier 0.5/0.4 defaults below. They remain multipliers of packed channels or untextured source values. See [runtime lighting profiles](LIGHTING_PROFILE.md) for the separate shader settings and compatibility rules.

# PBR export calibration â€” 3.3.7

The user compared the bedroom in KaDshow with Blender and selected **metallic 0.5 / roughness 0.4** for the exported shared material. These are the new defaults in this fork, with editable controls in the export dialog. They are visual calibration for this workflow, not a universal physical equivalence between renderers.

The controls multiply exported PBR values. With a packed metallic/roughness texture the previous JSON factors were 1/1; defaults now write 0.5/0.4. Babylon multiplies these by the texture's blue metallic and green roughness channels. For scalar-only materials, the authored scalar is multiplied: roughness 0.8 becomes 0.32 with factor 0.4. A metallic 0 region remains 0. StandardMaterial exports are unchanged. Set both controls to 1 for unscaled PBR export.

Source materials, node links, images and bake inputs are never modified. Defaults apply to direct API, UI and staged KTX/Basis/ENV exports. Explicit API override:

```python
exporter.execute(context, filepath, objects, ktx_options=options,
                 material_options=dict(metallic=.5, roughness=.4))
```

UI arguments are `material_metallic_multiplier` and `material_roughness_multiplier`. Both accept 0..1. Staged reports include `material_multipliers`. Validation rejects non-finite/out-of-range inputs before publishing.

`tests/test_material_export.py` executes real Blender serialization for packed/scalar materials, defaults/neutral/custom settings, staged propagation, source preservation, unaffected StandardMaterials, operator controls and invalid-input preservation.
