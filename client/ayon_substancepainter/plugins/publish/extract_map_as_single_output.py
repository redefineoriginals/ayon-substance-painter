import clique
import os

from ayon_core.pipeline import publish
from ayon_core.lib import (
    get_oiio_tool_args,
    run_subprocess,
)


def convert_texture_maps_for_udim_export(staging_dir, image_outputs, has_udim=False):
    if has_udim:
        collections, remainder = clique.assemble(image_outputs, minimum_items=1)
        return [
            os.path.join(
                staging_dir,
                collection.format(pattern="{head}{padding}{tail}")
            )
            for collection in collections
        ]
    else:
        return [
            os.path.join(staging_dir, output) for output in image_outputs
        ]


def convert_texture_maps_as_single_output(staging_dir, source_image_outputs,
                                          dest_image_outputs, has_udim=False,
                                          log=None):
    oiio_tool_args = get_oiio_tool_args("oiiotool")

    source_maps = convert_texture_maps_for_udim_export(
        staging_dir, source_image_outputs, has_udim=has_udim)
    dest_map = next(convert_texture_maps_for_udim_export(
        staging_dir, dest_image_outputs, has_udim=has_udim
        ), None)

    log.info(f"{source_maps} composited as {dest_map}")
    oiio_cmd = oiio_tool_args + source_maps + [
        "--over", "-o",
       dest_map
    ]

    subprocess_args = " ".join(oiio_cmd)

    env = os.environ.copy()
    env.pop("OCIO", None)
    log.info(" ".join(subprocess_args))
    try:
        run_subprocess(subprocess_args, env=env)
    except Exception:
        log.error("Texture maketx conversion failed", exc_info=True)
        raise


class ExtractTexturesAsSingleOutput(publish.Extractor):
    """Extract Texture As Single Output

    Combine the multliple texture sets into one single texture output.

    """

    label = "Extract Texture Sets as Single Texture Output"
    hosts = ["substancepainter"]
    families = ["image"]
    settings_category = "substancepainter"

    # Run directly after textures export
    order = publish.Extractor.order - 0.099

    def process(self, instance):
        if "exportTextureSetsAsOneOutput" not in instance.data["creator_attributes"]:
            self.log.debug(
                "Skipping to export texture sets as single texture output.."
            )
            return

        representations: "list[dict]" = instance.data["representations"]

        staging_dir = instance.data["stagingDir"]
        source_image_outputs = instance.data["image_outputs"]
        has_udim = False
        dest_image_outputs = []
        for representation in list(representations):
            dest_files = representation["files"]
            is_sequence = isinstance(dest_files, (list, tuple))
            if not is_sequence:
                dest_image_outputs = [dest_image_outputs]
            if "udim" in representation:
                has_udim = True

        convert_texture_maps_as_single_output(
            staging_dir, source_image_outputs,
            dest_image_outputs, has_udim=has_udim,
            log=self.log
        )
