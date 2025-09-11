"""
Warehouse Management System - Roles Administration GUI Module
User management and role assignment for administrators
"""

import tkinter as tk
from tkinter import ttk
import logging
from typing import Dict, List, Optional
from theme import theme_manager
from database import DatabaseManager


class RolesAdminFrame:
    """User roles and administration interface."""

    def __init__(self, parent, db_manager: DatabaseManager, current_user: Dict):
        self.parent = parent
        self.db_manager = db_manager
        self.current_user = current_user
        self.logger = logging.getLogger(__name__)

        self.users = []
        self.selected_users = []

        # Check permissions
        if not self._can_manage_users():
            self._show_access_denied()
            return

        # Create main frame
        self.main_frame = theme_manager.create_styled_frame(parent)
        self.main_frame.pack(fill='both', expand=True, padx=10, pady=10)

        self._create_interface()
        self._load_users()

    def _can_manage_users(self) -> bool:
        """Check if current user can manage other users."""
        user_role = self.current_user['role']
        return user_role in ['Admin1', 'Admin2', 'Admin3']

    def _can_manage_role(self, target_role: str) -> bool:
        """Check if current user can manage users with target role."""
        user_role = self.current_user['role']

        # Role hierarchy
        role_levels = {
            'Admin1': 5,  # Can manage everyone
            'Admin2': 4,  # Can manage Admin3, Operator, Viewer
            'Admin3': 3,  # Can manage Operator, Viewer
            'Operator': 2,
            'Viewer': 1
        }

        current_level = role_levels.get(user_role, 0)
        target_level = role_levels.get(target_role, 0)

        return current_level > target_level

    def _show_access_denied(self):
        """Show access denied message."""
        access_frame = theme_manager.create_styled_frame(self.parent)
        access_frame.pack(fill='both', expand=True, padx=50, pady=50)

        title_label = theme_manager.create_styled_label(
            access_frame, "Access Denied", 'Heading.TLabel'
        )
        title_label.pack(pady=(50, 20))

        message_label = theme_manager.create_styled_label(
            access_frame,
            "You don't have permission to access user management.\n"
            "This feature is only available to administrators.",
            'Secondary.TLabel'
        )
        message_label.pack()

    def _create_interface(self):
        """Create the user management interface."""
        # Title
        title_label = theme_manager.create_styled_label(
            self.main_frame, "User Management & Roles", 'Heading.TLabel'
        )
        title_label.pack(pady=(0, 20))

        # Toolbar
        self._create_toolbar()

        # Users list
        self._create_users_list()

        # User details panel
        self._create_details_panel()

    def _create_toolbar(self):
        """Create management toolbar."""
        toolbar = theme_manager.create_styled_frame(self.main_frame)
        toolbar.pack(fill='x', pady=(0, 10))

        # Add User button
        add_btn = theme_manager.create_primary_button(
            toolbar, "Add User", self._add_user
        )
        add_btn.pack(side='left', padx=(0, 10))

        # Edit User button
        self.edit_btn = theme_manager.create_styled_button(
            toolbar, "Edit User", self._edit_user
        )
        self.edit_btn.pack(side='left', padx=(0, 10))
        self.edit_btn.configure(state='disabled')

        # Change Role button
        self.role_btn = theme_manager.create_styled_button(
            toolbar, "Change Role", self._change_role
        )
        self.role_btn.pack(side='left', padx=(0, 10))
        self.role_btn.configure(state='disabled')

        # Reset Password button
        self.password_btn = theme_manager.create_styled_button(
            toolbar, "Reset Password", self._reset_password
        )
        self.password_btn.pack(side='left', padx=(0, 10))
        self.password_btn.configure(state='disabled')

        # Separator
        separator = ttk.Separator(toolbar, orient='vertical')
        separator.pack(side='left', fill='y', padx=10)

        # Deactivate User button
        self.deactivate_btn = theme_manager.create_styled_button(
            toolbar, "Deactivate User", self._toggle_user_status
        )
        self.deactivate_btn.pack(side='left', padx=(0, 10))
        self.deactivate_btn.configure(state='disabled')

        # Refresh button
        refresh_btn = theme_manager.create_styled_button(
            toolbar, "Refresh", self._load_users
        )
        refresh_btn.pack(side='right')

    def _create_users_list(self):
        """Create users list view."""
        list_frame = theme_manager.create_styled_frame(self.main_frame)
        list_frame.pack(fill='both', expand=True, pady=(0, 10))

        # Create treeview
        columns = ('Username', 'Role', 'Created', 'Last Login', 'Status')
        self.users_tree = ttk.Treeview(list_frame, columns=columns, show='headings',
                                       style='Dark.Treeview', selectmode='browse')

        # Configure columns
        self.users_tree.heading('Username', text='Username')
        self.users_tree.heading('Role', text='Role')
        self.users_tree.heading('Created', text='Created Date')
        self.users_tree.heading('Last Login', text='Last Login')
        self.users_tree.heading('Status', text='Status')

        # Column widths
        self.users_tree.column('Username', width=150)
        self.users_tree.column('Role', width=100)
        self.users_tree.column('Created', width=120)
        self.users_tree.column('Last Login', width=120)
        self.users_tree.column('Status', width=80)

        # Scrollbar
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical',
                                  command=self.users_tree.yview,
                                  style='Dark.Vertical.TScrollbar')
        self.users_tree.configure(yscrollcommand=scrollbar.set)

        # Pack elements
        self.users_tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # Bind events
        self.users_tree.bind('<<TreeviewSelect>>', self._on_user_selection)
        self.users_tree.bind('<Double-1>', self._on_user_double_click)

    def _create_details_panel(self):
        """Create user details panel."""
        details_frame = theme_manager.create_styled_frame(self.main_frame)
        details_frame.pack(fill='x', pady=(0, 10))

        # Title
        details_title = theme_manager.create_styled_label(
            details_frame, "User Details", 'Subheading.TLabel'
        )
        details_title.pack(anchor='w', pady=(0, 10))

        # Details content
        self.details_content = theme_manager.create_styled_frame(details_frame)
        self.details_content.pack(fill='x')

        # Initially empty
        self._update_details_panel(None)

    def _load_users(self):
        """Load users from database."""
        try:
            # Get all users - this would need to be implemented in DatabaseManager
            # For now, we'll simulate it
            self.users = self._get_all_users()
            self._update_users_tree()
            self.logger.info(f"Loaded {len(self.users)} users")
        except Exception as e:
            self.logger.error(f"Failed to load users: {e}")
            theme_manager.show_message(
                self.main_frame, "Error",
                f"Failed to load users: {e}", "error"
            )

    def _get_all_users(self) -> List[Dict]:
        """Get all users from database."""
        # This is a simplified implementation
        # In real implementation, this would be a method in DatabaseManager
        try:
            import sqlite3
            with sqlite3.connect(self.db_manager.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, username, role, created_date, last_login, is_active
                    FROM users
                    ORDER BY role DESC, username
                """)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"Failed to get users: {e}")
            return []

    def _update_users_tree(self):
        """Update users treeview."""
        # Clear existing items
        for item in self.users_tree.get_children():
            self.users_tree.delete(item)

        # Add users
        for user in self.users:
            # Format dates
            created_date = user['created_date'][:10] if user['created_date'] else ""
            last_login = user['last_login'][:10] if user['last_login'] else "Never"

            # Status
            status = "Active" if user['is_active'] else "Inactive"

            # Color coding based on role
            tags = []
            if user['role'] == 'Admin1':
                tags = ['admin1']
            elif user['role'] == 'Admin2':
                tags = ['admin2']
            elif user['role'] == 'Admin3':
                tags = ['admin3']

            self.users_tree.insert('', 'end', values=(
                user['username'],
                user['role'],
                created_date,
                last_login,
                status
            ), tags=tags)

        # Configure tag colors
        self.users_tree.tag_configure('admin1', foreground=theme_manager.theme.COLORS['fg_error'])
        self.users_tree.tag_configure('admin2', foreground=theme_manager.theme.COLORS['fg_warning'])
        self.users_tree.tag_configure('admin3', foreground=theme_manager.theme.COLORS['fg_secondary'])

    def _on_user_selection(self, event):
        """Handle user selection."""
        selection = self.users_tree.selection()
        if selection:
            item = selection[0]
            values = self.users_tree.item(item, 'values')
            if values:
                username = values[0]
                user = next((u for u in self.users if u['username'] == username), None)
                self._update_details_panel(user)
                self._update_button_states(user)
        else:
            self._update_details_panel(None)
            self._update_button_states(None)

    def _update_details_panel(self, user: Optional[Dict]):
        """Update user details panel."""
        # Clear existing content
        for widget in self.details_content.winfo_children():
            widget.destroy()

        if not user:
            no_selection_label = theme_manager.create_styled_label(
                self.details_content, "No user selected", 'Secondary.TLabel'
            )
            no_selection_label.pack()
            return

        # User info grid
        info_frame = theme_manager.create_styled_frame(self.details_content)
        info_frame.pack(fill='x')

        row = 0

        # Username
        username_label = theme_manager.create_styled_label(info_frame, "Username:")
        username_label.grid(row=row, column=0, sticky='w', padx=(0, 10))

        username_value = theme_manager.create_styled_label(
            info_frame, user['username'], 'Subheading.TLabel'
        )
        username_value.grid(row=row, column=1, sticky='w')
        row += 1

        # Role
        role_label = theme_manager.create_styled_label(info_frame, "Role:")
        role_label.grid(row=row, column=0, sticky='w', padx=(0, 10), pady=(5, 0))

        role_value = theme_manager.create_styled_label(info_frame, user['role'])
        role_value.grid(row=row, column=1, sticky='w', pady=(5, 0))
        row += 1

        # Created date
        created_label = theme_manager.create_styled_label(info_frame, "Created:")
        created_label.grid(row=row, column=0, sticky='w', padx=(0, 10), pady=(5, 0))

        created_date = user['created_date'][:19] if user['created_date'] else "Unknown"
        created_value = theme_manager.create_styled_label(info_frame, created_date)
        created_value.grid(row=row, column=1, sticky='w', pady=(5, 0))
        row += 1

        # Last login
        login_label = theme_manager.create_styled_label(info_frame, "Last Login:")
        login_label.grid(row=row, column=0, sticky='w', padx=(0, 10), pady=(5, 0))

        last_login = user['last_login'][:19] if user['last_login'] else "Never"
        login_value = theme_manager.create_styled_label(info_frame, last_login)
        login_value.grid(row=row, column=1, sticky='w', pady=(5, 0))
        row += 1

        # Status
        status_label = theme_manager.create_styled_label(info_frame, "Status:")
        status_label.grid(row=row, column=0, sticky='w', padx=(0, 10), pady=(5, 0))

        status_text = "Active" if user['is_active'] else "Inactive"
        status_color = theme_manager.theme.COLORS['fg_success'] if user['is_active'] else theme_manager.theme.COLORS[
            'fg_error']
        status_value = theme_manager.create_styled_label(info_frame, status_text)
        status_value.configure(foreground=status_color)
        status_value.grid(row=row, column=1, sticky='w', pady=(5, 0))

        # Role permissions
        row += 2
        perms_label = theme_manager.create_styled_label(
            info_frame, "Role Permissions:", 'Subheading.TLabel'
        )
        perms_label.grid(row=row, column=0, columnspan=2, sticky='w', pady=(10, 5))
        row += 1

        permissions = self._get_role_permissions(user['role'])
        perms_text = "\n".join(f"• {perm}" for perm in permissions)
        perms_value = theme_manager.create_styled_label(
            info_frame, perms_text, 'Secondary.TLabel'
        )
        perms_value.grid(row=row, column=0, columnspan=2, sticky='w')

    def _get_role_permissions(self, role: str) -> List[str]:
        """Get permissions for a role."""
        permissions = {
            'Admin1': [
                "Full system access",
                "Manage all users and roles",
                "System configuration",
                "Database management",
                "Audit trail access"
            ],
            'Admin2': [
                "Manage inventory and customers",
                "Manage lower-level users (Admin3, Operator, Viewer)",
                "Import/Export data",
                "Generate reports"
            ],
            'Admin3': [
                "Manage inventory and customers",
                "Manage Operators and Viewers",
                "Basic reporting"
            ],
            'Operator': [
                "Add/Edit inventory items",
                "Assign items to customers",
                "View customer information",
                "Limited reporting"
            ],
            'Viewer': [
                "View inventory",
                "View customer information",
                "Read-only access"
            ]
        }
        return permissions.get(role, ["No permissions defined"])

    def _update_button_states(self, user: Optional[Dict]):
        """Update button states based on selected user."""
        if not user:
            self.edit_btn.configure(state='disabled')
            self.role_btn.configure(state='disabled')
            self.password_btn.configure(state='disabled')
            self.deactivate_btn.configure(state='disabled')
            return

        # Check if current user can manage this user
        can_manage = self._can_manage_role(user['role'])

        # Can't manage yourself (except Admin1)
        is_self = user['username'] == self.current_user['username']
        if is_self and self.current_user['role'] != 'Admin1':
            can_manage = False

        # Can't manage Admin1 (except by Admin1 themselves)
        if user['role'] == 'Admin1' and self.current_user['role'] != 'Admin1':
            can_manage = False

        state = 'normal' if can_manage else 'disabled'
        self.edit_btn.configure(state=state)
        self.role_btn.configure(state=state)
        self.password_btn.configure(state=state)

        # Deactivate button - special rules
        can_deactivate = can_manage and not is_self and user['role'] != 'Admin1'
        deactivate_state = 'normal' if can_deactivate else 'disabled'
        self.deactivate_btn.configure(state=deactivate_state)

        # Update deactivate button text
        if user['is_active']:
            self.deactivate_btn.configure(text="Deactivate User")
        else:
            self.deactivate_btn.configure(text="Activate User")

    def _on_user_double_click(self, event):
        """Handle double-click on user."""
        selection = self.users_tree.selection()
        if selection:
            self._edit_user()

    def _add_user(self):
        """Add new user."""
        UserEditDialog(self.main_frame, self.db_manager, None,
                       self.current_user, self._on_user_saved)

    def _edit_user(self):
        """Edit selected user."""
        selection = self.users_tree.selection()
        if selection:
            item = selection[0]
            values = self.users_tree.item(item, 'values')
            if values:
                username = values[0]
                user = next((u for u in self.users if u['username'] == username), None)
                if user and self._can_manage_role(user['role']):
                    UserEditDialog(self.main_frame, self.db_manager, user,
                                   self.current_user, self._on_user_saved)

    def _change_role(self):
        """Change role of selected user."""
        selection = self.users_tree.selection()
        if selection:
            item = selection[0]
            values = self.users_tree.item(item, 'values')
            if values:
                username = values[0]
                user = next((u for u in self.users if u['username'] == username), None)
                if user and self._can_manage_role(user['role']):
                    ChangeRoleDialog(self.main_frame, self.db_manager, user,
                                     self.current_user, self._on_user_saved)

    def _reset_password(self):
        """Reset password for selected user."""
        selection = self.users_tree.selection()
        if selection:
            item = selection[0]
            values = self.users_tree.item(item, 'values')
            if values:
                username = values[0]
                user = next((u for u in self.users if u['username'] == username), None)
                if user and self._can_manage_role(user['role']):
                    ResetPasswordDialog(self.main_frame, self.db_manager, user,
                                        self.current_user, self._on_user_saved)

    def _toggle_user_status(self):
        """Toggle active status of selected user."""
        selection = self.users_tree.selection()
        if selection:
            item = selection[0]
            values = self.users_tree.item(item, 'values')
            if values:
                username = values[0]
                user = next((u for u in self.users if u['username'] == username), None)
                if user and self._can_manage_role(user['role']):
                    action = "deactivate" if user['is_active'] else "activate"
                    message = f"Are you sure you want to {action} user '{username}'?"

                    if theme_manager.confirm_action(self.main_frame, "Confirm Action", message):
                        # Update user status in database
                        new_status = not user['is_active']
                        if self._update_user_status(user['id'], new_status):
                            theme_manager.show_message(
                                self.main_frame, "Success",
                                f"User {action}d successfully.", "info"
                            )
                            self._load_users()

    def _update_user_status(self, user_id: int, is_active: bool) -> bool:
        """Update user active status in database."""
        try:
            import sqlite3
            with sqlite3.connect(self.db_manager.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE users SET is_active = ? WHERE id = ?
                """, (is_active, user_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            self.logger.error(f"Failed to update user status: {e}")
            return False

    def _on_user_saved(self):
        """Callback when user is saved."""
        self._load_users()


class UserEditDialog:
    """Dialog for editing user information."""

    def __init__(self, parent, db_manager: DatabaseManager, user: Optional[Dict],
                 current_user: Dict, on_success: callable):
        self.parent = parent
        self.db_manager = db_manager
        self.user = user
        self.current_user = current_user
        self.on_success = on_success
        self.logger = logging.getLogger(__name__)

        # For now, show placeholder
        action = "Edit" if user else "Add"
        theme_manager.show_message(
            parent, f"{action} User",
            f"{action} user functionality will be implemented in the next update.", "info"
        )


class ChangeRoleDialog:
    """Dialog for changing user role."""

    def __init__(self, parent, db_manager: DatabaseManager, user: Dict,
                 current_user: Dict, on_success: callable):
        self.parent = parent
        self.db_manager = db_manager
        self.user = user
        self.current_user = current_user
        self.on_success = on_success
        self.logger = logging.getLogger(__name__)

        # For now, show placeholder
        theme_manager.show_message(
            parent, "Change Role",
            "Role change functionality will be implemented in the next update.", "info"
        )


class ResetPasswordDialog:
    """Dialog for resetting user password."""

    def __init__(self, parent, db_manager: DatabaseManager, user: Dict,
                 current_user: Dict, on_success: callable):
        self.parent = parent
        self.db_manager = db_manager
        self.user = user
        self.current_user = current_user
        self.on_success = on_success
        self.logger = logging.getLogger(__name__)

        # For now, show placeholder
        theme_manager.show_message(
            parent, "Reset Password",
            "Password reset functionality will be implemented in the next update.", "info"
        )