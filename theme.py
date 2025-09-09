"""
Warehouse Management System - Ultra Dark Theme Module
Professional dark theme optimized for eye comfort
"""

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageDraw
import os
import logging
from typing import Dict, Optional


class UltraDarkTheme:
    """Ultra dark theme configuration for maximum eye comfort."""

    # Ultra dark color scheme - optimized for eye strain reduction
    COLORS = {
        'bg_primary': '#0a0a0a',  # Almost black background
        'bg_secondary': '#141414',  # Slightly lighter for panels
        'bg_tertiary': '#1a1a1a',  # Input fields background
        'bg_accent': '#242424',  # Accent/hover states
        'bg_selected': '#1e3a5f',  # Selected items (dark blue)
        'bg_button': '#1f1f1f',  # Button background
        'bg_button_hover': '#2a2a2a',  # Button hover
        'bg_button_active': '#1e3a5f',  # Active button (dark blue)

        'fg_primary': '#b8b8b8',  # Primary text (light gray)
        'fg_secondary': '#808080',  # Secondary text (medium gray)
        'fg_disabled': '#404040',  # Disabled text (dark gray)
        'fg_accent': '#4a7c9e',  # Accent text (muted blue)
        'fg_error': '#c95555',  # Error text (muted red)
        'fg_success': '#55a855',  # Success text (muted green)
        'fg_warning': '#c9a955',  # Warning text (muted yellow)

        'border': '#2a2a2a',  # Border color
        'border_focus': '#3a5a7a',  # Focused border (dark blue)
        'shadow': '#000000',  # Pure black shadow
    }

    # Comfortable fonts
    FONTS = {
        'default': ('Segoe UI', 10),
        'heading': ('Segoe UI Semibold', 12),
        'subheading': ('Segoe UI', 11),
        'small': ('Segoe UI', 9),
        'button': ('Segoe UI', 10),
        'monospace': ('Consolas', 10),
    }

    # Consistent dimensions
    DIMENSIONS = {
        'padding': 10,
        'small_padding': 5,
        'large_padding': 20,
        'button_height': 35,
        'entry_height': 32,
        'thumbnail_size': 64,
        'card_width': 220,
        'card_height': 260,
        'min_window_width': 1200,
        'min_window_height': 700,
    }


class ThemeManager:
    """Manages ultra dark theme application and styling utilities."""

    def __init__(self):
        self.theme = UltraDarkTheme()
        self.logger = logging.getLogger(__name__)
        self._thumbnail_cache = {}
        self._widget_refs = []  # Keep track of widgets
        self._setup_ttk_styles()

    def _setup_ttk_styles(self):
        """Configure ttk styles for ultra dark theme."""
        try:
            self.style = ttk.Style()

            # Configure root style
            self.style.theme_use('clam')  # Base theme that works well with customization

            # Frame styles
            self.style.configure('Dark.TFrame',
                                 background=self.theme.COLORS['bg_primary'],
                                 borderwidth=0,
                                 relief='flat')

            self.style.configure('Card.TFrame',
                                 background=self.theme.COLORS['bg_secondary'],
                                 relief='raised',
                                 borderwidth=1)

            # Label styles
            self.style.configure('Dark.TLabel',
                                 background=self.theme.COLORS['bg_primary'],
                                 foreground=self.theme.COLORS['fg_primary'],
                                 font=self.theme.FONTS['default'])

            self.style.configure('Heading.TLabel',
                                 background=self.theme.COLORS['bg_primary'],
                                 foreground=self.theme.COLORS['fg_primary'],
                                 font=self.theme.FONTS['heading'])

            self.style.configure('Subheading.TLabel',
                                 background=self.theme.COLORS['bg_primary'],
                                 foreground=self.theme.COLORS['fg_primary'],
                                 font=self.theme.FONTS['subheading'])

            self.style.configure('Secondary.TLabel',
                                 background=self.theme.COLORS['bg_primary'],
                                 foreground=self.theme.COLORS['fg_secondary'],
                                 font=self.theme.FONTS['default'])

            self.style.configure('Small.TLabel',
                                 background=self.theme.COLORS['bg_primary'],
                                 foreground=self.theme.COLORS['fg_secondary'],
                                 font=self.theme.FONTS['small'])

            # Entry styles
            self.style.configure('Dark.TEntry',
                                 fieldbackground=self.theme.COLORS['bg_tertiary'],
                                 background=self.theme.COLORS['bg_tertiary'],
                                 foreground=self.theme.COLORS['fg_primary'],
                                 insertcolor=self.theme.COLORS['fg_primary'],
                                 bordercolor=self.theme.COLORS['border'],
                                 lightcolor=self.theme.COLORS['border'],
                                 darkcolor=self.theme.COLORS['border'],
                                 font=self.theme.FONTS['default'])

            self.style.map('Dark.TEntry',
                           fieldbackground=[('focus', self.theme.COLORS['bg_tertiary'])],
                           bordercolor=[('focus', self.theme.COLORS['border_focus'])])

            # Button styles
            self.style.configure('Dark.TButton',
                                 background=self.theme.COLORS['bg_button'],
                                 foreground=self.theme.COLORS['fg_primary'],
                                 bordercolor=self.theme.COLORS['border'],
                                 lightcolor=self.theme.COLORS['bg_button'],
                                 darkcolor=self.theme.COLORS['bg_button'],
                                 font=self.theme.FONTS['button'],
                                 focuscolor='none',
                                 relief='flat',
                                 borderwidth=1)

            self.style.map('Dark.TButton',
                           background=[('active', self.theme.COLORS['bg_button_hover']),
                                       ('pressed', self.theme.COLORS['bg_button_active'])],
                           foreground=[('active', self.theme.COLORS['fg_primary'])],
                           bordercolor=[('active', self.theme.COLORS['border_focus'])])

            # Primary button style
            self.style.configure('Primary.TButton',
                                 background=self.theme.COLORS['bg_button_active'],
                                 foreground=self.theme.COLORS['fg_primary'],
                                 bordercolor=self.theme.COLORS['border_focus'],
                                 lightcolor=self.theme.COLORS['bg_button_active'],
                                 darkcolor=self.theme.COLORS['bg_button_active'],
                                 font=self.theme.FONTS['button'],
                                 focuscolor='none',
                                 relief='flat',
                                 borderwidth=1)

            self.style.map('Primary.TButton',
                           background=[('active', self.theme.COLORS['bg_selected'])],
                           foreground=[('active', self.theme.COLORS['fg_primary'])])

            # Combobox styles
            self.style.configure('Dark.TCombobox',
                                 fieldbackground=self.theme.COLORS['bg_tertiary'],
                                 background=self.theme.COLORS['bg_button'],
                                 foreground=self.theme.COLORS['fg_primary'],
                                 selectbackground=self.theme.COLORS['bg_selected'],
                                 selectforeground=self.theme.COLORS['fg_primary'],
                                 bordercolor=self.theme.COLORS['border'],
                                 arrowcolor=self.theme.COLORS['fg_secondary'],
                                 font=self.theme.FONTS['default'])

            # Notebook styles
            self.style.configure('Dark.TNotebook',
                                 background=self.theme.COLORS['bg_primary'],
                                 borderwidth=0,
                                 tabmargins=[0, 0, 0, 0])

            self.style.configure('Dark.TNotebook.Tab',
                                 background=self.theme.COLORS['bg_secondary'],
                                 foreground=self.theme.COLORS['fg_secondary'],
                                 padding=[20, 10],
                                 font=self.theme.FONTS['default'],
                                 borderwidth=0)

            self.style.map('Dark.TNotebook.Tab',
                           background=[('selected', self.theme.COLORS['bg_button_active'])],
                           foreground=[('selected', self.theme.COLORS['fg_primary']),
                                       ('active', self.theme.COLORS['fg_primary'])],
                           expand=[('selected', [0, 0, 0, 2])])

            # Treeview styles
            self.style.configure('Dark.Treeview',
                                 background=self.theme.COLORS['bg_secondary'],
                                 foreground=self.theme.COLORS['fg_primary'],
                                 fieldbackground=self.theme.COLORS['bg_secondary'],
                                 borderwidth=0,
                                 font=self.theme.FONTS['default'])

            self.style.configure('Dark.Treeview.Heading',
                                 background=self.theme.COLORS['bg_accent'],
                                 foreground=self.theme.COLORS['fg_primary'],
                                 borderwidth=0,
                                 font=self.theme.FONTS['subheading'])

            self.style.map('Dark.Treeview',
                           background=[('selected', self.theme.COLORS['bg_selected'])],
                           foreground=[('selected', self.theme.COLORS['fg_primary'])])

            # Scrollbar styles
            self.style.configure('Dark.Vertical.TScrollbar',
                                 background=self.theme.COLORS['bg_secondary'],
                                 darkcolor=self.theme.COLORS['bg_primary'],
                                 lightcolor=self.theme.COLORS['bg_accent'],
                                 troughcolor=self.theme.COLORS['bg_primary'],
                                 bordercolor=self.theme.COLORS['bg_primary'],
                                 arrowcolor=self.theme.COLORS['fg_secondary'],
                                 width=12)

            self.style.configure('Dark.Horizontal.TScrollbar',
                                 background=self.theme.COLORS['bg_secondary'],
                                 darkcolor=self.theme.COLORS['bg_primary'],
                                 lightcolor=self.theme.COLORS['bg_accent'],
                                 troughcolor=self.theme.COLORS['bg_primary'],
                                 bordercolor=self.theme.COLORS['bg_primary'],
                                 arrowcolor=self.theme.COLORS['fg_secondary'],
                                 height=12)

            # Separator styles
            self.style.configure('Dark.TSeparator',
                                 background=self.theme.COLORS['border'])

            # Radiobutton styles
            self.style.configure('Dark.TRadiobutton',
                                 background=self.theme.COLORS['bg_primary'],
                                 foreground=self.theme.COLORS['fg_primary'],
                                 font=self.theme.FONTS['default'])

            self.style.map('Dark.TRadiobutton',
                           background=[('active', self.theme.COLORS['bg_primary'])],
                           foreground=[('active', self.theme.COLORS['fg_accent'])])

            # Checkbutton styles
            self.style.configure('Dark.TCheckbutton',
                                 background=self.theme.COLORS['bg_primary'],
                                 foreground=self.theme.COLORS['fg_primary'],
                                 font=self.theme.FONTS['default'])

            # Progressbar styles
            self.style.configure('Dark.TProgressbar',
                                 background=self.theme.COLORS['bg_selected'],
                                 troughcolor=self.theme.COLORS['bg_tertiary'],
                                 bordercolor=self.theme.COLORS['border'],
                                 lightcolor=self.theme.COLORS['bg_selected'],
                                 darkcolor=self.theme.COLORS['bg_selected'])

        except Exception as e:
            self.logger.error(f"Failed to setup TTK styles: {e}")

    def apply_to_window(self, window: tk.Tk):
        """Apply ultra dark theme to main window."""
        window.configure(bg=self.theme.COLORS['bg_primary'])
        window.option_add('*Font', self.theme.FONTS['default'])
        window.option_add('*Background', self.theme.COLORS['bg_primary'])
        window.option_add('*Foreground', self.theme.COLORS['fg_primary'])
        window.option_add('*selectBackground', self.theme.COLORS['bg_selected'])
        window.option_add('*selectForeground', self.theme.COLORS['fg_primary'])

        # Set minimum size
        window.minsize(self.theme.DIMENSIONS['min_window_width'],
                       self.theme.DIMENSIONS['min_window_height'])

    def create_styled_frame(self, parent, style='Dark.TFrame', **kwargs) -> ttk.Frame:
        """Create a styled frame with consistent theming."""
        frame = ttk.Frame(parent, style=style, **kwargs)
        self._widget_refs.append(frame)
        return frame

    def create_styled_label(self, parent, text="", style='Dark.TLabel', **kwargs) -> ttk.Label:
        """Create a styled label with consistent theming."""
        label = ttk.Label(parent, text=text, style=style, **kwargs)
        self._widget_refs.append(label)
        return label

    def create_styled_button(self, parent, text="", command=None, style='Dark.TButton', **kwargs) -> ttk.Button:
        """Create a styled button with consistent theming."""
        btn = ttk.Button(parent, text=text, command=command, style=style, **kwargs)
        self._widget_refs.append(btn)
        return btn

    def create_primary_button(self, parent, text="", command=None, **kwargs) -> ttk.Button:
        """Create a primary styled button."""
        return self.create_styled_button(parent, text, command, 'Primary.TButton', **kwargs)

    def create_styled_entry(self, parent, style='Dark.TEntry', **kwargs) -> ttk.Entry:
        """Create a styled entry with consistent theming."""
        entry = ttk.Entry(parent, style=style, **kwargs)
        self._widget_refs.append(entry)
        return entry

    def create_card_frame(self, parent, **kwargs) -> ttk.Frame:
        """Create a card-style frame for product display."""
        frame = ttk.Frame(parent, style='Card.TFrame', **kwargs)
        frame.configure(width=self.theme.DIMENSIONS['card_width'],
                        height=self.theme.DIMENSIONS['card_height'])
        self._widget_refs.append(frame)
        return frame

    def create_search_frame(self, parent, search_callback=None) -> tuple:
        """Create a standardized search frame."""
        search_frame = self.create_styled_frame(parent)

        # Search label
        search_label = self.create_styled_label(search_frame, "Search:")
        search_label.grid(row=0, column=0, padx=5, pady=5, sticky='w')

        # Search entry
        search_var = tk.StringVar()
        search_entry = self.create_styled_entry(search_frame, textvariable=search_var, width=30)
        search_entry.grid(row=0, column=1, padx=5, pady=5, sticky='ew')

        # Search button
        search_btn = self.create_styled_button(search_frame, "Search",
                                               command=lambda: search_callback(
                                                   search_var.get()) if search_callback else None)
        search_btn.grid(row=0, column=2, padx=5, pady=5)

        # Clear button
        clear_btn = self.create_styled_button(search_frame, "Clear",
                                              command=lambda: (search_var.set(""),
                                                               search_callback("") if search_callback else None))
        clear_btn.grid(row=0, column=3, padx=5, pady=5)

        search_frame.columnconfigure(1, weight=1)

        return search_frame, search_var, search_entry

    def make_thumbnail(self, image_path: str, size: int = None) -> Optional[ImageTk.PhotoImage]:
        """Create thumbnail from image path with caching and error handling."""
        if size is None:
            size = self.theme.DIMENSIONS['thumbnail_size']

        # Handle None or empty path
        if not image_path:
            return self._get_default_thumbnail(size)

        cache_key = f"{image_path}_{size}"

        # Check cache first
        if cache_key in self._thumbnail_cache:
            return self._thumbnail_cache[cache_key]

        try:
            if not os.path.exists(image_path):
                return self._get_default_thumbnail(size)

            # Open and resize image
            with Image.open(image_path) as img:
                # Convert to RGB if necessary
                if img.mode in ('RGBA', 'LA', 'P'):
                    img = img.convert('RGB')

                # Resize maintaining aspect ratio
                img.thumbnail((size, size), Image.Resampling.LANCZOS)

                # Create new image with exact size and center the thumbnail
                final_img = Image.new('RGB', (size, size), color=self.theme.COLORS['bg_secondary'])
                paste_x = (size - img.width) // 2
                paste_y = (size - img.height) // 2
                final_img.paste(img, (paste_x, paste_y))

                # Convert to PhotoImage and cache
                photo = ImageTk.PhotoImage(final_img)
                self._thumbnail_cache[cache_key] = photo
                return photo

        except Exception as e:
            self.logger.debug(f"Could not load image {image_path}: {e}")
            return self._get_default_thumbnail(size)

    def _get_default_thumbnail(self, size: int) -> ImageTk.PhotoImage:
        """Get default thumbnail when image is not available."""
        cache_key = f"default_{size}"

        if cache_key in self._thumbnail_cache:
            return self._thumbnail_cache[cache_key]

        try:
            # Create a simple default image with dark colors
            img = Image.new('RGB', (size, size), color=self.theme.COLORS['bg_secondary'])

            # Add a simple icon
            draw = ImageDraw.Draw(img)

            # Draw a box icon
            margin = size // 4
            draw.rectangle([margin, margin, size - margin, size - margin],
                           outline=self.theme.COLORS['fg_secondary'], width=1)

            # Draw diagonal lines
            draw.line([margin + 5, margin + 5, size - margin - 5, size - margin - 5],
                      fill=self.theme.COLORS['fg_secondary'], width=1)
            draw.line([margin + 5, size - margin - 5, size - margin - 5, margin + 5],
                      fill=self.theme.COLORS['fg_secondary'], width=1)

            photo = ImageTk.PhotoImage(img)
            self._thumbnail_cache[cache_key] = photo
            return photo

        except Exception as e:
            self.logger.error(f"Failed to create default thumbnail: {e}")
            return None

    def show_message(self, parent, title: str, message: str, msg_type: str = "info"):
        """Show themed message dialog."""
        from tkinter import messagebox

        if msg_type == "error":
            messagebox.showerror(title, message, parent=parent)
        elif msg_type == "warning":
            messagebox.showwarning(title, message, parent=parent)
        elif msg_type == "question":
            return messagebox.askyesno(title, message, parent=parent)
        else:
            messagebox.showinfo(title, message, parent=parent)

    def confirm_action(self, parent, title: str, message: str) -> bool:
        """Show confirmation dialog."""
        return self.show_message(parent, title, message, "question")

    def create_tooltip(self, widget, text: str):
        """Create tooltip for widget."""

        def on_enter(event):
            tooltip = tk.Toplevel()
            tooltip.wm_overrideredirect(True)
            tooltip.configure(bg=self.theme.COLORS['bg_accent'])

            label = tk.Label(tooltip, text=text,
                             bg=self.theme.COLORS['bg_accent'],
                             fg=self.theme.COLORS['fg_primary'],
                             font=self.theme.FONTS['small'],
                             padx=8, pady=5)
            label.pack()

            # Position tooltip
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() - 35
            tooltip.geometry(f"+{x}+{y}")

            widget.tooltip = tooltip

        def on_leave(event):
            if hasattr(widget, 'tooltip'):
                try:
                    widget.tooltip.destroy()
                except:
                    pass
                del widget.tooltip

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    def setup_grid_weights(self, widget, rows: list = None, cols: list = None):
        """Setup grid weights for responsive layout."""
        if rows:
            for row in rows:
                widget.grid_rowconfigure(row, weight=1)

        if cols:
            for col in cols:
                widget.grid_columnconfigure(col, weight=1)

    def bind_mousewheel(self, widget, canvas):
        """Bind mousewheel scrolling to canvas."""

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _on_mousewheel_linux(event):
            if event.num == 4:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                canvas.yview_scroll(1, "units")

        # Windows and MacOS
        widget.bind("<MouseWheel>", _on_mousewheel)
        # Linux
        widget.bind("<Button-4>", _on_mousewheel_linux)
        widget.bind("<Button-5>", _on_mousewheel_linux)

    def create_loading_overlay(self, parent, text="Loading..."):
        """Create loading overlay."""
        overlay = tk.Toplevel(parent)
        overlay.title("")
        overlay.configure(bg=self.theme.COLORS['bg_primary'])
        overlay.resizable(False, False)
        overlay.transient(parent)
        overlay.grab_set()

        # Remove window decorations
        overlay.overrideredirect(True)

        # Create content
        frame = self.create_styled_frame(overlay)
        frame.pack(padx=30, pady=30)

        label = self.create_styled_label(frame, text, 'Heading.TLabel')
        label.pack(pady=10)

        # Progress bar
        progress = ttk.Progressbar(frame, mode='indeterminate', length=250,
                                   style='Dark.TProgressbar')
        progress.pack(pady=10)
        progress.start(10)

        # Center on parent
        parent.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - 125
        y = parent.winfo_y() + (parent.winfo_height() // 2) - 60
        overlay.geometry(f"+{x}+{y}")

        overlay.progress = progress
        return overlay

    def destroy_loading_overlay(self, overlay):
        """Destroy loading overlay safely."""
        if overlay and hasattr(overlay, 'winfo_exists'):
            try:
                if overlay.winfo_exists():
                    overlay.progress.stop()
                    overlay.destroy()
            except:
                pass

    def cleanup_widgets(self):
        """Clean up widget references to prevent memory leaks."""
        self._widget_refs = [w for w in self._widget_refs if hasattr(w, 'winfo_exists') and w.winfo_exists()]


# Global theme manager instance
theme_manager = ThemeManager()