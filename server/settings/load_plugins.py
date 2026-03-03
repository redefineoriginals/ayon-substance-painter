from ayon_server.settings import BaseSettingsModel, SettingsField


def normal_map_format_enum():
    return [
        {"label": "DirectX", "value": "NormalMapFormat.DirectX"},
        {"label": "OpenGL", "value": "NormalMapFormat.OpenGL"},
    ]


def tangent_space_enum():
    return [
        {"label": "Per Fragment", "value": "TangentSpace.PerFragment"},
        {"label": "Per Vertex", "value": "TangentSpace.PerVertex"},
    ]


def uv_workflow_enum():
    return [
        {"label": "Default", "value": "ProjectWorkflow.Default"},
        {"label": "UV Tile", "value": "ProjectWorkflow.UVTile"},
        {"label": "Texture Set Per UV Tile",
         "value": "ProjectWorkflow.TextureSetPerUVTile"}
    ]


def document_resolution_enum():
    return [
        {"label": "128", "value": 128},
        {"label": "256", "value": 256},
        {"label": "512", "value": 512},
        {"label": "1024", "value": 1024},
        {"label": "2048", "value": 2048},
        {"label": "4096", "value": 4096}
    ]


#((RDO-240226)rdo-modification
# Added max_publish_resolution_enum to support the new texture resolution
# limit setting. 8K included for shows with a legitimate override requirement
# e.g. DMP-heavy environments.)
def max_publish_resolution_enum():
    return [
        {"label": "256", "value": 256},
        {"label": "512", "value": 512},
        {"label": "1K", "value": 1024},
        {"label": "2K", "value": 2048},
        {"label": "4K (Default)", "value": 4096},
        {"label": "8K", "value": 8192},
    ]


class ProjectTemplatesModel(BaseSettingsModel):
    _layout = "expanded"
    name: str = SettingsField("default", title="Template Name")
    default_texture_resolution: int = SettingsField(
        1024, enum_resolver=document_resolution_enum,
        title="Document Resolution",
        description=("Set texture resolution when "
                     "creating new project.")
    )
    import_cameras: bool = SettingsField(
        True, title="Import Cameras",
        description="Import cameras from the mesh file.")
    normal_map_format: str = SettingsField(
        "DirectX", enum_resolver=normal_map_format_enum,
        title="Normal Map Format",
        description=("Set normal map format when "
                     "creating new project.")
    )
    project_workflow: str = SettingsField(
        "Default", enum_resolver=uv_workflow_enum,
        title="UV Tile Settings",
        description=("Set UV workflow when "
                     "creating new project.")
    )
    tangent_space_mode: str = SettingsField(
        "PerFragment", enum_resolver=tangent_space_enum,
        title="Tangent Space",
        description=("An option to compute tangent space "
                     "when creating new project.")
    )
    preserve_strokes: bool = SettingsField(
        True, title="Preserve Strokes",
        description=("Preserve strokes positions on mesh.\n"
                     "(only relevant when loading into "
                     "existing project)")
    )


class ProjectTemplateSettingModel(BaseSettingsModel):
    project_templates: list[ProjectTemplatesModel] = SettingsField(
        default_factory=ProjectTemplatesModel,
        title="Project Templates"
    )

    #((RDO-240226)rdo-modification
    # Added two fields to enforce a maximum texture resolution at Write and
    # Publish time. This prevents Artists from baking oversized texture sets
    # (e.g. 128x 8K maps) that cause crashes and waste hundreds of hours.
    #
    # Write tool (Gate 1): always warns with a clear message and allows bypass.
    # The warning explicitly states the texture cannot be published at this
    # resolution, so the Artist proceeds at their own risk.
    #
    # Publish validator (Gate 2): hard blocks by default. A show can request
    # sanity_check_optional=True if they have a legitimate need (e.g. DMPs).
    #
    # Gate 1 (write-time) is in: api/lib.py - check_texture_resolution_before_write
    # Gate 2 (publish-time) is in: plugins/publish/validate_texture_resolution.py)
    max_publish_texture_resolution: int = SettingsField(
        4096,
        enum_resolver=max_publish_resolution_enum,
        title="Max Publish Texture Resolution",
        description=(
            "Maximum texture resolution (longest axis) allowed at Publish "
            "time. Default 4K. Artists will be warned at Write time if "
            "exceeded but can still proceed. Publish will be blocked unless "
            "the show override below is enabled."
        )
    )
    sanity_check_optional: bool = SettingsField(
        False,
        title="Make Publish Validator Optional (Show Override)",
        description=(
            "When True, the publish validator becomes a WARNING instead of "
            "an ERROR. Use this for shows where DMPs or hero assets "
            "legitimately require higher resolution textures. "
            "Requires supervisor approval — off by default."
        )
    )


class LoadersModel(BaseSettingsModel):
    SubstanceLoadProjectMesh: ProjectTemplateSettingModel = SettingsField(
        default_factory=ProjectTemplateSettingModel,
        title="Load Mesh"
    )


DEFAULT_LOADER_SETTINGS = {
    "SubstanceLoadProjectMesh": {
        "project_templates": [
            {
                "name": "2K(Default)",
                "default_texture_resolution": 2048,
                "import_cameras": True,
                "normal_map_format": "NormalMapFormat.DirectX",
                "project_workflow": "ProjectWorkflow.Default",
                "tangent_space_mode": "TangentSpace.PerFragment",
                "preserve_strokes": True
            },
            {
                "name": "2K(UV tile)",
                "default_texture_resolution": 2048,
                "import_cameras": True,
                "normal_map_format": "NormalMapFormat.DirectX",
                "project_workflow": "ProjectWorkflow.UVTile",
                "tangent_space_mode": "TangentSpace.PerFragment",
                "preserve_strokes": True
            },
            {
                "name": "4K(Custom)",
                "default_texture_resolution": 4096,
                "import_cameras": True,
                "normal_map_format": "NormalMapFormat.OpenGL",
                "project_workflow": "ProjectWorkflow.UVTile",
                "tangent_space_mode": "TangentSpace.PerFragment",
                "preserve_strokes": True
            }
        ],
        #((RDO-240226)rdo-modification
        # Added default values for the two new resolution limit fields.
        # warn_on_write removed — Write always warns with bypass allowed.)
        "max_publish_texture_resolution": 4096,
        "sanity_check_optional": False
    }
}