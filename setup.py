"""
Warehouse Management System - Setup Configuration
Setup script for packaging and distribution
"""

from setuptools import setup, find_packages
import os


# Read long description from README
def read_readme():
    readme_path = os.path.join(os.path.dirname(__file__), 'README.md')
    if os.path.exists(readme_path):
        with open(readme_path, 'r', encoding='utf-8') as f:
            return f.read()
    return "Warehouse Management System - Professional Inventory & Customer Management"


# Read requirements
def read_requirements():
    req_path = os.path.join(os.path.dirname(__file__), 'requirements.txt')
    if os.path.exists(req_path):
        with open(req_path, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f.readlines()
                    if line.strip() and not line.startswith('#')]
    return ['Pillow>=9.0.0']


setup(
    name="warehouse-management-system",
    version="1.0.0",
    author="MallousG",
    author_email="admin@warehouse-system.local",
    description="Professional Inventory & Customer Management System",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/MallousG/warehouse-management-system",

    # Package information
    packages=find_packages(),
    py_modules=[
        'app',
        'database',
        'theme',
        'gui_main',
        'login_gui',
        'customer_gui',
        'material_gui',
        'roles_admin_gui'
    ],

    # Dependencies
    install_requires=read_requirements(),

    # Python version requirement
    python_requires=">=3.8",

    # Entry points
    entry_points={
        'console_scripts': [
            'warehouse-manager=app:main',
        ],
        'gui_scripts': [
            'warehouse-manager-gui=app:main',
        ]
    },

    # Package data
    package_data={
        '': ['*.md', '*.txt', '*.ini'],
    },

    # Additional data files
    data_files=[
        ('docs', ['README.md']),
        ('config', ['requirements.txt']),
    ],

    # Classification
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Office/Business :: Financial :: Accounting",
        "Topic :: Database :: Database Engines/Servers",
        "License :: Other/Proprietary License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
        "Environment :: X11 Applications :: GTK",
        "Environment :: Win32 (MS Windows)",
        "Environment :: MacOS X",
    ],

    # Keywords for searchability
    keywords="warehouse management inventory customer tracking database gui tkinter",

    # Project URLs
    project_urls={
        "Bug Reports": "https://github.com/MallousG/warehouse-management-system/issues",
        "Source": "https://github.com/MallousG/warehouse-management-system",
        "Documentation": "https://github.com/MallousG/warehouse-management-system/wiki",
    },

    # Additional options
    zip_safe=False,
    include_package_data=True,

    # Development dependencies
    extras_require={
        'dev': [
            'pytest>=6.0.0',
            'black>=22.0.0',
            'flake8>=4.0.0',
            'pyinstaller>=5.0.0',
        ],
        'excel': [
            'openpyxl>=3.0.0',
            'pandas>=1.3.0',
        ],
        'reports': [
            'reportlab>=3.6.0',
            'matplotlib>=3.5.0',
        ]
    }
)