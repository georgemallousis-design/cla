# Warehouse Management System

A professional, full-featured inventory and customer management application built with Python and Tkinter. Features a modern dark theme interface, role-based access control, and comprehensive data management capabilities.

## 🚀 Features

### Core Functionality
- **Customer Management**: 4-digit PIN system for unique customer identification
- **Inventory Management**: Track new and used inventory with serial numbers
- **Role-Based Access**: 5-tier user role system (Admin1, Admin2, Admin3, Operator, Viewer)
- **Professional UI**: Modern dark theme with grid/list views
- **Import/Export**: CSV and Excel support for bulk operations
- **Audit Trail**: Complete logging of user actions and changes

### Advanced Features
- **Multi-Select Operations**: Drag-select, Ctrl+click, and bulk actions
- **Product Comparison**: Side-by-side comparison of up to 3 products
- **Smart Search**: Fuzzy search with advanced filtering options
- **Session Management**: Secure login with session timeout
- **Database Management**: SQLite with foreign keys and indexing
- **Responsive Design**: Scales beautifully across different screen sizes

## 📋 System Requirements

- **Operating System**: Windows 10/11, macOS 10.14+, or Linux
- **Python**: 3.8 or higher
- **Memory**: Minimum 4GB RAM (8GB recommended)
- **Storage**: 500MB free space (for application and data)
- **Display**: 1024x768 minimum resolution (1920x1080 recommended)

## 🛠 Installation

### Option 1: Direct Python Installation

1. **Clone or download** the application files to a directory (e.g., `warehouse_manager/`)

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **For Linux systems**, install tkinter if not present:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install python3-tk
   
   # CentOS/RHEL
   sudo yum install tkinter
   ```

4. **Run the application**:
   ```bash
   python app.py
   ```

### Option 2: PyInstaller Executable (Recommended for Production)

1. **Install PyInstaller**:
   ```bash
   pip install pyinstaller
   ```

2. **Create executable**:
   ```bash
   pyinstaller --onefile --windowed --name "WarehouseManager" app.py
   ```

3. **Run the executable** from the `dist/` folder

### File Structure
```
warehouse_manager/
├── app.py                 # Main application entry point
├── requirements.txt       # Python dependencies
├── README.md             # This file
├── __init__.py           # Package initialization
├── database.py           # Database management
├── theme.py              # UI theme and styling
├── gui_main.py           # Main application window
├── login_gui.py          # Login interface
├── customer_gui.py       # Customer management
├── material_gui.py       # Inventory management
├── roles_admin_gui.py    # User administration
├── logs/                 # Application logs
├── data/                 # Database files
├── images/               # Product images
├── exports/              # Exported data
└── backups/              # Database backups
```

## 👤 Default Login Credentials

The application comes with a default administrator account:

- **Username**: `MallousG`
- **Password**: `MallousG`
- **Role**: `Admin1` (Full system access)

**⚠️ Important**: Change the default password immediately after first login for security.

## 🎯 Quick Start Guide

### First Time Setup

1. **Launch the application** using `python app.py`
2. **Login** with the default credentials above
3. **Create additional users** via Administration → User Management
4. **Import your data** or start adding customers and inventory manually

### Basic Operations

#### Customer Management
- Add customers with unique 4-digit PINs
- Search by name, phone, or PIN
- View complete assignment history
- Export customer data to CSV

#### Inventory Management
- Switch between Grid and List views
- Add products with images, descriptions, and pricing
- Manage serial numbers for each product
- Move items between new and used inventory
- Assign items to customers

#### User Administration (Admin Only)
- Create new user accounts
- Assign appropriate roles based on job functions
- Monitor user activity through audit trails
- Deactivate/reactivate user accounts

## 👥 User Roles & Permissions

| Role | Permissions |
|------|------------|
| **Admin1** | Full system access, manage all users, system configuration |
| **Admin2** | Manage inventory/customers, manage lower-level users, reports |
| **Admin3** | Manage inventory/customers, manage Operators/Viewers |
| **Operator** | Add/edit inventory, assign to customers, limited reporting |
| **Viewer** | Read-only access to inventory and customer information |

## ⌨️ Keyboard Shortcuts

### General
- `Ctrl+N` - Add new item
- `Ctrl+E` - Edit selected item
- `Ctrl+D` - Delete selected item
- `Ctrl+F` - Search/Filter
- `Ctrl+A` - Select all
- `F5` - Refresh current view
- `Ctrl+Q` - Logout
- `Esc` - Close dialog/Cancel

### Navigation
- `Ctrl+1` - Customers tab
- `Ctrl+2` - Inventory tab
- `Ctrl+3` - Used Inventory tab
- `Ctrl+4` - Reports tab

### View Controls
- `Ctrl+G` - Grid view
- `Ctrl+L` - List view

## 📊 Data Management

### Import/Export Formats
- **CSV**: Customer and inventory data
- **Excel**: Advanced data import with multiple sheets
- **Images**: JPEG, PNG for product photos (auto-resized to 64x64 thumbnails)

### Database Backup
- Automatic daily backups (configurable)
- Manual backup via Tools menu
- SQLite format for easy migration and portability

## 🔧 Configuration

### Database Settings
- Location: `data/warehouse.db`
- Automatic indexing for performance
- Foreign key constraints enabled
- Transaction-safe operations

### UI Customization
- Dark theme optimized for extended use
- Responsive layout adapts to screen size
- Consistent spacing and typography
- Professional color scheme

## 🐛 Troubleshooting

### Common Issues

**Application won't start**
- Ensure Python 3.8+ is installed
- Install required dependencies: `pip install -r requirements.txt`
- Check that tkinter is available: `python -c "import tkinter"`

**Database errors**
- Ensure the `data/` directory is writable
- Check available disk space
- Try deleting `warehouse.db` to reset (will lose data)

**Performance issues**
- Close unused applications to free memory
- Ensure sufficient disk space for database operations
- Consider using Grid view for better performance with large datasets

**Login issues**
- Verify username and password (case-sensitive)
- Check that user account is active
- Contact system administrator for password reset

### Logging
Application logs are stored in the `logs/` directory with daily rotation. Check these files for detailed error information.

## 📞 Support

For technical support or feature requests:
- Check the logs directory for error details
- Document steps to reproduce issues
- Contact your system administrator
- For development issues, check the source code comments

## 📄 License

This software is proprietary and confidential. Unauthorized copying, distribution, or modification is strictly prohibited.

**Copyright © 2024 MallousG. All rights reserved.**

## 🔄 Version History

### Version 1.0.0 (Current)
- Initial release
- Complete customer and inventory management
- Role-based user system
- Professional dark theme UI
- Import/export functionality
- Audit trail and logging

### Planned Features (Future Releases)
- Advanced reporting and analytics
- Barcode scanning integration
- Email notifications for warranty expiry
- Multi-language support
- Cloud backup integration
- Mobile companion app

---

*Built with Python and Tkinter for maximum compatibility and performance.*