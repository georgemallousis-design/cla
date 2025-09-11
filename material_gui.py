"""
Warehouse Management System - Material GUI Module (Fixed)
Inventory management with proper image handling and dark theme
"""

import tkinter as tk
from tkinter import ttk, filedialog
import logging
import os
from typing import Dict, List, Optional, Set
from theme import theme_manager
from database import DatabaseManager
import threading
from datetime import datetime


class MaterialManagementFrame:
    """Main material/inventory management interface with fixed image handling."""

    def __init__(self, parent, db_manager: DatabaseManager, current_user: Dict, is_used: bool = False):
        self.parent = parent
        self.db_manager = db_manager
        self.current_user = current_user
        self.is_used = is_used  # True for Used Inventory tab
        self.logger = logging.getLogger(__name__)

        self.materials = []
        self.selected_materials = set()
        self.view_mode = "grid"  # "grid" or "list"
        self.filtered_materials = []
        self._image_refs = []  # Keep references to prevent garbage collection

        # Create main frame
        self.main_frame = theme_manager.create_styled_frame(parent)
        self.main_frame.pack(fill='both', expand=True, padx=10, pady=10)

        self._create_interface()
        self._load_materials()

    def _create_interface(self):
        """Create the material management interface."""
        # Top toolbar
        self._create_toolbar()

        # Search and filters
        self._create_search_frame()

        # View toggle and sorting
        self._create_view_controls()

        # Main content area
        self._create_content_area()

        # Bottom status bar
        self._create_status_bar()

    def _create_toolbar(self):
        """Create top toolbar with main actions."""
        toolbar = theme_manager.create_styled_frame(self.main_frame)
        toolbar.pack(fill='x', pady=(0, 10))

        # Add Material button
        add_btn = theme_manager.create_primary_button(
            toolbar, "➕ Add Material", self._add_material
        )
        add_btn.pack(side='left', padx=(0, 10))

        # Edit Material button
        self.edit_btn = theme_manager.create_styled_button(
            toolbar, "✏️ Edit", self._edit_material
        )
        self.edit_btn.pack(side='left', padx=(0, 10))
        self.edit_btn.configure(state='disabled')

        # Delete Material button
        self.delete_btn = theme_manager.create_styled_button(
            toolbar, "🗑️ Delete", self._delete_material
        )
        self.delete_btn.pack(side='left', padx=(0, 10))
        self.delete_btn.configure(state='disabled')

        # Separator
        separator = ttk.Separator(toolbar, orient='vertical', style='Dark.TSeparator')
        separator.pack(side='left', fill='y', padx=10)

        # Material-specific actions
        if not self.is_used:
            # Move to Used button
            self.move_used_btn = theme_manager.create_styled_button(
                toolbar, "♻️ Move to Used", self._move_to_used
            )
            self.move_used_btn.pack(side='left', padx=(0, 10))
            self.move_used_btn.configure(state='disabled')

        # Manage Serials button
        self.serials_btn = theme_manager.create_styled_button(
            toolbar, "🔢 Manage Serials", self._manage_serials
        )
        self.serials_btn.pack(side='left', padx=(0, 10))
        self.serials_btn.configure(state='disabled')

        # Import/Export buttons on the right
        export_btn = theme_manager.create_styled_button(
            toolbar, "📤 Export", self._export_materials
        )
        export_btn.pack(side='right', padx=(5, 0))

        import_btn = theme_manager.create_styled_button(
            toolbar, "📥 Import", self._import_materials
        )
        import_btn.pack(side='right', padx=(5, 0))

    def _create_search_frame(self):
        """Create search and filter interface."""
        search_container = theme_manager.create_styled_frame(self.main_frame)
        search_container.pack(fill='x', pady=(0, 10))

        # Main search
        self.search_frame, self.search_var, self.search_entry = theme_manager.create_search_frame(
            search_container, self._perform_search
        )
        self.search_frame.pack(fill='x')

        # Advanced filters
        filters_frame = theme_manager.create_styled_frame(search_container)
        filters_frame.pack(fill='x', pady=(10, 0))

        # Filter by category
        cat_label = theme_manager.create_styled_label(filters_frame, "Category:")
        cat_label.grid(row=0, column=0, padx=(0, 5), sticky='w')

        self.category_var = tk.StringVar(value="All")
        cat_combo = ttk.Combobox(filters_frame, textvariable=self.category_var,
                                 values=['All', 'Electronics', 'Hardware', 'Software', 'Accessories'],
                                 state='readonly', width=15, style='Dark.TCombobox')
        cat_combo.grid(row=0, column=1, padx=(0, 20))
        cat_combo.bind('<<ComboboxSelected>>', lambda e: self._perform_search())

        # Filter by manufacturer
        mfg_label = theme_manager.create_styled_label(filters_frame, "Manufacturer:")
        mfg_label.grid(row=0, column=2, padx=(0, 5), sticky='w')

        self.manufacturer_var = tk.StringVar(value="All")
        self.mfg_combo = ttk.Combobox(filters_frame, textvariable=self.manufacturer_var,
                                      state='readonly', width=15, style='Dark.TCombobox')
        self.mfg_combo.grid(row=0, column=3, padx=(0, 20))
        self.mfg_combo.bind('<<ComboboxSelected>>', lambda e: self._perform_search())

        # Clear filters button
        clear_filters_btn = theme_manager.create_styled_button(
            filters_frame, "🔄 Clear Filters", self._clear_filters
        )
        clear_filters_btn.grid(row=0, column=4, padx=(20, 0))

    def _create_view_controls(self):
        """Create view mode and sorting controls."""
        controls_frame = theme_manager.create_styled_frame(self.main_frame)
        controls_frame.pack(fill='x', pady=(0, 10))

        # View mode toggle
        view_label = theme_manager.create_styled_label(controls_frame, "View:")
        view_label.pack(side='left', padx=(0, 10))

        self.grid_btn = theme_manager.create_primary_button(
            controls_frame, "⊞ Grid", lambda: self._change_view_mode("grid")
        )
        self.grid_btn.pack(side='left', padx=(0, 5))

        self.list_btn = theme_manager.create_styled_button(
            controls_frame, "☰ List", lambda: self._change_view_mode("list")
        )
        self.list_btn.pack(side='left', padx=(0, 20))

        # Sorting
        sort_label = theme_manager.create_styled_label(controls_frame, "Sort by:")
        sort_label.pack(side='left', padx=(0, 5))

        self.sort_var = tk.StringVar(value="name")
        sort_combo = ttk.Combobox(controls_frame, textvariable=self.sort_var,
                                  values=['name', 'model', 'manufacturer', 'price'],
                                  state='readonly', width=12, style='Dark.TCombobox')
        sort_combo.pack(side='left', padx=(0, 10))
        sort_combo.bind('<<ComboboxSelected>>', lambda e: self._update_display())

        # Selection info
        self.selection_label = theme_manager.create_styled_label(
            controls_frame, "No items selected", 'Secondary.TLabel'
        )
        self.selection_label.pack(side='right')

        # Bulk actions
        bulk_frame = theme_manager.create_styled_frame(controls_frame)
        bulk_frame.pack(side='right', padx=(0, 20))

        self.select_all_btn = theme_manager.create_styled_button(
            bulk_frame, "☑ Select All", self._select_all
        )
        self.select_all_btn.pack(side='left', padx=(0, 5))

        self.clear_selection_btn = theme_manager.create_styled_button(
            bulk_frame, "☐ Clear", self._clear_selection
        )
        self.clear_selection_btn.pack(side='left')

    def _create_content_area(self):
        """Create main content area with materials display."""
        # Create scrollable area for materials
        self.content_frame = theme_manager.create_styled_frame(self.main_frame)
        self.content_frame.pack(fill='both', expand=True, pady=(0, 10))

        # Canvas for scrolling
        self.canvas = tk.Canvas(self.content_frame,
                                bg=theme_manager.theme.COLORS['bg_primary'],
                                highlightthickness=0)

        # Scrollbars
        v_scrollbar = ttk.Scrollbar(self.content_frame, orient='vertical',
                                    command=self.canvas.yview,
                                    style='Dark.Vertical.TScrollbar')

        self.canvas.configure(yscrollcommand=v_scrollbar.set)

        # Content frame inside canvas
        self.materials_content = theme_manager.create_styled_frame(self.canvas)
        self.canvas_window = self.canvas.create_window(0, 0, anchor='nw', window=self.materials_content)

        # Pack scrolling elements
        self.canvas.pack(side='left', fill='both', expand=True)
        v_scrollbar.pack(side='right', fill='y')

        # Bind canvas events
        self.materials_content.bind('<Configure>', self._on_frame_configure)
        self.canvas.bind('<Configure>', self._on_canvas_configure)

        # Bind mousewheel
        theme_manager.bind_mousewheel(self.materials_content, self.canvas)
        theme_manager.bind_mousewheel(self.canvas, self.canvas)

    def _create_status_bar(self):
        """Create bottom status bar."""
        self.status_bar = theme_manager.create_styled_frame(self.main_frame)
        self.status_bar.pack(fill='x')

        self.status_label = theme_manager.create_styled_label(
            self.status_bar, "Ready", 'Small.TLabel'
        )
        self.status_label.pack(side='left', padx=5)

        # Item count
        self.count_label = theme_manager.create_styled_label(
            self.status_bar, "", 'Small.TLabel'
        )
        self.count_label.pack(side='right', padx=5)

    def _load_materials(self):
        """Load materials from database."""
        try:
            self.materials = self.db_manager.search_materials(is_used=self.is_used)
            self.filtered_materials = self.materials.copy()
            self._update_manufacturer_filter()
            self._update_display()
            self._update_status(f"Loaded {len(self.materials)} materials")
            self.count_label.configure(text=f"Total: {len(self.materials)} items")
            self.logger.info(f"Loaded {len(self.materials)} materials")
        except Exception as e:
            self.logger.error(f"Failed to load materials: {e}")
            # Don't show error to user, just log it
            self._update_status("No materials found")

    def _update_manufacturer_filter(self):
        """Update manufacturer filter combobox."""
        manufacturers = set()
        for material in self.materials:
            if material.get('manufacturer'):
                manufacturers.add(material['manufacturer'])

        values = ['All'] + sorted(manufacturers)
        self.mfg_combo.configure(values=values)

    def _perform_search(self, query: str = ""):
        """Perform material search with filters."""
        try:
            # Get base query
            if not query:
                query = self.search_var.get()

            # Apply filters
            filtered_materials = []

            for material in self.materials:
                # Text search
                if query:
                    search_text = f"{material.get('name', '')} {material.get('model', '')} {material.get('manufacturer', '')} {material.get('description', '') or ''}".lower()
                    if query.lower() not in search_text:
                        continue

                # Category filter
                category = self.category_var.get()
                if category != "All" and material.get('category', 'General') != category:
                    continue

                # Manufacturer filter
                manufacturer = self.manufacturer_var.get()
                if manufacturer != "All" and material.get('manufacturer', '') != manufacturer:
                    continue

                filtered_materials.append(material)

            # Update display with filtered results
            self.filtered_materials = filtered_materials
            self._update_display()
            self._update_status(f"Found {len(filtered_materials)} materials")

        except Exception as e:
            self.logger.error(f"Search failed: {e}")

    def _clear_filters(self):
        """Clear all filters and search."""
        self.search_var.set("")
        self.category_var.set("All")
        self.manufacturer_var.set("All")
        self._perform_search()

    def _change_view_mode(self, mode: str):
        """Change between grid and list view."""
        self.view_mode = mode

        # Update button styles
        if mode == "grid":
            self.grid_btn.configure(style='Primary.TButton')
            self.list_btn.configure(style='Dark.TButton')
        else:
            self.list_btn.configure(style='Primary.TButton')
            self.grid_btn.configure(style='Dark.TButton')

        self._update_display()

    def _update_display(self):
        """Update materials display based on current view mode and filters."""
        # Clear existing content and image references
        for widget in self.materials_content.winfo_children():
            widget.destroy()
        self._image_refs.clear()

        # Get materials to display
        materials_to_show = self.filtered_materials

        # Sort materials
        sort_key = self.sort_var.get()

        try:
            if sort_key == 'price':
                materials_to_show = sorted(materials_to_show,
                                           key=lambda x: float(x.get(sort_key, 0) or 0))
            else:
                materials_to_show = sorted(materials_to_show,
                                           key=lambda x: str(x.get(sort_key, '') or ''))
        except Exception as e:
            self.logger.warning(f"Sort failed: {e}")

        # Display based on view mode
        if self.view_mode == "grid":
            self._display_grid_view(materials_to_show)
        else:
            self._display_list_view(materials_to_show)

        # Update canvas scroll region
        self.materials_content.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _display_grid_view(self, materials: List[Dict]):
        """Display materials in grid view (cards)."""
        if not materials:
            no_data_label = theme_manager.create_styled_label(
                self.materials_content, "📦 No materials found", 'Heading.TLabel'
            )
            no_data_label.pack(pady=50)
            return

        # Create cards
        row = 0
        col = 0
        max_cols = 4  # Maximum cards per row

        for material in materials:
            # Create material card
            card = self._create_material_card(self.materials_content, material)
            card.grid(row=row, column=col, padx=10, pady=10, sticky='nw')

            col += 1
            if col >= max_cols:
                col = 0
                row += 1

    def _display_list_view(self, materials: List[Dict]):
        """Display materials in list view (table)."""
        if not materials:
            no_data_label = theme_manager.create_styled_label(
                self.materials_content, "📦 No materials found", 'Heading.TLabel'
            )
            no_data_label.pack(pady=50)
            return

        # Create treeview for list display
        columns = ('ID', 'Name', 'Model', 'Manufacturer', 'Price', 'Category', 'Stock')
        self.list_tree = ttk.Treeview(self.materials_content, columns=columns, show='headings',
                                      style='Dark.Treeview', selectmode='extended', height=20)

        # Configure columns
        self.list_tree.heading('ID', text='ID')
        self.list_tree.heading('Name', text='Name')
        self.list_tree.heading('Model', text='Model')
        self.list_tree.heading('Manufacturer', text='Manufacturer')
        self.list_tree.heading('Price', text='Price')
        self.list_tree.heading('Category', text='Category')
        self.list_tree.heading('Stock', text='Stock')

        # Column widths
        self.list_tree.column('ID', width=50)
        self.list_tree.column('Name', width=200)
        self.list_tree.column('Model', width=150)
        self.list_tree.column('Manufacturer', width=120)
        self.list_tree.column('Price', width=80)
        self.list_tree.column('Category', width=100)
        self.list_tree.column('Stock', width=80)

        # Add materials to tree
        for material in materials:
            price = material.get('price', 0) or 0
            stock = material.get('available_count', 0) or 0
            self.list_tree.insert('', 'end', values=(
                material.get('id', ''),
                material.get('name', ''),
                material.get('model', ''),
                material.get('manufacturer', ''),
                f"€{price:.2f}",
                material.get('category', 'General'),
                stock
            ), tags=(str(material.get('id', '')),))

        self.list_tree.pack(fill='both', expand=True)

        # Bind selection events
        self.list_tree.bind('<<TreeviewSelect>>', self._on_list_selection)
        self.list_tree.bind('<Double-1>', self._on_list_double_click)

    def _create_material_card(self, parent, material: Dict):
        """Create a material card widget with proper image handling."""
        # Main card frame
        card = theme_manager.create_card_frame(parent)

        # Make card darker for better contrast
        card.configure(style='Card.TFrame')

        # Image frame
        img_frame = theme_manager.create_styled_frame(card)
        img_frame.pack(fill='x', padx=10, pady=10)

        # Material image - handle None/empty paths properly
        image_path = material.get('image_path')
        thumbnail = theme_manager.make_thumbnail(image_path if image_path else None)

        if thumbnail:
            img_label = tk.Label(img_frame, image=thumbnail,
                                 bg=theme_manager.theme.COLORS['bg_secondary'])
            img_label.pack()
            # Keep reference to prevent garbage collection
            self._image_refs.append(thumbnail)
        else:
            # Show placeholder text if no image
            placeholder = theme_manager.create_styled_label(
                img_frame, "📦", 'Heading.TLabel'
            )
            placeholder.pack(pady=15)

        # Info frame
        info_frame = theme_manager.create_styled_frame(card)
        info_frame.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        # Material name
        name_label = theme_manager.create_styled_label(
            info_frame, material.get('name', 'Unknown'), 'Subheading.TLabel'
        )
        name_label.pack(anchor='w')

        # Model
        model_label = theme_manager.create_styled_label(
            info_frame, f"Model: {material.get('model', 'N/A')}", 'Small.TLabel'
        )
        model_label.pack(anchor='w')

        # Manufacturer
        mfg_label = theme_manager.create_styled_label(
            info_frame, material.get('manufacturer', 'Unknown'), 'Small.TLabel'
        )
        mfg_label.pack(anchor='w')

        # Price
        price = material.get('price', 0) or 0
        price_label = theme_manager.create_styled_label(
            info_frame, f"€{price:.2f}", 'Subheading.TLabel'
        )
        price_label.configure(foreground=theme_manager.theme.COLORS['fg_primary'])
        price_label.pack(anchor='w', pady=(5, 0))

        # Stock info
        stock = material.get('available_count', 0) or 0
        stock_label = theme_manager.create_styled_label(
            info_frame, f"Stock: {stock}", 'Small.TLabel'
        )
        stock_label.pack(anchor='w')

        # Bind click events for selection
        def on_card_click(event):
            self._toggle_card_selection(card, material.get('id'))

        def on_card_double_click(event):
            self._edit_material(material.get('id'))

        # Bind events to card and all children
        widgets_to_bind = [card, img_frame, info_frame, name_label, model_label,
                           mfg_label, price_label, stock_label]
        if thumbnail:
            widgets_to_bind.append(img_label)

        for widget in widgets_to_bind:
            try:
                widget.bind('<Button-1>', on_card_click)
                widget.bind('<Double-Button-1>', on_card_double_click)
            except:
                pass

        return card

    def _toggle_card_selection(self, card, material_id: int):
        """Toggle selection state of a card."""
        if not material_id:
            return

        if material_id in self.selected_materials:
            # Deselect
            self.selected_materials.remove(material_id)
            card.configure(style='Card.TFrame')
        else:
            # Select
            self.selected_materials.add(material_id)
            # Visual feedback for selection
            try:
                card.configure(relief='solid', borderwidth=2)
            except:
                pass

        self._update_selection_ui()

    def _select_all(self):
        """Select all visible materials."""
        self.selected_materials = {m.get('id') for m in self.filtered_materials if m.get('id')}
        self._update_display()  # Refresh to show selection
        self._update_selection_ui()

    def _clear_selection(self):
        """Clear all selections."""
        self.selected_materials.clear()
        self._update_display()  # Refresh to clear selection visuals
        self._update_selection_ui()

    def _update_selection_ui(self):
        """Update UI based on current selection."""
        count = len(self.selected_materials)

        if count == 0:
            self.selection_label.configure(text="No items selected")
        elif count == 1:
            self.selection_label.configure(text="1 item selected")
        else:
            self.selection_label.configure(text=f"{count} items selected")

        # Update button states
        has_selection = count > 0
        single_selection = count == 1

        self.edit_btn.configure(state='normal' if single_selection else 'disabled')
        self.delete_btn.configure(state='normal' if has_selection else 'disabled')
        self.serials_btn.configure(state='normal' if single_selection else 'disabled')

        if not self.is_used and hasattr(self, 'move_used_btn'):
            self.move_used_btn.configure(state='normal' if has_selection else 'disabled')

    def _update_status(self, message: str):
        """Update status bar message."""
        try:
            if hasattr(self, 'status_label') and self.status_label.winfo_exists():
                self.status_label.configure(text=message)
        except:
            pass

    def _on_frame_configure(self, event):
        """Handle content frame configuration."""
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _on_canvas_configure(self, event):
        """Handle canvas configuration."""
        # Make sure content frame fills canvas width
        canvas_width = event.width
        self.canvas.itemconfig(self.canvas_window, width=canvas_width)

    def _on_list_selection(self, event):
        """Handle list view selection."""
        if hasattr(self, 'list_tree'):
            selection = self.list_tree.selection()
            self.selected_materials.clear()

            for item in selection:
                tags = self.list_tree.item(item, 'tags')
                if tags:
                    try:
                        material_id = int(tags[0])
                        self.selected_materials.add(material_id)
                    except (ValueError, IndexError):
                        pass

            self._update_selection_ui()

    def _on_list_double_click(self, event):
        """Handle list view double-click."""
        if len(self.selected_materials) == 1:
            material_id = next(iter(self.selected_materials))
            self._edit_material(material_id)

    # Material management methods
    def _add_material(self):
        """Add new material."""
        theme_manager.show_message(
            self.main_frame, "Add Material",
            "Add material functionality will be implemented in the next update.", "info"
        )

    def _edit_material(self, material_id: int = None):
        """Edit selected material."""
        theme_manager.show_message(
            self.main_frame, "Edit Material",
            "Edit material functionality will be implemented in the next update.", "info"
        )

    def _delete_material(self):
        """Delete selected materials."""
        if not self.selected_materials:
            return

        if len(self.selected_materials) == 1:
            message = "Delete selected material?"
        else:
            message = f"Delete {len(self.selected_materials)} selected materials?"

        if theme_manager.confirm_action(self.main_frame, "Confirm Delete", message):
            deleted_count = 0
            for material_id in self.selected_materials:
                if self.db_manager.delete_material(material_id):
                    deleted_count += 1

            if deleted_count > 0:
                theme_manager.show_message(
                    self.main_frame, "Success",
                    f"Deleted {deleted_count} material(s).", "info"
                )
                self._load_materials()

    def _move_to_used(self):
        """Move selected materials to used inventory."""
        if not self.selected_materials:
            return

        message = f"Move {len(self.selected_materials)} material(s) to used inventory?"
        if theme_manager.confirm_action(self.main_frame, "Confirm Move", message):
            moved_count = 0
            for material_id in self.selected_materials:
                if self.db_manager.transfer_serials_to_used(material_id):
                    moved_count += 1

            if moved_count > 0:
                theme_manager.show_message(
                    self.main_frame, "Success",
                    f"Moved {moved_count} material(s) to used inventory.", "info"
                )
                self._load_materials()

    def _manage_serials(self):
        """Manage serials for selected material."""
        theme_manager.show_message(
            self.main_frame, "Manage Serials",
            "Serials management will be implemented in the next update.", "info"
        )

    def _export_materials(self):
        """Export materials to file."""
        theme_manager.show_message(
            self.main_frame, "Export Materials",
            "Export functionality will be implemented soon.", "info"
        )

    def _import_materials(self):
        """Import materials from file."""
        theme_manager.show_message(
            self.main_frame, "Import Materials",
            "Import functionality will be implemented soon.", "info"
        )

    def cleanup(self):
        """Clean up resources when frame is destroyed."""
        self._image_refs.clear()
        self.selected_materials.clear()