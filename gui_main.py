"""
Warehouse Management System - Main GUI Module (Fixed)
Main application window with proper cleanup and error handling
"""

import tkinter as tk
from tkinter import ttk, messagebox
import logging
import sys
import os
from typing import Dict, Optional
from datetime import datetime
import threading

# Import our modules
from theme import theme_manager
from database import DatabaseManager
from login_gui import LoginDialog
from customer_gui import CustomerManagementFrame
from material_gui import MaterialManagementFrame
from roles_admin_gui import RolesAdminFrame


class MainApplication:
    """Main warehouse management application with proper cleanup."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.db_manager = None
        self.current_user = None
        self.root = None
        self._update_clock_id = None
        self._active_frames = []

        # Initialize logging
        self._setup_logging()

        # Initialize database
        self._init_database()

        # Create main window
        self._create_main_window()

        # Show login dialog
        self._show_login()

    def _setup_logging(self):
        """Setup application logging."""
        # Create logs directory if it doesn't exist
        os.makedirs('logs', exist_ok=True)

        # Configure logging
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        logging.basicConfig(
            level=logging.INFO,
            format=log_format,
            handlers=[
                logging.FileHandler(f'logs/warehouse_{datetime.now().strftime("%Y%m%d")}.log'),
                logging.StreamHandler()
            ]
        )

        self.logger.info("Application starting...")

    def _init_database(self):
        """Initialize database connection."""
        try:
            self.db_manager = DatabaseManager()
            self.logger.info("Database initialized successfully")
        except Exception as e:
            self.logger.error(f"Database initialization failed: {e}")
            messagebox.showerror("Database Error",
                                 f"Failed to initialize database: {e}")
            sys.exit(1)

    def _create_main_window(self):
        """Create and configure main application window."""
        self.root = tk.Tk()
        self.root.title("Warehouse Management System - Professional Edition")
        self.root.state('zoomed')  # Maximize window on Windows

        # Apply ultra dark theme
        theme_manager.apply_to_window(self.root)

        # Set window icon (if available)
        try:
            # Uncomment and provide icon path if available
            # self.root.iconbitmap('assets/icon.ico')
            pass
        except:
            pass

        # Create menu bar
        self._create_menu_bar()

        # Create main container (initially hidden)
        self.main_container = theme_manager.create_styled_frame(self.root)
        # Don't pack yet - will be shown after login

        # Create welcome/locked screen
        self._create_locked_screen()

        # Bind window events
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # Center window
        self._center_window()

    def _create_menu_bar(self):
        """Create application menu bar with dark theme."""
        self.menubar = tk.Menu(self.root,
                               bg=theme_manager.theme.COLORS['bg_secondary'],
                               fg=theme_manager.theme.COLORS['fg_primary'],
                               activebackground=theme_manager.theme.COLORS['bg_selected'],
                               activeforeground=theme_manager.theme.COLORS['fg_primary'],
                               borderwidth=0)
        self.root.config(menu=self.menubar)

        # File menu
        file_menu = tk.Menu(self.menubar, tearoff=0,
                            bg=theme_manager.theme.COLORS['bg_secondary'],
                            fg=theme_manager.theme.COLORS['fg_primary'],
                            activebackground=theme_manager.theme.COLORS['bg_selected'],
                            activeforeground=theme_manager.theme.COLORS['fg_primary'])
        self.menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Export Data...", command=self._export_data)
        file_menu.add_command(label="Import Data...", command=self._import_data)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_closing)

        # Edit menu
        edit_menu = tk.Menu(self.menubar, tearoff=0,
                            bg=theme_manager.theme.COLORS['bg_secondary'],
                            fg=theme_manager.theme.COLORS['fg_primary'],
                            activebackground=theme_manager.theme.COLORS['bg_selected'],
                            activeforeground=theme_manager.theme.COLORS['fg_primary'])
        self.menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(label="Preferences...", command=self._show_preferences)

        # View menu
        view_menu = tk.Menu(self.menubar, tearoff=0,
                            bg=theme_manager.theme.COLORS['bg_secondary'],
                            fg=theme_manager.theme.COLORS['fg_primary'],
                            activebackground=theme_manager.theme.COLORS['bg_selected'],
                            activeforeground=theme_manager.theme.COLORS['fg_primary'])
        self.menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Refresh All", command=self._refresh_all)
        view_menu.add_command(label="Search...", command=self._global_search)

        # Tools menu
        tools_menu = tk.Menu(self.menubar, tearoff=0,
                             bg=theme_manager.theme.COLORS['bg_secondary'],
                             fg=theme_manager.theme.COLORS['fg_primary'],
                             activebackground=theme_manager.theme.COLORS['bg_selected'],
                             activeforeground=theme_manager.theme.COLORS['fg_primary'])
        self.menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Database Backup...", command=self._backup_database)
        tools_menu.add_command(label="System Info", command=self._show_system_info)

        # Admin menu (will be shown only for admins)
        self.admin_menu = tk.Menu(self.menubar, tearoff=0,
                                  bg=theme_manager.theme.COLORS['bg_secondary'],
                                  fg=theme_manager.theme.COLORS['fg_primary'],
                                  activebackground=theme_manager.theme.COLORS['bg_selected'],
                                  activeforeground=theme_manager.theme.COLORS['fg_primary'])

        # Help menu
        help_menu = tk.Menu(self.menubar, tearoff=0,
                            bg=theme_manager.theme.COLORS['bg_secondary'],
                            fg=theme_manager.theme.COLORS['fg_primary'],
                            activebackground=theme_manager.theme.COLORS['bg_selected'],
                            activeforeground=theme_manager.theme.COLORS['fg_primary'])
        self.menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="User Manual", command=self._show_help)
        help_menu.add_command(label="Keyboard Shortcuts", command=self._show_shortcuts)
        help_menu.add_separator()
        help_menu.add_command(label="About", command=self._show_about)

        # Initially disable all menus except Help
        self._set_menu_state('disabled')

    def _create_locked_screen(self):
        """Create locked screen shown before login."""
        self.locked_frame = theme_manager.create_styled_frame(self.root)
        self.locked_frame.pack(fill='both', expand=True)

        # Center content
        center_frame = theme_manager.create_styled_frame(self.locked_frame)
        center_frame.place(relx=0.5, rely=0.5, anchor='center')

        # Logo/Title
        title_label = theme_manager.create_styled_label(
            center_frame, "Warehouse Management System", 'Heading.TLabel'
        )
        title_label.pack(pady=(0, 10))

        # Subtitle
        subtitle_label = theme_manager.create_styled_label(
            center_frame, "Professional Inventory & Customer Management", 'Secondary.TLabel'
        )
        subtitle_label.pack(pady=(0, 40))

        # Login button
        login_btn = theme_manager.create_primary_button(
            center_frame, "🔐 Login to Continue", self._show_login
        )
        login_btn.pack(pady=10)

        # Version info
        version_label = theme_manager.create_styled_label(
            center_frame, "Version 1.0.0 - Ultra Dark Edition", 'Small.TLabel'
        )
        version_label.pack(pady=(40, 0))

    def _create_main_interface(self):
        """Create main interface after successful login."""
        # Clear locked screen
        if hasattr(self, 'locked_frame') and self.locked_frame:
            self.locked_frame.destroy()

        # Show main container
        self.main_container.pack(fill='both', expand=True)

        # Create header
        self._create_header()

        # Create main notebook with tabs
        self._create_notebook()

        # Create status bar
        self._create_status_bar()

        # Enable menus
        self._set_menu_state('normal')

        # Add admin menu if user is admin
        if self.current_user['role'] in ['Admin1', 'Admin2', 'Admin3']:
            self._setup_admin_menu()

        self.logger.info(f"Main interface created for user: {self.current_user['username']}")

    def _create_header(self):
        """Create application header with user info."""
        header_frame = theme_manager.create_styled_frame(self.main_container)
        header_frame.pack(fill='x', padx=10, pady=(10, 0))

        # App title
        title_label = theme_manager.create_styled_label(
            header_frame, "📦 Warehouse Management System", 'Heading.TLabel'
        )
        title_label.pack(side='left')

        # User info
        user_frame = theme_manager.create_styled_frame(header_frame)
        user_frame.pack(side='right')

        # User details
        user_text = f"👤 {self.current_user['username']} ({self.current_user['role']})"
        user_label = theme_manager.create_styled_label(user_frame, user_text, 'Secondary.TLabel')
        user_label.pack(side='left', padx=(0, 20))

        # Logout button
        logout_btn = theme_manager.create_styled_button(
            user_frame, "⚡ Logout", self._logout
        )
        logout_btn.pack(side='left')

    def _create_notebook(self):
        """Create main notebook with application tabs."""
        self.notebook = ttk.Notebook(self.main_container, style='Dark.TNotebook')
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)

        # Customers tab
        customers_container = theme_manager.create_styled_frame(self.notebook)
        self.customers_frame = CustomerManagementFrame(
            customers_container, self.db_manager, self.current_user
        )
        self._active_frames.append(self.customers_frame)
        self.notebook.add(customers_container, text="👥 Customers")

        # Inventory tab
        inventory_container = theme_manager.create_styled_frame(self.notebook)
        self.inventory_frame = MaterialManagementFrame(
            inventory_container, self.db_manager, self.current_user, is_used=False
        )
        self._active_frames.append(self.inventory_frame)
        self.notebook.add(inventory_container, text="📦 Inventory")

        # Used Inventory tab
        used_container = theme_manager.create_styled_frame(self.notebook)
        self.used_inventory_frame = MaterialManagementFrame(
            used_container, self.db_manager, self.current_user, is_used=True
        )
        self._active_frames.append(self.used_inventory_frame)
        self.notebook.add(used_container, text="♻️ Used Inventory")

        # Reports tab
        self._create_reports_tab()

        # Bind tab change event
        self.notebook.bind('<<NotebookTabChanged>>', self._on_tab_changed)

    def _create_reports_tab(self):
        """Create reports tab (placeholder)."""
        reports_frame = theme_manager.create_styled_frame(self.notebook)
        self.notebook.add(reports_frame, text="📊 Reports")

        # Placeholder content
        placeholder_frame = theme_manager.create_styled_frame(reports_frame)
        placeholder_frame.pack(fill='both', expand=True, padx=50, pady=50)

        title_label = theme_manager.create_styled_label(
            placeholder_frame, "📊 Reports & Analytics", 'Heading.TLabel'
        )
        title_label.pack(pady=(0, 20))

        desc_label = theme_manager.create_styled_label(
            placeholder_frame,
            "This section will include:\n\n"
            "• Inventory reports\n"
            "• Customer assignment history\n"
            "• Usage statistics\n"
            "• Custom report builder\n"
            "• Export functionality\n\n"
            "Coming in the next update!",
            'Secondary.TLabel'
        )
        desc_label.pack()

    def _create_status_bar(self):
        """Create bottom status bar."""
        self.status_bar = theme_manager.create_styled_frame(self.main_container)
        self.status_bar.pack(fill='x', side='bottom')

        self.status_label = theme_manager.create_styled_label(
            self.status_bar, "Ready", 'Small.TLabel'
        )
        self.status_label.pack(side='left', padx=10, pady=5)

        # Connection status
        db_status = "🟢 Database Connected" if self.db_manager else "🔴 Database Error"
        self.db_status_label = theme_manager.create_styled_label(
            self.status_bar, db_status, 'Small.TLabel'
        )
        self.db_status_label.pack(side='right', padx=10, pady=5)

        # Current time
        self.time_label = theme_manager.create_styled_label(
            self.status_bar, "", 'Small.TLabel'
        )
        self.time_label.pack(side='right', padx=(0, 10), pady=5)

        self._update_clock()

    def _setup_admin_menu(self):
        """Setup admin menu for admin users."""
        self.menubar.add_cascade(label="Administration", menu=self.admin_menu)

        self.admin_menu.add_command(label="User Management", command=self._show_user_management)
        self.admin_menu.add_command(label="System Settings", command=self._show_system_settings)
        self.admin_menu.add_separator()
        self.admin_menu.add_command(label="Audit Trail", command=self._show_audit_trail)
        self.admin_menu.add_command(label="Database Maintenance", command=self._show_db_maintenance)

    def _center_window(self):
        """Center the main window on screen."""
        self.root.update_idletasks()

        # Get screen dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        # Calculate position for center
        window_width = int(screen_width * 0.9)  # 90% of screen width
        window_height = int(screen_height * 0.9)  # 90% of screen height

        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2

        self.root.geometry(f"{window_width}x{window_height}+{x}+{y}")

    def _show_login(self):
        """Show login dialog."""
        LoginDialog(self.root, self.db_manager, self._on_login_success)

    def _on_login_success(self, user: Dict):
        """Handle successful login."""
        self.current_user = user
        self.logger.info(f"User logged in: {user['username']} ({user['role']})")

        # Log audit entry
        self.db_manager.log_audit(
            user['username'], "Login", "users", str(user['id'])
        )

        # Create main interface
        self._create_main_interface()

    def _logout(self):
        """Handle user logout with proper cleanup."""
        if self.current_user:
            # Log audit entry
            self.db_manager.log_audit(
                self.current_user['username'], "Logout", "users", str(self.current_user['id'])
            )

            self.logger.info(f"User logged out: {self.current_user['username']}")
            self.current_user = None

        # Stop clock updates
        if self._update_clock_id:
            self.root.after_cancel(self._update_clock_id)
            self._update_clock_id = None

        # Clean up frames
        for frame in self._active_frames:
            try:
                if hasattr(frame, 'cleanup'):
                    frame.cleanup()
            except:
                pass
        self._active_frames.clear()

        # Clear main interface
        if hasattr(self, 'main_container') and self.main_container:
            for widget in self.main_container.winfo_children():
                widget.destroy()
            self.main_container.destroy()
            self.main_container = theme_manager.create_styled_frame(self.root)

        # Remove admin menu if present
        try:
            self.menubar.delete("Administration")
        except:
            pass

        # Disable menus
        self._set_menu_state('disabled')

        # Clean up theme manager widgets
        theme_manager.cleanup_widgets()

        # Show locked screen again
        self._create_locked_screen()

    def _set_menu_state(self, state: str):
        """Enable or disable menu items."""
        menus_to_toggle = ["File", "Edit", "View", "Tools"]

        for i in range(self.menubar.index('end') + 1):
            try:
                menu_label = self.menubar.entrycget(i, 'label')
                if menu_label in menus_to_toggle:
                    self.menubar.entryconfig(i, state=state)
            except:
                continue

    def _on_tab_changed(self, event):
        """Handle notebook tab change."""
        try:
            selection = event.widget.select()
            tab_text = event.widget.tab(selection, "text")
            self._update_status(f"Switched to {tab_text} tab")
        except:
            pass

    def _update_status(self, message: str):
        """Update status bar message safely."""
        try:
            if hasattr(self, 'status_label') and self.status_label.winfo_exists():
                self.status_label.configure(text=message)
                # Clear status after 5 seconds
                self.root.after(5000, lambda: self._safe_update_status("Ready"))
        except:
            pass

    def _safe_update_status(self, message: str):
        """Safely update status without errors."""
        try:
            if hasattr(self, 'status_label') and self.status_label.winfo_exists():
                self.status_label.configure(text=message)
        except:
            pass

    def _update_clock(self):
        """Update clock in status bar safely."""
        try:
            if hasattr(self, 'time_label') and self.time_label.winfo_exists():
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.time_label.configure(text=f"🕐 {current_time}")
                # Schedule next update
                self._update_clock_id = self.root.after(1000, self._update_clock)
        except:
            self._update_clock_id = None

    def _on_closing(self):
        """Handle application closing with proper cleanup."""
        if self.current_user:
            # Log logout
            self.db_manager.log_audit(
                self.current_user['username'], "Application Exit", "users", str(self.current_user['id'])
            )

        self.logger.info("Application closing...")

        # Stop clock updates
        if self._update_clock_id:
            self.root.after_cancel(self._update_clock_id)

        # Clean up frames
        for frame in self._active_frames:
            try:
                if hasattr(frame, 'cleanup'):
                    frame.cleanup()
            except:
                pass

        # Close database connection
        if self.db_manager:
            self.db_manager.close()

        self.root.destroy()

    # Menu command handlers
    def _export_data(self):
        """Handle export data command."""
        theme_manager.show_message(
            self.root, "Export Data",
            "Data export functionality will be implemented soon.", "info"
        )

    def _import_data(self):
        """Handle import data command."""
        theme_manager.show_message(
            self.root, "Import Data",
            "Data import functionality will be implemented soon.", "info"
        )

    def _show_preferences(self):
        """Show application preferences."""
        theme_manager.show_message(
            self.root, "Preferences",
            "Preferences dialog will be implemented soon.", "info"
        )

    def _refresh_all(self):
        """Refresh all data in current tab."""
        try:
            current_tab = self.notebook.select()
            tab_text = self.notebook.tab(current_tab, "text")
            self._update_status(f"Refreshing {tab_text} data...")

            # Here you would call refresh methods on the current tab's frame
            self.root.after(1000, lambda: self._safe_update_status(f"{tab_text} data refreshed"))
        except:
            pass

    def _global_search(self):
        """Show global search dialog."""
        theme_manager.show_message(
            self.root, "Global Search",
            "Global search functionality will be implemented soon.", "info"
        )

    def _backup_database(self):
        """Create database backup."""
        theme_manager.show_message(
            self.root, "Database Backup",
            "Database backup functionality will be implemented soon.", "info"
        )

    def _show_system_info(self):
        """Show system information."""
        info = f"""Warehouse Management System
Version: 1.0.0
Database: SQLite
Python Version: {sys.version.split()[0]}
Platform: {sys.platform}

Current User: {self.current_user['username'] if self.current_user else 'Not logged in'}
Role: {self.current_user['role'] if self.current_user else 'N/A'}
"""

        theme_manager.show_message(self.root, "System Information", info, "info")

    def _show_user_management(self):
        """Show user management interface."""
        # Create new window for user management
        user_mgmt_window = tk.Toplevel(self.root)
        user_mgmt_window.title("User Management")
        user_mgmt_window.configure(bg=theme_manager.theme.COLORS['bg_primary'])
        user_mgmt_window.geometry("1000x700")
        user_mgmt_window.transient(self.root)

        # Apply dark theme to window
        for option in ['*Background', '*Foreground', '*selectBackground', '*selectForeground']:
            user_mgmt_window.option_add(option,
                                        theme_manager.theme.COLORS.get(option.replace('*', '').lower(),
                                                                       theme_manager.theme.COLORS['bg_primary']))

        # Create user management frame
        RolesAdminFrame(user_mgmt_window, self.db_manager, self.current_user)

    def _show_system_settings(self):
        """Show system settings."""
        theme_manager.show_message(
            self.root, "System Settings",
            "System settings will be implemented soon.", "info"
        )

    def _show_audit_trail(self):
        """Show audit trail."""
        theme_manager.show_message(
            self.root, "Audit Trail",
            "Audit trail viewer will be implemented soon.", "info"
        )

    def _show_db_maintenance(self):
        """Show database maintenance tools."""
        theme_manager.show_message(
            self.root, "Database Maintenance",
            "Database maintenance tools will be implemented soon.", "info"
        )

    def _show_help(self):
        """Show user manual."""
        help_text = """Warehouse Management System - Quick Start Guide

NAVIGATION:
• Use the tabs at the top to switch between sections
• Customers: Manage customer information and history
• Inventory: Manage new/current inventory items
• Used Inventory: Manage used/returned items
• Reports: View analytics and generate reports

KEYBOARD SHORTCUTS:
• Ctrl+N: Add new item (context-sensitive)
• Ctrl+E: Edit selected item
• Ctrl+D: Delete selected item
• Ctrl+F: Search/Filter
• F5: Refresh current view
• Ctrl+Q: Logout

CUSTOMER MANAGEMENT:
• Each customer has a unique 4-digit PIN
• Use PIN or name to search customers
• View assignment history for each customer

INVENTORY MANAGEMENT:
• Switch between Grid and List views
• Use filters to narrow down results
• Bulk operations available for multiple items
• Compare up to 3 items side-by-side

For more detailed information, contact your system administrator.
"""

        theme_manager.show_message(self.root, "User Manual", help_text, "info")

    def _show_shortcuts(self):
        """Show keyboard shortcuts."""
        shortcuts_text = """Keyboard Shortcuts

GENERAL:
Ctrl+N - Add new item
Ctrl+E - Edit selected item
Ctrl+D - Delete selected item
Ctrl+F - Search/Filter
Ctrl+A - Select all
Ctrl+Q - Logout
F5 - Refresh current view
Esc - Close dialog/Cancel

NAVIGATION:
Ctrl+1 - Switch to Customers tab
Ctrl+2 - Switch to Inventory tab
Ctrl+3 - Switch to Used Inventory tab
Ctrl+4 - Switch to Reports tab

SELECTION:
Click - Select single item
Ctrl+Click - Add to selection
Shift+Click - Select range
Drag - Select area (Grid view)

VIEW:
Ctrl+G - Grid view
Ctrl+L - List view
Ctrl+= - Zoom in
Ctrl+- - Zoom out
"""

        theme_manager.show_message(self.root, "Keyboard Shortcuts", shortcuts_text, "info")

    def _show_about(self):
        """Show about dialog."""
        about_text = """Warehouse Management System
Version 1.0.0 - Ultra Dark Edition

A professional inventory and customer management solution.

Features:
• Customer management with 4-digit PIN system
• Inventory tracking with serial numbers
• Role-based user access control
• Import/Export capabilities
• Audit trail and reporting
• Ultra dark theme for eye comfort

Developed with Python and Tkinter
© 2024 Warehouse Management Solutions

For support, contact your system administrator.
"""

        theme_manager.show_message(self.root, "About", about_text, "info")

    def run(self):
        """Start the application."""
        try:
            self.root.mainloop()
        except Exception as e:
            self.logger.error(f"Application error: {e}")
            messagebox.showerror("Application Error", f"An error occurred: {e}")
        finally:
            if self.db_manager:
                self.db_manager.close()


def main():
    """Main entry point."""
    try:
        app = MainApplication()
        app.run()
    except Exception as e:
        logging.error(f"Failed to start application: {e}")
        messagebox.showerror("Startup Error", f"Failed to start application: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()