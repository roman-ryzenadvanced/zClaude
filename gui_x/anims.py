#!/usr/bin/env python3
"""X Edition Animations — Hover effects, status animations, and micro-interactions.

WHY: The original GUI had ZERO visual feedback. Buttons didn't change on
hover, status changes were abrupt, and there was no way to tell if an
operation was in progress. A professional app should feel ALIVE:

  - Buttons glow when you hover over them (so you know they're clickable)
  - Status dots pulse when something is happening (so you know it's working)
  - Launch buttons show a spinner during startup (so you know it's launching)
  - Smooth transitions instead of instant color changes

Warp does all of this. When you hover a button, it subtly brightens.
When something loads, you see a spinner. When an error occurs, it flashes red.

These "micro-interactions" are the difference between an app that feels
professional and one that feels like a school project.
"""
import tkinter as tk
import time
from gui_x.theme import CATPPUCCIN


class PulseIndicator:
    """A pulsing status dot that animates between two colors.

    Used for "proxy starting" state — the dot gently pulses
    between yellow and dim so the user knows something is happening.

    Usage:
        pulse = PulseIndicator(my_label_widget)
        pulse.start()   # Dot starts pulsing
        pulse.stop()    # Dot stops pulsing, returns to static color
    """

    def __init__(self, widget, color_on="#F0C75E", color_off="#5C6180",
                 interval=600):
        """
        Args:
            widget:    The tk.Label widget showing the dot (text="●")
            color_on:  Color when "lit" (default: yellow)
            color_off: Color when "dim" (default: dim gray)
            interval:  Milliseconds between pulses (default: 600ms)
        """
        self._widget = widget
        self._color_on = color_on
        self._color_off = color_off
        self._interval = interval
        self._running = False
        self._lit = False
        self._after_id = None

    def start(self):
        """Start the pulsing animation."""
        self._running = True
        self._tick()

    def stop(self, final_color=None):
        """Stop the pulsing animation and set a final color."""
        self._running = False
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        if final_color:
            self._widget.configure(fg=final_color)

    def _tick(self):
        """One animation frame — toggle the color and schedule the next."""
        if not self._running:
            return
        self._lit = not self._lit
        color = self._color_on if self._lit else self._color_off
        try:
            self._widget.configure(fg=color)
        except tk.TclError:
            return  # Widget was destroyed
        self._after_id = self._widget.after(self._interval, self._tick)


class SpinnerButton:
    """A button that shows a spinning animation while an operation is in progress.

    When you click "Launch Desktop", the button text changes to "⠋ Launching..."
    with the spinner character cycling through braille patterns, giving visual
    feedback that the operation is happening.

    Usage:
        btn = tk.Button(parent, text="Launch Desktop", command=on_launch)
        spinner = SpinnerButton(btn, original_text="Launch Desktop")
        spinner.start()   # Text becomes "⠋ Launching..."
        spinner.stop()    # Text returns to "Launch Desktop"
    """

    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    SPINNER_INTERVAL = 80  # ms between frames

    def __init__(self, button, original_text="", spinning_text="Loading..."):
        """
        Args:
            button:         The tk.Button widget to animate
            original_text:  Text to show when not spinning
            spinning_text:  Text suffix while spinning (e.g., "Launching...")
        """
        self._button = button
        self._original_text = original_text or button.cget("text")
        self._spinning_text = spinning_text
        self._frame = 0
        self._running = False
        self._after_id = None

    def start(self):
        """Start the spinning animation."""
        self._running = True
        self._button.configure(state="disabled")
        self._tick()

    def stop(self):
        """Stop the spinning animation and restore original text."""
        self._running = False
        if self._after_id:
            self._button.after_cancel(self._after_id)
            self._after_id = None
        try:
            self._button.configure(text=self._original_text, state="normal")
        except tk.TclError:
            pass

    def _tick(self):
        """One animation frame — update the spinner character."""
        if not self._running:
            return
        frame = self.SPINNER_FRAMES[self._frame % len(self.SPINNER_FRAMES)]
        try:
            self._button.configure(text=f"{frame} {self._spinning_text}")
        except tk.TclError:
            return
        self._frame += 1
        self._after_id = self._button.after(self.SPINNER_INTERVAL, self._tick)


def add_hover_effect(widget, hover_bg=None, hover_fg=None, normal_bg=None, normal_fg=None):
    """Add a simple hover highlight to any widget.

    This is a convenience function that adds <Enter>/<Leave> bindings
    to change the widget's colors on hover.

    Args:
        widget:    The widget to add hover to
        hover_bg:  Background color on hover (default: surface1)
        hover_fg:  Foreground color on hover (default: text)
        normal_bg: Background color normally (default: widget's current bg)
        normal_fg: Foreground color normally (default: widget's current fg)
    """
    C = CATPPUCCIN
    if normal_bg is None:
        try:
            normal_bg = widget.cget("bg")
        except Exception:
            normal_bg = C["base"]
    if normal_fg is None:
        try:
            normal_fg = widget.cget("fg")
        except Exception:
            normal_fg = C["text"]
    if hover_bg is None:
        hover_bg = C["surface1"]
    if hover_fg is None:
        hover_fg = C["text"]

    def on_enter(e):
        try:
            widget.configure(bg=hover_bg, fg=hover_fg)
        except tk.TclError:
            pass

    def on_leave(e):
        try:
            widget.configure(bg=normal_bg, fg=normal_fg)
        except tk.TclError:
            pass

    widget.bind("<Enter>", on_enter)
    widget.bind("<Leave>", on_leave)
