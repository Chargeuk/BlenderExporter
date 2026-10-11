"""Read-only active-scene audit. Execute inside Blender; prints compact JSON.

Counts base mesh geometry, not evaluated modifiers, instances, or final exports.
Does not change selection, mode, scene datablocks, settings, or files.
"""
import collections
import json
import os

import bpy


def _ancestors(obj):
    seen = set()
    while obj is not None and obj.as_pointer() not in seen:
        seen.add(obj.as_pointer())
        yield obj
        obj = obj.parent


def _role(obj):
    names = [node.name.lower() for node in _ancestors(obj)]
    if any("physicsobjects" in name for name in names):
        return "collision"
    if "navmeshfloor" in names:
        return "navigation"
    return "visible"


def _images_from_tree(tree, visited=None):
    if tree is None:
        return []
    if visited is None:
        visited = set()
    key = tree.as_pointer()
    if key in visited:
        return []
    visited.add(key)
    result = []
    for node in tree.nodes:
        if node.type in {"TEX_IMAGE", "TEX_ENVIRONMENT"} and node.image:
            result.append(node.image)
        if node.type == "GROUP":
            result.extend(_images_from_tree(node.node_tree, visited))
    return result


def _image_record(image):
    packed = bool(image.packed_file) or bool(getattr(image, "packed_files", []))
    path = bpy.path.abspath(image.filepath, library=image.library) if image.filepath else ""
    # Tiled/sequence sources require specialised checks, not a literal-path verdict.
    exists = os.path.isfile(path) if image.source == "FILE" and not packed else None
    return {
        "name": image.name,
        "size": list(image.size),
        "source": image.source,
        "path": path,
        "packed": packed,
        "file_exists": exists,
        "colour_space": image.colorspace_settings.name,
    }


def inspect_environment(expect_physics=True, expect_navigation=True):
    scene = bpy.context.scene
    objects = list(scene.objects)
    meshes = [obj for obj in objects if obj.type == "MESH"]
    groups = {role: [obj for obj in meshes if _role(obj) == role]
              for role in ("visible", "collision", "navigation")}
    issues = []

    def issue(code, message, names=()):
        issues.append({"code": code, "message": message,
                       "count": len(names), "examples": list(names)[:12]})

    if expect_physics and not groups["collision"]:
        issue("no_collision_proxies", "No collision meshes under physicsObjects.")
    if expect_navigation and not groups["navigation"]:
        issue("no_navigation", "No meshes under navMeshFloor.")

    missing_uv1, missing_uv2, wrong_uv2, no_material = [], [], [], []
    lightmap_sets = collections.defaultdict(list)
    chunk_counts = collections.Counter()
    always_active = 0
    materials = {}
    for obj in groups["visible"]:
        uv_names = [uv.name for uv in obj.data.uv_layers]
        ancestors = list(_ancestors(obj))
        lightmap = next((n.name for n in ancestors if n.name.startswith("lightmap_")), None)
        if not uv_names:
            missing_uv1.append(obj.name)
        if lightmap:
            lightmap_sets[lightmap].append(obj.name)
            if len(uv_names) < 2:
                missing_uv2.append(obj.name)
            elif "SimpleBake" in uv_names and uv_names[1] != "SimpleBake":
                wrong_uv2.append(obj.name)
        chunks = [n.name for n in ancestors if "optimise_chunk" in n.name.lower()]
        chunk_counts[chunks[0] if chunks else "(unchunked)"] += 1
        if len(chunks) > 1:
            issue("nested_chunk", "Nested chunks need loader-order review.", [obj.name])
        always_active += any("alwaysselectasactivemesh" in n.name.lower() for n in ancestors)
        if not obj.material_slots or any(slot.material is None for slot in obj.material_slots):
            no_material.append(obj.name)
        for slot in obj.material_slots:
            if slot.material:
                materials[slot.material.name] = slot.material
    for code, message, names in [
        ("no_primary_uv", "Visible meshes without primary UVs; inspect intended material use.", missing_uv1),
        ("no_lightmap_uv2", "Lightmap descendants missing UV channel 2.", missing_uv2),
        ("named_uv_order", "SimpleBake exists but is not UV channel 2.", wrong_uv2),
        ("missing_material", "Visible meshes have missing materials or empty slots.", no_material),
    ]:
        if names:
            issue(code, message, names)

    shape_tokens = ("_cylinder", "_sphere", "_capsule", "_cube", "_box", "_cone", "_plane")
    unknown = [obj.name for obj in groups["collision"]
               if not any(token in obj.name.lower() for token in shape_tokens)]
    if unknown:
        issue("unrecognised_collision_shape", "Collision mesh names lack a recognised primitive token.", unknown)

    surface_images = {}
    material_records = []
    for name, material in sorted(materials.items()):
        images = _images_from_tree(material.node_tree)
        for image in images:
            surface_images[image.name] = image
        material_records.append({"name": name, "images": sorted({i.name for i in images})})
    world_images = {i.name: i for i in _images_from_tree(scene.world.node_tree if scene.world else None)}
    images = {**surface_images, **world_images}
    image_records = [_image_record(i) for _, i in sorted(images.items())]
    missing_images = [r["name"] for r in image_records if r["file_exists"] is False]
    if missing_images:
        issue("missing_referenced_image", "Images referenced by used material/world node trees are missing; inactive nodes may be included.", missing_images)
    if not world_images:
        issue("no_world_image", "No image found in World node trees; verify the supplied HDRI lighting setup.")

    geometry = {}
    for role, items in groups.items():
        geometry[role] = {
            "objects": len(items),
            "base_vertices": sum(len(o.data.vertices) for o in items),
            "base_triangle_estimate": sum(max(0, len(p.vertices) - 2) for o in items for p in o.data.polygons),
            "objects_with_modifiers": sum(bool(o.modifiers) for o in items),
        }
    return {
        "file": bpy.data.filepath, "scene": scene.name, "unsaved_changes": bpy.data.is_dirty,
        "blender_version": bpy.app.version_string,
        "units": {"system": scene.unit_settings.system, "scale_length": scene.unit_settings.scale_length},
        "object_types": dict(collections.Counter(o.type for o in objects)),
        "geometry": geometry,
        "uv_layouts": [{"channels": list(channels), "objects": count}
                       for channels, count in collections.Counter(tuple(u.name for u in o.data.uv_layers) for o in meshes).items()],
        "lightmap_groups": {name: len(items) for name, items in lightmap_sets.items()},
        "nearest_chunk_membership": dict(chunk_counts), "always_active_visible_objects": always_active,
        "visible_material_count": len(materials), "materials": material_records,
        "unique_material_image_count": len(surface_images),
        "world_images": sorted(world_images), "referenced_images": image_records,
        "lights": [{"name": o.name, "type": o.data.type, "strength": o.data.energy}
                   for o in objects if o.type == "LIGHT"],
        "issues": issues,
        "limits": "Base geometry only; modifier results, generated instances and runtime merging are not evaluated. Referenced node images can include inactive nodes. Lightmap output images not wired into materials are not counted as material images. UV overlap and visual quality require separate inspection.",
    }


if __name__ == "__main__":
    print(json.dumps(inspect_environment(), indent=2))
