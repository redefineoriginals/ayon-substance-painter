import os
import tempfile

from ayon_core.lib import get_ffmpeg_tool_args, run_subprocess
from ayon_core.pipeline import publish, get_temp_dir

from ayon_substancepainter.api.lib import get_review_screenshot_paths


class ExtractReviewMovie(publish.Extractor):
    """Compile curated viewport screenshots into a single review movie."""

    label = "Extract Review"
    hosts = ["substancepainter"]
    families = ["review"]

    def process(self, instance):
        creator_attrs = instance.data.get("creator_attributes", {})
        screenshots = get_review_screenshot_paths(creator_attrs)
        hold_duration = float(creator_attrs.get("hold_duration", 1.0))

        staging_dir = get_temp_dir(
            project_name=instance.context.data["projectName"],
            use_local_temp=True,
        )
        filename = "{}.mp4".format(instance.name)
        output_path = os.path.join(staging_dir, filename)

        self._encode(screenshots, output_path, hold_duration)

        instance.data.setdefault("representations", []).append({
            "name": "review",
            "ext": "mp4",
            "files": filename,
            "stagingDir": staging_dir,
            "tags": ["review", "kitsureview"],
        })

    def _encode(self, screenshots, output_path, hold_duration):
        concat_path = self._write_concat_file(screenshots, hold_duration)
        args = get_ffmpeg_tool_args(
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_path,
            "-vf", "scale=2048:858:force_original_aspect_ratio=decrease,"
                "pad=2048:858:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            "-g", "1",
            "-movflags", "faststart",
            output_path,
        )
        run_subprocess(args, logger=self.log)
        os.remove(concat_path)

    def _write_concat_file(self, screenshots, duration=1.0):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as concat_file:
            for path in screenshots:
                concat_file.write("file '{}'\n".format(path))
                concat_file.write("duration {}\n".format(duration))
            # Last entry repeated — ffmpeg concat ignores duration
            # of the final listed file otherwise.
            concat_file.write("file '{}'\n".format(screenshots[-1]))
            return concat_file.name
