import os

import pyblish.api

from ayon_core.pipeline import PublishValidationError


class ValidatePreExportedTextures(pyblish.api.InstancePlugin):
    """Validate that pre-exported texture files exist.

    When textures are exported outside of the publish loop (see USER‑612),
    the pyblish extractor will skip re-exporting them during publishing.
    This validator ensures that the files expected by the image instances
    actually exist on disk in the publish staging directory. If files are
    missing, the instance can be disabled (when "Allow Skipped Output Maps"
    is enabled) or a validation error will be raised.

    The validator only runs when the textureSet instance has the
    ``textures_exported`` flag set on its ``ayon_flags`` or ``flags``.
    """

    label = "Validate Pre‑Exported Textures"
    order = pyblish.api.ValidatorOrder - 0.1
    hosts = ["substancepainter"]
    families = ["textureSet"]

    def process(self, instance):
        # Only validate if textures were pre-exported
        flags = instance.data.get("ayon_flags") or instance.data.get("flags") or {}
        if not flags.get("textures_exported"):
            return

        creator_attrs = instance.data.get("creator_attributes", {})
        allow_skipped_maps = creator_attrs.get("allowSkippedMaps", True)

        error_report_missing = []

        for image_instance in instance:
            # Each image instance should have one representation with expected
            # file names and a stagingDir
            representation = image_instance.data["representations"][0]
            staging_dir = representation.get("stagingDir")
            filenames = representation.get("files")
            if not staging_dir or not filenames:
                # If we cannot determine expected files, skip validation for
                # this image instance.
                continue
            if not isinstance(filenames, (list, tuple)):
                filenames = [filenames]

            missing = []
            for fname in filenames:
                filepath = os.path.join(staging_dir, fname)
                if not os.path.exists(filepath):
                    missing.append(filepath)

            if not missing:
                continue

            if allow_skipped_maps:
                # Disable the image instance so it is not published
                image_instance.data["active"] = False
                image_instance.data["publish"] = False
                image_instance.data["integrate"] = False
                representation.setdefault("tags", []).append("delete")
            else:
                error_report_missing.append((image_instance, missing))

        if error_report_missing:
            message = (
                "Some pre‑exported textures are missing in the publish staging directory. "
                "Disable 'Allow Skipped Output Maps' or re‑export the textures before publishing.\n"
            )
            for inst, missing in error_report_missing:
                missing_str = ", ".join(missing)
                message += f"\n- {inst.name}: {missing_str}"
            raise PublishValidationError(message)