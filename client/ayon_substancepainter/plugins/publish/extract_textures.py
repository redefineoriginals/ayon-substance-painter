"""
Extract textures plugin for AYON Substance Painter integration.

Handles both:
1. Normal export (when textures haven't been pre-exported)
2. Pre-exported textures (from the Pre-Export Textures UI action)

This dynamically determines the publish directory from the
image instance representations, following the actual AYON structure.
"""

import os
import logging
import pyblish.api
from ayon_core.pipeline import KnownPublishError

log = logging.getLogger(__name__)


class ExtractTextures(pyblish.api.InstancePlugin):
    """Extract textures from Substance Painter project.
    
    When textures are pre-exported via the "Pre-Export Textures" UI action,
    this plugin skips the export and uses the already-exported files.
    
    It dynamically determines the publish directory from the image instances,
    following the actual project structure .
    """

    label = "Extract Textures"
    order = pyblish.api.ExtractorOrder
    hosts = ["substancepainter"]
    families = ["textureSet"]
    optional = False

    def process(self, instance):
        """Process texture extraction or skip if pre-exported.
        
        Args:
            instance: The pyblish instance
        """
        # [RDO Modification] Check for pre-exported textures flag
        flags = instance.data.get("ayon_flags") or instance.data.get("flags") or {}
        textures_exported = flags.get("textures_exported", False)

        if textures_exported:
            # [RDO Modification] Pre-exported path: skip extraction
            self._process_pre_exported(instance, flags)
        else:
            # Normal path: export textures as usual
            self._process_normal_export(instance)

        # Process colorspace data for all image instances
        self._process_colorspace_data(instance)

        # TextureSet instance should not be integrated
        instance.data["integrate"] = False

    # [RDO Modification] PIPE-612: New function for handling pre-exported textures
    def _process_pre_exported(self, instance, flags):
        """Handle pre-exported textures.
        
        Files are already exported to a temp location.
        We determine the publish directory from the image instances.
        
        Args:
            instance: The pyblish instance
            flags: The ayon_flags dictionary
        """
        log.info("Textures already pre-exported via UI action")

        # Get the staging directory (where files currently are - temp location)
        staging_dir = instance.data.get("stagingDir")
        
        if not staging_dir:
            raise KnownPublishError(
                "Pre-exported textures detected but stagingDir not set on instance"
            )

        log.info(f"Using pre-exported files from: {staging_dir}")

        # Log what was exported
        exported_materials = flags.get("exported_materials", [])
        exported_udims = flags.get("exported_udims", [])
        export_strategy = flags.get("export_strategy", "unknown")

        log.info(f"  Materials exported: {exported_materials}")
        log.info(f"  UDIMs: {exported_udims if exported_udims else 'all'}")
        log.info(f"  Strategy: {export_strategy}")

        # Verify files exist
        if os.path.exists(staging_dir):
            file_count = len([f for r, d, files in os.walk(staging_dir) for f in files])
            log.info(f"  Found {file_count} files in staging directory")
        else:
            raise KnownPublishError(f"Staging directory not found: {staging_dir}")

        # [RDO Modification] Get publish directory from image instances
        publish_dir = self._get_publish_directory_from_representations(instance)

        log.info(f"Files will be integrated to: {publish_dir}")

        # Update instance with both directories
        instance.data["stagingDir"] = staging_dir
        instance.data["publishDir"] = publish_dir

    def _process_normal_export(self, instance):
        """Handle normal texture export (not pre-exported).
        
        Args:
            instance: The pyblish instance
        """
        import substance_painter
        import substance_painter.export

        log.info("Exporting textures via Substance Painter API")

        # Get export configuration
        export_config = instance.data.get("exportConfig")
        if not export_config:
            raise KnownPublishError("No export config found on instance")

        # Get staging directory
        staging_dir = instance.data.get("stagingDir")
        if not staging_dir:
            raise KnownPublishError("No stagingDir set on instance")

        # Set export path in config
        export_config["exportPath"] = staging_dir
        os.makedirs(staging_dir, exist_ok=True)

        log.info(f"Exporting to: {staging_dir}")

        # Perform export
        result = substance_painter.export.export_project_textures(export_config)

        if result.status != substance_painter.export.ExportStatus.Success:
            raise KnownPublishError(f"Texture export failed: {result.message}")

        log.info(f"Export successful")

        # Log exported files
        for (texture_set_name, stack_name), maps in result.textures.items():
            log.info(f"Exported {texture_set_name}/{stack_name}: {len(maps)} files")

        # Get publish directory from representations
        publish_dir = self._get_publish_directory_from_representations(instance)

        log.info(f"Files will be integrated to: {publish_dir}")

        # Update instance
        instance.data["stagingDir"] = staging_dir
        instance.data["publishDir"] = publish_dir

    def _process_colorspace_data(self, instance):
        """Process colorspace data for image instances.
        
        Args:
            instance: The pyblish instance
        """
        try:
            from .colorspace import get_project_channel_data
        except ImportError:
            log.debug("Colorspace module not available")
            return

        log.debug("Processing colorspace data for image instances")

        # Get project channel data
        try:
            channel_data = get_project_channel_data()
        except Exception as exc:
            log.debug(f"Failed to get colorspace data: {exc}")
            return

        # Process each image instance
        for image_instance in instance:
            texture_set_name = image_instance.data.get("textureset")
            
            if not channel_data or texture_set_name not in channel_data:
                log.debug(f"No colorspace data for {texture_set_name}")
                continue

            texture_set_data = channel_data[texture_set_name]
            if not texture_set_data:
                continue

            # Get colorspace and apply to representations
            colorspace = texture_set_data.get("colorSpace")
            if colorspace:
                for representation in image_instance.data.get("representations", []):
                    representation["colorspace"] = colorspace
                    log.debug(f"Set colorspace for {image_instance.name}: {colorspace}")

    # [RDO Modification] PIPE-612: Dynamic publish directory determination
    def _get_publish_directory_from_representations(self, instance):
        """Get the publish directory from image instance representations.
        
        Dynamically determines the publish path from the actual AYON structure
        defined in the image instances, without hardcoding paths.
        
        Structure follows: P:\Bollywoof\assets\character\chartest\publish\image\
                          textureMain.T_chartest_skin.rgb\v003\
        
        Args:
            instance: The pyblish instance (textureSet)
            
        Returns:
            str: Path to final publish directory (parent directory for all image outputs)
        """
        log.debug("Determining publish directory from representations")

        # Get all image instances from the textureSet
        image_instances = list(instance)
        
        if not image_instances:
            raise KnownPublishError("No image instances found in textureSet")

        # Get the first image instance's representation
        first_image = image_instances[0]
        representations = first_image.data.get("representations", [])
        
        if not representations:
            raise KnownPublishError(
                f"No representations found for image instance: {first_image.name}"
            )

        first_rep = representations[0]
        
        # Get the publish directory from representation
        publish_dir = first_rep.get("publishDir")
        
        if not publish_dir:
            # Fallback: try to construct from available paths
            staging_dir = first_rep.get("stagingDir")
            if staging_dir:
                publish_dir = staging_dir
            else:
                raise KnownPublishError(
                    "Cannot determine publish directory from representation"
                )

        log.info(f"Publish directory from representation: {publish_dir}")
        
        # Get parent directory (the image folder, not the version folder)
        parent_dir = os.path.dirname(publish_dir)  # Remove version folder (003)
        parent_dir = os.path.dirname(parent_dir)   # Remove image-specific folder
        
        log.info(f"Parent publish directory: {parent_dir}")

        # Ensure directory exists
        os.makedirs(parent_dir, exist_ok=True)

        return parent_dir
    
    