# -*- coding: utf-8 -*-
"""Pipeline tools for OpenPype Substance Painter integration."""
import os
import logging
from functools import partial
 
# Substance 3D Painter modules
import substance_painter.ui
import substance_painter.event
import substance_painter.project
import substance_painter.export
 
import pyblish.api
 
from qtpy import QtWidgets, QtCore
 
from ayon_core.host import HostBase, IWorkfileHost, ILoadHost, IPublishHost
from ayon_core.settings import get_current_project_settings
 
from ayon_core.pipeline.template_data import get_template_data_with_names
from ayon_core.pipeline import (
    register_creator_plugin_path,
    register_loader_plugin_path,
    AVALON_CONTAINER_ID,
    Anatomy,
    KnownPublishError,
)
from ayon_core.lib import (
    StringTemplate,
    register_event_callback,
    emit_event,
)
from ayon_core.pipeline.load import any_outdated_containers
from ayon_substancepainter import SUBSTANCE_HOST_DIR
 
from . import lib
 
# Import lib functions used in pre-export workflow
from .lib import (
    build_export_config_from_instance_data,
    _resolve_publish_texture_staging_dir,
    _select_texture_instance_from_dialog,
    write_textures_to_publish_location_selective,
    set_layer_stack_opacity,
)

PLUGINS_DIR = os.path.join(SUBSTANCE_HOST_DIR, "plugins")
PUBLISH_PATH = os.path.join(PLUGINS_DIR, "publish")
LOAD_PATH = os.path.join(PLUGINS_DIR, "load")
CREATE_PATH = os.path.join(PLUGINS_DIR, "create")
INVENTORY_PATH = os.path.join(PLUGINS_DIR, "inventory")

OPENPYPE_METADATA_KEY = "OpenPype"
OPENPYPE_METADATA_CONTAINERS_KEY = "containers"  # child key
OPENPYPE_METADATA_CONTEXT_KEY = "context"        # child key
OPENPYPE_METADATA_INSTANCES_KEY = "instances"    # child key

#[RDO Modification] PIPE-612: Pre-export workflow function
def write_textures_to_publish_location(parent=None) -> str:
    """Export textures for a textureSet instance to its publish location.
 
    Runs outside of the Pyblish publish loop to avoid holding database
    connections open. Writes textures into the final publish staging
    directory and sets a flag on the instance so the publish extractor
    can skip exporting again.
    
    Args:
        parent (QtWidgets.QWidget, optional): Parent widget for dialogs
        
    Returns:
        str: Path to the published texture directory
        
    Raises:
        KnownPublishError: If no project open, no instances found, or export fails
    """
    # Ensure a project is open.
    if not substance_painter.project.is_open():
        raise KnownPublishError("No Substance Painter project is open.")
 
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
 
    # Use shared helper function for dialog selection
    instance = _select_texture_instance_from_dialog(texture_instances, parent)
 
    # Build export configuration from the instance data.
    config = build_export_config_from_instance_data(instance)
 
    # Determine export path and ensure the directory exists.
    publish_dir = _resolve_publish_texture_staging_dir(instance)
    if os.path.exists(publish_dir):
        base_dir = os.path.dirname(publish_dir)
        current_name = os.path.basename(publish_dir)
        # Only version up if the folder name is a purely numeric string (e.g., "001", "002")
        if current_name.isdigit():
            # Gather all existing numeric version directories (e.g., "001", "002", "003")
            versions = []
            for name in os.listdir(base_dir):
                if name.isdigit():
                    try:
                        versions.append(int(name))
                    except ValueError:
                        pass
            # Calculate next version: if "001" exists, next is "002"
            next_version = (max(versions) + 1) if versions else 1
            new_dir_name = f"{next_version:03d}"  # Formats as "001", "002", "003"...
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
        error_msg = f"Texture export failed: {result.message}"
        log.error(error_msg, exc_info=True)
        raise KnownPublishError(error_msg)
 
    # Mark instance so publish extractor knows textures are already exported.
    flags = instance.setdefault("ayon_flags", {})
    flags["textures_exported"] = True
 
    # Persist the updated instance data back into metadata.
    instance["stagingDir"] = publish_dir
    instance["publishDir"] = publish_dir
    set_instance(instance["instance_id"], instance, update=True)
    return publish_dir

class SubstanceHost(HostBase, IWorkfileHost, ILoadHost, IPublishHost):
    name = "substancepainter"

    def __init__(self):
        super(SubstanceHost, self).__init__()
        self._has_been_setup = False
        self.menu = None
        self.callbacks = []
        self.shelves = []

    def install(self):
        pyblish.api.register_host("substancepainter")

        pyblish.api.register_plugin_path(PUBLISH_PATH)
        register_loader_plugin_path(LOAD_PATH)
        register_creator_plugin_path(CREATE_PATH)

        log.info("Installing callbacks ... ")
        # register_event_callback("init", on_init)
        self._register_callbacks()
        # register_event_callback("before.save", before_save)
        # register_event_callback("save", on_save)
        register_event_callback("open", on_open)
        # register_event_callback("new", on_new)

        log.info("Installing menu ... ")
        self._install_menu()

        project_settings = get_current_project_settings()
        self._install_shelves(project_settings)

        self._has_been_setup = True

    def uninstall(self):
        self._uninstall_shelves()
        self._uninstall_menu()
        self._deregister_callbacks()

    def workfile_has_unsaved_changes(self):

        if not substance_painter.project.is_open():
            return False

        return substance_painter.project.needs_saving()

    def get_workfile_extensions(self):
        return [".spp", ".toc"]

    def save_workfile(self, dst_path=None):

        if not substance_painter.project.is_open():
            return False

        if not dst_path:
            dst_path = self.get_current_workfile()

        full_save_mode = substance_painter.project.ProjectSaveMode.Full
        substance_painter.project.save_as(dst_path, full_save_mode)

        return dst_path

    def open_workfile(self, filepath):

        if not os.path.exists(filepath):
            raise RuntimeError("File does not exist: {}".format(filepath))

        # We must first explicitly close current project before opening another
        if substance_painter.project.is_open():
            substance_painter.project.close()

        substance_painter.project.open(filepath)
        return filepath

    def get_current_workfile(self):
        if not substance_painter.project.is_open():
            return None

        filepath = substance_painter.project.file_path()
        if filepath and filepath.endswith(".spt"):
            # When currently in a Substance Painter template assume our
            # scene isn't saved. This can be the case directly after doing
            # "New project", the path will then be the template used. This
            # avoids Workfiles tool trying to save as .spt extension if the
            # file hasn't been saved before.
            return

        return filepath

    def get_containers(self):

        if not substance_painter.project.is_open():
            return

        metadata = substance_painter.project.Metadata(OPENPYPE_METADATA_KEY)
        containers = metadata.get(OPENPYPE_METADATA_CONTAINERS_KEY)
        if containers:
            for key, container in containers.items():
                container["objectName"] = key
                yield container

    def update_context_data(self, data, changes):

        if not substance_painter.project.is_open():
            return

        metadata = substance_painter.project.Metadata(OPENPYPE_METADATA_KEY)
        metadata.set(OPENPYPE_METADATA_CONTEXT_KEY, data)

    def get_context_data(self):

        if not substance_painter.project.is_open():
            return

        metadata = substance_painter.project.Metadata(OPENPYPE_METADATA_KEY)
        return metadata.get(OPENPYPE_METADATA_CONTEXT_KEY) or {}

    def _install_menu(self):
        from qtpy import QtWidgets , QtCore
        
        from ayon_core.tools.utils import host_tools

        parent = substance_painter.ui.get_main_window()

        tab_menu_label = os.environ.get("AYON_MENU_LABEL") or "AYON"
        menu = QtWidgets.QMenu(tab_menu_label)

        action = menu.addAction("Create...")
        action.triggered.connect(
            lambda: host_tools.show_publisher(parent=parent,
                                              tab="create")
        )

        action = menu.addAction("Load...")
        action.triggered.connect(
            lambda: host_tools.show_loader(parent=parent, use_context=True)
        )

        action = menu.addAction("Publish...")
        action.triggered.connect(
            lambda: host_tools.show_publisher(parent=parent,
                                              tab="publish")
        )

        action = menu.addAction("Manage...")
        action.triggered.connect(
            lambda: host_tools.show_scene_inventory(parent=parent)
        )

        action = menu.addAction("Library...")
        action.triggered.connect(
            lambda: host_tools.show_library_loader(parent=parent)
        )

        menu.addSeparator()
        action = menu.addAction("Work Files...")
        action.triggered.connect(
            lambda: host_tools.show_workfiles(parent=parent)
        )

        # [RDO Modification] PIPE-612: Pre-export textures menu action
        def _pre_export_textures():
            """Callback to pre-export textures with selective options.

            This runs outside of the pyblish publish loop. Users can select:
            - Which materials/texture sets to export
            - Which UDIMs to export (or all)
            - Whether to create new version or overwrite current
            """
            if parent is None:
                log.error("Cannot export textures: Substance Painter main window not available")
                return

            # Show a blocking progress dialog during export
            progress_dialog = QtWidgets.QProgressDialog(
                "Exporting textures...",
                None,
                0,
                0,
                parent
            )
            progress_dialog.setWindowModality(QtCore.Qt.WindowModal)
            progress_dialog.setWindowTitle("Export Textures")
            progress_dialog.show()
            QtWidgets.QApplication.instance().processEvents()

            try:
                log.info("Starting selective texture pre-export...")
                
                # Use the new selective export function
                publish_dir = write_textures_to_publish_location_selective(parent=parent)
                progress_dialog.close()

                log.info(f"Pre-export completed. Textures written to: {publish_dir}")

                # Show success message to user
                QtWidgets.QMessageBox.information(
                    parent,
                    "Textures Exported Successfully",
                    f"Textures have been exported to:\n\n{publish_dir}\n\n"
                    "You can now proceed to publish without re-exporting."
                )

            except Exception as exc:
                progress_dialog.close()
                log.error(f"Error during texture pre-export: {exc}", exc_info=True)

                # Show detailed error message to user
                error_msg = str(exc)
                QtWidgets.QMessageBox.critical(
                    parent,
                    "Texture Export Failed",
                    f"Failed to export textures:\n\n{error_msg}\n\n"
                    "Check the console log for more details."
                )

        export_action = menu.addAction("Pre‑Export Textures")
        export_action.triggered.connect(_pre_export_textures)

        substance_painter.ui.add_menu(menu)

        def on_menu_destroyed():
            self.menu = None

        menu.destroyed.connect(on_menu_destroyed)

        self.menu = menu

    def _uninstall_menu(self):
        if self.menu:
            self.menu.destroy()
            self.menu = None

    def _register_callbacks(self):
        # Prepare emit event callbacks
        open_callback = partial(emit_event, "open")

        # Connect to the Substance Painter events
        dispatcher = substance_painter.event.DISPATCHER
        for event, callback in [
            (substance_painter.event.ProjectOpened, open_callback)
        ]:
            dispatcher.connect(event, callback)
            # Keep a reference so we can deregister if needed
            self.callbacks.append((event, callback))

    def _deregister_callbacks(self):
        for event, callback in self.callbacks:
            substance_painter.event.DISPATCHER.disconnect(event, callback)
        self.callbacks.clear()

    def _install_shelves(self, project_settings):

        shelves = project_settings["substancepainter"].get("shelves", [])
        if not shelves:
            return

        # Prepare formatting data if we detect any path which might have
        # template tokens like {folder[name]} in there.
        formatting_data = {}
        has_formatting_entries = any("{" in item["value"] for item in shelves)
        if has_formatting_entries:
            project_name = self.get_current_project_name()
            folder_path = self.get_current_folder_path()
            task_name = self.get_current_task_name()
            formatting_data = get_template_data_with_names(
                project_name, folder_path, task_name, project_settings
            )
            anatomy = Anatomy(project_name)
            formatting_data["root"] = anatomy.roots

        for shelve_item in shelves:

            # Allow formatting with anatomy for the paths
            path = shelve_item["value"]
            if "{" in path:
                path = StringTemplate.format_template(path, formatting_data)

            name = shelve_item["name"]
            shelf_name = None
            try:
                shelf_name = lib.load_shelf(path, name=name)
            except ValueError as exc:
                print(f"Failed to load shelf -> {exc}")

            if shelf_name:
                self.shelves.append(shelf_name)

    def _uninstall_shelves(self):
        for shelf_name in self.shelves:
            substance_painter.resource.Shelves.remove(shelf_name)
        self.shelves.clear()


def on_open():
    log.info("Running callback on open..")

    if any_outdated_containers():
        from ayon_core.tools.utils import SimplePopup

        log.warning("Scene has outdated content.")

        # Get main window
        parent = substance_painter.ui.get_main_window()
        if parent is None:
            log.info("Skipping outdated content pop-up "
                     "because Substance window can't be found.")
        else:

            # Show outdated pop-up
            def _on_show_inventory():
                from ayon_core.tools.utils import host_tools
                host_tools.show_scene_inventory(parent=parent)

            dialog = SimplePopup(parent=parent)
            dialog.setWindowTitle("Substance scene has outdated content")
            dialog.set_message("There are outdated containers in "
                              "your Substance scene.")
            dialog.on_clicked.connect(_on_show_inventory)
            dialog.show()


def imprint_container(container,
                      name,
                      namespace,
                      context,
                      loader):
    """Imprint a loaded container with metadata.

    Containerisation enables a tracking of version, author and origin
    for loaded assets.

    Arguments:
        container (dict): The (substance metadata) dictionary to imprint into.
        name (str): Name of resulting assembly
        namespace (str): Namespace under which to host container
        context (dict): Asset information
        loader (load.LoaderPlugin): loader instance used to produce container.

    Returns:
        None

    """

    data = [
        ("schema", "openpype:container-2.0"),
        ("id", AVALON_CONTAINER_ID),
        ("name", str(name)),
        ("namespace", str(namespace) if namespace else None),
        ("loader", str(loader.__class__.__name__)),
        ("representation", context["representation"]["id"]),
        ("project_name", context["project"]["name"]),
    ]
    for key, value in data:
        container[key] = value


def set_container_metadata(object_name, container_data, update=False):
    """Helper method to directly set the data for a specific container

    Args:
        object_name (str): The unique object name identifier for the container
        container_data (dict): The data for the container.
            Note 'objectName' data is derived from `object_name` and key in
            `container_data` will be ignored.
        update (bool): Whether to only update the dict data.

    """
    # The objectName is derived from the key in the metadata so won't be stored
    # in the metadata in the container's data.
    container_data.pop("objectName", None)

    metadata = substance_painter.project.Metadata(OPENPYPE_METADATA_KEY)
    containers = metadata.get(OPENPYPE_METADATA_CONTAINERS_KEY) or {}
    if update:
        existing_data = containers.setdefault(object_name, {})
        existing_data.update(container_data)  # mutable dict, in-place update
    else:
        containers[object_name] = container_data
    metadata.set("containers", containers)


def remove_container_metadata(object_name):
    """Helper method to remove the data for a specific container"""
    metadata = substance_painter.project.Metadata(OPENPYPE_METADATA_KEY)
    containers = metadata.get(OPENPYPE_METADATA_CONTAINERS_KEY)
    if containers:
        containers.pop(object_name, None)
        metadata.set("containers", containers)


def set_instance(instance_id, instance_data, update=False):
    """Helper method to directly set the data for a specific container

    Args:
        instance_id (str): Unique identifier for the instance
        instance_data (dict): The instance data to store in the metaadata.
    """
    set_instances({instance_id: instance_data}, update=update)


def set_instances(instance_data_by_id, update=False):
    """Store data for multiple instances at the same time.

    This is more optimal than querying and setting them in the metadata one
    by one.
    """
    metadata = substance_painter.project.Metadata(OPENPYPE_METADATA_KEY)
    instances = metadata.get(OPENPYPE_METADATA_INSTANCES_KEY) or {}

    for instance_id, instance_data in instance_data_by_id.items():
        if update:
            existing_data = instances.get(instance_id, {})
            existing_data.update(instance_data)
        else:
            instances[instance_id] = instance_data

    metadata.set("instances", instances)


def remove_instance(instance_id):
    """Helper method to remove the data for a specific container"""
    metadata = substance_painter.project.Metadata(OPENPYPE_METADATA_KEY)
    instances = metadata.get(OPENPYPE_METADATA_INSTANCES_KEY) or {}
    instances.pop(instance_id, None)
    metadata.set("instances", instances)


def get_instances_by_id():
    """Return all instances stored in the project instances metadata"""
    if not substance_painter.project.is_open():
        return {}

    metadata = substance_painter.project.Metadata(OPENPYPE_METADATA_KEY)
    return metadata.get(OPENPYPE_METADATA_INSTANCES_KEY) or {}


def get_instances():
    """Return all instances stored in the project instances as a list"""
    return list(get_instances_by_id().values())


