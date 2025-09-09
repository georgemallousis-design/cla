#!/usr/bin/env python3
"""
Warehouse Management System - Application Entry Point
Main script to launch the warehouse management application
"""

import sys
import os
import tkinter as tk
from tkinter import messagebox
import logging

# Add current directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)


def check_dependencies():
    """Check if all required dependencies are available."""
    missing_deps = []

    try:
        import tkinter
    except ImportError:
        missing_deps.append("tkinter")

    try:
        import sqlite3
    except ImportError:
        missing_deps.append("sqlite3")

    try:
        from PIL import Image, ImageTk
    except ImportError:
        missing_deps.append("Pillow")

    if missing_deps:
        error_msg = f"Missing required dependencies: {', '.join(missing_deps)}\n\n"
        error_msg += "Please install them using:\n"
        if "Pillow" in missing_deps:
            error_msg += "pip install Pillow\n"
        if "tkinter" in missing_deps:
            error_msg += "On Ubuntu/Debian: sudo apt-get install python3-tk\n"

        print(error_msg)

        # Try to show GUI error if tkinter is available
        try:
            root = tk.Tk()
            root.withdraw()  # Hide main window
            messagebox.showerror("Dependencies Missing", error_msg)
            root.destroy()
        except:
            pass

        return False

    return True


def setup_environment():
    """Setup application environment."""
    # Create necessary directories
    directories = ['logs', 'data', 'images', 'exports', 'backups']

    for directory in directories:
        os.makedirs(directory, exist_ok=True)

    # Set up basic logging for startup
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def main():
    """Main application entry point."""
    print("=== Warehouse Management System ===")
    print("Starting application...")

    # Check dependencies
    if not check_dependencies():
        print("Cannot start application due to missing dependencies.")
        sys.exit(1)

    # Setup environment
    setup_environment()

    try:
        # Import and start the main application
        from gui_main import MainApplication

        print("Initializing application...")
        app = MainApplication()

        print("Application ready. Starting GUI...")
        app.run()

    except ImportError as e:
        error_msg = f"Failed to import application modules: {e}"
        print(f"ERROR: {error_msg}")

        # Try to show GUI error
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Import Error", error_msg)
            root.destroy()
        except:
            pass

        sys.exit(1)

    except Exception as e:
        error_msg = f"Application startup failed: {e}"
        print(f"CRITICAL ERROR: {error_msg}")

        # Log the error
        logging.error(error_msg, exc_info=True)

        # Try to show GUI error
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Startup Error", error_msg)
            root.destroy()
        except:
            pass

        sys.exit(1)


if __name__ == "__main__":
    main()