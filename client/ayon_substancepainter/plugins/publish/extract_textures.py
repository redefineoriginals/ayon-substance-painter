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

    #((PIPE-612)rdo-modification
    Textures can optionally be pre-exported ahead of Publish via the
    "Write Textures (Publish Location)" manual action, which writes them
    directly to their final publish location and flags the instance as
    already exported (see ayon_substancepainter.api.lib). When that flag
    is present, this Extractor skips re-exporting and only validates and
    resolves the publish directory from the already-written
    representations. This decouples the (potentially long-running)
    texture render from the Publish loop, avoiding long-held AYON
    database connections during heavy texture exports. Representation
    and color-space handling below are unchanged for both paths.)
    """

    label = "Extract Texture Set"
    hosts = ["substancepainter"]
    families = ["textureSet"]

    # Run before thumbnail extractors
    order = publish.Extractor.order - 0.1

    def process(self, instance):
        # [RDO Modification] PIPE-612: skip export if textures were
        # already written by the pre-export action.
        flags = instance.data.get("ayon_flags") or instance.data.get("flags") or {}
        if flags.get("textures_exported", False):
            self._process_pre_exported(instance, flags)
        else:
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

    # [RDO Modification] PIPE-612: handle textures pre-exported via the
    # "Write Textures (Publish Location)" manual action.
    def _process_pre_exported(self, instance, flags):
        """Validate and register pre-exported textures instead of exporting.

        Files were already written directly to their publish location by
        the manual pre-export action. Publish here only validates they
        exist, drops any channel skipped by allowSkippedMaps, and resolves
        the publish directory from the image instances.

        Args:
            instance: The pyblish instance (textureSet)
            flags: The ayon_flags dictionary
        """
        self.log.info("Textures already pre-exported via UI action")

        # Safe fallback: stagingDir may not be set directly on this
        # instance in every flow, so fall back to publishDir if needed.
        staging_dir = (
            instance.data.get("stagingDir")
            or instance.data.get("publishDir")
        )
        if not staging_dir:
            raise KnownPublishError(
                "Pre-exported textures detected but stagingDir/publishDir "
                "not set on instance"
            )

        self.log.info(f"Using pre-exported files from: {staging_dir}")

        exported_materials = flags.get("exported_materials", [])
        exported_udims = flags.get("exported_udims", [])
        self.log.info(f"  Materials exported: {exported_materials}")
        self.log.info(f"  UDIMs: {exported_udims if exported_udims else 'all'}")

        # Additional validation: textures must actually be present before
        # Publish proceeds.
        if not os.path.exists(staging_dir):
            raise KnownPublishError(f"Staging directory not found: {staging_dir}")

        exported_filenames = {
            f for _root, _dirs, files in os.walk(staging_dir) for f in files
        }
        self.log.info(f"  Found {len(exported_filenames)} files in staging directory")

        self._remove_skipped_image_instances(instance, exported_filenames)

        publish_dir = self._get_publish_directory_from_representations(instance)
        self.log.info(f"Files will be integrated to: {publish_dir}")

        instance.data["stagingDir"] = staging_dir
        instance.data["publishDir"] = publish_dir

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
        self._remove_skipped_image_instances(instance, exported_filenames)
        #((RDO-NEW)rdo-modification-end

    # [RDO Modification] Shared by both export paths - see allowSkippedMaps note
    def _remove_skipped_image_instances(self, instance, exported_filenames):
        """Drop image instances whose representation file wasn't actually written.

        allowSkippedMaps may cause a channel's file to be skipped entirely,
        in both the normal-export and pre-exported paths (both ultimately
        rely on Substance Painter's export_project_textures() output).

        Args:
            instance: The pyblish instance (textureSet)
            exported_filenames (set[str]): Basenames of files that were
                actually written to the staging directory.
        """
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

    # [RDO Modification] PIPE-612: Dynamic publish directory determination
    def _get_publish_directory_from_representations(self, instance):
        """Get the publish directory from image instance representations.

        Dynamically determines the publish path from the actual AYON structure
        defined in the image instances, without hardcoding paths.

        Structure follows: P:\\Bollywoof\\assets\\character\\chartest\\publish\\image\\
                          textureMain.T_chartest_skin.rgb\\v003\\

        Args:
            instance: The pyblish instance (textureSet)

        Returns:
            str: Path to final publish directory (parent directory for all image outputs)
        """
        self.log.debug("Determining publish directory from representations")

        image_instances = list(instance)
        if not image_instances:
            raise KnownPublishError("No image instances found in textureSet")

        first_image = image_instances[0]
        representations = first_image.data.get("representations", [])
        if not representations:
            raise KnownPublishError(
                f"No representations found for image instance: {first_image.name}"
            )

        # publishDir is set on the instance itself by ayon_core's
        # CollectResourcesPath collector (productType "image" is in its
        # whitelist) - it is never set on the representation dict.
        publish_dir = first_image.data.get("publishDir")
        if not publish_dir:
            raise KnownPublishError(
                f"No publishDir set on instance for {first_image.name}. "
                "Check AYON publish templates and anatomy configuration."
            )

        self.log.info(f"Publish directory from representation: {publish_dir}")

        # Get parent directory (the image folder, not the version folder)
        parent_dir = os.path.dirname(publish_dir)  # Remove version folder (003)
        parent_dir = os.path.dirname(parent_dir)   # Remove image-specific folder
        self.log.info(f"Parent publish directory: {parent_dir}")

        os.makedirs(parent_dir, exist_ok=True)

        return parent_dir
