"""Setup for rubiksql-lake-pipeline."""

from setuptools import setup, find_packages

setup(
    name="rubiksql-lake-pipeline",
    version="0.1.0",
    description="Daft + Ray distributed pipeline for building RubikSQL knowledge bases from Parquet data lakes",
    author="RubikSQL Team",
    python_requires=">=3.10",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "daft>=0.4.0",
        "ray[default]>=2.40.0",
        "pyarrow>=15.0.0",
        "pydantic>=2.0",
        "pyyaml>=6.0",
        "click>=8.0",
        "loguru>=0.7.0",
        "lancedb",
        "llama-index-vector-stores-lancedb",
        "litellm",
        "jinja2",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-cov",
            "black",
            "isort",
            "mypy",
        ],
    },
    entry_points={
        "console_scripts": [
            "rubiksql-lake=rubiksql_lake.cli:cli",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
