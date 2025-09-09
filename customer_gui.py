"""
Warehouse Management System - Customer GUI Module
Customer management with 4-digit PIN system and assignment history
"""

import tkinter as tk
from tkinter import ttk, filedialog
import logging
import csv
import random
from typing import Dict, List, Optional
from theme import theme_manager
from database import DatabaseManager


class CustomerManagementFrame:
    """Main customer management interface."""

    def __init__(self, parent, db_manager: DatabaseManager, current_user: Dict):
        self.parent = parent
        self.db_manager = db_manager
        self.current_user = current_user
        self.logger = logging.getLogger(__name__)

        self.customers = []
        self.selected_customers = []

        # Create main frame
        self.main_frame = theme_manager.create_styled_frame(parent)
        self.main_frame.pack(fill='both', expand=True, padx=10, pady=10)

        self._create_interface()
        self._load_customers()

    def _create_interface(self):
        """Create the customer management interface."""
        # Top toolbar
        self._create_toolbar()

        # Search frame
        self._create_search_frame()

        # Customer list
        self._create_customer_list()

        # Bottom buttons
        self._create_bottom_buttons()

    def _create_toolbar(self):
        """Create top toolbar with main actions."""
        toolbar = theme_manager.create_styled_frame(self.main_frame)
        toolbar.pack(fill='x', pady=(0, 10))

        # Add Customer button
        add_btn = theme_manager.create_primary_button(
            toolbar, "Add Customer", self._add_customer
        )
        add_btn.pack(side='left', padx=(0, 10))

        # Edit Customer button
        self.edit_btn = theme_manager.create_styled_button(
            toolbar, "Edit Customer", self._edit_customer
        )
        self.edit_btn.pack(side='left', padx=(0, 10))
        self.edit_btn.configure(state='disabled')

        # Delete Customer button
        self.delete_btn = theme_manager.create_styled_button(
            toolbar, "Delete Customer", self._delete_customer
        )
        self.delete_btn.pack(side='left', padx=(0, 10))
        self.delete_btn.configure(state='disabled')

        # Separator
        separator = ttk.Separator(toolbar, orient='vertical')
        separator.pack(side='left', fill='y', padx=10)

        # View History button
        self.history_btn = theme_manager.create_styled_button(
            toolbar, "View History", self._view_history
        )
        self.history_btn.pack(side='left', padx=(0, 10))
        self.history_btn.configure(state='disabled')

        # Export button
        export_btn = theme_manager.create_styled_button(
            toolbar, "Export to CSV", self._export_customers
        )
        export_btn.pack(side='right')

        # Import button
        import_btn = theme_manager.create_styled_button(
            toolbar, "Import from CSV", self._import_customers
        )
        import_btn.pack(side='right', padx=(0, 10))

    def _create_search_frame(self):
        """Create search interface."""
        self.search_frame, self.search_var, self.search_entry = theme_manager.create_search_frame(
            self.main_frame, self._perform_search
        )
        self.search_frame.pack(fill='x', pady=(0, 10))

        # Advanced search options
        options_frame = theme_manager.create_styled_frame(self.search_frame)
        options_frame.grid(row=1, column=0, columnspan=4, sticky='ew', pady=(5, 0))

        self.search_by_var = tk.StringVar(value="all")

        search_all_rb = ttk.Radiobutton(options_frame, text="All Fields",
                                        variable=self.search_by_var, value="all")
        search_all_rb.pack(side='left', padx=(0, 10))

        search_name_rb = ttk.Radiobutton(options_frame, text="Name Only",
                                         variable=self.search_by_var, value="name")
        search_name_rb.pack(side='left', padx=(0, 10))

        search_pin_rb = ttk.Radiobutton(options_frame, text="PIN Only",
                                        variable=self.search_by_var, value="pin")
        search_pin_rb.pack(side='left', padx=(0, 10))

    def _create_customer_list(self):
        """Create customer list with treeview."""
        # Frame for treeview and scrollbar
        list_frame = theme_manager.create_styled_frame(self.main_frame)
        list_frame.pack(fill='both', expand=True, pady=(0, 10))

        # Create treeview
        columns = ('PIN', 'Name', 'Phone', 'Email', 'Created', 'Assignments')
        self.tree = ttk.Treeview(list_frame, columns=columns, show='headings',
                                 style='Dark.Treeview', selectmode='extended')

        # Configure columns
        self.tree.heading('PIN', text='PIN')
        self.tree.heading('Name', text='Name')
        self.tree.heading('Phone', text='Phone')
        self.tree.heading('Email', text='Email')
        self.tree.heading('Created', text='Created')
        self.tree.heading('Assignments', text='Active Assignments')

        # Column widths
        self.tree.column('PIN', width=80, minwidth=60)
        self.tree.column('Name', width=200, minwidth=150)
        self.tree.column('Phone', width=120, minwidth=100)
        self.tree.column('Email', width=200, minwidth=150)
        self.tree.column('Created', width=100, minwidth=80)
        self.tree.column('Assignments', width=120, minwidth=100)

        # Scrollbars
        v_scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.tree.yview,
                                    style='Dark.Vertical.TScrollbar')
        h_scrollbar = ttk.Scrollbar(list_frame, orient='horizontal', command=self.tree.xview)

        self.tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)

        # Pack elements
        self.tree.grid(row=0, column=0, sticky='nsew')
        v_scrollbar.grid(row=0, column=1, sticky='ns')
        h_scrollbar.grid(row=1, column=0, sticky='ew')

        # Configure grid weights
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        # Bind events
        self.tree.bind('<<TreeviewSelect>>', self._on_selection_change)
        self.tree.bind('<Double-1>', self._on_double_click)
        self.tree.bind('<Delete>', self._on_delete_key)

        # Context menu
        self._create_context_menu()

    def _create_context_menu(self):
        """Create right-click context menu."""
        self.context_menu = tk.Menu(self.tree, tearoff=0,
                                    bg=theme_manager.theme.COLORS['bg_secondary'],
                                    fg=theme_manager.theme.COLORS['fg_primary'],
                                    activebackground=theme_manager.theme.COLORS['bg_accent'])

        self.context_menu.add_command(label="Edit Customer", command=self._edit_customer)
        self.context_menu.add_command(label="View History", command=self._view_history)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Delete Customer", command=self._delete_customer)

        def show_context_menu(event):
            try:
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()

        self.tree.bind('<Button-3>', show_context_menu)  # Right click

    def _create_bottom_buttons(self):
        """Create bottom action buttons."""
        button_frame = theme_manager.create_styled_frame(self.main_frame)
        button_frame.pack(fill='x')

        # Bulk actions
        bulk_label = theme_manager.create_styled_label(button_frame, "Bulk Actions:")
        bulk_label.pack(side='left')

        self.bulk_assign_btn = theme_manager.create_styled_button(
            button_frame, "Assign Selected", self._bulk_assign
        )
        self.bulk_assign_btn.pack(side='left', padx=(10, 5))
        self.bulk_assign_btn.configure(state='disabled')

        self.bulk_export_btn = theme_manager.create_styled_button(
            button_frame, "Export Selected", self._export_selected
        )
        self.bulk_export_btn.pack(side='left', padx=5)
        self.bulk_export_btn.configure(state='disabled')

        # Refresh button
        refresh_btn = theme_manager.create_styled_button(
            button_frame, "Refresh", self._load_customers
        )
        refresh_btn.pack(side='right')

    def _load_customers(self):
        """Load customers from database."""
        try:
            self.customers = self.db_manager.search_customers()
            self._update_tree()
            self.logger.info(f"Loaded {len(self.customers)} customers")
        except Exception as e:
            self.logger.error(f"Failed to load customers: {e}")
            theme_manager.show_message(
                self.main_frame, "Error",
                f"Failed to load customers: {e}", "error"
            )

    def _update_tree(self):
        """Update treeview with current customers."""
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Add customers
        for customer in self.customers:
            # Get assignment count
            assignments = self.db_manager.get_customer_history(customer['pin'])
            active_count = sum(1 for a in assignments if not a['returned_date'])

            # Format created date
            created_date = customer['created_date'][:10] if customer['created_date'] else ""

            self.tree.insert('', 'end', values=(
                customer['pin'],
                customer['name'],
                customer['phone'] or "",
                customer['email'] or "",
                created_date,
                active_count
            ))

    def _perform_search(self, query: str = ""):
        """Perform customer search."""
        try:
            if not query:
                self.customers = self.db_manager.search_customers()
            else:
                search_by = self.search_by_var.get()
                if search_by == "name":
                    # Search only by name
                    all_customers = self.db_manager.search_customers()
                    self.customers = [c for c in all_customers
                                      if query.lower() in c['name'].lower()]
                elif search_by == "pin":
                    # Search only by PIN
                    all_customers = self.db_manager.search_customers()
                    self.customers = [c for c in all_customers
                                      if query in str(c['pin'])]
                else:
                    # Search all fields
                    self.customers = self.db_manager.search_customers(query)

            self._update_tree()
            self.logger.info(f"Search '{query}' returned {len(self.customers)} customers")
        except Exception as e:
            self.logger.error(f"Search failed: {e}")
            theme_manager.show_message(
                self.main_frame, "Error",
                f"Search failed: {e}", "error"
            )

    def _on_selection_change(self, event):
        """Handle treeview selection change."""
        selection = self.tree.selection()
        self.selected_customers = []

        for item in selection:
            values = self.tree.item(item, 'values')
            if values:
                pin = int(values[0])
                customer = next((c for c in self.customers if c['pin'] == pin), None)
                if customer:
                    self.selected_customers.append(customer)

        # Update button states
        has_selection = len(self.selected_customers) > 0
        single_selection = len(self.selected_customers) == 1

        self.edit_btn.configure(state='normal' if single_selection else 'disabled')
        self.delete_btn.configure(state='normal' if has_selection else 'disabled')
        self.history_btn.configure(state='normal' if single_selection else 'disabled')
        self.bulk_assign_btn.configure(state='normal' if has_selection else 'disabled')
        self.bulk_export_btn.configure(state='normal' if has_selection else 'disabled')

    def _on_double_click(self, event):
        """Handle double-click on customer."""
        if len(self.selected_customers) == 1:
            self._edit_customer()

    def _on_delete_key(self, event):
        """Handle delete key press."""
        if self.selected_customers:
            self._delete_customer()

    def _add_customer(self):
        """Add new customer."""
        CustomerDialog(self.main_frame, self.db_manager, None, self._on_customer_saved)

    def _edit_customer(self):
        """Edit selected customer."""
        if len(self.selected_customers) == 1:
            CustomerDialog(self.main_frame, self.db_manager,
                           self.selected_customers[0], self._on_customer_saved)

    def _delete_customer(self):
        """Delete selected customers."""
        if not self.selected_customers:
            return

        if len(self.selected_customers) == 1:
            message = f"Delete customer '{self.selected_customers[0]['name']}' (PIN: {self.selected_customers[0]['pin']})?"
        else:
            message = f"Delete {len(self.selected_customers)} selected customers?"

        if theme_manager.confirm_action(self.main_frame, "Confirm Delete", message):
            deleted_count = 0
            for customer in self.selected_customers:
                if self.db_manager.delete_customer(customer['pin']):
                    deleted_count += 1

            if deleted_count > 0:
                theme_manager.show_message(
                    self.main_frame, "Success",
                    f"Deleted {deleted_count} customer(s).", "info"
                )
                self._load_customers()

    def _view_history(self):
        """View customer assignment history."""
        if len(self.selected_customers) == 1:
            CustomerHistoryDialog(self.main_frame, self.db_manager, self.selected_customers[0])

    def _bulk_assign(self):
        """Bulk assign serials to selected customers."""
        if self.selected_customers:
            theme_manager.show_message(
                self.main_frame, "Feature Not Implemented",
                "Bulk assignment feature will be implemented in the material management section.", "info"
            )

    def _export_customers(self):
        """Export all customers to CSV."""
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Customers"
        )

        if filename:
            self._export_to_csv(self.customers, filename)

    def _export_selected(self):
        """Export selected customers to CSV."""
        if not self.selected_customers:
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Selected Customers"
        )

        if filename:
            self._export_to_csv(self.selected_customers, filename)

    def _export_to_csv(self, customers: List[Dict], filename: str):
        """Export customers to CSV file."""
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['PIN', 'Name', 'Phone', 'Email', 'Created Date', 'Notes']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

                writer.writeheader()
                for customer in customers:
                    writer.writerow({
                        'PIN': customer['pin'],
                        'Name': customer['name'],
                        'Phone': customer['phone'] or '',
                        'Email': customer['email'] or '',
                        'Created Date': customer['created_date'] or '',
                        'Notes': customer['notes'] or ''
                    })

            theme_manager.show_message(
                self.main_frame, "Export Successful",
                f"Exported {len(customers)} customers to {filename}", "info"
            )
            self.logger.info(f"Exported {len(customers)} customers to {filename}")
        except Exception as e:
            self.logger.error(f"Export failed: {e}")
            theme_manager.show_message(
                self.main_frame, "Export Failed",
                f"Failed to export customers: {e}", "error"
            )

    def _import_customers(self):
        """Import customers from CSV."""
        filename = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Import Customers"
        )

        if filename:
            ImportCustomersDialog(self.main_frame, self.db_manager, filename, self._load_customers)

    def _on_customer_saved(self):
        """Callback when customer is saved."""
        self._load_customers()


class CustomerDialog:
    """Dialog for adding/editing customers."""

    def __init__(self, parent, db_manager: DatabaseManager, customer: Optional[Dict],
                 on_success: callable):
        self.parent = parent
        self.db_manager = db_manager
        self.customer = customer
        self.on_success = on_success
        self.logger = logging.getLogger(__name__)

        self.dialog = None
        self.pin_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.phone_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.notes_var = tk.StringVar()

        self._create_dialog()
        self._populate_fields()

    def _create_dialog(self):
        """Create the customer dialog."""
        self.dialog = tk.Toplevel(self.parent)
        title = "Edit Customer" if self.customer else "Add Customer"
        self.dialog.title(title)
        self.dialog.configure(bg=theme_manager.theme.COLORS['bg_primary'])
        self.dialog.resizable(False, False)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        main_frame = theme_manager.create_styled_frame(self.dialog)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)

        # Title
        title_label = theme_manager.create_styled_label(main_frame, title, 'Heading.TLabel')
        title_label.pack(pady=(0, 20))

        # Form
        self._create_form(main_frame)

        # Buttons
        self._create_buttons(main_frame)

        # Center dialog
        self._center_dialog()

    def _create_form(self, parent):
        """Create customer form."""
        form_frame = theme_manager.create_styled_frame(parent)
        form_frame.pack(fill='x', pady=10)

        row = 0

        # PIN field
        pin_label = theme_manager.create_styled_label(form_frame, "PIN (4 digits):")
        pin_label.grid(row=row, column=0, sticky='w', pady=(0, 5))
        row += 1

        pin_frame = theme_manager.create_styled_frame(form_frame)
        pin_frame.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(0, 10))

        self.pin_entry = theme_manager.create_styled_entry(
            pin_frame, textvariable=self.pin_var, width=10
        )
        self.pin_entry.pack(side='left')

        if not self.customer:  # Only show generate button for new customers
            generate_btn = theme_manager.create_styled_button(
                pin_frame, "Generate", self._generate_pin
            )
            generate_btn.pack(side='left', padx=(10, 0))

        row += 1

        # Name field
        name_label = theme_manager.create_styled_label(form_frame, "Name:")
        name_label.grid(row=row, column=0, sticky='w', pady=(0, 5))
        row += 1

        self.name_entry = theme_manager.create_styled_entry(
            form_frame, textvariable=self.name_var, width=30
        )
        self.name_entry.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        row += 1

        # Phone field
        phone_label = theme_manager.create_styled_label(form_frame, "Phone:")
        phone_label.grid(row=row, column=0, sticky='w', pady=(0, 5))
        row += 1

        self.phone_entry = theme_manager.create_styled_entry(
            form_frame, textvariable=self.phone_var, width=30
        )
        self.phone_entry.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        row += 1

        # Email field
        email_label = theme_manager.create_styled_label(form_frame, "Email:")
        email_label.grid(row=row, column=0, sticky='w', pady=(0, 5))
        row += 1

        self.email_entry = theme_manager.create_styled_entry(
            form_frame, textvariable=self.email_var, width=30
        )
        self.email_entry.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        row += 1

        # Notes field
        notes_label = theme_manager.create_styled_label(form_frame, "Notes:")
        notes_label.grid(row=row, column=0, sticky='w', pady=(0, 5))
        row += 1

        notes_frame = theme_manager.create_styled_frame(form_frame)
        notes_frame.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(0, 10))

        self.notes_text = tk.Text(notes_frame, height=4, width=30,
                                  bg=theme_manager.theme.COLORS['bg_tertiary'],
                                  fg=theme_manager.theme.COLORS['fg_primary'],
                                  font=theme_manager.theme.FONTS['default'])
        self.notes_text.pack(fill='both', expand=True)

        # Error label
        self.error_label = theme_manager.create_styled_label(
            form_frame, "", 'Secondary.TLabel'
        )
        self.error_label.configure(foreground=theme_manager.theme.COLORS['fg_error'])
        self.error_label.grid(row=row + 1, column=0, columnspan=2, pady=(0, 10))

        form_frame.columnconfigure(0, weight=1)

    def _create_buttons(self, parent):
        """Create dialog buttons."""
        button_frame = theme_manager.create_styled_frame(parent)
        button_frame.pack(fill='x', pady=10)

        # Save button
        save_text = "Update" if self.customer else "Add"
        save_btn = theme_manager.create_primary_button(
            button_frame, save_text, self._save_customer
        )
        save_btn.pack(side='left', padx=(0, 10))

        # Cancel button
        cancel_btn = theme_manager.create_styled_button(
            button_frame, "Cancel", self._cancel
        )
        cancel_btn.pack(side='left')

    def _populate_fields(self):
        """Populate fields with customer data."""
        if self.customer:
            self.pin_var.set(str(self.customer['pin']))
            self.pin_entry.configure(state='readonly')  # Don't allow PIN changes

            self.name_var.set(self.customer['name'])
            self.phone_var.set(self.customer['phone'] or '')
            self.email_var.set(self.customer['email'] or '')

            # Notes
            notes = self.customer['notes'] or ''
            self.notes_text.delete('1.0', 'end')
            self.notes_text.insert('1.0', notes)
        else:
            self._generate_pin()

    def _generate_pin(self):
        """Generate unique 4-digit PIN."""
        # Get existing PINs
        existing_customers = self.db_manager.search_customers()
        existing_pins = {c['pin'] for c in existing_customers}

        # Generate new PIN
        while True:
            pin = random.randint(1000, 9999)
            if pin not in existing_pins:
                self.pin_var.set(str(pin))
                break

    def _center_dialog(self):
        """Center dialog on parent."""
        self.dialog.update_idletasks()

        dialog_width = self.dialog.winfo_reqwidth()
        dialog_height = self.dialog.winfo_reqheight()

        parent_x = self.parent.winfo_x() if hasattr(self.parent, 'winfo_x') else 0
        parent_y = self.parent.winfo_y() if hasattr(self.parent, 'winfo_y') else 0
        parent_width = self.parent.winfo_width() if hasattr(self.parent, 'winfo_width') else 800
        parent_height = self.parent.winfo_height() if hasattr(self.parent, 'winfo_height') else 600

        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2

        self.dialog.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")

    def _save_customer(self):
        """Save customer data."""
        try:
            pin_str = self.pin_var.get().strip()
            name = self.name_var.get().strip()
            phone = self.phone_var.get().strip()
            email = self.email_var.get().strip()
            notes = self.notes_text.get('1.0', 'end').strip()

            # Clear error
            self.error_label.configure(text="")

            # Validate
            if not pin_str or not name:
                self._show_error("PIN and Name are required.")
                return

            try:
                pin = int(pin_str)
                if pin < 1000 or pin > 9999:
                    self._show_error("PIN must be a 4-digit number.")
                    return
            except ValueError:
                self._show_error("PIN must be a valid number.")
                return

            # Save to database
            if self.customer:
                # Update existing
                success = self.db_manager.update_customer(pin, name, phone, email, notes)
            else:
                # Add new
                success = self.db_manager.add_customer(pin, name, phone, email, notes)

            if success:
                action = "updated" if self.customer else "added"
                self.logger.info(f"Customer {action}: {name} (PIN: {pin})")
                self.dialog.destroy()
                self.on_success()
            else:
                if self.customer:
                    self._show_error("Failed to update customer.")
                else:
                    self._show_error("PIN already exists or failed to add customer.")

        except Exception as e:
            self.logger.error(f"Failed to save customer: {e}")
            self._show_error(f"Error: {e}")

    def _cancel(self):
        """Cancel dialog."""
        self.dialog.destroy()

    def _show_error(self, message: str):
        """Show error message."""
        self.error_label.configure(text=message)


class CustomerHistoryDialog:
    """Dialog showing customer assignment history."""

    def __init__(self, parent, db_manager: DatabaseManager, customer: Dict):
        self.parent = parent
        self.db_manager = db_manager
        self.customer = customer
        self.logger = logging.getLogger(__name__)

        self._create_dialog()
        self._load_history()

    def _create_dialog(self):
        """Create history dialog."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title(f"Assignment History - {self.customer['name']} (PIN: {self.customer['pin']})")
        self.dialog.configure(bg=theme_manager.theme.COLORS['bg_primary'])
        self.dialog.geometry("800x600")
        self.dialog.transient(self.parent)

        main_frame = theme_manager.create_styled_frame(self.dialog)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)

        # Title
        title_text = f"Assignment History - {self.customer['name']} (PIN: {self.customer['pin']})"
        title_label = theme_manager.create_styled_label(main_frame, title_text, 'Heading.TLabel')
        title_label.pack(pady=(0, 20))

        # History tree
        self._create_history_tree(main_frame)

        # Close button
        close_btn = theme_manager.create_styled_button(
            main_frame, "Close", self.dialog.destroy
        )
        close_btn.pack(pady=(10, 0))

    def _create_history_tree(self, parent):
        """Create history treeview."""
        tree_frame = theme_manager.create_styled_frame(parent)
        tree_frame.pack(fill='both', expand=True)

        columns = ('Item', 'Serial', 'Assigned', 'Returned', 'Status', 'Notes')
        self.history_tree = ttk.Treeview(tree_frame, columns=columns, show='headings',
                                         style='Dark.Treeview')

        # Configure columns
        self.history_tree.heading('Item', text='Item')
        self.history_tree.heading('Serial', text='Serial Number')
        self.history_tree.heading('Assigned', text='Assigned Date')
        self.history_tree.heading('Returned', text='Returned Date')
        self.history_tree.heading('Status', text='Status')
        self.history_tree.heading('Notes', text='Notes')

        # Column widths
        self.history_tree.column('Item', width=200)
        self.history_tree.column('Serial', width=150)
        self.history_tree.column('Assigned', width=100)
        self.history_tree.column('Returned', width=100)
        self.history_tree.column('Status', width=80)
        self.history_tree.column('Notes', width=150)

        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame, orient='vertical',
                                  command=self.history_tree.yview,
                                  style='Dark.Vertical.TScrollbar')
        self.history_tree.configure(yscrollcommand=scrollbar.set)

        # Pack
        self.history_tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

    def _load_history(self):
        """Load customer history."""
        try:
            history = self.db_manager.get_customer_history(self.customer['pin'])

            for item in self.history_tree.get_children():
                self.history_tree.delete(item)

            for record in history:
                item_name = f"{record['name']} {record['model']} ({record['manufacturer']})"
                assigned_date = record['assigned_date'][:10] if record['assigned_date'] else ""
                returned_date = record['returned_date'][:10] if record['returned_date'] else ""
                status = "Returned" if record['returned_date'] else "Active"

                self.history_tree.insert('', 'end', values=(
                    item_name,
                    record['serial_number'],
                    assigned_date,
                    returned_date,
                    status,
                    record['notes'] or ""
                ))

        except Exception as e:
            self.logger.error(f"Failed to load history: {e}")
            theme_manager.show_message(
                self.dialog, "Error",
                f"Failed to load history: {e}", "error"
            )


class ImportCustomersDialog:
    """Dialog for importing customers from CSV."""

    def __init__(self, parent, db_manager: DatabaseManager, filename: str, on_success: callable):
        self.parent = parent
        self.db_manager = db_manager
        self.filename = filename
        self.on_success = on_success
        self.logger = logging.getLogger(__name__)

        self._process_import()

    def _process_import(self):
        """Process CSV import."""
        try:
            imported = 0
            errors = []

            with open(self.filename, 'r', newline='', encoding='utf-8') as csvfile:
                # Detect dialect
                sample = csvfile.read(1024)
                csvfile.seek(0)
                sniffer = csv.Sniffer()
                dialect = sniffer.sniff(sample)

                reader = csv.DictReader(csvfile, dialect=dialect)

                for row_num, row in enumerate(reader, start=2):
                    try:
                        # Extract data
                        pin = int(row.get('PIN', '').strip())
                        name = row.get('Name', '').strip()
                        phone = row.get('Phone', '').strip()
                        email = row.get('Email', '').strip()
                        notes = row.get('Notes', '').strip()

                        if not name:
                            errors.append(f"Row {row_num}: Name is required")
                            continue

                        if pin < 1000 or pin > 9999:
                            errors.append(f"Row {row_num}: Invalid PIN {pin}")
                            continue

                        if self.db_manager.add_customer(pin, name, phone, email, notes):
                            imported += 1
                        else:
                            errors.append(f"Row {row_num}: PIN {pin} already exists")

                    except (ValueError, KeyError) as e:
                        errors.append(f"Row {row_num}: {e}")

            # Show results
            message = f"Import completed.\nImported: {imported} customers"
            if errors:
                error_text = "\n".join(errors[:10])  # Show first 10 errors
                if len(errors) > 10:
                    error_text += f"\n... and {len(errors) - 10} more errors"
                message += f"\n\nErrors:\n{error_text}"

            theme_manager.show_message(self.parent, "Import Results", message, "info")

            if imported > 0:
                self.on_success()

        except Exception as e:
            self.logger.error(f"Import failed: {e}")
            theme_manager.show_message(
                self.parent, "Import Failed",
                f"Failed to import customers: {e}", "error"
            )