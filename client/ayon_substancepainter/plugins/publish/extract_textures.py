import os

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
        substance_painter.project.execute_when_not_busy(
            lambda: self._export_texture_set(instance)
        )

        # We'll insert the color space data for each image instance that we
        # added into this texture set. The collector couldn't do so because
        # some anatomy and other instance data needs to be collected prior
        context = instance.context
        for image_instance in instance:
            representation = next(iter(image_instance.data["representations"]))
            colorspace = image_instance.data.get("colorspace")
            if not colorspace:
                self.log.debug("No color space data present for instance: "
                               f"{image_instance}")
                continue
            self.set_representation_colorspace(representation,
                                               context=context,
                                               colorspace=colorspace)

        # The TextureSet instance should not be integrated. It generates no
        # output data. Instead the separated texture instances are generated
        # from it which themselves integrate into the database.
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

            exported_filenames = set()
            for (texture_set_name, stack_name), maps in (
                result.textures.items()
            ):
                # Log our texture outputs
                self.log.info(
                    f"Exported stack: {texture_set_name} {stack_name}"
                )
                for texture_map in maps:
                    self.log.info(f"Exported texture: {texture_map}")
                    exported_filenames.add(os.path.basename(texture_map))

        #((RDO-NEW)rdo-modification
        # allowSkippedMaps may cause a channel's file to be skipped entirely.
        # Drop image instances whose representation was not actually written.
        context = instance.context
        for image_instance in list(instance):
            representation = next(
                iter(image_instance.data.get("representations", [])), None
            )
            if representation is None:
                continue

            files = representation.get("files", [])
            if isinstance(files, str):
                files = [files]

            if files and not any(f in exported_filenames for f in files):
                self.log.debug(
                    "Skipped channel, no export for %s: %s",
                    image_instance, files
                )
                instance.remove(image_instance)
                context.remove(image_instance)
        #((RDO-NEW)rdo-modification-end
