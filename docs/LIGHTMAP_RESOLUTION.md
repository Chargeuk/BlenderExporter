# Lightmap export resolution (3.3.10)

The KTX2 export dialog offers Original (default), 2048, 1024 and 512 square. This is a maximum size: smaller images are not enlarged. Fixed-size export requires a square source. Material atlases, skybox faces and ENV resolution are independent.

The full-resolution source is never modified. Filter decoded scene-linear RGB with area averaging, then encode RGBD and perform the usual vertical flip and UASTC compression. Never resize an already RGBD-encoded RGBA texture: alpha stores the divisor and is not transparency. LDR sRGB sources are decoded before filtering and re-encoded afterwards. The report records source and delivery dimensions and recomputes the RGBD divisor bound from the resized image.

This generic export filter averages the already dilated atlas; it does not have mesh/island ownership. Narrow UV islands and boundaries can lose detail at reduced sizes. Review the delivered texture on the model. No repacking or new physical bake is performed by this option.

Python staged export: `ktx_options={..., 'lightmap_size': 1024}`. Standalone conversion manifest: marker lightmap texture spec `"size": 1024`. Omission or zero preserves original resolution. Existing conversions therefore retain their dimensions.

## Accepted example

On 2026-09-22 the user confirmed that the 1024-square RGBD map for grandBedroomDayRealisticV3 looks fine in KaDshow. Its UASTC file is 895,762 bytes versus 3,348,649 bytes for the earlier 2048 map. Keep Original as the generic default: acceptable resolution depends on the environment and UV allocation.
