"""Compatibility build entry point for the Python 3.9/setuptools 59 host."""

from pathlib import Path

from setuptools import find_packages, setup


ROOTS = (
    Path("packages/contracts/src"),
    Path("packages/execution/src"),
    Path("packages/scheduler/src"),
    Path("packages/analysis/src"),
    Path("packages/visualization/src"),
)

packages = []
package_dir = {}
for root in ROOTS:
    for package in find_packages(str(root)):
        packages.append(package)
        package_dir[package] = str(root / package.replace(".", "/"))

setup(
    name="openroad-platform",
    version="0.1.0",
    description="A durable control plane and isolated execution layer for OpenROAD workflows",
    python_requires=">=3.9",
    packages=packages,
    package_dir=package_dir,
    package_data={"openroad_platform_analysis": ["assets/*.tcl"]},
    entry_points={
        "console_scripts": [
            "openroad-run=openroad_platform_execution.cli:main",
            "openroad-jobs=openroad_platform_scheduler.cli:main",
        ]
    },
)

