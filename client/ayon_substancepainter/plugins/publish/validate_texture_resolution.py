import os
import sys

import pyblish.api

from ayon_core.pipeline import PublishValidationError
from ayon_core.lib.transcoding import get_oiio_info_for_input


def _resolve_long_path(path):
    """Resolve a Windows 8.3 short path to its full long path.

    On Windows, temp directories may be represented with 8.3 short names
    (e.g. USERF~1.NAM instead of username.full). Python's os.path.isfile
    can fail to match these correctly in some environments. This function
    resolves the short path to the full long path using the Windows API.

    On non-Windows platforms the path is returned unchanged.

    Args:
        path (str): File path, potentially containing 8.3 short components.

    Returns:
        str: Resolved long path on Windows, original path on other platforms.
    """
    if sys.platform != "win32":
        return path
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        get_long = ctypes.windll.kernel32.GetLongPathNameW
        get_long(path, buf, 32768)
        return buf.value or path
    except Exception:
        # Broad catch is intentional here. This function is best-effort -
        # if the Windows API call fails for any reason (ctypes unavailable,
        # kernel32 not found, unexpected return value), we silently fall back
        # to the original path and let os.path.isfile try its luck. Raising
        # or logging here would produce noise that confuses Artists and TDs,
        # since the failure mode is benign: worst case, os.path.isfile
        # returns False and the file is caught by the fail-safe.
        return path


class ValidateTextureResolution(pyblish.api.InstancePlugin):
    """Validate that texture files do not exceed the project resolution limit.

    The maximum resolution is configured in Ayon project settings under:
        substancepainter > load > SubstanceLoadProjectMesh
            > max_publish_texture_resolution

    This is Gate 2 of the two-gate resolution enforcement system.
    Gate 1 (write-time) warns the Artist via a dialog in the Write tool
    before baking begins, but always allows bypass.

    Gate 2 (this validator) hard blocks publish by default. A show can
    request 'sanity_check_optional=True' in the project settings if they
    have a legitimate need for higher resolution textures (e.g. DMPs).
    When optional, this validator downgrades to a WARNING and publish
    is allowed to proceed.

    Artists submitting via Tray Publisher are also covered by this validator
    since it is DCC-agnostic and reads from representations on disk.

    Dimension reading:
        Uses get_oiio_info_for_input from ayon_core.lib.transcoding, which
        wraps the OIIO iinfo CLI tool distributed with Ayon via ayon-third-party.
        This is the standard ayon-core approach for image inspection.

    Fail-safe behaviour:
        If the dimensions of a texture file cannot be determined (e.g. the
        file cannot be opened by OIIO), the file is treated as a violation
        and publish is blocked. This ensures oversized textures cannot slip
        through due to unreadable files.
    """

    # Must run after ExtractorOrder so "Extract Texture Set" has already
    # written files to staging before we check dimensions.
    order = pyblish.api.ExtractorOrder + 0.1
    label = "Validate texture resolution"
    hosts = ["substancepainter"]
    families = ["textureSet"]

    # Dynamically set from project settings in apply_settings
    optional = False
    _sanity_check_optional = False
    _max_publish_texture_resolution = 4096

    @classmethod
    def apply_settings(cls, project_settings):
        #((RDO-240226)rdo-modification
        # Resolution limit settings are read directly here rather than via
        # lib.py to keep the logic local and avoid a cyclic import.)
        limits = (
            project_settings
            .get("substancepainter", {})
            .get("load", {})
            .get("SubstanceLoadProjectMesh", {})
        )
        cls._max_publish_texture_resolution = limits.get(
            "max_publish_texture_resolution", 4096
        )
        cls._sanity_check_optional = limits.get("sanity_check_optional", False)
        # When the show override is active, mark the validator as optional
        # so Artists can still publish with an acknowledged warning.
        cls.optional = cls._sanity_check_optional

    def process(self, instance):
        max_res = self._max_publish_texture_resolution
        if max_res == 0:
            self.log.debug(
                "Texture resolution limit is disabled. Skipping validation."
            )
            return

        violations = []
        unreadable = []

        # Representations are on the child instances, not the parent textureSet
        for child in instance:
            if not hasattr(child, "data"):
                continue
            representations = child.data.get("representations", [])
            if not representations:
                continue

            for representation in representations:
                staging_dir = _resolve_long_path(
                    representation.get("stagingDir", "")
                )
                filenames = representation.get("files", [])
                if isinstance(filenames, str):
                    filenames = [filenames]

                for filename in filenames:
                    filepath = os.path.join(staging_dir, filename)

                    if not os.path.isfile(filepath):
                        # File does not exist - treat as unreadable, fail safe
                        self.log.warning(
                            "Texture file not found, treating as violation "
                            "to fail safe: %s", filepath
                        )
                        unreadable.append(filename)
                        continue

                    width, height = self._get_dimensions(filepath)
                    if width is None:
                        # Dimensions unreadable - treat as violation, fail safe
                        self.log.warning(
                            "Could not determine dimensions of %s, treating "
                            "as violation to fail safe.", filepath
                        )
                        unreadable.append(filename)
                        continue

                    longest = max(width, height)
                    if longest > max_res:
                        violations.append({
                            "name": filename,
                            "width": width,
                            "height": height,
                            "longest": longest,
                        })

        if not violations and not unreadable:
            self.log.info(
                "All textures are within the %spx resolution limit.", max_res
            )
            return

        lines = []

        if violations:
            lines.append(
                "{} texture(s) exceed the project limit of {}px:\n".format(
                    len(violations), max_res
                )
            )
            for v in violations:
                lines.append(
                    "  - {}  ->  {} x {}px".format(
                        v["name"], v["width"], v["height"]
                    )
                )

        if unreadable:
            lines.append(
                "\n{} texture(s) could not be read and have been blocked "
                "as a precaution:\n".format(len(unreadable))
            )
            for name in unreadable:
                lines.append("  - {}".format(name))
            lines.append(
                "\nIf this is unexpected, ask your TD to verify that "
                "OpenImageIO tools are available in the pipeline environment."
            )

        lines.append(
            "\nReduce texture resolution to {}px or below.".format(max_res)
        )

        if self._sanity_check_optional:
            lines.append(
                "\nShow override is active - publish will proceed "
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
                "One or more textures exceed the maximum allowed resolution "
                "of {}px, or their dimensions could not be verified.\n\n"
                "Publishing has been blocked to prevent downstream crashes "
                "and wasted processing time.\n\n"
                "If this show requires higher resolution textures, ask your "
                "supervisor to enable 'Make Publish Validator Optional' in "
                "the Ayon project settings for this show.".format(max_res)
            ),
        )

    def _get_dimensions(self, filepath):
        """Return (width, height) of an image file.

        Uses get_oiio_info_for_input from ayon_core.lib.transcoding, which
        wraps the OIIO iinfo CLI tool distributed with Ayon. This is the
        standard ayon-core approach for reading image metadata.

        If the file cannot be read, returns (None, None). The caller treats
        this as a violation to ensure fail-safe behaviour - oversized textures
        cannot slip through due to missing or unreadable files.

        Args:
            filepath (str): Absolute path to the image file.

        Returns:
            tuple[int, int] | tuple[None, None]: Image dimensions or
                (None, None) if the file could not be read.
        """
        try:
            image_info = get_oiio_info_for_input(filepath)
            if not image_info:
                self.log.warning(
                    "OIIO returned no info for %s", filepath
                )
                return None, None
            width = image_info.get("width")
            height = image_info.get("height")
            if width is None or height is None:
                self.log.warning(
                    "OIIO info missing width/height for %s: %s",
                    filepath, image_info
                )
                return None, None
            return int(width), int(height)
        except KeyError as exc:
            self.log.warning(
                "Unexpected OIIO info structure for %s: %s",
                filepath, exc, exc_info=True
            )
        except (ValueError, TypeError) as exc:
            self.log.warning(
                "Could not parse OIIO dimensions for %s: %s",
                filepath, exc, exc_info=True
            )
        except Exception as exc:
            # get_oiio_info_for_input may raise if the OIIO tool is not
            # found or the subprocess call fails unexpectedly.
            self.log.warning(
                "OIIO info call failed for %s: %s",
                filepath, exc, exc_info=True
            )
        return None, None