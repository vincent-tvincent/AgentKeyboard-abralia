# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Priority and overlay composition after physical-key resolution."""

from __future__ import annotations

from .scene import ResolvedScene, ResolvedVisual


class PriorityOverlayComposer:
    """Resolve collisions using explicit numeric priority and call order."""

    def compose(self, scenes: list[ResolvedScene]) -> ResolvedScene:
        winners: dict[str, tuple[int, int, ResolvedVisual]] = {}
        for order, scene in enumerate(scenes):
            for visual in scene.visuals:
                candidate = (visual.priority, order, visual)
                if (
                    visual.element_id not in winners
                    or candidate[:2] >= winners[visual.element_id][:2]
                ):
                    winners[visual.element_id] = candidate
        return ResolvedScene(
            scene_id="composed",
            visuals=tuple(winners[key][2] for key in sorted(winners)),
        )
