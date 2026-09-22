# PBR export material calibration

Default metallic **1.0**, roughness **0.8**, with editable overrides. These are
multipliers of packed channels or untextured source values, not replacements for
material variation or a universal physical equivalence between renderers. See
[runtime lighting profiles](LIGHTING_PROFILE.md) for separate shader settings.

Babylon multiplies the blue metallic and green roughness channels by the exported
factors. For example, authored roughness 0.8 multiplied by 0.8 becomes 0.64.
Metallic 0 remains 0. StandardMaterial exports are unchanged. Set both controls
to 1 for unscaled PBR export.

Source materials, node links, images and bake inputs are never modified. Defaults apply to direct API, UI and staged KTX/Basis/ENV exports. Explicit API override:

```python
exporter.execute(context, filepath, objects, ktx_options=options,
                 material_options=dict(metallic=1.0, roughness=0.8))
```

UI arguments are `material_metallic_multiplier` and `material_roughness_multiplier`. Both accept 0..1. Staged reports include `material_multipliers`. Validation rejects non-finite/out-of-range inputs before publishing.

`tests/test_material_export.py` executes real Blender serialization for packed/scalar materials, defaults/neutral/custom settings, staged propagation, source preservation, unaffected StandardMaterials, operator controls and invalid-input preservation.
