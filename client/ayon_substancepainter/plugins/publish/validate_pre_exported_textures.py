import os
import logging
import pyblish.api
from ayon_core.pipeline import PublishValidationError

log = logging.getLogger(__name__)


class ValidatePreExportedTextures(pyblish.api.InstancePlugin):
    """Validate that pre-exported texture files exist on disk.
    
    When textures are exported outside of the publish loop via the 
    "Pre-Export Textures" action, this validator ensures that the files 
    expected by the image instances actually exist on disk in the publish 
    staging directory.
    
    If files are missing, the instance can be disabled (when 
    "Allow Skipped Output Maps" is enabled) or a validation error will 
    be raised.
    
    The validator only runs when the textureSet instance has the
    ``textures_exported`` flag set on its ``ayon_flags`` or ``flags``.
    """
    
    label = "Validate Pre‑Exported Textures"
    order = pyblish.api.ValidatorOrder + 0.1
    hosts = ["substancepainter"]
    families = ["textureSet"]

    def process(self, instance):
        # Only validate if textures were pre-exported
        flags = instance.data.get("ayon_flags") or instance.data.get("flags") or {}
        if not flags.get("textures_exported"):
            return

        # Get export info (for logging)
        exported_materials = flags.get("exported_materials")
        exported_udims = flags.get("exported_udims")
        export_strategy = flags.get("export_strategy", "unknown")
        
        self.log.info(
            f"Validating pre-exported textures: "
            f"materials={exported_materials}, "
            f"udims={exported_udims}, "
            f"strategy={export_strategy}"
        )

        creator_attrs = instance.data.get("creator_attributes", {})
        allow_skipped_maps = creator_attrs.get("allowSkippedMaps", True)
        error_report_missing = []

        for image_instance in instance:
            # Get representations with safety check
            representations = image_instance.data.get("representations", [])
            if not representations:
                self.log.warning(
                    f"No representations found for image instance: {image_instance.name}"
                )
                continue

            representation = representations[0]

            # Resolve staging directory with fallbacks
            staging_dir = (
                representation.get("stagingDir") or
                image_instance.data.get("stagingDir") or
                instance.data.get("stagingDir") or
                instance.data.get("publishDir")
            )
            filenames = representation.get("files")

            if not staging_dir or not filenames:
                # If we cannot determine expected files, skip validation
                self.log.debug(
                    f"Skipping validation for {image_instance.name}: "
                    f"staging_dir={staging_dir}, files={filenames}"
                )
                continue

            # Normalize filenames to list
            if not isinstance(filenames, (list, tuple)):
                filenames = [filenames]

            # Check for missing files
            missing = []
            for fname in filenames:
                filepath = os.path.join(staging_dir, fname)
                if not os.path.exists(filepath):
                    missing.append(filepath)
                    self.log.debug(f"Missing texture file: {filepath}")

            if not missing:
                continue

            # Handle missing files based on settings
            if allow_skipped_maps:
                self.log.info(
                    f"Disabling image instance '{image_instance.name}' "
                    f"due to missing textures"
                )
                image_instance.data["active"] = False
                image_instance.data["publish"] = False
                image_instance.data["integrate"] = False
                representation.setdefault("tags", []).append("delete")
            else:
                error_report_missing.append((image_instance, missing))

        # Report all missing files at once
        if error_report_missing:
            message = (
                "Some pre‑exported textures are missing in the publish staging directory. "
                "Disable 'Allow Skipped Output Maps' or re‑export the textures before publishing.\n"
            )
            for inst, missing in error_report_missing:
                missing_str = ", ".join(missing)
                message += f"\n- {inst.name}: {missing_str}"

            raise PublishValidationError(message)