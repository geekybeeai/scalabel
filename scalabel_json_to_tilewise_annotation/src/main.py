"""Entry point for the scalable JSON to tile-wise annotation pipeline."""

import argparse
import logging
import os
import shutil

from logger_config import setup_logging
from process_image import ProcessImage
from settings import IGNORE_CATEGORIES
from split_annotation import TileAnnotationSplitter

logger = logging.getLogger(__name__)


def validate_dir(input_dir: str) -> None:
    """Validate that *input_dir* exists and is a directory.

    Args:
        input_dir: Filesystem path to validate.

    Raises:
        ValueError: If the path does not exist or is not a directory.
    """
    if not os.path.exists(input_dir):
        raise ValueError(
            f"Input directory {input_dir} does not exist."
        )
    if not os.path.isdir(input_dir):
        raise ValueError(
            f"Input path {input_dir} is not a directory."
        )


def validate_file(file_path: str) -> None:
    """Validate that *file_path* exists and is a regular file.

    Args:
        file_path: Filesystem path to validate.

    Raises:
        ValueError: If the path does not exist or is not a file.
    """
    if not os.path.exists(file_path):
        raise ValueError(f"File {file_path} does not exist.")
    if not os.path.isfile(file_path):
        raise ValueError(f"Path {file_path} is not a file.")


def main(
    input_dir: str,
    scalable_json_path: str,
    output_dir: str,
    ignore_categories: list[str] | None = None,
) -> None:
    """Run the full annotation processing and tile-splitting pipeline.

    Args:
        input_dir: Directory containing source images.
        scalable_json_path: Path to the Scalabel JSON export file.
        output_dir: Directory where outputs will be written.
        ignore_categories: Category names to exclude from COCO output.
    """
    logger.info("Current working directory: %s", os.getcwd())

    validate_dir(input_dir)
    validate_file(scalable_json_path)
    validate_dir(output_dir)
    logger.info("All input paths are valid.")

    processor = ProcessImage(
        input_dir, scalable_json_path, output_dir,
        ignore_categories=ignore_categories,
    )
    processor.load_json()
    processor.find_images()
    processor.process_all_images()
    processor.save_report()

    logger.info(
        "Image processing completed. Starting annotation splitting..."
    )

    splitter = TileAnnotationSplitter(input_dir, output_dir)
    splitter.run()

    logger.info("Annotation splitting completed.")

    # Zip the output directory for easy sharing
    output_zip = f"{output_dir}.zip"
    if os.path.exists(output_zip):
        logger.warning(
            "Output zip file already exists and will be overwritten: %s",
            output_zip,
        )
        os.remove(output_zip)
    shutil.make_archive(output_dir, 'zip', output_dir)
    logger.info("Output directory zipped successfully: %s", output_zip)

    # Now move the zip file into the output directory for better organisation
    final_zip_path = os.path.join(output_dir, os.path.basename(output_zip))
    shutil.move(output_zip, final_zip_path)
    logger.info(
        "Output zip file moved into output directory: %s", final_zip_path)   


if __name__ == "__main__":
    # Initialise logging before anything else so all modules benefit
    setup_logging(log_dir="logs")
    parser = argparse.ArgumentParser(
        description=(
            "Convert Scalabel JSON annotations to tile-wise COCO format."
        ),
    )
    parser.add_argument(
        '-i', '--input_dir',
        type=str,
        required=True,
        help='Directory containing source images',
    )
    parser.add_argument(
        '-json', '--scalable_json_path',
        type=str,
        required=True,
        help='Path to Scalabel JSON export file',
    )
    parser.add_argument(
        '-o', '--output_dir',
        type=str,
        required=True,
        help='Output directory for processed tiles',
    )
    parser.add_argument(
        '--ignore-categories',
        nargs='*',
        default=IGNORE_CATEGORIES,
        help=(
            'Category names to exclude from COCO output. '
            'Remaining category IDs are re-sequenced. '
            'Defaults to IGNORE_CATEGORIES in settings.py.'
        ),
    )
    args = parser.parse_args()

    logger.info("Input directory: %s", args.input_dir)
    logger.info("Scalable JSON path: %s", args.scalable_json_path)
    logger.info("Output directory: %s", args.output_dir)
    if args.ignore_categories:
        logger.info("Ignoring categories: %s", args.ignore_categories)

    os.makedirs(args.output_dir, exist_ok=True)
    main(
        args.input_dir,
        args.scalable_json_path,
        args.output_dir,
        ignore_categories=args.ignore_categories,
    )
