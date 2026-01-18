bl_info = {
    "name": "Vertex Baker",
    "author": "Robin Karlsson",
    "version": (1, 1, 0),
    "blender": (5, 0, 0),
    "location": "Object > VC Baker",
    "description": "Bakes AO and curvature into a packed color attribute and projects it baack onto meshes",
    "category": "Object",
}

import bpy
import os

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

BAKE_SUFFIX = "_bake"
BAKE_COLLECTION = "VC_Bake"
DT_MOD_NAME = "DT_VC_Packed"

# ------------------------------------------------------------
# Utility: Temporarily disable originals in render
# ------------------------------------------------------------

def disable_render_temporarily(objs):
    """
    Disable objects in render and return a dict storing previous states.
    """
    state = {}
    for obj in objs:
        state[obj] = obj.hide_render
        obj.hide_render = True
    return state

def restore_render_state(state):
    """
    Restore hide_render flags from a state dict.
    """
    for obj, was_hidden in state.items():
        obj.hide_render = was_hidden

def store_selection(context):
    return {
        "active": context.view_layer.objects.active,
        "selected": [o for o in context.selected_objects],
    }

def restore_selection(context, state):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in state["selected"]:
        if obj.name in bpy.data.objects:
            obj.select_set(True)
    if state["active"] and state["active"].name in bpy.data.objects:
        context.view_layer.objects.active = state["active"]


def select_bake_objects(context, bake_objs):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in bake_objs:
        obj.select_set(True)
    context.view_layer.objects.active = bake_objs[0]


# ------------------------------------------------------------
# Utility: Load material / node groups
# ------------------------------------------------------------

def load_from_blend():
    addon_dir = os.path.dirname(__file__)
    blend_path = os.path.join(addon_dir, "VC_baker.blend")

    if not os.path.exists(blend_path):
        raise FileNotFoundError("VC_baker.blend not found next to add-on")

    with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):

        if "VC_Curvature" not in bpy.data.materials:
            if "VC_Curvature" in data_from.materials:
                data_to.materials.append("VC_Curvature")

        for ng in ("VC_Smudger", "VC_Packer"):
            if ng not in bpy.data.node_groups:
                if ng in data_from.node_groups:
                    data_to.node_groups.append(ng)

# ------------------------------------------------------------
# Utility: Collections / Duplication
# ------------------------------------------------------------

def ensure_collection(name):
    if name in bpy.data.collections:
        coll = bpy.data.collections[name]
    else:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
        #coll.hide_viewport = True

        # Ensure visible while baking
        coll.hide_viewport = False
        coll.hide_render = False
    return coll

def duplicate_object(obj, suffix, collection):
    new_name = obj.name + suffix

    if new_name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[new_name], do_unlink=True)

    dup = obj.copy()
    dup.data = obj.data.copy()
    dup.name = new_name
    collection.objects.link(dup)
    return dup

# ------------------------------------------------------------
# Utility: Vertex Colors
# ------------------------------------------------------------

def ensure_color_attribute(obj, name):
    if name not in obj.data.color_attributes:
        obj.data.color_attributes.new(
            name=name,
            type='FLOAT_COLOR',
            domain='CORNER'
            
        )

def remove_unused_target_color_attributes(obj):
    """
    Remove unused VC_AO and VC_Curvature color attributes
    from target (original) meshes only.
    """
    for name in ("VC_AO", "VC_Curvature"):
        attr = obj.data.color_attributes.get(name)
        if attr:
            obj.data.color_attributes.remove(attr)

def clean_bake_object_modifiers(obj):
    """
    Remove VC-related and inactive modifiers from bake meshes,
    then apply remaining modifiers to ensure a clean mesh for baking.
    """

    # Must be active + object mode to apply modifiers
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # -------------------------------------------------
    # Pass 1: determine modifiers to remove (by name)
    # -------------------------------------------------
    mods_to_remove = []

    for mod in obj.modifiers:
        # Remove any Data Transfer modifiers
        if mod.type == 'DATA_TRANSFER':
            mods_to_remove.append(mod.name)
            continue

        # Remove Geometry Nodes using VC tools
        if mod.type == 'NODES' and mod.node_group:
            if mod.node_group.name in {"VC_Smudger", "VC_Packer"}:
                mods_to_remove.append(mod.name)
                continue

        # Remove any inactive modifiers
        if not mod.show_viewport:
            mods_to_remove.append(mod.name)

    # -------------------------------------------------
    # Pass 2: remove them safely
    # -------------------------------------------------
    for mod_name in mods_to_remove:
        mod = obj.modifiers.get(mod_name)
        if mod:
            obj.modifiers.remove(mod)

    # -------------------------------------------------
    # Pass 3: apply remaining modifiers (collapse stack)
    # -------------------------------------------------
    for mod in list(obj.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except RuntimeError:
            # Some modifiers may fail to apply; ignore safely
            pass


def bake_with_ui_updates(bake_type):
    bpy.ops.object.bake(type=bake_type)
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def ensure_collection_visible_and_editable(context, collection):
    """
    Ensure the collection is visible, selectable, and not excluded
    in the active view layer.
    """

    collection.hide_viewport = False
    collection.hide_render = False

    # Walk view layer collections to find matching LayerCollection
    def find_layer_collection(layer_coll, target_coll):
        if layer_coll.collection == target_coll:
            return layer_coll
        for child in layer_coll.children:
            found = find_layer_collection(child, target_coll)
            if found:
                return found
        return None

    layer_coll = find_layer_collection(
        context.view_layer.layer_collection,
        collection
    )

    if layer_coll:
        layer_coll.exclude = False
        layer_coll.hide_viewport = False
        layer_coll.collection.hide_viewport = False


def set_active_bake_color(obj, name):
    obj.data.color_attributes.active_color = obj.data.color_attributes[name]

def set_viewport_color(obj, name):
    obj.data.color_attributes.active = obj.data.color_attributes[name]

def prepare_bake_context(context, active_obj, all_objs):
    # Force Object Mode
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.object.select_all(action='DESELECT')

    for obj in all_objs:
        obj.select_set(True)

    context.view_layer.objects.active = active_obj

# ------------------------------------------------------------
# Utility: Data Transfer
# ------------------------------------------------------------

def ensure_datatransfer(obj, src):
    mod = obj.modifiers.get(DT_MOD_NAME)
    if not mod:
        mod = obj.modifiers.new(DT_MOD_NAME, type='DATA_TRANSFER')

    mod.object = src

    # Disable vertex data
    mod.use_vert_data = False

    # Enable corner (loop) color data
    mod.use_loop_data = True
    mod.data_types_loops = {'COLOR_CORNER'}

    # Match by attribute name
    mod.layers_vcol_loop_select_src = 'ALL'
    mod.layers_vcol_loop_select_dst = 'NAME'

    # Mapping
    mod.loop_mapping = 'NEAREST_POLYNOR'

    mod.mix_mode = 'REPLACE'
    mod.mix_factor = 1.0


# ------------------------------------------------------------
# Main Operator
# ------------------------------------------------------------

class OBJECT_OT_vc_bake_project(bpy.types.Operator):
    bl_idname = "object.vc_bake_project"
    bl_label = "VCBake Project"
    bl_options = {'REGISTER', 'UNDO'}

    ao_samples: bpy.props.IntProperty(
        name="AO Samples",
        default=64,
        min=1,
        max=4096
    ) # type: ignore

    def execute(self, context):

        originals = [o for o in context.selected_objects if o.type == 'MESH']
        if not originals:
            self.report({'ERROR'}, "No mesh objects selected")
            return {'CANCELLED'}

        load_from_blend()
        curvature_mat = bpy.data.materials["VC_Curvature"]

        bake_coll = ensure_collection(BAKE_COLLECTION)
        ensure_collection_visible_and_editable(context, bake_coll)

        # ----------------------------------------------------
        # Remember current render engine to restore later
        # ----------------------------------------------------
        original_engine = context.scene.render.engine

        # ----------------------------------------------------
        # Duplicate meshes into bake collection
        # ----------------------------------------------------
        dup_map = {}
        for obj in originals:
            ensure_color_attribute(obj, "VC_Packed")
            ensure_color_attribute(obj, "VC_Preview")
            dup = duplicate_object(obj, BAKE_SUFFIX, bake_coll)
            dup_map[obj] = dup

        bake_objs = list(dup_map.values())

        # ----------------------------------------------------
        # Setup bake meshes
        # ----------------------------------------------------
        for obj in bake_objs:
            obj.hide_set(False)
            obj.hide_viewport = False
            obj.data.materials.clear()
            obj.data.materials.append(curvature_mat)
            ensure_color_attribute(obj, "VC_Packed")
            ensure_color_attribute(obj, "VC_AO")
            ensure_color_attribute(obj, "VC_Curvature")
            ensure_color_attribute(obj, "VC_Preview")

        # Clean bake meshes (modifiers)
        for obj in bake_objs:
            clean_bake_object_modifiers(obj)

        # ----------------------------------------------------
        # Setup render engine and Cycles
        # ----------------------------------------------------
        scene = context.scene
        scene.render.engine = 'CYCLES'
        scene.cycles.device = 'GPU'
        scene.cycles.samples = self.ao_samples
        scene.cycles.use_adaptive_sampling = False
        scene.cycles.light_sampling_threshold = 0.0
        scene.cycles.use_denoising = False

        bake = scene.render.bake
        bake.target = 'VERTEX_COLORS'
        bake.use_selected_to_active = False
        bake.use_multires = False

        # ----------------------------------------------------
        # Helper: bake with mesh-count-based progress bar
        # ----------------------------------------------------
        def bake_with_progress(bake_type, color_name):
            total_meshes = len(bake_objs)
            bpy.context.window_manager.progress_begin(0, total_meshes)

            # Set active color on all bake meshes
            for obj in bake_objs:
                set_active_bake_color(obj, color_name)

            # Select all bake objects
            select_bake_objects(context, bake_objs)

            # Perform the bake
            bpy.ops.object.bake(type=bake_type)

            # Update progress bar proportionally for each mesh
            for i in range(total_meshes):
                bpy.context.window_manager.progress_update(i + 1)

            bpy.context.window_manager.progress_end()

        # ----------------------------------------------------
        # Curvature Bake
        # ----------------------------------------------------
        bake.use_pass_direct = False
        bake.use_pass_indirect = False
        bake.use_pass_color = True
        bake_with_progress('DIFFUSE', 'VC_Curvature')

        # ----------------------------------------------------
        # Ambient Occlusion Bake
        # ----------------------------------------------------
        bake.use_pass_direct = True
        bake.use_pass_indirect = True
        bake.use_pass_color = True

        render_state = disable_render_temporarily(originals)
        try:
            bake_with_progress('AO', 'VC_AO')
        finally:
            restore_render_state(render_state)

        # ----------------------------------------------------
        # Apply Packer Nodes
        # ----------------------------------------------------
        packer = bpy.data.node_groups["VC_Packer"]
        for obj in bake_objs:
            mod = obj.modifiers.new("VC_Packer", 'NODES')
            mod.node_group = packer
            context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=mod.name)
            set_viewport_color(obj, "VC_Packed")

        # ----------------------------------------------------
        # Data Transfer Back to Originals
        # ----------------------------------------------------
        for orig, dup in dup_map.items():
            ensure_datatransfer(orig, dup)
            remove_unused_target_color_attributes(orig)
            if "VC_Smudger" not in orig.modifiers:
                mod = orig.modifiers.new("VC_Smudger", 'NODES')
                mod.node_group = bpy.data.node_groups["VC_Smudger"]

        # Force VC_Packed active on originals
        for orig in originals:
            if "VC_Packed" in orig.data.color_attributes:
                orig.data.color_attributes.active_color = orig.data.color_attributes["VC_Packed"]

        # Hide bake collection
        bake_coll.hide_viewport = True

        # Restore the original render engine
        context.scene.render.engine = original_engine

        # Setup viewport for inspection
        for area in context.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            shading = space.shading
            shading.type = 'SOLID'
            shading.light = 'FLAT'
            shading.color_type = 'VERTEX'
            shading.show_shadows = False
            shading.show_cavity = False
            shading.show_xray = False

        self.report({'INFO'}, "VC Bake + Project completed")
        return {'FINISHED'}


# ------------------------------------------------------------
# Menu / Register
# ------------------------------------------------------------

def menu_func(self, context):
    self.layout.operator(OBJECT_OT_vc_bake_project.bl_idname)

def register():
    bpy.utils.register_class(OBJECT_OT_vc_bake_project)
    bpy.types.VIEW3D_MT_object.append(menu_func)

def unregister():
    bpy.types.VIEW3D_MT_object.remove(menu_func)
    bpy.utils.unregister_class(OBJECT_OT_vc_bake_project)

if __name__ == "__main__":
    register()
