import os
import re
import json
import tempfile
import logging
from collections import defaultdict

import contextlib
import substance_painter
import substance_painter.project
import substance_painter.resource
import substance_painter.js
import substance_painter.export
import substance_painter.textureset

from qtpy import QtGui, QtWidgets, QtCore

from ayon_core.pipeline import KnownPublishError

log = logging.getLogger(__name__)


def build_export_config_from_instance_data(instance):
    """Build export configuration from stored instance data.

    Accepts a raw instance dict (from project metadata) and returns a
    configuration dictionary suitable for `substance_painter.export.export_project_textures`.
    
    Uses a reliable built-in preset (gltf) to avoid issues with invalid custom presets.
    """
    creator_attrs = instance.get("creator_attributes") or {}
    
    # Use a reliable built-in preset
    # Ignores potentially invalid instance preset like "resource://your_assets/BWFRO"
    preset_url = "export-preset-generator://gltf"
    
    log.info(f"Using export preset: {preset_url}")
    
    config = {
        "exportShaderParams": True,
        "exportPath": "__REPLACE_ME__",
        "defaultExportPreset": preset_url,
        "exportParameters": [{
            "parameters": {
                "fileFormat": creator_attrs.get("exportFileFormat", "png"),
                "sizeLog2": creator_attrs.get("exportSize"),
                "paddingAlgorithm": creator_attrs.get("exportPadding"),
                "dilationDistance": creator_attrs.get("exportDilationDistance"),
            }
        }],
    }

    # Determine which texture sets to export; if none specified, export all in the document.
    export_texture_sets = creator_attrs.get("exportTextureSets") or []
    if not export_texture_sets:
        export_texture_sets = [ts.name() for ts in substance_painter.textureset.all_texture_sets()]
    config["exportList"] = [{"rootPath": name} for name in export_texture_sets]

    # Remove any None values to let Painter use defaults.
    params = config["exportParameters"][0]["parameters"]
    for key in list(params.keys()):
        if params[key] is None:
            params.pop(key)

    return config


def _resolve_publish_texture_staging_dir(instance):
    """Resolve the publish texture staging directory from instance data.

    Looks for a staging or publish directory on the instance. If not found,
    attempts to compute a default staging directory.
    
    Args:
        instance (dict): Instance data dictionary
        
    Returns:
        str: Path to staging directory
        
    Raises:
        KnownPublishError: If staging directory cannot be determined or created
    """
    staging_dir = (
        instance.get("stagingDir")
        or instance.get("publishDir")
        or instance.get("collect_staging_dir")
    )
    
    if not staging_dir:
        # Try to compute a default staging directory
        try:
            staging_dir = _compute_default_staging_dir(instance)
            log.warning(f"Staging directory not set on instance, using computed path: {staging_dir}")
        except Exception as exc:
            raise KnownPublishError(
                f"Cannot determine publish texture staging directory from instance. "
                f"Instance missing stagingDir/publishDir. Error: {exc}"
            )
    
    return staging_dir


def _compute_default_staging_dir(instance):
    """Compute a default staging directory if not set on instance.
    
    Tries to use AYON anatomy if available, falls back to temp directory.
    
    Args:
        instance (dict): Instance data
        
    Returns:
        str: Path to staging directory
        
    Raises:
        Exception: If directory cannot be created
    """
    project_name = instance.get("projectName")
    asset_name = instance.get("assetName") or instance.get("asset")
    task_name = instance.get("taskName") or instance.get("task")
    
    # First, try using AYON anatomy if we have all the context
    if all([project_name, asset_name, task_name]):
        try:
            return _compute_staging_dir_with_anatomy(
                project_name, asset_name, task_name, instance
            )
        except Exception as exc:
            log.warning(f"Failed to compute staging dir with anatomy: {exc}")
    
    # Fallback: use temp directory with project/asset structure
    if project_name and asset_name:
        temp_base = tempfile.gettempdir()
        staging_dir = os.path.join(
            temp_base,
            "ayon_texture_export",
            project_name,
            asset_name,
            "textureSet",
            "001"
        )
    else:
        # Last resort: just temp directory
        staging_dir = tempfile.mkdtemp(prefix="ayon_texture_")
    
    # Create the directory
    os.makedirs(staging_dir, exist_ok=True)
    return staging_dir


def _compute_staging_dir_with_anatomy(project_name, asset_name, task_name, instance):
    """Compute staging directory using AYON anatomy.
    
    Args:
        project_name (str): Project name
        asset_name (str): Asset/folder name
        task_name (str): Task name
        instance (dict): Instance data for additional context
        
    Returns:
        str: Computed staging directory path
        
    Raises:
        Exception: If anatomy cannot be loaded or path cannot be created
    """
    from ayon_core.pipeline import Anatomy
    from ayon_core.settings import get_current_project_settings
    
    try:
        anatomy = Anatomy(project_name)
        project_settings = get_current_project_settings()
    except Exception as exc:
        raise Exception(f"Failed to load anatomy for project '{project_name}': {exc}")
    
    # Get work root
    work_root = anatomy.roots.get("work")
    if not work_root:
        raise Exception("No 'work' root configured in anatomy")
    
    # Build staging path
    staging_dir = os.path.join(
        work_root,
        project_name,
        asset_name,
        task_name,
        "publish",
        "textureSet",
        "001"
    )
    
    # Create the directory
    os.makedirs(staging_dir, exist_ok=True)
    return staging_dir


def write_textures_to_publish_location(parent=None):
    """
    Export textures for a textureSet instance to its publish location.

    Runs outside of the Pyblish publish loop to avoid holding database
    connections open. Writes textures into the final publish staging
    directory and sets a flag on the instance so the publish extractor
    can skip exporting again.
    """
    # Ensure a project is open.
    if not substance_painter.project.is_open():
        raise KnownPublishError("No Substance Painter project is open.")

    # Defer importing these functions to avoid circular import issues.
    from .pipeline import get_instances_by_id, set_instance

    # Retrieve stored instances and find textureSet instances.
    instances_by_id = get_instances_by_id()
    texture_instances = [
        inst
        for inst in instances_by_id.values()
        if inst.get("productType") == "textureSet"
        or inst.get("family") == "textureSet"
        or "textureSet" in (inst.get("families") or [])
    ]

    if not texture_instances:
        raise KnownPublishError("No 'textureSet' instances found. Create one first.")

    # If multiple texture set instances are present, prompt the user to select
    # which one to export. Otherwise default to the sole instance.
    instance = None
    if len(texture_instances) == 1:
        instance = texture_instances[0]
    else:
        try:
            items = []
            # Derive a display label for each instance; fall back to id
            for inst in texture_instances:
                label = inst.get("productName") or inst.get("label") or inst.get("name") or inst.get("instance_id")
                items.append(label)
            # Show a list selection dialog
            item, ok = QtWidgets.QInputDialog.getItem(
                parent or QtWidgets.QApplication.activeWindow(),
                "Select Texture Set",
                "Choose the texture set instance to export:",
                items,
                0,
                False,
            )
            if ok:
                index = items.index(item)
                instance = texture_instances[index]
            else:
                raise KnownPublishError("Pre-export cancelled: no instance selected")
        except Exception:
            # Fall back to first instance if Qt is unavailable or selection fails
            instance = texture_instances[0]

    # Build export configuration from the instance data.
    config = build_export_config_from_instance_data(instance)

    # Determine export path and ensure the directory exists.
    publish_dir = _resolve_publish_texture_staging_dir(instance)
    if os.path.exists(publish_dir):
        base_dir = os.path.dirname(publish_dir)
        current_name = os.path.basename(publish_dir)
        # Only version up if the folder name is a purely numeric string
        if current_name.isdigit():
            # Gather all existing numeric version directories
            versions = []
            for name in os.listdir(base_dir):
                if name.isdigit():
                    try:
                        versions.append(int(name))
                    except ValueError:
                        pass
            next_version = (max(versions) + 1) if versions else 1
            new_dir_name = f"{next_version:03d}"
            publish_dir = os.path.join(base_dir, new_dir_name)
    
    # Create the final export directory
    os.makedirs(publish_dir, exist_ok=True)
    config["exportPath"] = publish_dir

    # Determine channels and layer IDs for export.
    export_channel = instance.get("creator_attributes", {}).get("exportChannel", [])
    node_ids = instance.get("selected_node_id", [])

    # Perform the export with the correct layer visibility.
    with set_layer_stack_opacity(node_ids, export_channel):
        result = substance_painter.export.export_project_textures(config)

    if result.status != substance_painter.export.ExportStatus.Success:
        raise KnownPublishError(f"Texture export failed: {result.message}")

    # Mark instance so publish extractor knows textures are already exported.
    flags = instance.setdefault("ayon_flags", {})
    flags["textures_exported"] = True

    # Persist the updated instance data back into metadata.
    instance["stagingDir"] = publish_dir
    instance["publishDir"] = publish_dir
    set_instance(instance["instance_id"], instance, update=True)
    return publish_dir

def write_textures_to_publish_location_selective(parent=None):
    """
    Export textures for a textureSet instance with selective material/UDIM options.

    Runs outside of the Pyblish publish loop. Allows users to:
    - Select which materials/texture sets to export
    - Select which UDIMs to export (or all)
    - Choose between creating new version or overwriting current version
    
    Args:
        parent: Parent widget for dialogs
    """
    from .pipeline import get_instances_by_id, set_instance
    from .pre_export_dialog import PreExportDialog, ExportStrategyDialog
    
    # Ensure a project is open
    if not substance_painter.project.is_open():
        raise KnownPublishError("No Substance Painter project is open.")

    # Get instances and find textureSet instances
    instances_by_id = get_instances_by_id()
    texture_instances = [
        inst
        for inst in instances_by_id.values()
        if inst.get("productType") == "textureSet"
        or inst.get("family") == "textureSet"
        or "textureSet" in (inst.get("families") or [])
    ]

    if not texture_instances:
        raise KnownPublishError("No 'textureSet' instances found. Create one first.")

    # Select instance if multiple
    instance = None
    if len(texture_instances) == 1:
        instance = texture_instances[0]
    else:
        try:
            items = [
                inst.get("productName") or inst.get("label") or inst.get("name") or inst.get("instance_id")
                for inst in texture_instances
            ]
            item, ok = QtWidgets.QInputDialog.getItem(
                parent or QtWidgets.QApplication.activeWindow(),
                "Select Texture Set",
                "Choose the texture set instance to export:",
                items,
                0,
                False,
            )
            if ok:
                instance = texture_instances[items.index(item)]
            else:
                raise KnownPublishError("Pre-export cancelled: no instance selected")
        except Exception:
            instance = texture_instances[0]

    # Get available texture sets in the Substance Painter project
    all_texture_sets = [ts.name() for ts in substance_painter.textureset.all_texture_sets()]
    
    # Get available UDIMs from first texture set
    all_udims = []
    if all_texture_sets:
        try:
            ts = substance_painter.textureset.TextureSet.from_name(all_texture_sets[0])
            all_udims = [tile.name for tile in ts.all_uv_tiles()]
        except Exception as exc:
            log.warning(f"Failed to get UDIMs: {exc}")
    
    # Show pre-export dialog
    dialog = PreExportDialog(
        texture_sets=all_texture_sets,
        udim_tiles=all_udims,
        parent=parent
    )
    
    if dialog.exec_() != QtWidgets.QDialog.Accepted:
        raise KnownPublishError("Pre-export cancelled by user")
    
    selected_materials = dialog.get_selected_materials()
    selected_udims = dialog.get_selected_udims()  # Empty list = all UDIMs
    export_strategy = dialog.get_strategy()
    
    log.info(f"Export settings: materials={selected_materials}, udims={selected_udims}, strategy={export_strategy}")
    
    # Build export config
    config = build_export_config_from_instance_data(instance)
    
    # Filter texture sets based on selection
    if selected_materials:
        config["exportList"] = [{"rootPath": name} for name in selected_materials]
    
    # Determine export path
    publish_dir = _resolve_publish_texture_staging_dir(instance)
    
    # Handle versioning
    if export_strategy == "version":
        # Always create new version
        if os.path.exists(publish_dir):
            base_dir = os.path.dirname(publish_dir)
            current_name = os.path.basename(publish_dir)
            
            if current_name.isdigit():
                versions = []
                for name in os.listdir(base_dir):
                    if name.isdigit():
                        try:
                            versions.append(int(name))
                        except ValueError:
                            pass
                next_version = (max(versions) + 1) if versions else 1
                new_dir_name = f"{next_version:03d}"
                publish_dir = os.path.join(base_dir, new_dir_name)
    
    elif export_strategy == "overwrite":
        # Ask user if version exists
        if os.path.exists(publish_dir):
            base_dir = os.path.dirname(publish_dir)
            current_name = os.path.basename(publish_dir)
            
            if current_name.isdigit():
                versions = []
                for name in os.listdir(base_dir):
                    if name.isdigit():
                        try:
                            versions.append(int(name))
                        except ValueError:
                            pass
                next_version = (max(versions) + 1) if versions else 1
                new_dir_name = f"{next_version:03d}"
                
                # Ask user if they want to overwrite or version up
                strategy_dialog = ExportStrategyDialog(
                    current_name,
                    new_dir_name,
                    parent=parent
                )
                
                if strategy_dialog.exec_() != QtWidgets.QDialog.Accepted:
                    raise KnownPublishError("Pre-export cancelled by user")
                
                choice = strategy_dialog.get_choice()
                if choice == "version":
                    publish_dir = os.path.join(base_dir, new_dir_name)
    
    # Create directory
    os.makedirs(publish_dir, exist_ok=True)
    config["exportPath"] = publish_dir
    
    # ===== DIAGNOSTIC OUTPUT =====
    log.info("\n" + "="*80)
    log.info("TEXTURE EXPORT DIAGNOSTIC")
    log.info("="*80)
    log.info(f"Config exportPath: {config.get('exportPath')}")
    log.info(f"Config defaultExportPreset: {config.get('defaultExportPreset')}")
    log.info(f"Config exportList: {config.get('exportList')}")
    log.info(f"Publish directory: {publish_dir}")
    log.info(f"Directory exists: {os.path.exists(publish_dir)}")
    log.info(f"Directory writable: {os.access(publish_dir, os.W_OK)}")
    log.info("="*80)
    
    # Export
    export_channel = instance.get("creator_attributes", {}).get("exportChannel", [])
    node_ids = instance.get("selected_node_id", [])
    
    log.info("Starting export...")
    with set_layer_stack_opacity(node_ids, export_channel):
        result = substance_painter.export.export_project_textures(config)
    
    # ===== DIAGNOSTIC: Check result =====
    log.info("\n" + "="*80)
    log.info("EXPORT RESULT")
    log.info("="*80)
    log.info(f"Status: {result.status}")
    log.info(f"Message: {result.message}")
    log.info(f"Textures exported: {len(result.textures)} sets/stacks")
    
    total_files = 0
    for (texture_set_name, stack_name), maps in result.textures.items():
        log.info(f"\n  {texture_set_name} / {stack_name}:")
        for i, texture_map in enumerate(maps):
            log.info(f"    [{i}] {texture_map}")
            total_files += 1
    
    log.info(f"\nTotal files in result: {total_files}")
    
    # Check what's actually on disk
    log.info(f"\nChecking disk at: {publish_dir}")
    if os.path.exists(publish_dir):
        disk_files = []
        for root, dirs, files in os.walk(publish_dir):
            for file in files:
                filepath = os.path.join(root, file)
                file_size = os.path.getsize(filepath)
                rel_path = os.path.relpath(filepath, publish_dir)
                disk_files.append(filepath)
                log.info(f"  ✓ {rel_path} ({file_size} bytes)")
        
        if not disk_files:
            log.warning(" DIRECTORY IS EMPTY - NO FILES FOUND!")
        else:
            log.info(f"\n  Total files on disk: {len(disk_files)}")
    else:
        log.error(f"  DIRECTORY DOES NOT EXIST!")
    
    log.info("="*80 + "\n")
    # ===== END DIAGNOSTIC =====
    
    if result.status != substance_painter.export.ExportStatus.Success:
        raise KnownPublishError(f"Texture export failed: {result.message}")
    
    # Mark instance as pre-exported
    flags = instance.setdefault("ayon_flags", {})
    flags["textures_exported"] = True
    
    # Store which materials/UDIMs were exported for reference
    flags["exported_materials"] = selected_materials
    flags["exported_udims"] = selected_udims
    flags["export_strategy"] = export_strategy
    
    # Update instance
    instance["stagingDir"] = publish_dir
    instance["publishDir"] = publish_dir
    set_instance(instance["instance_id"], instance, update=True)
    
    log.info(f"Pre-export completed. Textures written to: {publish_dir}")
    return publish_dir


def get_export_presets():
    """Return Export Preset resource URLs for all available Export Presets.

    Returns:
        dict: {Resource url: GUI Label}

    """
    preset_resources = {}
    for shelf in substance_painter.resource.Shelves.all():
        shelf_path = os.path.normpath(shelf.path())

        presets_path = os.path.join(shelf_path, "export-presets")
        if not os.path.exists(presets_path):
            continue

        for filename in os.listdir(presets_path):
            if filename.endswith(".spexp"):
                template_name = os.path.splitext(filename)[0]

                resource = substance_painter.resource.ResourceID(
                    context=shelf.name(),
                    name=template_name
                )
                resource_url = resource.url()

                preset_resources[resource_url] = template_name

    # Sort by template name
    export_templates = dict(sorted(preset_resources.items(),
                                   key=lambda x: x[1]))

    # Add default built-ins at the start
    result = {
        "export-preset-generator://viewport2d": "2D View",  # noqa E501
        "export-preset-generator://doc-channel-normal-no-alpha": "Document channels + Normal + AO (No Alpha)",  # noqa E501
        "export-preset-generator://doc-channel-normal-with-alpha": "Document channels + Normal + AO (With Alpha)",  # noqa E501
        "export-preset-generator://sketchfab": "Sketchfab",  # noqa E501
        "export-preset-generator://adobe-standard-material": "Substance 3D Stager",  # noqa E501
        "export-preset-generator://usd": "USD PBR Metal Roughness",  # noqa E501
        "export-preset-generator://gltf": "glTF PBR Metal Roughness",  # noqa E501
        "export-preset-generator://gltf-displacement": "glTF PBR Metal Roughness + Displacement texture (experimental)"  # noqa E501
    }
    result.update(export_templates)
    return result


def _convert_stack_path_to_cmd_str(stack_path):
    """Convert stack path `str` or `[str, str]` for javascript query

    Example usage:
        >>> stack_path = _convert_stack_path_to_cmd_str(stack_path)
        >>> cmd = f"alg.mapexport.channelIdentifiers({stack_path})"
        >>> substance_painter.js.evaluate(cmd)

    Args:
        stack_path (list or str): Path to the stack, could be
            "Texture set name" or ["Texture set name", "Stack name"]

    Returns:
        str: Stack path usable as argument in javascript query.

    """
    return json.dumps(stack_path)


def get_channel_identifiers(stack_path=None):
    """Return the list of channel identifiers.

    If a context is passed (texture set/stack),
    return only used channels with resolved user channels.

    Channel identifiers are:
        basecolor, height, specular, opacity, emissive, displacement,
        glossiness, roughness, anisotropylevel, anisotropyangle, transmissive,
        scattering, reflection, ior, metallic, normal, ambientOcclusion,
        diffuse, specularlevel, blendingmask, [custom user names].

    Args:
        stack_path (list or str, Optional): Path to the stack, could be
            "Texture set name" or ["Texture set name", "Stack name"]

    Returns:
        list: List of channel identifiers.

    """
    if stack_path is None:
        stack_path = ""
    else:
        stack_path = _convert_stack_path_to_cmd_str(stack_path)
    cmd = f"alg.mapexport.channelIdentifiers({stack_path})"
    return substance_painter.js.evaluate(cmd)


def get_channel_format(stack_path, channel):
    """Retrieve the channel format of a specific stack channel.

    See `alg.mapexport.channelFormat` (javascript API) for more details.

    The channel format data is:
        "label" (str): The channel format label: could be one of
            [sRGB8, L8, RGB8, L16, RGB16, L16F, RGB16F, L32F, RGB32F]
        "color" (bool): True if the format is in color, False is grayscale
        "floating" (bool): True if the format uses floating point
            representation, false otherwise
        "bitDepth" (int): Bit per color channel (could be 8, 16 or 32 bpc)

    Arguments:
        stack_path (list or str): Path to the stack, could be
            "Texture set name" or ["Texture set name", "Stack name"]
        channel (str): Identifier of the channel to export
            (see `get_channel_identifiers`)

    Returns:
        dict: The channel format data.

    """
    stack_path = _convert_stack_path_to_cmd_str(stack_path)
    cmd = f"alg.mapexport.channelFormat({stack_path}, '{channel}')"
    return substance_painter.js.evaluate(cmd)


def get_document_structure():
    """Dump the document structure.

    See `alg.mapexport.documentStructure` (javascript API) for more details.

    Returns:
        dict: Document structure or None when no project is open

    """
    return substance_painter.js.evaluate("alg.mapexport.documentStructure()")


def get_export_templates(config, format="png", strip_folder=True):
    """Return export config outputs.

    This use the Javascript API `alg.mapexport.getPathsExportDocumentMaps`
    which returns a different output than using the Python equivalent
    `substance_painter.export.list_project_textures(config)`.

    The nice thing about the Javascript API version is that it returns the
    output textures grouped by filename template.

    A downside is that it doesn't return all the UDIM tiles but per template
    always returns a single file.

    Note:
        The file format needs to be explicitly passed to the Javascript API
        but upon exporting through the Python API the file format can be based
        on the output preset. So it's likely the file extension will mismatch

    Warning:
        Even though the function appears to solely get the expected outputs
        the Javascript API will actually create the config's texture output
        folder if it does not exist yet. As such, a valid path must be set.

    Example output:
    {
        "DefaultMaterial": {
            "$textureSet_BaseColor(_$colorSpace)(.$udim)": "DefaultMaterial_BaseColor_ACES - ACEScg.1002.png",
            "$textureSet_Emissive(_$colorSpace)(.$udim)": "DefaultMaterial_Emissive_ACES - ACEScg.1002.png",
            "$textureSet_Height(_$colorSpace)(.$udim)": "DefaultMaterial_Height_Utility - Raw.1002.png",
            "$textureSet_Metallic(_$colorSpace)(.$udim)": "DefaultMaterial_Metallic_Utility - Raw.1002.png",
            "$textureSet_Normal(_$colorSpace)(.$udim)": "DefaultMaterial_Normal_Utility - Raw.1002.png",    
            "$textureSet_Roughness(_$colorSpace)(.$udim)": "DefaultMaterial_Roughness_Utility - Raw.1002.png"
        }
    }

    Arguments:
        config (dict) Export config
        format (str, Optional): Output format to write to, defaults to 'png'
        strip_folder (bool, Optional): Whether to strip the output folder
            from the output filenames.

    Returns:
        dict: The expected output maps.

    """  # noqa E501
    folder = config["exportPath"].replace("\\", "/")
    preset = config["defaultExportPreset"]
    cmd = f'alg.mapexport.getPathsExportDocumentMaps("{preset}", "{folder}", "{format}")'  # noqa

    # The optional stack path argument is broken in Substance Painter 10.1
    # and fails on painter's C++ API triggering from the javascript API through
    # python. So we pass it the empty list of stack paths explicitly.
    # See `ayon-substancepainter` issue #13
    version_info = substance_painter.application.version_info()
    if version_info[0:2] >= (10, 1):
        cmd = f'alg.mapexport.getPathsExportDocumentMaps("{preset}", "{folder}", "{format}", [])'  # noqa

    result = substance_painter.js.evaluate(cmd)
    if strip_folder:
        for _stack, maps in result.items():
            for map_template, map_filepath in maps.items():
                map_filepath = map_filepath.replace("\\", "/")
                assert map_filepath.startswith(folder)
                map_filename = map_filepath[len(folder):].lstrip("/")
                maps[map_template] = map_filename

    return result


def _templates_to_regex(templates,
                        texture_set,
                        colorspaces,
                        project,
                        mesh,
                        tile_names):
    """Return regex based on a Substance Painter export filename template.

    This converts Substance Painter export filename templates like
    `$mesh_$textureSet_BaseColor(_$colorSpace)(.$udim)` into a regex
    which can be used to query an output filename to help retrieve:

        - Which template filename the file belongs to.
        - Which color space the file is written with.
        - Which udim tile it is exactly.

    This is used by `get_parsed_export_maps` which tries to as explicitly
    as possible match the filename pattern against the known possible outputs.
    That's why Texture Set name, Color spaces, Project path and mesh path must
    be provided. By doing so we get the best shot at correctly matching the
    right template because otherwise $texture_set could basically be any string
    and thus match even that of a color space or mesh.

    Arguments:
        templates (list): List of templates to convert to regex.
        texture_set (str): The texture set to match against.
        colorspaces (list): The colorspaces defined in the current project.
        project (str): Filepath of current substance project.
        mesh (str): Path to mesh file used in current project.
        tile_names (str): The uvTileName set in the template in
                          the current project.

    Returns:
        dict: Template: Template regex pattern

    """
    def _filename_no_ext(path):
        return os.path.splitext(os.path.basename(path))[0]

    if colorspaces and any(colorspaces):
        colorspace_match = "|".join(re.escape(c) for c in set(colorspaces))
        colorspace_match = f"({colorspace_match})"
    else:
        # No colorspace support enabled
        colorspace_match = ""

    tile_name_match = "|".join(tile_names) if tile_names else ""

    # Key to regex valid search values
    key_matches = {
        "$project": re.escape(_filename_no_ext(project)),
        "$mesh": re.escape(_filename_no_ext(mesh)),
        "$textureSet": re.escape(texture_set),
        "$colorSpace": colorspace_match,
        "$udim": "([0-9]{4})"
    }

    if tile_name_match:
        # Added in Substance Painter 11.0.0
        key_matches["$uvTileName"] = tile_name_match

    # Turn the templates into regexes
    regexes = {}
    for template in templates:

        # We need to tweak a temp
        search_regex = re.escape(template)

        # Let's assume that any ( and ) character in the file template was
        # intended as an optional template key and do a simple `str.replace`
        # Note: we are matching against re.escape(template) so will need to
        #       search for the escaped brackets.
        search_regex = search_regex.replace(re.escape("("), "(")
        search_regex = search_regex.replace(re.escape(")"), ")?")

        # Substitute each key into a named group
        for key, key_expected_regex in key_matches.items():

            # We want to use the template as a regex basis in the end so will
            # escape the whole thing first. Note that thus we'll need to
            # search for the escaped versions of the keys too.
            escaped_key = re.escape(key)
            key_label = key[1:]  # key without $ prefix

            key_expected_grp_regex = f"(?P<{key_label}>{key_expected_regex})"
            search_regex = search_regex.replace(escaped_key,
                                                key_expected_grp_regex)

        # The filename templates don't include the extension so we add it
        # to be able to match the out filename beginning to end
        ext_regex = r"(?P<ext>\.[A-Za-z][A-Za-z0-9-]*)"
        search_regex = rf"^{search_regex}{ext_regex}$"

        regexes[template] = search_regex

    return regexes


def strip_template(template, strip="._ "):
    """Return static characters in a substance painter filename template.

    >>> strip_template("$textureSet_HELLO(.$udim)")
    # HELLO
    >>> strip_template("$mesh_$textureSet_HELLO_WORLD_$colorSpace(.$udim)")
    # HELLO_WORLD
    >>> strip_template("$textureSet_HELLO(.$udim)", strip=None)
    # _HELLO
    >>> strip_template("$mesh_$textureSet_$colorSpace(.$udim)", strip=None)
    # _HELLO_
    >>> strip_template("$textureSet_HELLO(.$udim)")
    # _HELLO

    Arguments:
        template (str): Filename template to strip.
        strip (str, optional): Characters to strip from beginning and end
            of the static string in template. Defaults to: `._ `.

    Returns:
        str: The static string in filename template.

    """
    # Return only characters that were part of the template that were static.
    # Remove all keys
    keys = ["$project", "$mesh", "$textureSet", "$udim", "$colorSpace"]
    version_info = substance_painter.application.version_info()
    if version_info >= (11, 0, 0):
        keys.append("$uvTileName")

    stripped_template = template
    for key in keys:
        stripped_template = stripped_template.replace(key, "")

    # Everything inside an optional bracket space is excluded since it's not
    # static. We keep a counter to track whether we are currently iterating
    # over parts of the template that are inside an 'optional' group or not.
    counter = 0
    result = ""
    for char in stripped_template:
        if char == "(":
            counter += 1
        elif char == ")":
            counter -= 1
            if counter < 0:
                counter = 0
        else:
            if counter == 0:
                result += char

    if strip:
        # Strip of any trailing start/end characters. Technically these are
        # static but usually start and end separators like space or underscore
        # aren't wanted.
        result = result.strip(strip)

    return result


def get_parsed_export_maps(config, strip_texture_set=False):
    """Return Export Config's expected output textures with parsed data.

    This tries to parse the texture outputs using a Python API export config.

    Parses template keys: $project, $mesh, , $colorSpace,
                          $udim, $uvTileName

    Example:
    {("DefaultMaterial", ""): {
        "$mesh_$textureSet_BaseColor(_$colorSpace)(.$udim)": [
                {
                    // OUTPUT DATA FOR FILE #1 OF THE TEMPLATE
                },
                {
                    // OUTPUT DATA FOR FILE #2 OF THE TEMPLATE
                },
            ]
        },
    }}

    File output data (all outputs are `str`).
    1) Parsed tokens: These are parsed tokens from the template, they will
        only exist if found in the filename template and output filename.

        project: Workfile filename without extension
        mesh: Filename of the loaded mesh without extension
        textureSet: The texture set, e.g. "DefaultMaterial",
        colorSpace: The color space, e.g. "ACES - ACEScg",
        udim: The udim tile, e.g. "1001"

    2) Template output and filepath

        filepath: Full path to the resulting texture map, e.g.
            "/path/to/mesh_DefaultMaterial_BaseColor_ACES - ACEScg.1002.png",
        output: "mesh_DefaultMaterial_BaseColor_ACES - ACEScg.1002.png"
            Note: if template had slashes (folders) then `output` will too.
                  So `output` might include a folder.

    Returns:
        dict: [texture_set, stack]: {template: [file1_data, file2_data]}

    """
    # Import is here to avoid recursive lib <-> colorspace imports
    from .colorspace import get_project_channel_data

    outputs = substance_painter.export.list_project_textures(config)
    templates = get_export_templates(config, strip_folder=False)

    # Get project channel data with safe None handling
    channel_data = get_project_channel_data() or {}
    project_colorspaces = set(
        data["colorSpace"]
        for data in channel_data.values()
        if data and "colorSpace" in data
    )

    # Get current project mesh path and project path to explicitly match
    # the $mesh and $project tokens
    project_mesh_path = substance_painter.project.last_imported_mesh_path()
    project_path = substance_painter.project.file_path() or ""

    # Get the current export path to strip this of the beginning of filepath
    # results, since filename templates don't have these we'll match without
    # that part of the filename.
    export_path = config["exportPath"]
    export_path = export_path.replace("\\", "/")
    if not export_path.endswith("/"):
        export_path += "/"

    # Parse the outputs
    result = {}
    version_info = substance_painter.application.version_info()
    for key, filepaths in outputs.items():
        texture_set_name, stack = key

        texture_set = (
            substance_painter.textureset.TextureSet.from_name(
                texture_set_name)
        )

        tile_names = set()
        if version_info >= (11, 0, 0):
            tile_names = set(tile.name for tile in texture_set.all_uv_tiles())

        if stack:
            stack_path = f"{texture_set_name}/{stack}"
        else:
            stack_path = texture_set_name
        if strip_texture_set:
            stack_templates = list(
                re.sub(r"[_.-]?\$textureSet[_.-]?", "", template)
                for template in templates[stack_path].keys()
            )
        else:
            stack_templates = list(templates[stack_path].keys())
        template_regex = _templates_to_regex(stack_templates,
                                             texture_set=texture_set_name,
                                             colorspaces=project_colorspaces,
                                             mesh=project_mesh_path,
                                             project=project_path,
                                             tile_names=tile_names)
        # Let's precompile the regexes
        for template, regex in template_regex.items():
            template_regex[template] = re.compile(regex)

        stack_results = defaultdict(list)
        for filepath in sorted(filepaths):
            # We strip explicitly using the full parent export path instead of
            # using `os.path.basename` because export template is allowed to
            # have subfolders in its template which we want to match against
            filepath = filepath.replace("\\", "/")
            assert filepath.startswith(export_path), (
                f"Filepath {filepath} must start with folder {export_path}"
            )
            filename = filepath[len(export_path):]
            stack_results = get_stack_results(stack_results, template_regex,
                                              filename, filepath,
                                              strip_texture_set=strip_texture_set)

        result[key] = dict(stack_results)
    if strip_texture_set:
        result = get_parsed_output_maps_as_single_output(result)

    return result


def get_stack_results(stack_results, template_regex,
                      filename, filepath,
                      strip_texture_set=False):
    """Function to get filename and filepath for parsed outputs
    """
    # Attempt to match the filename against each template
    for template, regex in template_regex.items():
        match = regex.match(filename)
        if match:
            parsed = match.groupdict(default={})
            parsed["output"] = filename  # Add filename for convenience
            parsed["filepath"] = filepath  # Add filepath for convenience
            uv_tilename = parsed.get("uvTileName", "")
            if uv_tilename:
                updated_key = (template, uv_tilename)
            else:
                updated_key = (template, "")
            stack_results[updated_key].append(parsed)
            break
    else:
        if not strip_texture_set:
            # Raise an error if no match is found
            raise ValueError(f"Unable to match {filename} against any "
                             f"template in: {list(template_regex.keys())}")
    return stack_results


def get_parsed_output_maps_as_single_output(result):
    """Get parsed output maps as single output

    Args:
        result (dict): all parsed output maps

    Returns:
        dict: parsed output maps as single output
    """
    result_with_single_output = {}
    result_with_single_output[("", "")] = {}
    for template_maps in result.values():
        for template, outputs in template_maps.items():
            if template not in result_with_single_output[("", "")]:
                result_with_single_output[("", "")][template] = []
            result_with_single_output[("", "")][template].extend(outputs)
    return result_with_single_output


def load_shelf(path, name=None):
    """Add shelf to substance painter (for current application session)

    This will dynamically add a Shelf for the current session. It's good
    to note however that these will *not* persist on restart of the host.

    Note:
        Consider the loaded shelf a static library of resources.

        The shelf will *not* be visible in application preferences in
        Edit > Settings > Libraries.

        The shelf will *not* show in the Assets browser if it has no existing
        assets

        The shelf will *not* be a selectable option for selecting it as a
        destination to import resources too.

    """

    # Ensure expanded path with forward slashes
    path = os.path.expandvars(path)
    path = os.path.abspath(path)
    path = path.replace("\\", "/")

    # Path must exist
    if not os.path.isdir(path):
        raise ValueError(f"Path is not an existing folder: {path}")

    # This name must be unique and must only contain lowercase letters,
    # numbers, underscores or hyphens.
    if name is None:
        name = os.path.basename(path)

    name = name.lower()
    name = re.sub(r"[^a-z0-9_\-]", "_", name)   # sanitize to underscores

    if substance_painter.resource.Shelves.exists(name):
        shelf = next(
            shelf for shelf in substance_painter.resource.Shelves.all()
            if shelf.name() == name
        )
        if os.path.normpath(shelf.path()) != os.path.normpath(path):
            raise ValueError(f"Shelf with name '{name}' already exists "
                             f"for a different path: '{shelf.path()}")

        return

    print(f"Adding Shelf '{name}' to path: {path}")
    substance_painter.resource.Shelves.add(name, path)

    return name


def _get_new_project_action():
    """Return QAction which triggers Substance Painter's new project dialog"""

    main_window = substance_painter.ui.get_main_window()

    # Find the file menu's New file action
    menubar = main_window.menuBar()
    new_action = None
    for action in menubar.actions():
        menu = action.menu()
        if not menu:
            continue

        if menu.objectName() != "file":
            continue

        # Find the action with the CTRL+N key sequence
        new_action = next(action for action in menu.actions()
                          if action.shortcut() == QtGui.QKeySequence.New)
        break

    return new_action


def prompt_new_file_with_mesh(mesh_filepath):
    """Prompts the user for a new file using Substance Painter's own dialog.

    This will set the mesh path to load to the given mesh and disables the
    dialog box to disallow the user to change the path. This way we can allow
    user configuration of a project but set the mesh path ourselves.

    Warning:
        This is very hacky and experimental.

    Note:
       If a project is currently open using the same mesh filepath it can't
       accurately detect whether the user had actually accepted the new project
       dialog or whether the project afterwards is still the original project,
       for example when the user might have cancelled the operation.

    """

    app = QtWidgets.QApplication.instance()
    assert os.path.isfile(mesh_filepath), \
        f"Mesh filepath does not exist: {mesh_filepath}"

    def _setup_file_dialog():
        """Set filepath in QFileDialog and trigger accept result"""
        file_dialog = app.activeModalWidget()
        assert isinstance(file_dialog, QtWidgets.QFileDialog)

        # Quickly hide the dialog
        file_dialog.hide()
        app.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents, 1000)

        file_dialog.setDirectory(os.path.dirname(mesh_filepath))
        url = QtCore.QUrl.fromLocalFile(os.path.basename(mesh_filepath))
        file_dialog.selectUrl(url)
        # TODO: find a way to improve the process event to
        # load more complicated mesh
        app.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents, 3000)
        file_dialog.done(file_dialog.Accepted)
        app.processEvents(QtCore.QEventLoop.AllEvents)

    def _setup_prompt():
        app.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
        dialog = app.activeModalWidget()
        assert dialog.objectName() == "NewProjectDialog"

        # Set the window title
        mesh = os.path.basename(mesh_filepath)
        dialog.setWindowTitle(f"New Project with mesh: {mesh}")

        # Get the select mesh file button
        mesh_select = dialog.findChild(QtWidgets.QPushButton, "meshSelect")

        # Hide the select mesh button to the user to block changing of mesh
        mesh_select.setVisible(False)

        # Ensure UI is visually up-to-date
        app.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents, 8000)

        # Trigger the 'select file' dialog to set the path and have the
        # new file dialog to use the path.
        QtCore.QTimer.singleShot(10, _setup_file_dialog)
        mesh_select.click()

        app.processEvents(QtCore.QEventLoop.AllEvents, 5000)

        mesh_filename = dialog.findChild(QtWidgets.QFrame, "meshFileName")
        mesh_filename_label = mesh_filename.findChild(QtWidgets.QLabel)
        if not mesh_filename_label.text():
            dialog.close()
            substance_painter.logging.warning(
                "Failed to set mesh path with the prompt dialog:"
                f"{mesh_filepath}\n\n"
                "Creating new project directly with the mesh path instead.")

    new_action = _get_new_project_action()
    if not new_action:
        raise RuntimeError("Unable to detect new file action..")

    QtCore.QTimer.singleShot(0, _setup_prompt)
    new_action.trigger()
    app.processEvents(QtCore.QEventLoop.AllEvents, 5000)

    if not substance_painter.project.is_open():
        return

    # Confirm mesh was set as expected
    project_mesh = substance_painter.project.last_imported_mesh_path()
    if os.path.normpath(project_mesh) != os.path.normpath(mesh_filepath):
        return

    return project_mesh


def get_filtered_export_preset(export_preset_name, channel_type_names,
                               strip_texture_set=False):
    """Return export presets included with specific channels
    requested by users.

    Args:
        export_preset_name (str): Name of export preset
        channel_type_list (list): A list of channel type requested by users
        strip_texture_set (bool): strip texture set name

    Returns:
        dict: export preset data
    """

    all_output_maps = []
    target_maps = []

    export_presets = get_export_presets()
    export_preset_nice_name = export_presets.get(export_preset_name)
    
    if not export_preset_nice_name:
        log.warning(f"Export preset '{export_preset_name}' not found in available presets")
        return {"exportPresets": [{"name": export_preset_name, "maps": []}]}
    
    resource_presets = substance_painter.export.list_resource_export_presets()
    preset = next(
        (
            preset for preset in resource_presets
            if preset.resource_id.name == export_preset_nice_name
        ), None
    )
    if preset is None:
        log.warning(f"Preset '{export_preset_nice_name}' not found in resources")
        return {"exportPresets": [{"name": export_preset_name, "maps": []}]}

    maps = preset.list_output_maps()
    for channel_map in maps:
        if strip_texture_set:
            old_channel_map = channel_map["fileName"]
            channel_map["fileName"] = re.sub(
                r"[_.-]?\$textureSet[_.-]?", "",
                old_channel_map
            )
            all_output_maps.append(channel_map)
        else:
            all_output_maps = maps
    
    for channel_map in all_output_maps:
        if channel_type_names:
            for channel_name in channel_type_names:
                if not channel_map.get("fileName"):
                    continue

                if channel_name in channel_map["fileName"]:
                    target_maps.append(channel_map)
        else:
            target_maps = all_output_maps
    
    # Create a new preset
    return {
        "exportPresets": [
            {
                "name": export_preset_name,
                "maps": target_maps
            }
        ],
    }


@contextlib.contextmanager
def set_layer_stack_opacity(node_ids, channel_types):
    """Function to set the opacity of the layer stack during
    context
    Args:
        node_ids (list[int]): Substance painter root layer node ids
        channel_types (list[str]): Channel type names as defined as
            attributes in `substance_painter.textureset.ChannelType`
    """
    # Do nothing
    if not node_ids or not channel_types:
        yield
        return

    stack = substance_painter.textureset.get_active_stack()
    stack_root_layers = (
        substance_painter.layerstack.get_root_layer_nodes(stack)
    )
    node_ids = set(node_ids)  # lookup
    excluded_nodes = [
        node for node in stack_root_layers
        if node.uid() not in node_ids
    ]

    original_opacity_values = []
    for node in excluded_nodes:
        for channel in channel_types:
            channel = channel.replace("_", "")
            chan = getattr(substance_painter.textureset.ChannelType, channel)
            original_opacity_values.append((chan, node.get_opacity(chan)))
    try:
        for node in excluded_nodes:
            for channel, _ in original_opacity_values:
                node.set_opacity(0.0, channel)
        yield
    finally:
        for node in excluded_nodes:
            for channel, opacity in original_opacity_values:
                node.set_opacity(opacity, channel)