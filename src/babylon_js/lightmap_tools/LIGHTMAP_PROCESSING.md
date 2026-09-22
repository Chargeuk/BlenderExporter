# Shared lightmap processing - BlenderExporter 3.3.13

Use these tools for any environment. They ship inside the installed addon ZIP,
but run **separately from export**, in an external Python interpreter. They do
not start a bake, edit a Blender scene, install dependencies or approve a result.
Processing algorithm version 1.0 retains the previously tested mirrored-island
method. Keep scene-specific inputs/settings in the environment's README instead
of copying these scripts into each project.

## Locate the command

From a source checkout: `src/babylon_js/lightmap_tools/cli.py`.
From an installed addon: `babylon_js/lightmap_tools/cli.py` and this guide are
included in the ZIP. In Blender's Python Console, locate the installed copy:

```python
import babylon_js
from pathlib import Path
print(Path(babylon_js.__file__).parent / 'lightmap_tools' / 'cli.py')
```

Run the file by path. Do not use `python -m babylon_js...` from an ordinary Python
installation: importing the parent addon requires Blender's `bpy`. This CLI does
not require Blender to be open and loads processing dependencies only on demand.

## Dependencies and preflight

- Processing Python: NumPy and SciPy; Pillow only with `--diagnostics`.
- OpenImageDenoise shared library and its dependencies. The verified host uses
  OIDN 2.5.0 bundled with Blender 5.2.2, with the default CUDA device.
- Optional EXR-writing Python: NumPy and OpenImageIO. Blender's bundled Python
  supplied these on the verified host, but did not supply SciPy.

Example PowerShell; adapt installation paths on another machine:

```powershell
$processingPython = 'C:\Program Files\Python310\python.exe'
$tools = 'H:\code\babylonJs\BlenderExporter\src\babylon_js\lightmap_tools\cli.py'
$imagePython = 'C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe'
$oidn = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.shared\OpenImageDenoise.dll'
& $processingPython $tools --version
& $processingPython $tools check --oidn-library $oidn --image-python $imagePython --diagnostics
if ($LASTEXITCODE -ne 0) { throw 'Lightmap dependencies are not ready.' }
```

`check` tests imports, actually initialises OIDN and filters a small probe, and
checks linear full-float EXR writing/reading in a disposable temporary directory.
It returns structured errors and nonzero status when a dependency fails. Nothing
is silently installed or substituted. Windows is validated; other platforms need
their own dependency/library verification. Preflight also runs before `process`
and fails before creating its output directory if dependencies are unavailable.

## Input contract and command

Prepare these through the environment's Blender bake/UV extraction procedure:

- Raw array: finite float32 square `H x W x 4`, **decoded scene-linear Rec.709**,
  top-left orientation, zero bake margin, binary original-coverage alpha. Black
  RGB can be valid coverage. Do not pass already dilated or display-encoded data.
- Labels: matching integer `H x W`, 0 outside original coverage and a positive
  ID per UV island. `labels > 0` must exactly equal raw coverage. Conflicting UV
  ownership must be resolved during extraction, not guessed during processing.
- Optional catalogue: JSON list of records with unique positive integer `id`
  fields. Object/UV information supports diagnostics of unsampled charts.

```powershell
# Paths below are examples: use the prepared inputs and a NEW scratch directory.
$raw = 'D:\scratch\environment\indirect_raw.npy'
$labels = 'D:\scratch\environment\island_labels.npy'
$catalogue = 'D:\scratch\environment\islands.json'
$candidate = 'D:\scratch\environment\indirect_processed_new'
& $processingPython $tools process `
  --raw $raw --labels $labels --catalogue $catalogue --output $candidate `
  --oidn-library $oidn --image-python $imagePython `
  --context 8 --confirm-linear-zero-margin --diagnostics
if ($LASTEXITCODE -ne 0) { throw 'Processing failed; inspect the report/output.' }
```

The existing output directory is refused. Run direct and indirect passes separately
with separate candidates. Without `--image-python`, the tool explicitly produces
arrays only; that is not finished EXR delivery. Processing is separate from the
2048 fixed-sample requirement for final indirect **baking**.

## Preserved algorithm

1. Crop each island with 8 pixels of temporary context by default.
2. Reflect padding coordinates into the same island, up to 32 reflection attempts,
   with nearest-owned-pixel fallback for thin/concave regions. Never borrow RGB
   from another island. This mirror expansion is temporary denoising context.
3. Denoise with OIDN RT, HDR, HIGH, colour only, inputScale1.
4. Copy only cleaned owned interiors back to the atlas.
5. Fill unowned gaps using ordinary nearest-owned-pixel dilation. Do not change
   cleaned interiors or expand UV spacing to make room for temporary context.

The algorithm and writer are unchanged by moving them into the addon. New work
adds dependency preflight, a supported entry point and plugin-version reporting.

## Output and review

Outputs: `interiors.npy`, `final.npy`, corresponding full-float linear EXRs when
requested, `report.json`, and optional diagnostic images. The report records
plugin/algorithm versions, inputs and code hashes, OIDN version/device/library,
island and padding counts, exact preservation/round-trip checks and elapsed time.

`candidate_processing_verified` means numerical processing succeeded; visual
acceptance remains false until the environment's bake review. Reconnect every
dependent Blender image sampler after installing a new result, including adaptive
neighbour taps. Do not overwrite accepted masters merely because processing passed.

The tool does not generate raw bakes, UV ownership labels, the live lighting shader,
combined-map flattening, or the room HDR panorama. Their settings and ordering stay
in each environment's restoration instructions. No automatic export hook is added.

## Regression checks

Repository tests include five saved OIDN crops, synthetic coverage/ownership cases,
invalid-input rejection, output-preservation checks and CLI integration. Fixture
outputs use a maximum absolute difference of 0.000002 on the verified host; changed
hardware results require investigation rather than silently increasing tolerance.
Tests and fixtures stay in the repository, outside the installed addon ZIP.
