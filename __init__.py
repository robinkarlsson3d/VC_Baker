bl_info = {
    "name": "Vertex Baker",
    "author": "Robin Karlsson",
    "version": (1, 1, 0),
    "blender": (5, 0, 0),
    "location": "Object > Vertex Baker",
    "description": "Bakes AO and curvature into a packed color attribute and projects it back onto meshes",
    "category": "Object",
}

import bpy
import os
import datetime
import mathutils

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

BAKE_SUFFIX = "_bake"
BAKE_COLLECTION = "VC_Bake"
DT_MOD_NAME = "DT_VC_Packed"

VC_PACKED = "VC_Packed"
VC_AO = "VC_AO"
VC_CURVATURE = "VC_Curvature"
VC_PREVIEW = "VC_Preview"
VC_GRADIENT = "VC_Gradient"

MAT_CURVATURE = "Mat_Curvature"
MAT_GRADIENT = "Mat_Gradient"

# ------------------------------------------------------------
# UI Properties
# ------------------------------------------------------------

class VertexBakerProperties(bpy.types.PropertyGroup):

    ao_samples: bpy.props.IntProperty(
        name="AO Samples",
        description="Number of samples used for AO and diffuse baking",
        default=32,
        min=1,
        soft_max=512,
        update=lambda self, context: update_vc_processor_sockets(context)
    )# type: ignore


    ao_blur: bpy.props.IntProperty(
        name="AO - Blur",
        default=2,
        min=0,
        soft_max=10,
        update=lambda self, context: update_vc_processor_sockets(context)
    )  # type: ignore


    ao_contrast: bpy.props.FloatProperty(
        name="AO - Contrast",
        default=1.0,
        min=1.0,
        soft_max=5.0,
        update=lambda self, context: update_vc_processor_sockets(context)
    )# type: ignore


    curvature_blur: bpy.props.IntProperty(
        name="Curvature - Blur",
        default=2,
        min=0,
        soft_max=10,
        update=lambda self, context: update_vc_processor_sockets(context)
    )# type: ignore


    curvature_contrast: bpy.props.FloatProperty(
        name="Curvature - Contrast",
        default=1.0,
        min=1.0,
        soft_max=5.0,
        update=lambda self, context: update_vc_processor_sockets(context)
    )# type: ignore
    
    preview_channel: bpy.props.EnumProperty(
        name="Channel",
        description="Which baked channel to preview",
        items=[
            ('0', "Packed", "Preview packed result"),
            ('1', "AO", "Preview ambient occlusion"),
            ('2', "Curvature", "Preview curvature"),
            ('3', "Gradient", "Preview gradient"),
        ],
        default='1',
        update=lambda self, context: update_vc_processor_sockets(context)
    )  # type: ignore

    last_bake_duration: bpy.props.StringProperty(
    name="Last Bake Duration",
    default="—"
    )  # type: ignore

# ------------------------------------------------------------
# Logging Helpers
# ------------------------------------------------------------

def timestamp_now():
    # Time only – bakes can be long, dates are unnecessary
    return datetime.datetime.now().strftime("%H:%M:%S")


def log(message):
    print(f"[Vertex Baker] {message}")


# ------------------------------------------------------------
# Context / Selection Utilities
# ------------------------------------------------------------

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


def prepare_object_mode(context):
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')


def select_objects(context, objects, active=None):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    context.view_layer.objects.active = active or (objects[0] if objects else None)


# ------------------------------------------------------------
# Viewport Setup
# ------------------------------------------------------------

def setup_viewport_for_vertex_colors(context):
    """
    Configure all 3D viewports for flat vertex color inspection.
    """
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

        space.overlay.show_overlays = False


# ------------------------------------------------------------
# Asset Loading
# ------------------------------------------------------------

def load_from_blend():
    addon_dir = os.path.dirname(__file__)
    blend_path = os.path.join(addon_dir, "VertexBaker.blend")

    if not os.path.exists(blend_path):
        raise FileNotFoundError("VertexBaker.blend not found next to add-on")

    with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):

        for mat in (MAT_CURVATURE, MAT_GRADIENT):
            if mat not in bpy.data.materials and mat in data_from.materials:
                data_to.materials.append(mat)

        for ng in ("VC_Processor", "VC_Packer"):
            if ng not in bpy.data.node_groups and ng in data_from.node_groups:
                data_to.node_groups.append(ng)


# ------------------------------------------------------------
# Collection & Duplication
# ------------------------------------------------------------

def ensure_collection(context, name):
    if name in bpy.data.collections:
        coll = bpy.data.collections[name]
    else:
        coll = bpy.data.collections.new(name)
        context.scene.collection.children.link(coll)

    coll.hide_viewport = False
    coll.hide_render = False
    return coll


def ensure_collection_visible_and_editable(context, collection):
    collection.hide_viewport = False
    collection.hide_render = False

    def find_layer_collection(layer_coll, target):
        if layer_coll.collection == target:
            return layer_coll
        for child in layer_coll.children:
            found = find_layer_collection(child, target)
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
# Mesh Preparation (Attributes & Modifiers)
# ------------------------------------------------------------

def ensure_color_attribute(obj, name):
    if name not in obj.data.color_attributes:
        obj.data.color_attributes.new(
            name=name,
            type='FLOAT_COLOR',
            domain='POINT'
        )


def set_active_color_attribute(obj, name):
    """
    Set both the active bake target and viewport-displayed color attribute.
    """
    attr = obj.data.color_attributes.get(name)
    if attr:
        obj.data.color_attributes.active_color = attr
        obj.data.color_attributes.active = attr


def remove_unused_target_color_attributes(obj):
    for name in (VC_AO, VC_CURVATURE, VC_GRADIENT):
        attr = obj.data.color_attributes.get(name)
        if attr:
            obj.data.color_attributes.remove(attr)


def clean_bake_object_modifiers(obj, context):
    """
    Bake meshes must:
    - Have no Data Transfer modifiers
    - Have no VC-related Geometry Nodes modifiers
    - Have no disabled modifiers
    - Have all remaining modifiers applied
    """

    prepare_object_mode(context)
    select_objects(context, [obj], obj)

    mods_to_remove = []

    for mod in obj.modifiers:
        if mod.type == 'DATA_TRANSFER':
            mods_to_remove.append(mod.name)
        elif mod.type == 'NODES' and mod.node_group:
            if mod.node_group.name in {"VC_Processor", "VC_Packer"}:
                mods_to_remove.append(mod.name)
        elif not mod.show_viewport:
            mods_to_remove.append(mod.name)

    for mod_name in mods_to_remove:
        mod = obj.modifiers.get(mod_name)
        if mod:
            obj.modifiers.remove(mod)

    for mod in list(obj.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except RuntimeError:
            pass


# ------------------------------------------------------------
# Baking Helpers
# ------------------------------------------------------------

def configure_cycles_for_baking(scene, samples):
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'GPU'
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.light_sampling_threshold = 0.0
    scene.cycles.use_denoising = False

    bake = scene.render.bake
    bake.target = 'VERTEX_COLORS'
    bake.use_selected_to_active = False
    bake.use_multires = False


def bake_to_color(context, bake_type, bake_objs, color_name):
    for obj in bake_objs:
        set_active_color_attribute(obj, color_name)

    select_objects(context, bake_objs)
    bpy.ops.object.bake(type=bake_type)


def disable_render_temporarily(objs):
    state = {}
    for obj in objs:
        state[obj] = obj.hide_render
        obj.hide_render = True
    return state


def restore_render_state(state):
    for obj, was_hidden in state.items():
        obj.hide_render = was_hidden


# ------------------------------------------------------------
# Projection Helpers
# ------------------------------------------------------------

def ensure_datatransfer(obj, src):
    mod = obj.modifiers.get(DT_MOD_NAME)
    if not mod:
        mod = obj.modifiers.new(DT_MOD_NAME, type='DATA_TRANSFER')

    mod.object = src

    # --- Vertex color transfer ---
    mod.use_vert_data = True
    mod.use_loop_data = False

    mod.data_types_verts = {'VGROUP_WEIGHTS', 'COLOR_VERTEX'}
    mod.layers_vcol_vert_select_src = 'ALL'
    mod.layers_vcol_vert_select_dst = 'NAME'

    # Mapping
    mod.vert_mapping = 'NEAREST'

    # Blend
    mod.mix_mode = 'REPLACE'
    mod.mix_factor = 1.0

    # Visibility
    mod.show_in_editmode = False


# ------------------------------------------------------------
# Bounding Box Helper
# ------------------------------------------------------------

def create_combined_bounding_box(context, objects, collection):
    """
    Create a world-aligned bounding box mesh that encloses all given objects.
    Origin is centered in X/Y and aligned to the bottom in Z.
    """

    if not objects:
        return None

    # --------------------------------------------------------
    # Compute combined world-space bounds
    # --------------------------------------------------------
    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")

    for obj in objects:
        for corner in obj.bound_box:
            world_corner = obj.matrix_world @ mathutils.Vector(corner)
            min_x = min(min_x, world_corner.x)
            min_y = min(min_y, world_corner.y)
            min_z = min(min_z, world_corner.z)
            max_x = max(max_x, world_corner.x)
            max_y = max(max_y, world_corner.y)
            max_z = max(max_z, world_corner.z)

    size_x = max_x - min_x
    size_y = max_y - min_y
    size_z = max_z - min_z

    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    bottom_z = min_z

    # --------------------------------------------------------
    # Remove existing bounding box if present
    # --------------------------------------------------------
    name = "_boundingbox_bake"
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)

    # --------------------------------------------------------
    # Create mesh
    # --------------------------------------------------------
    mesh = bpy.data.meshes.new(name)
    bbox = bpy.data.objects.new(name, mesh)

    collection.objects.link(bbox)

    # Create cube geometry
    bm = bpy.data.meshes.new_from_object(
        bpy.data.objects.new("temp", bpy.data.meshes.new("temp_mesh")),
        preserve_all_data_layers=False,
        depsgraph=context.evaluated_depsgraph_get()
    )

    # Manually define cube
    verts = [
        (-0.5, -0.5, 0.0),
        ( 0.5, -0.5, 0.0),
        ( 0.5,  0.5, 0.0),
        (-0.5,  0.5, 0.0),
        (-0.5, -0.5, 1.0),
        ( 0.5, -0.5, 1.0),
        ( 0.5,  0.5, 1.0),
        (-0.5,  0.5, 1.0),
    ]

    faces = [
        (0, 1, 2, 3),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ]

    mesh.from_pydata(verts, [], faces)
    mesh.update()

    # --------------------------------------------------------
    # Transform to match bounds
    # --------------------------------------------------------
    bbox.scale = (size_x, size_y, size_z)
    bbox.location = (center_x, center_y, bottom_z)

    # Hide from render
    bbox.hide_render = True

    return bbox


def update_vc_processor_sockets(context):
    """
    Push UI values into VC_Processor geometry node sockets
    for all selected mesh objects.
    """
    props = context.scene.vertex_baker

    for obj in context.selected_objects:
        if obj.type != 'MESH':
            continue

        mod = obj.modifiers.get("VC_Processor")
        if not mod:
            continue

        # Preview Channel (enum → int)
        mod["Socket_2"] = int(props.preview_channel)-1

        # AO
        mod["Socket_7"] = props.ao_blur
        mod["Socket_6"] = props.ao_contrast

        # Curvature
        mod["Socket_3"] = props.curvature_blur
        mod["Socket_4"] = props.curvature_contrast

        #Force update geometry nodes
        obj.update_tag()


# ------------------------------------------------------------
# Main Operator
# ------------------------------------------------------------

class OBJECT_OT_vc_bake_project(bpy.types.Operator):
    bl_idname = "object.vc_bake_project"
    bl_label = "VCBake Project"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        start_time = datetime.datetime.now()
        selection_state = store_selection(context)


        originals = [o for o in context.selected_objects if o.type == 'MESH']
        if not originals:
            self.report({'ERROR'}, "No mesh objects selected")
            return {'CANCELLED'}

        log(f"Bake started at: {timestamp_now()}")

        try:
            load_from_blend()
            curvature_mat = bpy.data.materials[MAT_CURVATURE]
            gradient_mat = bpy.data.materials[MAT_GRADIENT]

            bake_coll = ensure_collection(context, BAKE_COLLECTION)
            ensure_collection_visible_and_editable(context, bake_coll)

            original_engine = context.scene.render.engine

            try:
                dup_map = {}
                for obj in originals:
                    ensure_color_attribute(obj, VC_PACKED)
                    ensure_color_attribute(obj, VC_PREVIEW)
                    dup_map[obj] = duplicate_object(obj, BAKE_SUFFIX, bake_coll)

                bake_objs = list(dup_map.values())


                # --------------------------------------------------------
                # Curvature Bake
                # --------------------------------------------------------

                for obj in bake_objs:
                    obj.hide_set(False)
                    obj.hide_viewport = False
                    obj.data.materials.clear()
                    obj.data.materials.append(curvature_mat)

                    for attr in (VC_PACKED, VC_AO, VC_CURVATURE, VC_GRADIENT, VC_PREVIEW):
                        ensure_color_attribute(obj, attr)

                    clean_bake_object_modifiers(obj, context)

                props = context.scene.vertex_baker
                configure_cycles_for_baking(context.scene, props.ao_samples)
                bake = context.scene.render.bake

                bake.use_pass_direct = False
                bake.use_pass_indirect = False
                bake.use_pass_color = True
                bake_to_color(context, 'DIFFUSE', bake_objs, VC_CURVATURE)



                # --------------------------------------------------------
                # AO Bake
                # --------------------------------------------------------
                bake.use_pass_direct = True
                bake.use_pass_indirect = True
                bake.use_pass_color = True

                render_state = disable_render_temporarily(originals)
                try:
                    bake_to_color(context, 'AO', bake_objs, VC_AO)
                finally:
                    restore_render_state(render_state)

                # --------------------------------------------------------
                # Create Combined Bounding Box
                # --------------------------------------------------------

                create_combined_bounding_box(
                    context,
                    bake_objs,
                    bake_coll
                )

                
                
                # --------------------------------------------------------
                # Gradient Bake
                # --------------------------------------------------------

                for obj in bake_objs:
                    obj.data.materials.clear()
                    obj.data.materials.append(gradient_mat)

                bake.use_pass_direct = False
                bake.use_pass_indirect = False
                bake.use_pass_color = True
                bake_to_color(context, 'DIFFUSE', bake_objs, VC_GRADIENT)


                # --------------------------------------------------------
                # Packing
                # --------------------------------------------------------
                packer = bpy.data.node_groups["VC_Packer"]
                for obj in bake_objs:
                    mod = obj.modifiers.new("VC_Packer", 'NODES')
                    mod.node_group = packer
                    select_objects(context, [obj], obj)
                    bpy.ops.object.modifier_apply(modifier=mod.name)
                    set_active_color_attribute(obj, VC_AO)

                for orig, dup in dup_map.items():
                    ensure_datatransfer(orig, dup)
                    remove_unused_target_color_attributes(orig)
                    if "VC_Processor" not in orig.modifiers:
                        mod = orig.modifiers.new("VC_Processor", 'NODES')
                        mod.node_group = bpy.data.node_groups["VC_Processor"]
                        mod.show_in_editmode = False

                for orig in originals:
                    set_active_color_attribute(orig, VC_PREVIEW)

                setup_viewport_for_vertex_colors(context)

            finally:
                bake_coll.hide_viewport = True
                context.scene.render.engine = original_engine
        
        

        except Exception:
            log(f"Bake failed at: {timestamp_now()}")
            raise

        else:
            log(f"Bake finished at: {timestamp_now()}")

        restore_selection(context, selection_state)

        end_time = datetime.datetime.now()
        duration = end_time - start_time

        # Format as H:MM:SS
        seconds = int(duration.total_seconds())
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        duration_str = f"{hours}:{minutes:02d}:{secs:02d}"

        context.scene.vertex_baker.last_bake_duration = duration_str
        log(f"Bake duration: {duration_str}")

        self.report({'INFO'}, "Finished Bake")
        return {'FINISHED'}

# ------------------------------------------------------------
# UI Panel
# ------------------------------------------------------------

class VIEW3D_PT_vertex_baker(bpy.types.Panel):
    bl_label = "Vertex Baker"
    bl_idname = "VIEW3D_PT_vertex_baker"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VertexBaker"

    def draw(self, context):
        layout = self.layout
        props = context.scene.vertex_baker

        # --- Bake Settings ---
        box = layout.box()
        box.label(text="Bake Settings", icon='RENDER_STILL')
        box.prop(props, "ao_samples")

        box.operator(
            OBJECT_OT_vc_bake_project.bl_idname,
            text="Bake",
            icon='RENDER_STILL'
        )

        if props.last_bake_duration != "—":
            layout.label(
                text=f"Bake duration: {props.last_bake_duration}",
                icon='TIME'
            )

        # --- Post Controls ---
        box = layout.box()
        box.label(text="Post Controls", icon='MODIFIER')

        box.prop(props, "preview_channel")

        box.separator()

        box.prop(props, "ao_blur")
        box.prop(props, "ao_contrast")

        box.separator()

        box.prop(props, "curvature_blur")
        box.prop(props, "curvature_contrast")


# ------------------------------------------------------------
# Menu / Register
# ------------------------------------------------------------

def register():
    bpy.utils.register_class(VertexBakerProperties)
    bpy.utils.register_class(OBJECT_OT_vc_bake_project)
    bpy.utils.register_class(VIEW3D_PT_vertex_baker)

    bpy.types.Scene.vertex_baker = bpy.props.PointerProperty(
        type=VertexBakerProperties
    )



def unregister():

    del bpy.types.Scene.vertex_baker

    bpy.utils.unregister_class(VIEW3D_PT_vertex_baker)
    bpy.utils.unregister_class(OBJECT_OT_vc_bake_project)
    bpy.utils.unregister_class(VertexBakerProperties)



if __name__ == "__main__":
    register()
