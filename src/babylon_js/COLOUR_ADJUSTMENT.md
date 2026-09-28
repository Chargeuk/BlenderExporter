# Optional UV2 colour adjustment

Exporter 3.3.18 and the matching KaDshow client support an optional 512-square
RGBA colour-finishing map. 1024-square is available for scenes whose smaller
islands need it. This is independent of the shared albedo atlas and lightmap.

## Meaning and colour space

The PNG and KTX2 RGB channels store **sRGB(linear multiplier / 2)**. Multipliers
range from 0 to 2 per channel. Alpha is linear influence, not opacity. The shader
uses `baseColour * mix(1, 2 * decodedRGB, alpha * strength)` before PBR lighting.
It therefore preserves underlying grain/detail and applies to both baked and
realtime lighting. Metallic reflection colour follows the adjusted base colour;
dielectric reflection colour, roughness, emission and geometry are unchanged.

Neutral new maps have alpha zero. A multiplier of 1 with alpha 1 has small
8-bit/compression quantisation error; use zero alpha for exact neutrality.
Black base colour cannot be lightened by a multiplicative layer.

Blender images must use sRGB and **Channel Packed** alpha. Never connect map
alpha to material opacity. Runtime shaders decode sRGB exactly once, with a
hardware-decode guard; they never gamma-correct alpha. The map uses UV2 without
the material texture's repeat, scale or parallax offset.

## Authoring

1. Save the scene and create the existing managed split-lighting preview first.
2. Search F3 for **KaDshow: Create colour adjustment map**. Choose a new PNG
   path, normally `//textures/colour_adjustments/colour_adjustment.png`, and
   512 or 1024. The operator refuses to overwrite an existing map.
3. Select lightmap receiver meshes and run **KaDshow: Tint selected UV2
   surfaces**. Enter linear RGB multipliers and influence, for example
   `(1.10, 0.78, 0.56)` with influence `0.8` for warmer wood. This writes the
   external PNG immediately; preserve a versioned copy before further painting.
4. Use **Colour Adjustment Strength** on the existing lighting node for the
   overall blend. The layer is inserted before material-colour multiplication;
   physical source PBR materials and transport bakes are preserved.
5. Inspect the mapped surfaces and adjacent islands. The tool reports triangles
   that hit no texel centres; this is a resolution warning, not proof of an
   invalid mesh. Prefer broad adjustments. Use 1024 or improve allocation when
   a required small surface has insufficient coverage. Do not change lighting
   UVs without refreshing corresponding maps.

Python API: `colour_adjustment.create_image(path, size)`, `attach(scene, image,
uv='SimpleBake', strength=1)`, `tint_objects(image, objects, multiplier, alpha,
selected_faces=False)`, and `pad_gaps(image, all_receivers)`. UV2 must already
be non-overlapping and match the exported geometry. The tint operator is for
object selections; the Python API can restrict edits to selected faces.

Padding fills only unoccupied texels, copying colour and influence from the
nearest occupied neighbours. It does not enlarge islands or overwrite another
receiver. Save explicit image versions alongside Blender milestones if both
must remain independently reproducible.

## Export

Opening the dialog reads the saved layer's presence and strength. **Export
colour adjustment**, strength and size remain editable; the actual dialog
values are serialized. Scripted operator calls must explicitly enable it.
KTX2 conversion is required. A missing/dirty/packed source or unsupported size
is rejected. Downsampling averages alpha-weighted colour in linear space.

The extra KTX2 uses the existing colour codec setting (ETC1S/Basis-LZ by
default), sRGB RGB and preserved influence alpha. **No mipmaps** are generated
for this tightly packed UV2 atlas, to avoid distant mip levels mixing islands.
The colour map is flipped consistently with the existing export setting.

The existing lightmap parent receives:

```json
"kadshowColourAdjustment": {
  "version": 1,
  "texture": "room_colour_adjustment.ktx2",
  "coordinatesIndex": 1,
  "encoding": "srgb-linear-multiplier2-alpha",
  "strength": 1
}
```

The staged converter validates and publishes the companion and metadata with
the rest of the package. The map adds one runtime sampler/lookup to affected
materials and does not add meshes or material groups. Absent metadata preserves
legacy behaviour with no extra lookup. KaDshow warns and uses unchanged colour
for invalid metadata or a missing companion. Upload the matching `.babylon`,
manifest, lightmap and colour-adjustment KTX2 together.

## AgX, bakes and captures

The shared illumination evaluator explicitly bypasses this layer. It must not
be multiplied into the lightmap and then applied again by the client. AgX
export instead bakes **adjusted albedo** on disposable PBR copies, using the
selected export map size and strength, and computes compensation from that
colour. No source PBR material is changed. Uncompensated linear lightmaps need
no rebake for a colour-finishing change.

Regenerate AgX compensation after colour changes. The development-only
KaDshow strength slider gives a useful temporary comparison, but the fixed
AgX-compensated lightmap cannot exactly follow a changed colour in realtime.
Re-export the chosen value for the intended match. The user still controls
Baked Diffuse Preservation independently.

Managed Blender previews and baked HDR captures contain the layer naturally;
their appearance hashes include it. Recombine/prove provenance and refresh
captures/thumbnails before final delivery. Physical-material renders/bakes do
not incorporate this finishing overlay into bounced light. For a substantial
physical material change, edit the source material and rebake instead.

## Proven checks

- Native tests: alpha 0/0.5/1, strength zero, saved controls, exact unchanged
  illumination, source-material preservation, 1024-to-512 alpha-weighted
  downsampling, filename rewriting, explicit disabled export.
- Real KTX2/WebGL: 24 strength/alpha/colour cases; maximum 3/255 rendered code
  error with ETC1S; absent map equals strength zero; opacity unaffected.
- Pub proof: 512 map with three local tint regions; actual KaDshow parser,
  shader, KTX decoder and merging preserve 17,400 triangles and five groups.
  Harness checks are not a claim of authenticated upload or user acceptance.
