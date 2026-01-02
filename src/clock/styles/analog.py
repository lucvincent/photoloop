# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""Analog clock style implementations."""

import math
from typing import List, Tuple

import pygame

from .base import BaseClockStyle, ClockRenderContext, RenderedElement


class AnalogClassicStyle(BaseClockStyle):
    """Classic analog clock with numbers and tick marks."""

    name = "analog_classic"
    display_name = "Analog Classic"
    supports_seconds = True

    # Size as percentage of screen height
    SIZE_PRESETS = {
        'small': {'clock_scale': 0.25, 'date_scale': 0.03},
        'medium': {'clock_scale': 0.35, 'date_scale': 0.04},
        'large': {'clock_scale': 0.45, 'date_scale': 0.05},
    }

    def get_clock_size(self, ctx: ClockRenderContext) -> int:
        """Get clock diameter in pixels."""
        preset = self.SIZE_PRESETS.get(ctx.size, self.SIZE_PRESETS['medium'])
        return int(ctx.screen_height * preset['clock_scale'])

    def render(self, ctx: ClockRenderContext) -> List[RenderedElement]:
        elements = []

        clock_size = self.get_clock_size(ctx)
        radius = clock_size // 2

        # Create clock surface with alpha
        clock_surface = pygame.Surface((clock_size, clock_size), pygame.SRCALPHA)

        # Draw clock face (circle outline)
        center = (radius, radius)
        pygame.draw.circle(clock_surface, (80, 80, 80), center, radius, 3)

        # Draw hour numbers
        num_font_size = int(radius * 0.25)
        num_font = self.get_font(num_font_size, bold=True)
        for hour in range(1, 13):
            angle = math.radians(90 - hour * 30)  # 30 degrees per hour
            num_radius = radius - int(radius * 0.18)
            x = center[0] + int(num_radius * math.cos(angle))
            y = center[1] - int(num_radius * math.sin(angle))
            num_surface = num_font.render(str(hour), True, (200, 200, 200))
            # Center the number on the position
            num_x = x - num_surface.get_width() // 2
            num_y = y - num_surface.get_height() // 2
            clock_surface.blit(num_surface, (num_x, num_y))

        # Draw tick marks for minutes
        for minute in range(60):
            if minute % 5 != 0:  # Skip hour positions
                angle = math.radians(90 - minute * 6)
                outer_r = radius - 4
                inner_r = radius - 10
                x1 = center[0] + int(outer_r * math.cos(angle))
                y1 = center[1] - int(outer_r * math.sin(angle))
                x2 = center[0] + int(inner_r * math.cos(angle))
                y2 = center[1] - int(inner_r * math.sin(angle))
                pygame.draw.line(clock_surface, (100, 100, 100), (x1, y1), (x2, y2), 1)

        # Draw hands
        self._draw_hands(clock_surface, center, radius, ctx.now)

        # Calculate total height including date
        total_height = clock_size
        date_surface = None
        if ctx.show_date:
            date_str = ctx.now.strftime("%A, %B %d")
            date_font_size = self.get_scaled_size(ctx, 'date_scale')
            date_font = self.get_font(date_font_size)
            date_surface = self._render_text(date_str, date_font, color=(200, 200, 200))
            total_height += 25 + date_surface.get_height()

        start_y = (ctx.screen_height - total_height) // 2 + ctx.offset_y
        clock_x = (ctx.screen_width - clock_size) // 2 + ctx.offset_x

        elements.append(RenderedElement(
            surface=clock_surface,
            x=clock_x,
            y=start_y
        ))

        if date_surface:
            elements.append(RenderedElement(
                surface=date_surface,
                x=self._center_x(date_surface, ctx),
                y=start_y + clock_size + 25
            ))

        return elements

    def _draw_hands(
        self,
        surface: pygame.Surface,
        center: Tuple[int, int],
        radius: int,
        now
    ) -> None:
        """Draw hour, minute, and second hands."""
        # Hour hand
        hour = now.hour % 12 + now.minute / 60
        hour_angle = math.radians(90 - hour * 30)
        hour_length = radius * 0.5
        hour_end = (
            center[0] + int(hour_length * math.cos(hour_angle)),
            center[1] - int(hour_length * math.sin(hour_angle))
        )
        pygame.draw.line(surface, (255, 255, 255), center, hour_end, 6)

        # Minute hand
        minute = now.minute + now.second / 60
        minute_angle = math.radians(90 - minute * 6)
        minute_length = radius * 0.7
        minute_end = (
            center[0] + int(minute_length * math.cos(minute_angle)),
            center[1] - int(minute_length * math.sin(minute_angle))
        )
        pygame.draw.line(surface, (255, 255, 255), center, minute_end, 4)

        # Second hand (smooth)
        second = now.second + now.microsecond / 1000000
        second_angle = math.radians(90 - second * 6)
        second_length = radius * 0.75
        second_end = (
            center[0] + int(second_length * math.cos(second_angle)),
            center[1] - int(second_length * math.sin(second_angle))
        )
        pygame.draw.line(surface, (200, 50, 50), center, second_end, 2)

        # Center dot
        pygame.draw.circle(surface, (255, 255, 255), center, 6)


class AnalogModernStyle(BaseClockStyle):
    """Modern minimal analog clock without numbers."""

    name = "analog_modern"
    display_name = "Analog Modern"
    supports_seconds = True

    SIZE_PRESETS = {
        'small': {'clock_scale': 0.25, 'date_scale': 0.03},
        'medium': {'clock_scale': 0.35, 'date_scale': 0.04},
        'large': {'clock_scale': 0.45, 'date_scale': 0.05},
    }

    def get_clock_size(self, ctx: ClockRenderContext) -> int:
        """Get clock diameter in pixels."""
        preset = self.SIZE_PRESETS.get(ctx.size, self.SIZE_PRESETS['medium'])
        return int(ctx.screen_height * preset['clock_scale'])

    def render(self, ctx: ClockRenderContext) -> List[RenderedElement]:
        elements = []

        clock_size = self.get_clock_size(ctx)
        radius = clock_size // 2

        # Create clock surface with alpha
        clock_surface = pygame.Surface((clock_size, clock_size), pygame.SRCALPHA)

        center = (radius, radius)

        # Draw minimal tick marks (only at 12, 3, 6, 9)
        for hour in [12, 3, 6, 9]:
            angle = math.radians(90 - hour * 30)
            outer_r = radius - 4
            inner_r = radius - 20
            x1 = center[0] + int(outer_r * math.cos(angle))
            y1 = center[1] - int(outer_r * math.sin(angle))
            x2 = center[0] + int(inner_r * math.cos(angle))
            y2 = center[1] - int(inner_r * math.sin(angle))
            pygame.draw.line(clock_surface, (150, 150, 150), (x1, y1), (x2, y2), 3)

        # Draw hands
        self._draw_hands(clock_surface, center, radius, ctx.now)

        # Calculate total height including date
        total_height = clock_size
        date_surface = None
        if ctx.show_date:
            date_str = ctx.now.strftime("%B %d")
            date_font_size = self.get_scaled_size(ctx, 'date_scale')
            date_font = self.get_font(date_font_size)
            date_surface = self._render_text(date_str, date_font, color=(180, 180, 180))
            total_height += 25 + date_surface.get_height()

        start_y = (ctx.screen_height - total_height) // 2 + ctx.offset_y
        clock_x = (ctx.screen_width - clock_size) // 2 + ctx.offset_x

        elements.append(RenderedElement(
            surface=clock_surface,
            x=clock_x,
            y=start_y
        ))

        if date_surface:
            elements.append(RenderedElement(
                surface=date_surface,
                x=self._center_x(date_surface, ctx),
                y=start_y + clock_size + 25
            ))

        return elements

    def _draw_hands(
        self,
        surface: pygame.Surface,
        center: Tuple[int, int],
        radius: int,
        now
    ) -> None:
        """Draw hour, minute, and second hands with modern style."""
        # Hour hand (thick, rounded ends)
        hour = now.hour % 12 + now.minute / 60
        hour_angle = math.radians(90 - hour * 30)
        hour_length = radius * 0.45
        hour_end = (
            center[0] + int(hour_length * math.cos(hour_angle)),
            center[1] - int(hour_length * math.sin(hour_angle))
        )
        pygame.draw.line(surface, (220, 220, 220), center, hour_end, 8)

        # Minute hand (thinner)
        minute = now.minute + now.second / 60
        minute_angle = math.radians(90 - minute * 6)
        minute_length = radius * 0.65
        minute_end = (
            center[0] + int(minute_length * math.cos(minute_angle)),
            center[1] - int(minute_length * math.sin(minute_angle))
        )
        pygame.draw.line(surface, (220, 220, 220), center, minute_end, 4)

        # Second hand (thin, accent color)
        second = now.second + now.microsecond / 1000000
        second_angle = math.radians(90 - second * 6)
        second_length = radius * 0.70
        second_end = (
            center[0] + int(second_length * math.cos(second_angle)),
            center[1] - int(second_length * math.sin(second_angle))
        )
        # Counter-balance on opposite side
        counter_length = radius * 0.15
        counter_end = (
            center[0] - int(counter_length * math.cos(second_angle)),
            center[1] + int(counter_length * math.sin(second_angle))
        )
        pygame.draw.line(surface, (220, 100, 80), counter_end, second_end, 2)

        # Center dot
        pygame.draw.circle(surface, (220, 100, 80), center, 5)
