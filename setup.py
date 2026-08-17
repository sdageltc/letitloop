#!/usr/bin/env python
"""Setup script for letitloop.

This file is maintained for backward compatibility with older pip versions.
The canonical configuration is in pyproject.toml.
"""

import os

from setuptools import find_packages, setup


# Read the README file
def read_readme():
    readme_path = os.path.join(os.path.dirname(__file__), "README.md")
    if os.path.exists(readme_path):
        with open(readme_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


# Read requirements
def read_requirements():
    req_path = os.path.join(os.path.dirname(__file__), "requirements-ci.txt")
    if os.path.exists(req_path):
        with open(req_path, "r") as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return ["pyyaml>=6.0"]


setup(
    name="letitloop",
    version="0.1.0",
    author="letitloop Maintainers",
    author_email="maintainers@letitloop.dev",
    description="Autonomous task orchestration system — a durable macro-task control loop",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/sdageltc/letitloop",
    packages=find_packages(exclude=["tests", "tests.*", "scratch", "scratch.*"]),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.11",
    install_requires=read_requirements(),
    entry_points={
        "console_scripts": [
            "lil=orchestrator.cli:main",
            "letitloop=orchestrator.cli:main",
            "letitloop-mcp=orchestrator.mcp_server:main",
        ],
    },
    keywords="orchestration automation ai agent llm",
    project_urls={
        "Bug Tracker": "https://github.com/sdageltc/letitloop/issues",
        "Source Code": "https://github.com/sdageltc/letitloop",
        "Documentation": "https://github.com/sdageltc/letitloop#readme",
    },
)
