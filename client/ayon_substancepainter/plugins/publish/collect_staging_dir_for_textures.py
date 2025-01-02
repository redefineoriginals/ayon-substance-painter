import pyblish.api
from ayon_core.pipeline import publish


class CollectStagingDirExportConfig(pyblish.api.InstancePlugin):
    """Collect Staging Dir for Export Config to export the textures"""

    label = "Collect Staging dir for Export Config"
    hosts = ["substancepainter"]
    families = ["textureSet", "textures", "image"]
    order = pyblish.api.CollectorOrder + 0.4992

    def process(self, instance):
        instance.data["exportConfig"]["exportPath"] = instance.data["stagingDir"]


class CollectStagingDirTexture(pyblish.api.InstancePlugin):
    """Collect Staging Dir as the representation data for the texture publish"""

    label = "Collect Staging dir for texture"
    hosts = ["substancepainter"]
    families = ["image", "textures", "textureSet"]
    order = pyblish.api.CollectorOrder + 0.4993

    def process(self, instance):
        # Update the collected staging dir because we initially
        # collected the textures sets using a temp directory
        # to allow the instances to be defined prior to defining
        # their expected paths (staging dir) which may be based
        # on anatomy data and custom staging dirs, etc.
        for repre in instance.data["representations"]:
            repre["stagingDir"] = instance.data["stagingDir"]
