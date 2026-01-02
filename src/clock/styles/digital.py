# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""Digital clock style implementations."""

from datetime import datetime
from typing import List

from .base import BaseClockStyle, ClockRenderContext, RenderedElement


class Digital24HStyle(BaseClockStyle):
    """24-hour digital clock (HH:MM)."""

    name = "digital_24h"
    display_name = "Digital 24-Hour"
    supports_seconds = False

    # Larger sizes with thin fonts
    SIZE_PRESETS = {
        'small': {'time_scale': 0.12, 'date_scale': 0.04},
        'medium': {'time_scale': 0.18, 'date_scale': 0.05},
        'large': {'time_scale': 0.25, 'date_scale': 0.06},
    }

    def render(self, ctx: ClockRenderContext) -> List[RenderedElement]:
        elements = []

        # Time with elegant thin font
        time_str = ctx.now.strftime("%H:%M")
        time_font_size = self.get_scaled_size(ctx, 'time_scale')
        time_font = self.get_digital_font(time_font_size, for_time=True)
        time_surface = self._render_text(time_str, time_font)

        # Calculate vertical positioning
        total_height = time_surface.get_height()
        date_surface = None
        if ctx.show_date:
            date_str = ctx.now.strftime("%A, %B %d")
            date_font_size = self.get_scaled_size(ctx, 'date_scale')
            date_font = self.get_digital_font(date_font_size, for_time=False)
            date_surface = self._render_text(date_str, date_font, color=(200, 200, 200))
            total_height += date_surface.get_height() + 20  # 20px gap

        # Center vertically
        start_y = (ctx.screen_height - total_height) // 2 + ctx.offset_y

        # Add time element
        elements.append(RenderedElement(
            surface=time_surface,
            x=self._center_x(time_surface, ctx),
            y=start_y
        ))

        # Add date element if enabled
        if date_surface:
            elements.append(RenderedElement(
                surface=date_surface,
                x=self._center_x(date_surface, ctx),
                y=start_y + time_surface.get_height() + 20
            ))

        return elements


class Digital12HStyle(BaseClockStyle):
    """12-hour digital clock with AM/PM."""

    name = "digital_12h"
    display_name = "Digital 12-Hour"
    supports_seconds = False

    # Larger sizes with thin fonts
    SIZE_PRESETS = {
        'small': {'time_scale': 0.12, 'date_scale': 0.04},
        'medium': {'time_scale': 0.18, 'date_scale': 0.05},
        'large': {'time_scale': 0.25, 'date_scale': 0.06},
    }

    def render(self, ctx: ClockRenderContext) -> List[RenderedElement]:
        elements = []

        # Time with AM/PM
        time_str = ctx.now.strftime("%I:%M")
        if time_str.startswith('0'):
            time_str = time_str[1:]  # Remove leading zero
        ampm = ctx.now.strftime("%p")

        time_font_size = self.get_scaled_size(ctx, 'time_scale')
        time_font = self.get_digital_font(time_font_size, for_time=True)
        time_surface = self._render_text(time_str, time_font)

        # AM/PM in smaller font
        ampm_font_size = int(time_font_size * 0.4)
        ampm_font = self.get_digital_font(ampm_font_size, for_time=False)
        ampm_surface = self._render_text(ampm, ampm_font, color=(180, 180, 180))

        # Calculate vertical positioning
        total_height = time_surface.get_height()
        date_surface = None
        if ctx.show_date:
            date_str = ctx.now.strftime("%A, %B %d")
            date_font_size = self.get_scaled_size(ctx, 'date_scale')
            date_font = self.get_digital_font(date_font_size, for_time=False)
            date_surface = self._render_text(date_str, date_font, color=(200, 200, 200))
            total_height += date_surface.get_height() + 20

        start_y = (ctx.screen_height - total_height) // 2 + ctx.offset_y

        # Position time and AM/PM together
        combined_width = time_surface.get_width() + 10 + ampm_surface.get_width()
        time_x = (ctx.screen_width - combined_width) // 2 + ctx.offset_x

        elements.append(RenderedElement(
            surface=time_surface,
            x=time_x,
            y=start_y
        ))

        # AM/PM aligned to bottom of time text
        ampm_y = start_y + time_surface.get_height() - ampm_surface.get_height() - 5
        elements.append(RenderedElement(
            surface=ampm_surface,
            x=time_x + time_surface.get_width() + 10,
            y=ampm_y
        ))

        if date_surface:
            elements.append(RenderedElement(
                surface=date_surface,
                x=self._center_x(date_surface, ctx),
                y=start_y + time_surface.get_height() + 20
            ))

        return elements


class DigitalSecondsStyle(BaseClockStyle):
    """24-hour digital clock with seconds (HH:MM:SS)."""

    name = "digital_seconds"
    display_name = "Digital with Seconds"
    supports_seconds = True

    # Slightly smaller to fit HH:MM:SS, but still larger than before
    SIZE_PRESETS = {
        'small': {'time_scale': 0.10, 'date_scale': 0.035},
        'medium': {'time_scale': 0.15, 'date_scale': 0.045},
        'large': {'time_scale': 0.20, 'date_scale': 0.055},
    }

    def render(self, ctx: ClockRenderContext) -> List[RenderedElement]:
        elements = []

        # Time with seconds - thin font
        time_str = ctx.now.strftime("%H:%M:%S")
        time_font_size = self.get_scaled_size(ctx, 'time_scale')
        time_font = self.get_digital_font(time_font_size, for_time=True)
        time_surface = self._render_text(time_str, time_font)

        # Calculate vertical positioning
        total_height = time_surface.get_height()
        date_surface = None
        if ctx.show_date:
            date_str = ctx.now.strftime("%A, %B %d")
            date_font_size = self.get_scaled_size(ctx, 'date_scale')
            date_font = self.get_digital_font(date_font_size, for_time=False)
            date_surface = self._render_text(date_str, date_font, color=(200, 200, 200))
            total_height += date_surface.get_height() + 20

        start_y = (ctx.screen_height - total_height) // 2 + ctx.offset_y

        elements.append(RenderedElement(
            surface=time_surface,
            x=self._center_x(time_surface, ctx),
            y=start_y
        ))

        if date_surface:
            elements.append(RenderedElement(
                surface=date_surface,
                x=self._center_x(date_surface, ctx),
                y=start_y + time_surface.get_height() + 20
            ))

        return elements


class DigitalLargeStyle(BaseClockStyle):
    """Oversized digital clock for high visibility."""

    name = "digital_large"
    display_name = "Digital Large"
    supports_seconds = False

    # Extra large sizes with thin elegant font
    SIZE_PRESETS = {
        'small': {'time_scale': 0.20, 'date_scale': 0.05},
        'medium': {'time_scale': 0.28, 'date_scale': 0.065},
        'large': {'time_scale': 0.38, 'date_scale': 0.08},
    }

    def render(self, ctx: ClockRenderContext) -> List[RenderedElement]:
        elements = []

        # Large time with elegant thin font
        time_str = ctx.now.strftime("%H:%M")
        time_font_size = self.get_scaled_size(ctx, 'time_scale')
        time_font = self.get_digital_font(time_font_size, for_time=True)
        time_surface = self._render_text(time_str, time_font)

        # Calculate vertical positioning
        total_height = time_surface.get_height()
        date_surface = None
        if ctx.show_date:
            date_str = ctx.now.strftime("%A, %B %d")
            date_font_size = self.get_scaled_size(ctx, 'date_scale')
            date_font = self.get_digital_font(date_font_size, for_time=False)
            date_surface = self._render_text(date_str, date_font, color=(200, 200, 200))
            total_height += date_surface.get_height() + 30  # Larger gap

        start_y = (ctx.screen_height - total_height) // 2 + ctx.offset_y

        elements.append(RenderedElement(
            surface=time_surface,
            x=self._center_x(time_surface, ctx),
            y=start_y
        ))

        if date_surface:
            elements.append(RenderedElement(
                surface=date_surface,
                x=self._center_x(date_surface, ctx),
                y=start_y + time_surface.get_height() + 30
            ))

        return elements
