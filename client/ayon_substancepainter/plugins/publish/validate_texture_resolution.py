import os

import pyblish.api

from ayon_core.pipeline import PublishValidationError
from ayon_substancepainter.api.lib import get_max_texture_resolution


class ValidateTextureResolution(pyblish.api.InstancePlugin):
    """Validate that texture files do not exceed the project resolution limit.

    The maximum resolution is configured in Ayon project settings under:
        substancepainter > texture_limits > max_texture_resolution

    When 'sanity_check_optional' is enabled for a show, this validator is
    demoted to a warning and does not block publish. This allows edge-cases
    such as DMP-heavy environments or hero assets to proceed while still
    informing the Artist that oversized textures are being published.

    The write tool (write_textures_to_publish_location) also performs an
    early check so Artists are warned before wasting bake time.
    """

    order = pyblish.api.ValidatorOrder + 0.1
    label = "Validate texture resolution"
    hosts = ["substancepainter"]
    families = ["textureSet"]

    # Dynamically set from project settings in apply_settings
    optional = False
    _sanity_check_optional = False

    @classmethod
    def apply_settings(cls, project_settings):
        limits = (
            project_settings
                .get("substancepainter", {})
                .get("load", {})
                .get("SubstanceLoadProjectMesh", {})
        )
        cls._sanity_check_optional = limits.get("sanity_check_optional", False)
        # When the show override is active, mark the validator as optional
        # so Artists can still publish with an acknowledged warning.
        cls.optional = cls._sanity_check_optional

    def process(self, instance):
        max_res = get_max_texture_resolution()
        if max_res == 0:
            self.log.debug(
                "Texture resolution limit is disabled. Skipping validation."
            )
            return

        violations = []

        for image_instance in instance:
            representations = image_instance.data.get("representations", [])
            if not representations:
                continue

            representation = representations[0]
            staging_dir = representation.get("stagingDir", "")
            filenames = representation.get("files", [])
            if isinstance(filenames, str):
                filenames = [filenames]

            for filename in filenames:
                filepath = os.path.join(staging_dir, filename)
                if not os.path.isfile(filepath):
                    continue

                width, height = self._get_dimensions(filepath)
                if width is None:
                    continue

                longest = max(width, height)
                if longest > max_res:
                    violations.append({
                        "name": filename,
                        "width": width,
                        "height": height,
                        "longest": longest,
                    })

        if not violations:
            self.log.info(
                "All textures are within the %spx resolution limit.", max_res
            )
            return

        lines = [
            f"{len(violations)} texture(s) exceed the project limit "
            f"of {max_res}px:\n"
        ]
        for v in violations:
            lines.append(
                f"  \u2022 {v['name']}  \u2192  "
                f"{v['width']} x {v['height']}px"
            )
        lines.append(
            f"\nReduce texture resolution to {max_res}px or below."
        )

        if self._sanity_check_optional:
            lines.append(
                "\nShow override is active \u2014 publish will proceed "
                "with this warning. Consult your supervisor before delivery."
            )
            self.log.warning("\n".join(lines))
            # optional=True means Pyblish will not hard-fail, but we
            # still log it clearly so it appears in the publisher UI.
            return

        raise PublishValidationError(
            "\n".join(lines),
            title="Texture Resolution Limit Exceeded",
            description=(
                f"One or more textures exceed the maximum allowed resolution "
                f"of {max_res}px.\n\n"
                "Publishing has been blocked to prevent downstream crashes "
                "and wasted processing time.\n\n"
                "If this show requires higher resolution textures, ask your "
                "supervisor to enable 'Make Publish Validator Optional' in "
                "the Ayon project settings for this show."
            ),
        )

    def _get_dimensions(self, filepath):
        """Return (width, height) of an image file without loading pixel data.

        Args:
            filepath (str): Absolute path to the image file.

        Returns:
            tuple[int, int] | tuple[None, None]: Image dimensions or
                (None, None) if the file could not be read.
        """
        try:
            from PIL import Image
            with Image.open(filepath) as img:
                return img.size  # (width, height)
        except ImportError:
            pass
        except Exception as exc:
            self.log.warning(
                "Could not read dimensions of %s via Pillow: %s", filepath, exc
            )

        # Fallback: OpenImageIO iinfo command-line tool
        try:
            import subprocess
            result = subprocess.run(
                ["iinfo", filepath],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.splitlines():
                # iinfo output: "filename.exr: 4096 x 4096, ..."
                if " x " in line:
                    parts = line.split(":")[1].strip().split()
                    if len(parts) >= 3 and parts[1] == "x":
                        return int(parts[0]), int(parts[2].rstrip(","))
        except Exception as exc:
            self.log.warning(
                "Could not read dimensions of %s via iinfo: %s", filepath, exc
            )

        return None, None