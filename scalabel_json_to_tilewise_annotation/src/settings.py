#!/usr/bin/env python3
"""Configuration settings for Full Image Annotation Verification module."""

import os
from pathlib import Path

# JSON formatting
JSON_INDENT = 2

# Tile configuration
TILE_WIDTH = 1250
TILE_HEIGHT = 1180

# Matplotlib rendering
MATPLOTLIB_DPI = 100

# Categories to exclude from COCO output.
# Remaining category IDs are re-sequenced starting from 1.
IGNORE_CATEGORIES: list[str] = ["crosswalk_line"]

# Maximum number of images to process in parallel.
# Set to 1 to disable parallel processing.
# Defaults to half of available CPU cores (minimum 1).
MAX_PARALLEL_IMAGES: int = max(1, (os.cpu_count() or 2) // 2)

# Contour extraction
CONTOUR_EPSILON_FACTOR = 0.001
