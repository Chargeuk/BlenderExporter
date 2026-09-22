# Shared lightmap tools

Exporter 3.3.13 includes reusable offline lightmap processing for every environment.
The [maintained command and dependency guide](../src/babylon_js/lightmap_tools/LIGHTMAP_PROCESSING.md)
ships in the addon ZIP alongside `lightmap_tools/cli.py`.

Use the shared command rather than copying scripts into individual projects.
Keep scene settings, raw-input preparation and accepted asset hashes in each
environment's documentation. Processing remains independent of model export.
