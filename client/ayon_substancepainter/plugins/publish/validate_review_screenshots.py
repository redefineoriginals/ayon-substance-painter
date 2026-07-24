import pyblish.api

from ayon_core.pipeline import PublishValidationError

from ayon_substancepainter.api.lib import get_review_screenshot_paths


class ValidateReviewScreenshots(pyblish.api.InstancePlugin):
    """Validate at least one screenshot is attached to the review."""

    order = pyblish.api.ValidatorOrder
    label = "Validate review screenshots"
    hosts = ["substancepainter"]
    families = ["review"]

    def process(self, instance):
        creator_attrs = instance.data.get("creator_attributes", {})
        screenshots = get_review_screenshot_paths(creator_attrs)
        if not screenshots:
            raise PublishValidationError(
                "No screenshots attached to review instance "
                f"{instance.name}. Capture at least one viewport "
                "screenshot before publishing, or disable Review on the "
                "Textures instance.",
                title="Missing review screenshots"
            )
