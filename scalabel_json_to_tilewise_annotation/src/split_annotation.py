"""TileAnnotationSplitter: split full-image COCO annotations into tiles."""

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from constants import (
    JSON_INDENT,
    MIN_POLYGON_COORDS,
    MIN_TILE_CONTOUR_AREA,
    PIXEL_MAX_VALUE,
    TILE_HEIGHT,
    TILE_WIDTH,
)

logger = logging.getLogger(__name__)


class TileAnnotationSplitter:
    """Split full images and annotations into tiles."""

    # Tile dimensions
    TILE_WIDTH = TILE_WIDTH
    TILE_HEIGHT = TILE_HEIGHT

    def __init__(
        self,
        input_dir: str,
        output_dir: str,
        debug: bool = False,
    ) -> None:
        """Initialise TileAnnotationSplitter.

        Args:
            input_dir: Directory containing full images and COCO JSON files.
            output_dir: Where to save tiles.
            debug: If True, print debug information.
        """
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.debug = debug

    def _initialize_tile_coco_jsons(self) -> None:
        """Initialize COCO JSON structure for each tile."""
        for row in range(self.tile_rows):
            for col in range(self.tile_cols):
                tile_id = f"r{row}_c{col}"

                # Copy metadata from original COCO
                self.tile_coco_data[tile_id] = {
                    'info': self.coco_data.get('info', {}),
                    'licenses': self.coco_data.get('licenses', []),
                    'images': [],
                    'annotations': [],
                    'categories': self.coco_data.get('categories', []),
                }

                # Initialize an image entry for this tile
                self.tile_coco_data[tile_id]['images'].append({
                    'id': 1,
                    'file_name': f'{self.base_name}_r{row:02d}_c{col:02d}.png',
                    'height': self.TILE_HEIGHT,
                    'width': self.TILE_WIDTH,
                })

    def get_tile_roi(
        self, row: int, col: int
    ) -> Tuple[int, int, int, int]:
        """Return ROI for a tile as (y_start, y_end, x_start, x_end)."""
        y_start = row * self.TILE_HEIGHT
        y_end = min(y_start + self.TILE_HEIGHT, self.img_height)
        x_start = col * self.TILE_WIDTH
        x_end = min(x_start + self.TILE_WIDTH, self.img_width)
        return y_start, y_end, x_start, x_end

    def extract_annotation_mask(
        self,
        annotation: Dict[str, Any],
        canvas_shape: Optional[Tuple] = None,
    ) -> np.ndarray:
        """Extract binary mask for an annotation.

        Args:
            annotation: COCO annotation dict.
            canvas_shape: Shape of canvas (height, width, channels).

        Returns:
            Binary mask (uint8).
        """
        if canvas_shape is None:
            canvas_shape = (self.img_height, self.img_width, 3)

        mask = np.zeros((canvas_shape[0], canvas_shape[1]), dtype=np.uint8)

        if 'segmentation' in annotation:
            seg = annotation['segmentation']
            if isinstance(seg, list) and len(seg) > 0:
                # RLE or polygon
                if isinstance(seg[0], dict):
                    # RLE format - not implemented here
                    pass
                elif isinstance(seg[0], (int, float)):
                    # Single polygon
                    pts = np.array(seg, dtype=np.int32).reshape(-1, 2)
                    cv2.drawContours(
                        mask, [pts], 0, PIXEL_MAX_VALUE,
                        thickness=cv2.FILLED,
                    )
                else:
                    # Multiple polygons
                    for polygon in seg:
                        pts = np.array(
                            polygon, dtype=np.int32
                        ).reshape(-1, 2)
                        cv2.drawContours(
                            mask, [pts], 0, PIXEL_MAX_VALUE,
                            thickness=cv2.FILLED,
                        )

        return mask

    def find_contours_in_mask(
        self, mask: np.ndarray
    ) -> List[np.ndarray]:
        """Find contours in a binary mask.

        Args:
            mask: Binary mask (uint8).

        Returns:
            List of contours (OpenCV format).
        """
        contours, _ = cv2.findContours(
            mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
        )
        return contours

    def filter_small_contours(
        self,
        contours: List[np.ndarray],
        min_area: int = MIN_TILE_CONTOUR_AREA,
    ) -> List[np.ndarray]:
        """Filter out contours smaller than *min_area* pixels.

        Args:
            contours: List of contours.
            min_area: Minimum area to keep.

        Returns:
            Filtered list of contours.
        """
        filtered = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area >= min_area:
                filtered.append(contour)
        return filtered

    def process_annotation_for_tiles(
        self,
        annotation: Dict[str, Any],
        annotation_id: int,
    ) -> Dict[str, Any]:
        """Process a single annotation and split it into tiles.

        Workflow:
        1. Draw annotation on full image.
        2. For each tile:
           - Extract tile ROI from mask.
           - Find contours in tile.
           - If contours found, adjust coords to tile space.
           - Add to tile's COCO JSON.

        Args:
            annotation: COCO annotation dict.
            annotation_id: Unique ID for this annotation.

        Returns:
            Dict with summary of which tiles contain this annotation.
        """
        # Create mask of annotation on full image
        mask_full = self.extract_annotation_mask(annotation)

        if mask_full.max() == 0:
            logger.debug(
                "Annotation %d has empty mask", annotation_id
            )
            return {'annotation_id': annotation_id, 'tiles': []}

        tiles_with_annotation = []
        category_id = annotation.get('category_id', 1)

        # Process each tile
        for row in range(self.tile_rows):
            for col in range(self.tile_cols):
                y_start, y_end, x_start, x_end = self.get_tile_roi(row, col)
                tile_id = f"r{row}_c{col}"

                # Extract mask for this tile
                tile_mask = mask_full[y_start:y_end, x_start:x_end]

                # Find contours in tile mask
                if tile_mask.max() == 0:
                    # No annotation in this tile
                    continue

                contours = self.find_contours_in_mask(tile_mask)
                filtered_contours = self.filter_small_contours(
                    contours, min_area=MIN_TILE_CONTOUR_AREA
                )

                if not filtered_contours:
                    continue

                # Convert contours to tile-local coordinates
                tile_annotation = self._create_tile_annotation(
                    filtered_contours,
                    category_id,
                    row,
                    col,
                    annotation
                )

                if tile_annotation:
                    # Add to tile COCO JSON
                    tile_coco = self.tile_coco_data[tile_id]
                    tile_coco['annotations'].append(tile_annotation)
                    tiles_with_annotation.append(tile_id)
                    logger.debug(
                        "Annotation %d added to tile %s",
                        annotation_id,
                        tile_id,
                    )

        return {
            'annotation_id': annotation_id,
            'category_id': category_id,
            'tiles': tiles_with_annotation,
            'tile_count': len(tiles_with_annotation)
        }

    def _create_tile_annotation(
        self,
        contours: List[np.ndarray],
        category_id: int,
        tile_row: int,
        tile_col: int,
        original_annotation: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Create a COCO annotation entry for a tile.

        Args:
            contours: List of contours in tile-local space.
            category_id: Category ID.
            tile_row: Tile row index.
            tile_col: Tile column index.
            original_annotation: Original annotation for reference.

        Returns:
            COCO annotation dict or None if invalid.
        """
        if not contours:
            return None

        # Combine all contours into single segmentation
        segmentation = []
        total_area = 0

        for contour in contours:
            # Convert contour to flat list of coordinates
            contour_flat = contour.flatten().tolist()
            if len(contour_flat) >= MIN_POLYGON_COORDS:  # At least 3 points
                segmentation.append(contour_flat)
                total_area += cv2.contourArea(contour)

        if not segmentation:
            return None

        # Create COCO annotation
        bbox = self._extract_bbox_from_contours(contours)
        if bbox is None:
            return None

        annotation_id = len(self.coco_data['annotations']) + len(
            [
                a
                for tile_coco in self.tile_coco_data.values()
                for a in tile_coco['annotations']
            ]
        )

        return {
            'id': annotation_id,
            'image_id': 1,  # Single image per tile
            'category_id': category_id,
            'segmentation': segmentation,
            'area': float(total_area),
            'bbox': [float(x) for x in bbox],
            'iscrowd': 0,
            'source_annotation_id': original_annotation.get('id'),
            'tile_row': tile_row,
            'tile_col': tile_col,
        }

    def _extract_bbox_from_contours(
        self,
        contours: List[np.ndarray],
    ) -> Optional[List[float]]:
        """Extract bounding box from a list of contours.

        Args:
            contours: List of contours.

        Returns:
            [x, y, width, height] or None.
        """
        if not contours:
            return None

        # Find overall bounding rect
        min_x, min_y = float('inf'), float('inf')
        max_x, max_y = 0, 0

        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x + w)
            max_y = max(max_y, y + h)

        if min_x >= max_x or min_y >= max_y:
            return None

        return [min_x, min_y, max_x - min_x, max_y - min_y]

    def extract_tile_images(self) -> None:
        """Extract and save tile images."""
        logger.info("Extracting tile images")

        tile_dir = self.output_dir / self.full_image_path.stem / "images"
        tile_dir.mkdir(parents=True, exist_ok=True)

        for row in range(self.tile_rows):
            for col in range(self.tile_cols):
                y_start, y_end, x_start, x_end = self.get_tile_roi(row, col)

                # Extract tile
                tile_image = self.full_image[
                    y_start:y_end, x_start:x_end
                ].copy()

                # Store for later use
                tile_id = f"r{row}_c{col}"
                self.tile_images[tile_id] = tile_image

                # Save tile image
                tile_path = (
                    tile_dir
                    / f"{self.base_name}_r{row:02d}_c{col:02d}.png"
                )
                cv2.imwrite(str(tile_path), tile_image)

        logger.info("Extracted %d tile image(s)", len(self.tile_images))

    def split_annotations(self) -> Dict[str, Any]:
        """Split all annotations across tiles."""
        logger.info("Splitting annotations into tiles")

        annotations = self.coco_data.get('annotations', [])
        total_annotations = len(annotations)
        logger.info("Processing %d annotation(s)", total_annotations)

        summary = {
            'total_annotations': total_annotations,
            'processed_annotations': 0,
            'annotations_per_tile': {},
        }

        for idx, annotation in enumerate(annotations):
            result = self.process_annotation_for_tiles(
                annotation, annotation['id']
            )
            summary['processed_annotations'] += 1

            if result['tiles']:
                logger.info(
                    "Annotation %d split into %d tile(s)",
                    annotation['id'],
                    result['tile_count'],
                )

        # Count annotations per tile
        for tile_id, tile_coco in self.tile_coco_data.items():
            anno_count = len(tile_coco['annotations'])
            if anno_count > 0:
                summary['annotations_per_tile'][tile_id] = anno_count

        logger.info(
            "Annotations processed: %d", summary['processed_annotations']
        )
        n_tiles = len(summary['annotations_per_tile'])
        logger.info("Tiles with annotations: %d", n_tiles)

        return summary

    def save_tile_jsons(self) -> None:
        """Save COCO JSON for each tile."""
        logger.info("Saving tile COCO JSONs")

        json_dir = self.output_dir / self.full_image_path.stem / "jsons"
        json_dir.mkdir(parents=True, exist_ok=True)

        tiles_with_annotations = 0

        for tile_id, tile_coco in self.tile_coco_data.items():
            # Only save tiles with annotations
            if len(tile_coco['annotations']) == 0:
                continue

            row_num = int(tile_id.split('_')[0][1:])
            col_num = int(tile_id.split('_')[1][1:])
            json_path = (
                json_dir
                / f"{self.base_name}_r{row_num:02d}_c{col_num:02d}.json"
            )
            with open(json_path, 'w') as f:
                json.dump(tile_coco, f, indent=JSON_INDENT)

            anno_count = len(tile_coco['annotations'])
            logger.info(
                "Tile %s saved: %d annotation(s)", tile_id, anno_count
            )
            tiles_with_annotations += 1

        logger.info(
            "Total tiles with annotations: %d", tiles_with_annotations
        )

    def run(self) -> Dict[str, Any]:
        """Run complete tile splitting pipeline.

        Returns:
            Summary dictionary
        """
        # list all the images from the input directory

        all_images = (
            list(self.input_dir.glob("*.png"))
            + list(self.input_dir.glob("*.jpg"))
            + list(self.input_dir.glob("*.jpeg"))
        )
        if not all_images:
            raise ValueError(
                f"No images found in input directory: {self.input_dir}"
            )

        for img_path in all_images:

            # Get full image path
            self.full_image_path = img_path
            stem = self.full_image_path.stem
            self.base_name = stem.split('_verification')[0]
            self.coco_json_path = (
                self.output_dir / stem / f"{stem}_coco.json"
            )

            # Load image and JSON
            self.full_image = cv2.imread(str(self.full_image_path))
            if self.full_image is None:
                raise ValueError(
                    f"Failed to load image: {self.full_image_path}"
                )

            if not self.coco_json_path.exists():
                logger.warning(
                    "Skipping image without generated COCO JSON: %s",
                    self.coco_json_path,
                )
                continue

            with open(self.coco_json_path, 'r') as f:
                self.coco_data = json.load(f)

            self.img_height, self.img_width = self.full_image.shape[:2]

            # Calculate tile grid
            self.tile_rows = (
                (self.img_height + self.TILE_HEIGHT - 1)
                // self.TILE_HEIGHT
            )
            self.tile_cols = (
                (self.img_width + self.TILE_WIDTH - 1)
                // self.TILE_WIDTH
            )

            if self.debug:
                logger.debug(
                    "Image size: %dx%d",
                    self.img_width,
                    self.img_height,
                )
                logger.debug(
                    "Tile grid: %d rows x %d cols",
                    self.tile_rows,
                    self.tile_cols,
                )
                logger.debug(
                    "Total tiles: %d",
                    self.tile_rows * self.tile_cols,
                )

            # Tile COCO JSONs (one per tile)
            self.tile_coco_data = {}
            self._initialize_tile_coco_jsons()

            # State
            self.tile_images = {}  # (row, col) -> image

            try:
                # Step 1: Extract tile images
                self.extract_tile_images()

                # Step 2: Split annotations
                split_summary = self.split_annotations()

                # Step 3: Save tile JSONs
                self.save_tile_jsons()

                logger.info("=" * 70)
                logger.info(
                    "Tile splitting complete for %s",
                    self.full_image_path.name,
                )
                logger.info("=" * 70)

            except Exception:
                logger.exception(
                    "Tile splitting failed for %s",
                    self.full_image_path.name,
                )
                split_summary = {
                    'total_annotations': 0,
                    'processed_annotations': 0,
                    'annotations_per_tile': {},
                }
        return {
            'status': 'success',
            'split_summary': split_summary,
        }
