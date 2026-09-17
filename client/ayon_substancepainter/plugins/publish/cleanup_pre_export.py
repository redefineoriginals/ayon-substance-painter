"""
Reset pre-export staging state after a successful publish.

The "Pre-Export Textures" UI action persists stagingDir/publishDir and the
textures_exported flag onto the stored instance (see
write_textures_to_publish_location_selective in api/lib.py) so a later
publish run can find and reuse the already-exported files.

AYON's generic CleanUp plugin (ayon_core.plugins.publish.cleanup.CleanUp)
does not include "substancepainter" in its hosts list, so nothing else
ever clears that persisted state. Without this plugin, every future
publish/pre-export cycle for this instance would keep reusing the exact
same staging folder from the very first pre-export ever done, instead of
getting a fresh one the way every other host does.
"""
import os
import shutil
import tempfile

import pyblish.api

from ayon_substancepainter.api.pipeline import set_instance


class ResetPreExportedState(pyblish.api.InstancePlugin):
    """Clear persisted pre-export staging state after a successful publish."""

    label = "Reset Pre-Export State"
    order = pyblish.api.IntegratorOrder + 10
    hosts = ["substancepainter"]
    families = ["textureSet"]
    optional = True
    active = True

    def process(self, instance):
        flags = instance.data.get("ayon_flags") or {}
        if not flags.get("textures_exported"):
            # Textures were exported normally during this publish, not
            # pre-exported - nothing to reset.
            return

        for result in instance.context.data.get("results", []):
            if result["error"] is not None and result["instance"] is instance:
                # Publish failed for this instance - keep the pre-exported
                # state so the artist doesn't lose the already-exported
                # files on the next attempt.
                return

        instance_id = instance.data.get("instance_id")
        if not instance_id:
            return

        staging_dir = instance.data.get("stagingDir")

        set_instance(
            instance_id,
            {
                "stagingDir": None,
                "publishDir": None,
                "ayon_flags": {},
            },
            update=True,
        )
        self.log.info(
            "Cleared pre-export state for "
            f"'{instance.data.get('productName', instance_id)}' - "
            "next pre-export/publish will use a fresh staging directory."
        )

        if not staging_dir:
            return

        if instance.data.get("stagingDir_persistent"):
            self.log.debug(f"Staging dir is persistent, keeping: {staging_dir}")
            return

        temp_root = tempfile.gettempdir()
        if not os.path.normpath(staging_dir).startswith(temp_root):
            self.log.debug(
                f"Not removing staging dir outside temp root: {staging_dir}"
            )
            return

        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)
            self.log.info(f"Removed staging directory: {staging_dir}")
