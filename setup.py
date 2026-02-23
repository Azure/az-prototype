#!/usr/bin/env python
"""Azure CLI Extension: az prototype — Innovation Factory rapid prototyping."""

from setuptools import find_packages, setup

VERSION = "0.2.1b1"
CLASSIFIERS = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "Intended Audience :: System Administrators",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "License :: OSI Approved :: MIT License",
]

DEPENDENCIES = [
    "knack>=0.11.0",
    "pyyaml>=6.0",
    "requests>=2.28.0",
    "rich>=13.0.0",
    "jinja2>=3.1.0",
    "openai>=1.0.0",
    "opencensus-ext-azure>=1.1.0",
    # prompt_toolkit for multi-line input (Shift+Enter, backslash continuation)
    "prompt_toolkit>=3.0.0",
    # Pin psutil — only 7.1.1 ships a pre-built win32 binary wheel.
    # Later versions (7.1.2+) require a source build which fails on
    # Azure CLI's bundled 32-bit Python (no setuptools).
    "psutil>=5.6.3,<=7.1.1",
    # Document text + image extraction for binary artifact support
    "pypdf>=4.0",
    "python-docx>=1.0",
    "python-pptx>=1.0",
    "openpyxl>=3.1",
]

setup(
    name="az-prototype",
    version=VERSION,
    description="Azure CLI extension for rapid prototype generation using AI agents and GitHub Copilot",
    long_description="Empowers customers to rapidly create Azure prototypes using AI-driven agent teams.",
    license="MIT",
    author="Microsoft Innovation Factory",
    author_email="",
    url="https://github.com/microsoft/az-prototype",
    classifiers=CLASSIFIERS,
    packages=find_packages(exclude=["tests", "tests.*"]),
    install_requires=DEPENDENCIES,
    package_data={
        "azext_prototype": [
            "agents/builtin/definitions/*.yaml",
            "policies/**/*.yaml",
            "policies/*.json",
            "templates/**/*",
            "templates/workloads/*.yaml",
        ]
    },
    entry_points={
        "azure.cli.extensions": [
            "prototype=azext_prototype",
        ]
    },
)
