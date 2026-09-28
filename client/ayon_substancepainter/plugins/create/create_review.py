# -*- coding: utf-8 -*-
"""Creator plugin for texture set reviews."""
from ayon_core.pipeline import CreatedInstance, Creator
from ayon_core.lib import FileDef, UILabelDef, NumberDef

from ayon_substancepainter.api.pipeline import (
    get_instances,
    set_instance,
    set_instances,
    remove_instance
)

import substance_painter.project


class CreateReview(Creator):
    """Create a texture set review.

    Spawned alongside a textureSet instance when Review is enabled.
    Holds curated viewport screenshots that get compiled into a single
    review movie on extraction.
    """
    identifier = "io.openpype.creators.substancepainter.review"
    label = "Review"
    product_type = "review"
    icon = "video-camera"

    default_variant = "Main"
    settings_category = "substancepainter"

    def create(self, product_name, instance_data, pre_create_data):
        if not substance_painter.project.is_open():
            return

        instance_data.setdefault("creator_attributes", dict())

        instance = self.create_instance_in_context(product_name,
                                                   instance_data)
        set_instance(
            instance_id=instance["instance_id"],
            instance_data=instance.data_to_store()
        )
        return instance

    def collect_instances(self):
        for instance in get_instances():
            if (instance.get("creator_identifier") == self.identifier or
                    instance.get("productType") == self.product_type):
                self.create_instance_in_context_from_existing(instance)

    def update_instances(self, update_list):
        instance_data_by_id = {}
        for instance, _changes in update_list:
            instance_id = instance.get("instance_id")
            instance_data = instance.data_to_store()
            instance_data_by_id[instance_id] = instance_data
        set_instances(instance_data_by_id, update=True)

    def remove_instances(self, instances):
        for instance in instances:
            remove_instance(instance["instance_id"])
            self._remove_instance_from_context(instance)

    def create_instance_in_context(self, product_name, data):
        instance = CreatedInstance(
            self.product_type, product_name, data, self
        )
        self.create_context.creator_adds_instance(instance)
        return instance

    def create_instance_in_context_from_existing(self, data):
        instance = CreatedInstance.from_existing(data, self)
        self.create_context.creator_adds_instance(instance)
        return instance

    def get_instance_attr_defs(self):
        return [
            UILabelDef(
                "Capture viewport screenshots using the camera button above "
                "and curate them below. At least one screenshot is required "
                "to publish."
            ),
            FileDef("screenshots",
                    label="Screenshots",
                    single_item=False,
                    folders=False,
                    extensions=[".png", ".jpg", ".jpeg"],
                    allow_sequences=False),
            NumberDef("hold_duration",
                    label="Hold duration (seconds)",
                    tooltip="How long each screenshot is held in the "
                            "compiled review movie.",
                    minimum=0.5,
                    maximum=10.0,
                    decimals=1,
                    default=1.0),
        ]

