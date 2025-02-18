import clique
import os

from ayon_core.pipeline import publish
from ayon_core.lib import (
    get_oiio_tool_args,
    run_subprocess,
)


def get_texture_outputs(staging_dir, image_outputs):
    """Getting the expected texture output(s) with/without udim sequence
    before merging them with oiio tools.

    Args:
        staging_dir (str): staging dir
        image_outputs (list): source image outputs

    Returns:
        list: Texture outputs which are used for merging.
    """
    return [
        os.path.join(staging_dir, output) for output in image_outputs
    ]


def convert_texture_maps_as_single_output(staging_dir, source_image_outputs,
                                          dest_image_outputs, log=None):
    oiio_tool_args = get_oiio_tool_args("oiiotool")

    source_maps = get_texture_outputs(
        staging_dir, source_image_outputs)
    dest_map = next(
        (dest_texture for dest_texture in
         get_texture_outputs(
             staging_dir, dest_image_outputs)), None)

    log.info(f"{source_maps} composited as {dest_map}")
    oiio_cmd = oiio_tool_args + source_maps + [
        "--mosaic",
        "{}x1".format(len(source_maps)),
        "-o",
       dest_map
    ]

    env = os.environ.copy()

    try:
        run_subprocess(oiio_cmd, env=env)
    except Exception as exc:
        raise RuntimeError("Flattening texture stack to single output image failed") from exc


class ExtractTexturesAsSingleOutput(publish.Extractor):
    """Extract Texture As Single Output

    Combine the multliple texture sets into one single texture output.

    """

    label = "Extract Texture Sets as Single Texture Output"
    hosts = ["substancepainter"]
    families = ["image"]
    settings_category = "substancepainter"

    # Run directly after textures export
    order = publish.Extractor.order - 0.0991

    def process(self, instance):
        if not instance.data.get("creator_attributes", {}).get(
            "flattenTextureSets", False):
            self.log.debug(
                "Skipping to export texture sets as single texture output.."
            )
            return

        representations: "list[dict]" = instance.data["representations"]
        repre = representations[0]

        staging_dir = instance.data["stagingDir"]
        dest_image_outputs = instance.data["image_outputs"]
        source_image = repre["files"]
        is_sequence = isinstance(source_image, (list, tuple))
        if not is_sequence:
            source_image_outputs = [source_image]
        else:
            source_image_outputs = source_image
        repre["files"] = dest_image_outputs[0]
        repre.pop("udim", None)

        convert_texture_maps_as_single_output(
            staging_dir, source_image_outputs,
            dest_image_outputs, log=self.log
        )
