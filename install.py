#!/usr/bin/env python3
"""
Warehouse Management System - Installation Script
Automated installation and setup script
"""

import os
import sys
import subprocess
import platform
import shutil
from pathlib import Path


class WarehouseInstaller:
    """Automated installer for Warehouse Management System."""

    def __init__(self):
        self.system = platform.system().lower()
        self.python_version = sys.version_info
        self.install_dir = Path.cwd()
        self.errors = []

    def print_header(self):
        """Print installation header."""
        print("=" * 60)
        print("   WAREHOUSE MANAGEMENT SYSTEM - INSTALLER")
        print("=" * 60)
        print(f"System: {platform.system()} {platform.release()}")
        print(f"Python: {sys.version.split()[0]}")
        print(f"Install Directory: {self.install_dir}")
        print("=" * 60)
        print()

    def check_python_version(self):
        """Check Python version compatibility."""
        print("🐍 Checking Python version...")

        if self.python_version < (3, 8):
            self.errors.append(
                f"Python 3.8+ required. Current version: {sys.version.split()[0]}"
            )
            return False

        print(f"✅ Python {sys.version.split()[0]} - Compatible")
        return True

    def check_tkinter(self):
        """Check if tkinter is available."""
        print("🖥️  Checking tkinter availability...")

        try:
            import tkinter
            print("✅ tkinter - Available")
            return True
        except ImportError:
            self.errors.append("tkinter not available")
            print("❌ tkinter - Not available")

            if self.system == "linux":
                print("💡 To install tkinter on Linux:")
                print("   Ubuntu/Debian: sudo apt-get install python3-tk")
                print("   CentOS/RHEL: sudo yum install tkinter")

            return False

    def install_dependencies(self):
        """Install Python dependencies."""
        print("📦 Installing Python dependencies...")

        requirements_file = self.install_dir / "requirements.txt"

        if not requirements_file.exists():
            print("⚠️  requirements.txt not found, installing minimal dependencies...")
            dependencies = ["Pillow>=9.0.0"]
        else:
            with open(requirements_file, 'r') as f:
                dependencies = [
                    line.strip() for line in f.readlines()
                    if line.strip() and not line.startswith('#')
                ]

        for dep in dependencies:
            if dep:
                try:
                    print(f"   Installing {dep}...")
                    subprocess.check_call([
                        sys.executable, "-m", "pip", "install", dep
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    print(f"   ✅ {dep} installed")
                except subprocess.CalledProcessError as e:
                    error_msg = f"Failed to install {dep}: {e}"
                    self.errors.append(error_msg)
                    print(f"   ❌ {dep} failed")

        return len(self.errors) == 0

    def create_directories(self):
        """Create necessary application directories."""
        print("📁 Creating application directories...")

        directories = [
            "logs", "data", "images", "exports", "backups"
        ]

        for directory in directories:
            dir_path = self.install_dir / directory
            try:
                dir_path.mkdir(exist_ok=True)
                print(f"   ✅ {directory}/ created")
            except Exception as e:
                error_msg = f"Failed to create directory {directory}: {e}"
                self.errors.append(error_msg)
                print(f"   ❌ {directory}/ failed")

        return len(self.errors) == 0

    def test_application(self):
        """Test if application can start."""
        print("🧪 Testing application startup...")

        app_file = self.install_dir / "app.py"
        if not app_file.exists():
            self.errors.append("app.py not found")
            print("   ❌ app.py not found")
            return False

        try:
            # Test import without starting GUI
            result = subprocess.run([
                sys.executable, "-c",
                "import sys; sys.path.insert(0, '.'); "
                "from gui_main import MainApplication; "
                "print('Import successful')"
            ], cwd=self.install_dir, capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                print("   ✅ Application modules import successfully")
                return True
            else:
                self.errors.append(f"Import test failed: {result.stderr}")
                print("   ❌ Application import failed")
                return False

        except subprocess.TimeoutExpired:
            self.errors.append("Import test timed out")
            print("   ❌ Import test timed out")
            return False
        except Exception as e:
            self.errors.append(f"Import test error: {e}")
            print("   ❌ Import test error")
            return False

    def create_shortcuts(self):
        """Create desktop shortcuts and start scripts."""
        print("🔗 Creating shortcuts and start scripts...")

        # Create start script
        if self.system == "windows":
            script_content = f'''@echo off
cd /d "{self.install_dir}"
python app.py
pause
'''
            script_file = self.install_dir / "start_warehouse.bat"
        else:
            script_content = f'''#!/bin/bash
cd "{self.install_dir}"
python3 app.py
'''
            script_file = self.install_dir / "start_warehouse.sh"

        try:
            with open(script_file, 'w') as f:
                f.write(script_content)

            if self.system != "windows":
                os.chmod(script_file, 0o755)

            print(f"   ✅ Start script created: {script_file.name}")
        except Exception as e:
            print(f"   ⚠️  Could not create start script: {e}")

    def print_summary(self):
        """Print installation summary."""
        print("\n" + "=" * 60)
        print("   INSTALLATION SUMMARY")
        print("=" * 60)

        if self.errors:
            print("❌ Installation completed with errors:")
            for error in self.errors:
                print(f"   • {error}")
        else:
            print("✅ Installation completed successfully!")

        print("\n📋 NEXT STEPS:")
        print("1. Run the application:")

        if self.system == "windows":
            print("   • Double-click start_warehouse.bat")
            print("   • Or run: python app.py")
        else:
            print("   • Run: ./start_warehouse.sh")
            print("   • Or run: python3 app.py")

        print("\n2. Default login credentials:")
        print("   • Username: MallousG")
        print("   • Password: MallousG")
        print("   • Role: Admin1")

        print("\n3. Important:")
        print("   • Change the default password after first login")
        print("   • Create additional user accounts as needed")
        print("   • Check the logs/ directory for any issues")

        print("\n📖 For help, see README.md")
        print("=" * 60)

    def run_installation(self):
        """Run the complete installation process."""
        self.print_header()

        # Check prerequisites
        if not self.check_python_version():
            self.print_summary()
            return False

        if not self.check_tkinter():
            self.print_summary()
            return False

        # Install components
        self.install_dependencies()
        self.create_directories()
        self.test_application()
        self.create_shortcuts()

        # Show summary
        self.print_summary()

        return len(self.errors) == 0


def main():
    """Main installation function."""
    installer = WarehouseInstaller()
    success = installer.run_installation()

    if success:
        print("\n🎉 Installation completed successfully!")
        input("\nPress Enter to exit...")
        sys.exit(0)
    else:
        print("\n⚠️  Installation completed with errors.")
        input("\nPress Enter to exit...")
        sys.exit(1)


if __name__ == "__main__":
    main()