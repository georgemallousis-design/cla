"""
Warehouse Management System - Login GUI Module
Modal login overlay with create account and forgot password functionality
"""

import tkinter as tk
from tkinter import ttk
import logging
from typing import Dict, Optional, Callable
from theme import theme_manager
from database import DatabaseManager


class LoginDialog:
    """Modal login dialog with role-based authentication."""

    def __init__(self, parent, db_manager: DatabaseManager, on_success: Callable[[Dict], None]):
        self.parent = parent
        self.db_manager = db_manager
        self.on_success = on_success
        self.logger = logging.getLogger(__name__)

        self.result = None
        self.dialog = None

        # Initialize variables after dialog creation
        self.username_var = None
        self.password_var = None

        self._create_dialog()
        self._setup_bindings()

    def _create_dialog(self):
        """Create the modal login dialog."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Warehouse Management - Login")
        self.dialog.configure(bg=theme_manager.theme.COLORS['bg_primary'])
        self.dialog.resizable(False, False)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # Now create StringVars with proper parent
        self.username_var = tk.StringVar(self.dialog)
        self.password_var = tk.StringVar(self.dialog)

        # Create main container
        main_frame = theme_manager.create_styled_frame(self.dialog)
        main_frame.pack(fill='both', expand=True, padx=40, pady=40)

        # Title
        title_label = theme_manager.create_styled_label(
            main_frame, "Warehouse Management System", 'Heading.TLabel'
        )
        title_label.pack(pady=(0, 30))

        # Login form
        self._create_login_form(main_frame)

        # Buttons
        self._create_buttons(main_frame)

        # Center dialog
        self._center_dialog()

    def _create_login_form(self, parent):
        """Create the login form fields."""
        form_frame = theme_manager.create_styled_frame(parent)
        form_frame.pack(fill='x', pady=20)

        # Username field
        username_label = theme_manager.create_styled_label(form_frame, "Username:")
        username_label.grid(row=0, column=0, sticky='w', pady=(0, 5))

        self.username_entry = theme_manager.create_styled_entry(
            form_frame, textvariable=self.username_var, width=30
        )
        self.username_entry.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(0, 15))

        # Password field
        password_label = theme_manager.create_styled_label(form_frame, "Password:")
        password_label.grid(row=2, column=0, sticky='w', pady=(0, 5))

        self.password_entry = theme_manager.create_styled_entry(
            form_frame, textvariable=self.password_var, show='*', width=30
        )
        self.password_entry.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(0, 20))

        # Error label (initially hidden)
        self.error_label = theme_manager.create_styled_label(
            form_frame, "", 'Secondary.TLabel'
        )
        self.error_label.configure(foreground=theme_manager.theme.COLORS['fg_error'])
        self.error_label.grid(row=4, column=0, columnspan=2, pady=(0, 10))

        form_frame.columnconfigure(0, weight=1)

    def _create_buttons(self, parent):
        """Create login and action buttons."""
        # Main buttons frame
        button_frame = theme_manager.create_styled_frame(parent)
        button_frame.pack(fill='x', pady=10)

        # Login button
        self.login_btn = theme_manager.create_primary_button(
            button_frame, "Login", self._handle_login
        )
        self.login_btn.pack(side='left', padx=(0, 10))

        # Cancel button
        cancel_btn = theme_manager.create_styled_button(
            button_frame, "Cancel", self._handle_cancel
        )
        cancel_btn.pack(side='left')

        # Secondary buttons frame
        secondary_frame = theme_manager.create_styled_frame(parent)
        secondary_frame.pack(fill='x', pady=(20, 0))

        # Create Account button
        create_btn = theme_manager.create_styled_button(
            secondary_frame, "Create Account", self._show_create_account
        )
        create_btn.pack(side='left', padx=(0, 10))

        # Forgot Password button
        forgot_btn = theme_manager.create_styled_button(
            secondary_frame, "Forgot Password", self._show_forgot_password
        )
        forgot_btn.pack(side='left')

    def _setup_bindings(self):
        """Setup keyboard bindings."""
        self.dialog.bind('<Return>', lambda e: self._handle_login())
        self.dialog.bind('<Escape>', lambda e: self._handle_cancel())

        # Focus on username entry
        self.dialog.after(100, lambda: self.username_entry.focus())

    def _center_dialog(self):
        """Center dialog on parent window."""
        self.dialog.update_idletasks()

        # Get dimensions
        dialog_width = self.dialog.winfo_reqwidth()
        dialog_height = self.dialog.winfo_reqheight()

        parent_x = self.parent.winfo_x()
        parent_y = self.parent.winfo_y()
        parent_width = self.parent.winfo_width()
        parent_height = self.parent.winfo_height()

        # Calculate position
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2

        self.dialog.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")

    def _handle_login(self):
        """Handle login attempt."""
        username = self.username_var.get().strip()
        password = self.password_var.get()

        # Clear previous error
        self.error_label.configure(text="")

        # Validate input
        if not username or not password:
            self._show_error("Please enter both username and password.")
            return

        # Attempt login
        user = self.db_manager.verify_login(username, password)

        if user:
            self.logger.info(f"Successful login: {username} ({user['role']})")
            self.result = user
            self.dialog.destroy()
            self.on_success(user)
        else:
            self.logger.warning(f"Failed login attempt: {username}")
            self._show_error("Invalid username or password.")
            self.password_var.set("")  # Clear password
            self.password_entry.focus()

    def _handle_cancel(self):
        """Handle cancel/close."""
        self.result = None
        self.dialog.destroy()

    def _show_error(self, message: str):
        """Show error message."""
        self.error_label.configure(text=message)

    def _show_create_account(self):
        """Show create account dialog."""
        CreateAccountDialog(self.dialog, self.db_manager, self._on_account_created)

    def _show_forgot_password(self):
        """Show forgot password dialog."""
        ForgotPasswordDialog(self.dialog, self.db_manager)

    def _on_account_created(self, username: str):
        """Handle successful account creation."""
        self.username_var.set(username)
        self.password_var.set("")
        self.password_entry.focus()
        theme_manager.show_message(
            self.dialog, "Account Created",
            f"Account '{username}' created successfully. Please log in."
        )


class CreateAccountDialog:
    """Create new user account dialog."""

    def __init__(self, parent, db_manager: DatabaseManager, on_success: Callable[[str], None]):
        self.parent = parent
        self.db_manager = db_manager
        self.on_success = on_success
        self.logger = logging.getLogger(__name__)

        self.dialog = None

        # Initialize variables after dialog creation
        self.username_var = None
        self.password_var = None
        self.confirm_password_var = None
        self.role_var = None

        self._create_dialog()
        self._setup_bindings()

    def _create_dialog(self):
        """Create the account creation dialog."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Create Account")
        self.dialog.configure(bg=theme_manager.theme.COLORS['bg_primary'])
        self.dialog.resizable(False, False)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # Now create StringVars with proper parent
        self.username_var = tk.StringVar(self.dialog)
        self.password_var = tk.StringVar(self.dialog)
        self.confirm_password_var = tk.StringVar(self.dialog)
        self.role_var = tk.StringVar(self.dialog, value="Viewer")

        # Main container
        main_frame = theme_manager.create_styled_frame(self.dialog)
        main_frame.pack(fill='both', expand=True, padx=30, pady=30)

        # Title
        title_label = theme_manager.create_styled_label(
            main_frame, "Create New Account", 'Heading.TLabel'
        )
        title_label.pack(pady=(0, 20))

        # Form
        self._create_form(main_frame)

        # Buttons
        self._create_buttons(main_frame)

        # Center dialog
        self._center_dialog()

    def _create_form(self, parent):
        """Create the account creation form."""
        form_frame = theme_manager.create_styled_frame(parent)
        form_frame.pack(fill='x', pady=10)

        row = 0

        # Username
        username_label = theme_manager.create_styled_label(form_frame, "Username:")
        username_label.grid(row=row, column=0, sticky='w', pady=(0, 5))
        row += 1

        self.username_entry = theme_manager.create_styled_entry(
            form_frame, textvariable=self.username_var, width=25
        )
        self.username_entry.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        row += 1

        # Password
        password_label = theme_manager.create_styled_label(form_frame, "Password:")
        password_label.grid(row=row, column=0, sticky='w', pady=(0, 5))
        row += 1

        self.password_entry = theme_manager.create_styled_entry(
            form_frame, textvariable=self.password_var, show='*', width=25
        )
        self.password_entry.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        row += 1

        # Confirm Password
        confirm_label = theme_manager.create_styled_label(form_frame, "Confirm Password:")
        confirm_label.grid(row=row, column=0, sticky='w', pady=(0, 5))
        row += 1

        self.confirm_entry = theme_manager.create_styled_entry(
            form_frame, textvariable=self.confirm_password_var, show='*', width=25
        )
        self.confirm_entry.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        row += 1

        # Role selection
        role_label = theme_manager.create_styled_label(form_frame, "Role:")
        role_label.grid(row=row, column=0, sticky='w', pady=(0, 5))
        row += 1

        role_combo = ttk.Combobox(form_frame, textvariable=self.role_var,
                                  values=['Viewer', 'Operator', 'Admin3', 'Admin2'],
                                  state='readonly', width=22)
        role_combo.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(0, 15))
        row += 1

        # Error label
        self.error_label = theme_manager.create_styled_label(
            form_frame, "", 'Secondary.TLabel'
        )
        self.error_label.configure(foreground=theme_manager.theme.COLORS['fg_error'])
        self.error_label.grid(row=row, column=0, columnspan=2, pady=(0, 10))

        form_frame.columnconfigure(0, weight=1)

    def _create_buttons(self, parent):
        """Create dialog buttons."""
        button_frame = theme_manager.create_styled_frame(parent)
        button_frame.pack(fill='x', pady=10)

        # Create button
        create_btn = theme_manager.create_primary_button(
            button_frame, "Create Account", self._handle_create
        )
        create_btn.pack(side='left', padx=(0, 10))

        # Cancel button
        cancel_btn = theme_manager.create_styled_button(
            button_frame, "Cancel", self._handle_cancel
        )
        cancel_btn.pack(side='left')

    def _setup_bindings(self):
        """Setup keyboard bindings."""
        self.dialog.bind('<Return>', lambda e: self._handle_create())
        self.dialog.bind('<Escape>', lambda e: self._handle_cancel())

        # Focus on username entry
        self.dialog.after(100, lambda: self.username_entry.focus())

    def _center_dialog(self):
        """Center dialog on parent."""
        self.dialog.update_idletasks()

        dialog_width = self.dialog.winfo_reqwidth()
        dialog_height = self.dialog.winfo_reqheight()

        parent_x = self.parent.winfo_x()
        parent_y = self.parent.winfo_y()
        parent_width = self.parent.winfo_width()
        parent_height = self.parent.winfo_height()

        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2

        self.dialog.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")

    def _handle_create(self):
        """Handle account creation."""
        username = self.username_var.get().strip()
        password = self.password_var.get()
        confirm = self.confirm_password_var.get()
        role = self.role_var.get()

        # Clear previous error
        self.error_label.configure(text="")

        # Validate input
        if not username or not password or not confirm:
            self._show_error("Please fill in all fields.")
            return

        if len(username) < 3:
            self._show_error("Username must be at least 3 characters.")
            return

        if len(password) < 4:
            self._show_error("Password must be at least 4 characters.")
            return

        if password != confirm:
            self._show_error("Passwords do not match.")
            return

        # Attempt to create account
        if self.db_manager.add_user(username, password, role):
            self.logger.info(f"Account created: {username} ({role})")
            self.dialog.destroy()
            self.on_success(username)
        else:
            self._show_error("Username already exists.")

    def _handle_cancel(self):
        """Handle cancel."""
        self.dialog.destroy()

    def _show_error(self, message: str):
        """Show error message."""
        self.error_label.configure(text=message)


class ForgotPasswordDialog:
    """Forgot password dialog (placeholder implementation)."""

    def __init__(self, parent, db_manager: DatabaseManager):
        self.parent = parent
        self.db_manager = db_manager
        self.logger = logging.getLogger(__name__)

        self._show_info()

    def _show_info(self):
        """Show information about password recovery."""
        message = (
            "Password recovery is not implemented in this demo version.\n\n"
            "In a production environment, this would:\n"
            "• Send a reset email to the user's registered email\n"
            "• Generate a temporary reset token\n"
            "• Allow secure password reset\n\n"
            "For now, please contact the system administrator\n"
            "(MallousG) to reset your password."
        )

        theme_manager.show_message(
            self.parent, "Password Recovery", message, "info"
        )