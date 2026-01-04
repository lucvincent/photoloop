# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
"""Minimal/modern clock style implementations."""

from typing import List

from .base import BaseClockStyle, ClockRenderContext, RenderedElement


class MinimalTimeStyle(BaseClockStyle):
    """Minimal time-only display with thin font."""

    name = "minimal_time"
    display_name = "Minimal Time"
    supports_seconds = False

    # Larger to compensate for thin appearance
    SIZE_PRESETS = {
        'small': {'time_scale': 0.10, 'date_scale': 0.03},
        'medium': {'time_scale': 0.14, 'date_scale': 0.04},
        'large': {'time_scale': 0.20, 'date_scale': 0.05},
    }

    def render(self, ctx: ClockRenderContext) -> List[RenderedElement]:
        elements = []

        # Time only (no seconds, thin font)
        time_str = ctx.now.strftime("%H:%M")
        time_font_size = self.get_scaled_size(ctx, 'time_scale')
        # Use non-bold for thin appearance
        time_font = self.get_font(time_font_size, bold=False)
        time_surface = self._render_text(time_str, time_font, color=(230, 230, 230))

        # Center on screen
        elements.append(RenderedElement(
            surface=time_surface,
            x=self._center_x(time_surface, ctx),
            y=(ctx.screen_height - time_surface.get_height()) // 2 + ctx.offset_y
        ))

        return elements


class MinimalDateStyle(BaseClockStyle):
    """Minimal date-focused display with small time."""

    name = "minimal_date"
    display_name = "Minimal Date"
    supports_seconds = False

    SIZE_PRESETS = {
        'small': {'time_scale': 0.04, 'date_scale': 0.08},
        'medium': {'time_scale': 0.05, 'date_scale': 0.10},
        'large': {'time_scale': 0.06, 'date_scale': 0.14},
    }

    def render(self, ctx: ClockRenderContext) -> List[RenderedElement]:
        elements = []

        # Date is prominent
        date_str = ctx.now.strftime("%B %d")
        date_font_size = self.get_scaled_size(ctx, 'date_scale')
        date_font = self.get_font(date_font_size, bold=False)
        date_surface = self._render_text(date_str, date_font, color=(230, 230, 230))

        # Day of week above
        day_str = ctx.now.strftime("%A")
        day_font_size = int(date_font_size * 0.5)
        day_font = self.get_font(day_font_size)
        day_surface = self._render_text(day_str, day_font, color=(160, 160, 160))

        # Small time below
        time_str = ctx.now.strftime("%H:%M")
        time_font_size = self.get_scaled_size(ctx, 'time_scale')
        time_font = self.get_font(time_font_size)
        time_surface = self._render_text(time_str, time_font, color=(140, 140, 140))

        # Calculate total height
        total_height = (
            day_surface.get_height() + 10 +
            date_surface.get_height() + 15 +
            time_surface.get_height()
        )
        start_y = (ctx.screen_height - total_height) // 2 + ctx.offset_y

        # Day of week
        elements.append(RenderedElement(
            surface=day_surface,
            x=self._center_x(day_surface, ctx),
            y=start_y
        ))

        # Date (main element)
        elements.append(RenderedElement(
            surface=date_surface,
            x=self._center_x(date_surface, ctx),
            y=start_y + day_surface.get_height() + 10
        ))

        # Time (small, below)
        elements.append(RenderedElement(
            surface=time_surface,
            x=self._center_x(time_surface, ctx),
            y=start_y + day_surface.get_height() + 10 + date_surface.get_height() + 15
        ))

        return elements
