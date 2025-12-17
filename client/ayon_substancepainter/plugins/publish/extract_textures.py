import substance_painter.project
import substance_painter.export
from ayon_core.pipeline import KnownPublishError, publish
from ayon_substancepainter.api.lib import set_layer_stack_opacity


class ExtractTextures(publish.Extractor,
                      publish.ColormanagedPyblishPluginMixin):
    """Extract Textures using an output template config.

    Note:
        This Extractor assumes that `collect_textureset_images` has prepared
        the relevant export config and has also collected the individual image
        instances for publishing including its representation. That is why this
        particular Extractor doesn't specify representations to integrate.

    """

    label = "Extract Texture Set"
    hosts = ["substancepainter"]
    families = ["textureSet"]

    # Run before thumbnail extractors
    order = publish.Extractor.order - 0.1

    def process(self, instance):
        # Skip exporting if textures were already exported via the UI action.
        flags = instance.data.get("ayon_flags") or instance.data.get("flags") or {}
        if not flags.get("textures_exported"):
            config = instance.data["exportConfig"]
            creator_attrs = instance.data["creator_attributes"]
            export_channel = creator_attrs.get("exportChannel", [])
            node_ids = instance.data.get("selected_node_id", [])

            with set_layer_stack_opacity(node_ids, export_channel):
                result = substance_painter.export.export_project_textures(config)
                if result.status != substance_painter.export.ExportStatus.Success:
                    raise KnownPublishError(
                        f"Failed to export texture set: {result.message}"
                    )
                # Log what files we generated
                for (texture_set_name, stack_name), maps in result.textures.items():
                    self.log.info(f"Exported stack: {texture_set_name} {stack_name}")
                    for texture_map in maps:
                        self.log.info(f"Exported texture: {texture_map}")
        else:
            self.log.info("Textures already exported via UI action; skipping export.")

        # Insert color space data for each image instance added into this texture set
        context = instance.context
        for image_instance in instance:
            representation = next(iter(image_instance.data["representations"]))
            colorspace = image_instance.data.get("colorspace")
            if not colorspace:
                self.log.debug(
                    f"No color space data present for instance: {image_instance}"
                )
                continue
            self.set_representation_colorspace(
                representation,
                context=context,
                colorspace=colorspace,
            )

        # The TextureSet instance should not be integrated.
        instance.data["integrate"] = False


    def _export_texture_set(self, instance):
        """Export the texture set for the given instance.

        Args:
            instance (pyblish.api.Instance): The instance to export.

        Raises:
            KnownPublishError: If the export fails.
        """
        config = instance.data["exportConfig"]
        creator_attrs = instance.data["creator_attributes"]
        export_channel = creator_attrs.get("exportChannel", [])
        node_ids = instance.data.get("selected_node_id", [])
        with set_layer_stack_opacity(node_ids, export_channel):
            result = substance_painter.export.export_project_textures(config)

            if result.status != substance_painter.export.ExportStatus.Success:
                raise KnownPublishError(
                    "Failed to export texture set: {}".format(result.message)
                )

            # Log what files we generated
            for (texture_set_name, stack_name), maps in (
                result.textures.items()
            ):
                # Log our texture outputs
                self.log.info(
                    f"Exported stack: {texture_set_name} {stack_name}"
                )
                for texture_map in maps:
                    self.log.info(f"Exported texture: {texture_map}")
