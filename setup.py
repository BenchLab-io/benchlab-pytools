from setuptools import setup, find_packages
from pathlib import Path

def read_requirements(path):
    """Read a requirements.txt file and return a list of dependencies."""
    req_path = Path(path)
    if not req_path.is_file():
        return []
    with open(req_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return lines

setup(
    name="BENCHLAB PyTools",
    version="0.1.0",
    description="A collection of python tools for BENCHLAB telemetry",
    author="Pieter Plaisier",
    author_email="contact@benchlab.io",
    packages=find_packages(),  # Automatically find all subpackages
    python_requires=">=3.10",
    install_requires=read_requirements("benchlab/requirements.txt"),  # core dependencies
    extras_require={
        "fastapi": read_requirements("benchlab/fastapi/requirements.txt"),
        "tests": read_requirements("benchlab/tests/requirements.txt"),
    },
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "benchlab=benchlab.main:main",  # optional if you have a main() function
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
