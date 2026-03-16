"""ProcessImage: batch-convert Scalabel JSON + images to COCO JSON files."""

import json
import logging
from pathlib import Path
from typing import Dict, List
import shutil

from scalable_to_coco import ScalableDirectToCoco
from constants import DEFAULT_LANE_WIDTH, JSON_INDENT

logger = logging.getLogger(__name__)


class ProcessImage:
    """Batch-convert Scalabel JSON annotations to per-image COCO JSON files."""

    def __init__(
        self,
        input_dir: str,
        scalable_json_path: str,
        output_dir: str,
        lane_width: int = DEFAULT_LANE_WIDTH,
    ) -> None:
        """Initialise ProcessImage.

        Args:
            input_dir: Directory containing source images.
            scalable_json_path: Path to the Scalabel JSON export file.
            output_dir: Root directory where outputs will be written.
            lane_width: Dilation width (px) applied to lane annotations.
        """
        self.input_dir = Path(input_dir)
        self.scalable_json_path = Path(scalable_json_path)
        self.output_dir = Path(output_dir)
        self.lane_width = lane_width
        self.image_extensions: List[str] = ['.jpg', '.jpeg', '.png', '.bmp']

        # State
        self.scalable_data: Dict = {}
        self.images: List[Path] = []
        self.results: Dict = {
            'total_images': 0,
            'processed_images': 0,
            'skipped_images': 0,
            'total_annotations': 0,
            'per_image_stats': [],
        }

    def load_json(self) -> None:
        """Load the Scalabel JSON export file into memory."""
        logger.info(
            "Loading Scalabel JSON: %s", self.scalable_json_path.name
        )
        with open(self.scalable_json_path, 'r') as f:
            self.scalable_data = json.load(f)

        if (
            not isinstance(self.scalable_data, dict)
            or 'frames' not in self.scalable_data
        ):
            raise ValueError("Scalabel JSON must have 'frames' key")

        num_frames = len(self.scalable_data['frames'])
        logger.info("Loaded %d frame(s)", num_frames)

    def find_images(self) -> None:
        """Scan the input directory and collect all supported image paths."""
        logger.info("Scanning images directory: %s", self.input_dir)

        self.images = []
        for ext in self.image_extensions:
            self.images.extend(sorted(self.input_dir.glob(f'*{ext}')))

        logger.info("Found %d image(s)", len(self.images))
        for img in self.images:
            logger.debug("  Image: %s", img.name)

    def process_single_image(self, image_path: Path) -> Dict:
        """Process one image and return per-image statistics.

        Args:
            image_path: Path to the image file to process.

        Returns:
            Dict with keys: image_name, status, annotation_count, etc.
        """
        image_name = image_path.name
        stem = image_path.stem

        logger.info("Processing image: %s", image_name)

        # Create per-image output directory
        per_image_output = self.output_dir / stem
        per_image_output.mkdir(parents=True, exist_ok=True)

        try:
            converter = ScalableDirectToCoco(
                scalable_json_path=str(self.scalable_json_path),
                image_path=str(image_path),
                output_dir=str(per_image_output),
                lane_width=self.lane_width,
            )
            converter.load_data()

            matching_frame = converter._get_matching_frame()
            if not matching_frame:
                logger.warning(
                    "No matching frame in JSON for: %s", image_name
                )
                return {
                    'image_name': image_name,
                    'status': 'skipped',
                    'annotation_count': 0,
                    'frame_found': False,
                }

            converter.scalable_to_coco()
            num_annotations = len(converter.coco_data['annotations'])

            # Save COCO JSON directly to per-image folder
            coco_json_path = per_image_output / f"{stem}_coco.json"
            with open(coco_json_path, 'w') as f:
                json.dump(converter.coco_data, f, indent=JSON_INDENT)

            logger.info(
                "Processed %d annotation(s) for %s",
                num_annotations,
                image_name,
            )
            logger.info("COCO JSON saved to: %s", coco_json_path)

            result = {
                'image_name': image_name,
                'image_path': str(image_path),
                'status': 'success',
                'annotation_count': num_annotations,
                'frame_found': True,
                'output_dir': str(per_image_output),
                'image_resolution': [
                    converter.image_width,
                    converter.image_height,
                ],
            }
            logger.debug(
                "Image result:\n%s", json.dumps(result, indent=2)
            )
            return result

        except Exception as e:
            logger.exception("Error processing image %s", image_name)
            return {
                'image_name': image_name,
                'status': 'error',
                'annotation_count': 0,
                'frame_found': False,
                'error': str(e),
            }

    def save_report(self) -> None:
        """Persist the batch processing report as a JSON file."""
        logger.info("Saving batch processing report...")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.output_dir / "batch_processing_report.json"

        with open(report_path, 'w') as f:
            json.dump(self.results, f, indent=JSON_INDENT)

        logger.info("Report saved: %s", report_path)
        logger.debug(
            "Batch report:\n%s", json.dumps(self.results, indent=2)
        )

    def process_all_images(self) -> None:
        """Orchestrate loading, conversion, and reporting for all images."""
        logger.info("=" * 70)
        logger.info("BATCH: SCALABEL -> COCO -> DRAW VERIFICATION")
        logger.info("=" * 70)
        logger.info("Images directory: %s", self.input_dir)
        logger.info("Output directory: %s", self.output_dir)
        logger.info("Scalabel JSON:    %s", self.scalable_json_path.name)

        self.load_json()
        self.find_images()


        output_json_path = self.output_dir / self.scalable_json_path.name
        if self.scalable_json_path.resolve() != output_json_path.resolve():
            shutil.copy(str(self.scalable_json_path), str(output_json_path))
            logger.info(
                "Copied Scalabel JSON to output directory for reference: %s",
                output_json_path,
            )
        else:
            logger.info(
                "Scalabel JSON already in output directory, skipping copy: %s",
                output_json_path,
            )

        
        if not self.images:
            logger.warning("No images found to process")
            return

        logger.info("Processing images...")

        for image_path in self.images:

            result = self.process_single_image(image_path)
            self.results['per_image_stats'].append(result)

            if result['status'] == 'success':
                self.results['processed_images'] += 1
                self.results['total_annotations'] += (
                    result['annotation_count']
                )
            else:
                self.results['skipped_images'] += 1
            
            # copy input image to the output directory
            shutil.copy(
                image_path,
                self.output_dir
                / image_path.stem
                / image_path.name,
            )


        self.results['total_images'] = len(self.images)
        self.save_report()
