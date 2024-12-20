import copy

import pyblish.api
from ayon_core.pipeline import publish
from ayon_substancepainter.api.lib import get_parsed_export_maps

class CollectStagingDirExportConfig(pyblish.api.InstancePlugin):
    """Collect Staging Dir for Export Config to export the textures"""


    label = "Collect Staging dir for Export Config"
    hosts = ["substancepainter"]
    families = ["textureSet", "textures", "image"]
    order = pyblish.api.CollectorOrder + 0.491

    def process(self, instance):
        export_config = copy.deepcopy(instance.data["exportConfig"])
        export_config["exportPath"] = publish.get_instance_staging_dir(instance)
        instance.data["exportConfig"] = export_config
        instance.data.update(instance.data["exportConfig"])

class CollectStagingDirTexture(pyblish.api.InstancePlugin):
    """Collect Staging Dir as the representation data for the texture publish"""


    label = "Collect Staging dir for texture"
    hosts = ["substancepainter"]
    families = ["image", "textures"]
    order = pyblish.api.CollectorOrder + 0.4911

    def process(self, instance):
        representations: "list[dict]" = instance.data["representations"]
        staging_dir = instance.data["exportConfig"]["exportPath"]
        updated_representations = []
        for representation in list(representations):
            tmp_representation = copy.deepcopy(representation)
            tmp_representation["stagingDir"] = staging_dir
            updated_representations.append(tmp_representation)
        instance.data["representations"] = updated_representations
