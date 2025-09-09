"""
Warehouse Management System
A professional inventory and customer management solution

Version: 1.0.0
Author: MallousG
License: Proprietary

This package provides a complete warehouse management system with:
- Customer management with 4-digit PIN system
- Inventory tracking with serial numbers
- Role-based user access control
- Import/Export capabilities
- Audit trail and reporting
- Professional dark theme interface
"""

__version__ = "1.0.0"
__author__ = "MallousG"
__email__ = "admin@warehouse-system.local"
__license__ = "Proprietary"

# Module imports for easier access
try:
    from .database import DatabaseManager
    from .theme import theme_manager, DarkTheme, ThemeManager
    from .gui_main import MainApplication

    __all__ = [
        'DatabaseManager',
        'theme_manager',
        'DarkTheme',
        'ThemeManager',
        'MainApplication'
    ]

except ImportError:
    # Handle import errors gracefully during development
    pass

# Application metadata
APP_NAME = "Warehouse Management System"
APP_VERSION = __version__
APP_DESCRIPTION = "Professional Inventory & Customer Management"

# Database schema version (for migrations)
DB_SCHEMA_VERSION = "1.0"

# Default configuration
DEFAULT_CONFIG = {
    'database': {
        'name': 'warehouse.db',
        'backup_interval': 24,  # hours
        'auto_backup': True
    },
    'ui': {
        'theme': 'dark',
        'auto_refresh': True,
        'refresh_interval': 300,  # seconds
        'default_view': 'grid'
    },
    'security': {
        'session_timeout': 3600,  # seconds
        'max_login_attempts': 5,
        'password_min_length': 4
    },
    'logging': {
        'level': 'INFO',
        'max_file_size': '10MB',
        'backup_count': 5
    }
}


def get_version():
    """Get application version."""
    return __version__


def get_app_info():
    """Get application information."""
    return {
        'name': APP_NAME,
        'version': APP_VERSION,
        'description': APP_DESCRIPTION,
        'author': __author__,
        'license': __license__
    }
