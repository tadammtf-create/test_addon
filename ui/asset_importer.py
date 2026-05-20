"""AssetImporter: bridge from service-layer file paths to Blender data-blocks.

This is the **UI-layer** counterpart to the bpy-free service layer. The
service layer (jobs, providers, history) never calls ``bpy``; instead,
when a Generation_Job completes successfully and yields a file path on
disk, the UI dispatcher hands that path to :class:`AssetImporter`, which
loads the file into the active scene as the appropriate data-block:

* 3D models (``.glb``, ``.gltf``, ``.fbx``, ``.obj``) -> imported via
  Blender's matching native importer (Req 16.2). Newly-created top-level
  objects are snapped to the 3D Cursor location captured immediately
  before the import call (Req 16.3).
* Render_Preview output images -> loaded as :class:`bpy.types.Image`
  data-blocks **without** placing a plane in the scene (Req 16.4).
* AI_Texturing output texture maps -> loaded as Image data-blocks and
  wired into a freshly-created Principled BSDF material that is then
  assigned to the named target object (Req 16.5, 6.6, 6.7).

Every public method is **atomic on failure** (Req 16.7, 16.8): before
the operation begins, the importer snapshots the keys of
``bpy.data.objects``, ``bpy.data.materials``, and ``bpy.data.images``;
if any step raises an exception, the importer removes every data-block
that was added since the snapshot and re-raises as :class:`ImportError_`.
The scene is therefore guaranteed to match its pre-import state on every
failure path.

Files whose extension is not in ``SUPPORTED_3D | SUPPORTED_IMAGE`` are
rejected up-front with a UI notification that names both the path and
the rejected extension (Req 16.9).

A class-level :attr:`AssetImporter.last_error` carries the most recent
failure message so a UI panel can render an error banner without having
to subscribe to a separate signal. The same message is also surfaced via
``bpy.context.window_manager.popup_menu`` when one is available, so
interactive sessions get an immediate popup.

The class is otherwise stateless -- every method is safe to call from
any number of UI panels concurrently; they cooperate through Blender's
own data-block layer.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, List, Mapping, Optional

import bpy

logger = logging.getLogger("ai_toolkit")


# Lower-cased extension sets. Membership tests must lower-case the
# inspected extension first so callers don't have to (Req 16.1's
# "evaluated case-insensitively").
SUPPORTED_3D = frozenset({".glb", ".gltf", ".fbx", ".obj"})
SUPPORTED_IMAGE = frozenset({".png", ".jpg", ".jpeg", ".webp", ".exr"})

# Map kinds accepted by :meth:`AssetImporter.attach_texture_maps`. Kept
# in sync with the AI_Texturing module's UI multi-select (Req 6.2).
_VALID_TEXTURE_MAP_KINDS = frozenset(
    {"base_color", "normal", "roughness", "metallic"}
)

# Principled BSDF socket names matching each map kind. ``normal`` is
# routed through an intermediate Normal Map node, so its target socket
# here is the Normal Map node's input rather than the Principled BSDF's.
_PRINCIPLED_SOCKET_FOR_MAP = {
    "base_color": "Base Color",
    "roughness": "Roughness",
    "metallic": "Metallic",
}

# Image color-space hints for non-color data maps. Setting this is
# important for correct shading; without it Blender would treat
# roughness/metallic/normal images as sRGB and gamma-correct them.
_NON_COLOR_MAPS = frozenset({"normal", "roughness", "metallic"})


class ImportError_(Exception):
    """Raised when an asset import fails AND the scene has been rolled back.

    The trailing underscore avoids shadowing the built-in
    :class:`ImportError` (which is reserved for module-import failures).
    Callers should catch :class:`ImportError_` to detect AssetImporter
    failures specifically.
    """


def _ext_of(file_path: str) -> str:
    """Return the lowercase file extension (including the leading dot)."""
    return os.path.splitext(file_path)[1].lower()


class AssetImporter:
    """Imports model files, images, and texture maps into the active scene.

    Public surface:

    * :meth:`accepts` -- static gate used both internally and by the
      history re-import helper in :mod:`services.history.manager`.
    * :meth:`import_model` -- import a 3D model, snap to the cursor.
    * :meth:`validate_model` -- magic-byte sanity check.
    * :meth:`import_image` -- load an image as a data-block.
    * :meth:`attach_texture_maps` -- build a Principled BSDF material
      from a set of texture maps and assign it to a named object.

    All methods that mutate scene state are atomic on failure: every
    data-block they add is removed if any step raises, leaving the
    scene as it was before the call (Req 16.7, 16.8).
    """

    #: Most recent failure message, exposed as a class attribute so a UI
    #: panel can poll it for an inline error banner. Cleared on
    #: successful operations; never raised from this module.
    last_error: str = ""

    # ------------------------------------------------------------------
    # Static gate -- shared with services.history.manager.reimport_entry
    # ------------------------------------------------------------------
    @staticmethod
    def accepts(file_path: str) -> bool:
        """Return ``True`` iff the path's extension is supported.

        Static so the bpy-free service layer can call it via duck
        typing -- :func:`services.history.manager.reimport_entry`
        accepts any object exposing a callable ``accepts(path)``
        attribute (see that module's docstring), and a
        :class:`@staticmethod` satisfies that contract whether the
        attribute is fetched from the class or from an instance.
        """
        return _ext_of(file_path) in (SUPPORTED_3D | SUPPORTED_IMAGE)

    # ------------------------------------------------------------------
    # Notification helper
    # ------------------------------------------------------------------
    @classmethod
    def _notify(cls, message: str) -> None:
        """Log an error and surface it through the UI.

        Two channels:

        1. ``logger.error(message)`` so headless runs (and the addon
           console buffer) capture every failure.
        2. ``bpy.context.window_manager.popup_menu(...)`` for an
           immediate modal popup in interactive sessions. Wrapped in a
           defensive try/except so a missing ``window_manager`` (e.g.
           during early register() calls) cannot break the import flow.

        Also stamps :attr:`last_error` so a UI panel can render the
        message inline without subscribing to a separate signal.
        """
        cls.last_error = message
        logger.error("AssetImporter: %s", message)
        try:
            wm = getattr(bpy.context, "window_manager", None)
            popup = getattr(wm, "popup_menu", None) if wm is not None else None
            if callable(popup):
                def _draw(self, _context):  # noqa: ANN001 - Blender callback signature
                    self.layout.label(text=message)

                popup(_draw, title="AI Toolkit", icon="ERROR")
        except Exception:  # noqa: BLE001 - never let UI feedback break the importer
            logger.exception("AssetImporter: popup_menu failed")

    # ------------------------------------------------------------------
    # Snapshot / rollback helpers (Req 16.7, 16.8)
    # ------------------------------------------------------------------
    @staticmethod
    def _snapshot_keys() -> tuple:
        """Capture the current keys of objects, materials, and images.

        Returned as a 3-tuple of ``set[str]`` so a single ``before``
        snapshot can be passed around and diffed cheaply.
        """
        return (
            set(bpy.data.objects.keys()),
            set(bpy.data.materials.keys()),
            set(bpy.data.images.keys()),
        )

    @staticmethod
    def _rollback(before: tuple) -> None:
        """Remove every data-block added since ``before``.

        Each collection is rolled back inside its own ``try`` so a
        failure in one rollback (e.g. a stale reference) does not
        abort the rollback of the others.
        """
        before_objects, before_materials, before_images = before
        try:
            for name in set(bpy.data.objects.keys()) - before_objects:
                obj = bpy.data.objects.get(name)
                if obj is not None:
                    bpy.data.objects.remove(obj)
        except Exception:  # noqa: BLE001
            logger.exception("AssetImporter: rollback (objects) failed")
        try:
            for name in set(bpy.data.materials.keys()) - before_materials:
                mat = bpy.data.materials.get(name)
                if mat is not None:
                    bpy.data.materials.remove(mat)
        except Exception:  # noqa: BLE001
            logger.exception("AssetImporter: rollback (materials) failed")
        try:
            for name in set(bpy.data.images.keys()) - before_images:
                img = bpy.data.images.get(name)
                if img is not None:
                    bpy.data.images.remove(img)
        except Exception:  # noqa: BLE001
            logger.exception("AssetImporter: rollback (images) failed")

    # ------------------------------------------------------------------
    # validate_model -- magic-byte sanity check (Req 5.5, 16.x)
    # ------------------------------------------------------------------
    def validate_model(self, file_path: str) -> bool:
        """Return ``True`` iff the file looks like a real model of its declared type.

        Pure-disk operation: never invokes a Blender importer, so it's
        safe to call in tight loops (e.g. for every history entry on
        panel open). Any IO error returns ``False`` rather than raising.

        Per-extension checks:

        * ``.glb`` -- first 4 bytes equal ``b"glTF"`` (the binary glTF
          magic header, GLB spec).
        * ``.gltf`` -- file decodes as UTF-8 and the first non-whitespace
          character is ``{`` (a JSON object). We do not run a full JSON
          parse: a malformed glTF is still rejected later by the
          actual importer, and a parse here would be O(file-size) for
          potentially hundreds of MB of geometry.
        * ``.fbx`` -- either the binary FBX header
          ``b"Kaydara FBX Binary  \\x00\\x1a\\x00"`` (note the two
          spaces -- this is the actual on-disk magic) appears at byte 0,
          OR an ASCII-FBX line contains ``"; FBX"`` somewhere in the
          first ~8 KB of the file.
        * ``.obj`` -- the file decodes as UTF-8/Latin-1 and at least
          one of the first 200 lines starts with ``"v "`` (a vertex
          line). This is the cheapest distinguishing feature of a
          real OBJ file vs. a stray text file.
        * Any other extension -> ``False``.
        """
        ext = _ext_of(file_path)
        try:
            if ext == ".glb":
                return self._validate_glb(file_path)
            if ext == ".gltf":
                return self._validate_gltf(file_path)
            if ext == ".fbx":
                return self._validate_fbx(file_path)
            if ext == ".obj":
                return self._validate_obj(file_path)
        except (OSError, IOError) as exc:
            logger.warning(
                "AssetImporter.validate_model IO error for %s: %s",
                file_path,
                exc,
            )
            return False
        except Exception:  # noqa: BLE001 - never raise from validate
            logger.exception(
                "AssetImporter.validate_model unexpected error for %s",
                file_path,
            )
            return False
        return False

    @staticmethod
    def _validate_glb(file_path: str) -> bool:
        with open(file_path, "rb") as fh:
            return fh.read(4) == b"glTF"

    @staticmethod
    def _validate_gltf(file_path: str) -> bool:
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                # Read up to 8 KB -- enough to find the opening brace
                # without slurping a multi-megabyte JSON file.
                head = fh.read(8 * 1024)
        except UnicodeDecodeError:
            return False
        stripped = head.lstrip()
        return bool(stripped) and stripped[0] == "{"

    @staticmethod
    def _validate_fbx(file_path: str) -> bool:
        # Binary FBX magic per the FBX spec: 21 bytes "Kaydara FBX
        # Binary  " (with two trailing spaces) followed by NUL, 0x1a,
        # NUL. 23 bytes total.
        binary_magic = b"Kaydara FBX Binary  \x00\x1a\x00"
        with open(file_path, "rb") as fh:
            head_bytes = fh.read(max(len(binary_magic), 8 * 1024))
        if head_bytes.startswith(binary_magic):
            return True
        # ASCII-FBX path. Decode with errors='replace' so stray bytes
        # in an otherwise-text file don't abort the check.
        try:
            head_text = head_bytes.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - decoding fallback
            return False
        return "; FBX" in head_text

    @staticmethod
    def _validate_obj(file_path: str) -> bool:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i >= 200:
                        break
                    if line.startswith("v "):
                        return True
        except (OSError, IOError):
            return False
        return False

    # ------------------------------------------------------------------
    # import_model (Req 16.1-16.4, 16.7-16.9)
    # ------------------------------------------------------------------
    def import_model(self, file_path: str) -> List[Any]:
        """Import a 3D model file and snap newly-created objects to the cursor.

        Returns the list of newly-created top-level Blender objects
        (those whose ``parent`` is ``None``). Raises
        :class:`ImportError_` on any failure, after rolling back every
        data-block added during the attempt.
        """
        ext = _ext_of(file_path)
        if ext not in SUPPORTED_3D:
            self._notify(
                f"Unsupported 3D model extension '{ext}' for path: {file_path}"
            )
            raise ImportError_(
                f"Unsupported 3D model extension '{ext}' for path: {file_path}"
            )

        before = self._snapshot_keys()
        before_objects = before[0]

        # Capture the cursor BEFORE the import so the saved location
        # reflects the user's intent at submission time, not where the
        # cursor happens to be when the import call returns.
        try:
            cursor_loc = bpy.context.scene.cursor.location.copy()
        except Exception as exc:  # noqa: BLE001
            self._notify(
                f"AssetImporter could not read cursor location: {exc}"
            )
            raise ImportError_(
                f"Could not read cursor location: {exc}"
            ) from exc

        try:
            self._dispatch_model_import(ext, file_path)
        except Exception as exc:  # noqa: BLE001 - we always rollback
            self._rollback(before)
            message = (
                f"Failed to import 3D model {file_path}: "
                f"{exc.__class__.__name__}: {exc}"
            )
            self._notify(message)
            raise ImportError_(message) from exc

        # Diff the objects collection to find newly-imported objects,
        # then snap each top-level (parent-less) object to the captured
        # cursor location (Req 16.3).
        after_object_names = set(bpy.data.objects.keys())
        new_object_names = after_object_names - before_objects

        new_objects: List[Any] = []
        for name in new_object_names:
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            new_objects.append(obj)

        try:
            for obj in new_objects:
                if getattr(obj, "parent", None) is None:
                    obj.location = cursor_loc
        except Exception as exc:  # noqa: BLE001 - mutation failure must roll back
            self._rollback(before)
            message = (
                f"Failed to position imported objects from {file_path}: "
                f"{exc.__class__.__name__}: {exc}"
            )
            self._notify(message)
            raise ImportError_(message) from exc

        # Clear the last_error sentinel on success so a stale message
        # from an earlier failure does not linger in a UI panel.
        type(self).last_error = ""
        return new_objects

    @staticmethod
    def _dispatch_model_import(ext: str, file_path: str) -> None:
        """Route to Blender's matching native importer (Req 16.2)."""
        if ext in (".glb", ".gltf"):
            bpy.ops.import_scene.gltf(filepath=file_path)
            return
        if ext == ".fbx":
            bpy.ops.import_scene.fbx(filepath=file_path)
            return
        if ext == ".obj":
            # Blender 4.x exposes the new C++ OBJ importer under
            # bpy.ops.wm.obj_import. The legacy Python importer remains
            # available under bpy.ops.import_scene.obj on older builds
            # and as a Python addon. Try the modern operator first.
            wm_ops = getattr(bpy.ops, "wm", None)
            obj_import = getattr(wm_ops, "obj_import", None) if wm_ops else None
            if callable(obj_import):
                obj_import(filepath=file_path)
                return
            bpy.ops.import_scene.obj(filepath=file_path)
            return
        # The caller already validated ext membership in SUPPORTED_3D,
        # so reaching here means SUPPORTED_3D and this dispatch table
        # have drifted apart -- surface that loudly.
        raise ImportError_(f"No native importer registered for extension '{ext}'")

    # ------------------------------------------------------------------
    # import_image (Req 16.4, 7.10)
    # ------------------------------------------------------------------
    def import_image(self, file_path: str, *, as_plane: bool = False) -> Any:
        """Load the file as a :class:`bpy.types.Image` data-block.

        ``as_plane=False`` (the default and the value Render_Preview
        uses) loads the image into ``bpy.data.images`` only; no plane
        is added to the scene (Req 16.4). ``as_plane=True`` additionally
        invokes ``bpy.ops.import_image.to_plane`` when that operator is
        available; missing operators are tolerated silently so the call
        still succeeds at loading the data-block.
        """
        ext = _ext_of(file_path)
        if ext not in SUPPORTED_IMAGE:
            self._notify(
                f"Unsupported image extension '{ext}' for path: {file_path}"
            )
            raise ImportError_(
                f"Unsupported image extension '{ext}' for path: {file_path}"
            )

        before = self._snapshot_keys()

        try:
            image = bpy.data.images.load(file_path, check_existing=True)
        except Exception as exc:  # noqa: BLE001
            self._rollback(before)
            message = (
                f"Failed to load image {file_path}: "
                f"{exc.__class__.__name__}: {exc}"
            )
            self._notify(message)
            raise ImportError_(message) from exc

        if as_plane:
            # The "Images as Planes" addon ships with Blender but is not
            # always enabled. Best-effort dispatch -- the data-block has
            # already been loaded, so a missing operator is not fatal.
            try:
                import_image_ops = getattr(bpy.ops, "import_image", None)
                to_plane = (
                    getattr(import_image_ops, "to_plane", None)
                    if import_image_ops is not None
                    else None
                )
                if callable(to_plane):
                    to_plane(
                        files=[{"name": file_path}],
                        directory=os.path.dirname(file_path),
                    )
            except Exception:  # noqa: BLE001 - best-effort
                logger.exception(
                    "AssetImporter: as_plane import failed for %s; "
                    "image data-block was loaded but no plane was placed",
                    file_path,
                )

        type(self).last_error = ""
        return image

    # ------------------------------------------------------------------
    # attach_texture_maps (Req 6.6, 6.7, 16.5, 16.6)
    # ------------------------------------------------------------------
    def attach_texture_maps(
        self,
        object_name: str,
        material_name: str,
        texture_files: Mapping[str, str],
    ) -> Any:
        """Create a Principled BSDF material from texture maps and assign it.

        ``texture_files`` is a mapping of map kind -> filesystem path,
        where map kind is one of ``"base_color"``, ``"normal"``,
        ``"roughness"``, ``"metallic"``. Each file is loaded as an
        :class:`bpy.types.Image`; a fresh material named ``material_name``
        is created (Blender will auto-suffix ``.001`` etc. if the name
        is already taken); each map is wired into the matching
        Principled BSDF socket (with ``normal`` routed through an
        intermediate Normal Map node); and the material is assigned to
        the object identified by ``object_name``.

        Returns the new material data-block. Raises :class:`ImportError_`
        on any failure, after rolling back every newly-created Image,
        Material, and Object back to the pre-call snapshot
        (Req 16.7, 16.8).
        """
        if not texture_files:
            self._notify(
                f"AssetImporter.attach_texture_maps called with no textures "
                f"for object '{object_name}'"
            )
            raise ImportError_(
                f"No textures supplied for object '{object_name}'"
            )

        target_obj = bpy.data.objects.get(object_name)
        if target_obj is None:
            self._notify(
                f"AssetImporter.attach_texture_maps: object '{object_name}' "
                f"not found in scene"
            )
            raise ImportError_(
                f"Object '{object_name}' not found in scene"
            )

        # Validate every entry up-front before we touch any data-block,
        # so an obviously bad request fails without leaving partial
        # state on the rollback path.
        for map_kind, path in texture_files.items():
            if map_kind not in _VALID_TEXTURE_MAP_KINDS:
                self._notify(
                    f"AssetImporter.attach_texture_maps: unknown map kind "
                    f"'{map_kind}' (expected one of "
                    f"{sorted(_VALID_TEXTURE_MAP_KINDS)})"
                )
                raise ImportError_(
                    f"Unknown texture map kind '{map_kind}'"
                )
            ext = _ext_of(path)
            if ext not in SUPPORTED_IMAGE:
                self._notify(
                    f"AssetImporter.attach_texture_maps: unsupported texture "
                    f"extension '{ext}' for {map_kind} path: {path}"
                )
                raise ImportError_(
                    f"Unsupported texture extension '{ext}' for path: {path}"
                )

        before = self._snapshot_keys()

        try:
            material = self._build_textured_material(
                material_name, texture_files
            )
            self._assign_material_to_object(target_obj, material)
        except ImportError_:
            # _build_textured_material has already rolled back and
            # notified; just re-raise.
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(before)
            message = (
                f"Failed to attach texture maps to object '{object_name}': "
                f"{exc.__class__.__name__}: {exc}"
            )
            self._notify(message)
            raise ImportError_(message) from exc

        type(self).last_error = ""
        return material

    def _build_textured_material(
        self,
        material_name: str,
        texture_files: Mapping[str, str],
    ) -> Any:
        """Load images, create the material, and wire up the node graph.

        On any failure, rolls back every data-block created here and
        raises :class:`ImportError_`. The caller (:meth:`attach_texture_maps`)
        relies on this to keep its own rollback path simple.
        """
        before = self._snapshot_keys()

        # 1. Load every image first so a missing texture aborts before
        #    we create the material. This minimises the rollback set.
        loaded_images: dict = {}
        try:
            for map_kind, path in texture_files.items():
                image = bpy.data.images.load(path, check_existing=True)
                # Tag non-color maps so Blender does not gamma-correct
                # them. Defensive getattr keeps stub objects happy.
                if map_kind in _NON_COLOR_MAPS:
                    cs = getattr(image, "colorspace_settings", None)
                    if cs is not None:
                        try:
                            cs.name = "Non-Color"
                        except Exception:  # noqa: BLE001 - stubs may be read-only
                            pass
                loaded_images[map_kind] = image
        except Exception as exc:  # noqa: BLE001
            self._rollback(before)
            message = (
                f"Failed to load texture images for material "
                f"'{material_name}': {exc.__class__.__name__}: {exc}"
            )
            self._notify(message)
            raise ImportError_(message) from exc

        # 2. Create the material. Blender auto-suffixes when ``material_name``
        #    collides, which satisfies the "don't reuse" rule by giving us
        #    a unique data-block.
        try:
            material = bpy.data.materials.new(material_name)
            material.use_nodes = True
        except Exception as exc:  # noqa: BLE001
            self._rollback(before)
            message = (
                f"Failed to create material '{material_name}': "
                f"{exc.__class__.__name__}: {exc}"
            )
            self._notify(message)
            raise ImportError_(message) from exc

        # 3. Wire each image into the Principled BSDF node tree.
        try:
            self._wire_principled_bsdf(material, loaded_images)
        except Exception as exc:  # noqa: BLE001
            self._rollback(before)
            message = (
                f"Failed to wire texture nodes for material "
                f"'{material_name}': {exc.__class__.__name__}: {exc}"
            )
            self._notify(message)
            raise ImportError_(message) from exc

        return material

    @staticmethod
    def _wire_principled_bsdf(material: Any, loaded_images: Mapping[str, Any]) -> None:
        """Insert ShaderNodeTexImage nodes and connect them.

        The default Blender material created with ``use_nodes = True``
        already contains a ``Principled BSDF`` node and a
        ``Material Output`` node connected to it; we only have to add
        the texture nodes and the (single) Normal Map node, then link
        them in.
        """
        node_tree = material.node_tree
        nodes = node_tree.nodes
        links = node_tree.links

        principled = nodes.get("Principled BSDF")
        if principled is None:
            # Some material init paths might not emit the default node;
            # create one rather than failing. The Material Output node
            # will be created the same way if absent.
            principled = nodes.new("ShaderNodeBsdfPrincipled")
            output = nodes.get("Material Output")
            if output is None:
                output = nodes.new("ShaderNodeOutputMaterial")
            links.new(principled.outputs["BSDF"], output.inputs["Surface"])

        for map_kind, image in loaded_images.items():
            tex_node = nodes.new("ShaderNodeTexImage")
            tex_node.image = image

            if map_kind == "normal":
                normal_map_node = nodes.new("ShaderNodeNormalMap")
                links.new(tex_node.outputs["Color"], normal_map_node.inputs["Color"])
                links.new(
                    normal_map_node.outputs["Normal"],
                    principled.inputs["Normal"],
                )
                continue

            socket_name = _PRINCIPLED_SOCKET_FOR_MAP.get(map_kind)
            if socket_name is None:
                # Should be unreachable thanks to the validation pass
                # in attach_texture_maps, but stay defensive.
                continue
            links.new(tex_node.outputs["Color"], principled.inputs[socket_name])

    @staticmethod
    def _assign_material_to_object(target_obj: Any, material: Any) -> None:
        """Append or replace the material on ``target_obj``.

        Replaces slot 0 when at least one slot exists so the new
        AI-generated material visibly takes over from any preview
        material the object was already wearing. Otherwise appends a
        fresh slot.
        """
        obj_data = target_obj.data
        if obj_data is None:
            raise ImportError_(
                f"Object '{getattr(target_obj, 'name', '?')}' has no mesh data; "
                f"cannot attach material"
            )
        materials = obj_data.materials
        if len(materials) == 0:
            materials.append(material)
        else:
            materials[0] = material


__all__ = [
    "AssetImporter",
    "SUPPORTED_3D",
    "SUPPORTED_IMAGE",
    "ImportError_",
]
