"""FastAPI backend service for tile-wise annotation generation."""

import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from logger_config import setup_logging
from main import main as run_pipeline

setup_logging(log_dir="logs")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Tile Annotation Backend",
    version="1.0.0",
)


def _map_host_path_for_container(raw_path: str) -> str:
    """Map host filesystem path to container path when configured.

    Use env vars:
      TILE_PATH_MAP_FROM (e.g. D:\\Nikhil\\Projects\\GitHub\\scalabel)
      TILE_PATH_MAP_TO   (e.g. /opt/scalabel)
    """
    map_from = os.environ.get("TILE_PATH_MAP_FROM", "").strip()
    map_to = os.environ.get("TILE_PATH_MAP_TO", "").strip()
    if not map_from or not map_to:
        return raw_path

    raw_norm = raw_path.replace("\\", "/")
    from_norm = map_from.replace("\\", "/").rstrip("/")
    if raw_norm.lower().startswith(from_norm.lower()):
        suffix = raw_norm[len(from_norm):].lstrip("/")
        mapped = Path(map_to) / Path(suffix)
        return str(mapped)
    return raw_path


class ProcessRequest(BaseModel):
    """Request body for tile-generation jobs."""

    input_dir: str = Field(..., description="Input image directory")
    scalable_json_path: str = Field(
        ..., description="Path to Scalabel JSON export file"
    )
    output_dir: str = Field(
        ..., description="Output directory for generated tile data"
    )


class ProcessResponse(BaseModel):
    """Response body for tile-generation jobs."""

    output_dir: str
    zip_path: str


@app.get("/health")
def health() -> dict[str, str]:
    """Health endpoint."""
    return {"status": "ok"}


@app.post("/process", response_model=ProcessResponse)
def process(request: ProcessRequest) -> ProcessResponse:
    """Run the tile pipeline and return the generated zip path."""
    input_dir = Path(_map_host_path_for_container(request.input_dir)).resolve()
    scalable_json_path = Path(
        _map_host_path_for_container(request.scalable_json_path)
    ).resolve()
    output_dir = Path(_map_host_path_for_container(request.output_dir)).resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Input directory does not exist: {input_dir}",
        )
    if not scalable_json_path.exists() or not scalable_json_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"Scalabel JSON file does not exist: {scalable_json_path}",
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        run_pipeline(
            str(input_dir),
            str(scalable_json_path),
            str(output_dir),
        )
    except Exception as exc:
        logger.exception("Tile pipeline execution failed")
        raise HTTPException(
            status_code=500,
            detail=f"Tile pipeline failed: {exc}",
        ) from exc

    zip_path = output_dir / f"{output_dir.name}.zip"
    if not zip_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Expected zip not found: {zip_path}",
        )

    return ProcessResponse(
        output_dir=str(output_dir),
        zip_path=str(zip_path),
    )


if __name__ == "__main__":
    host = os.environ.get("TILE_BACKEND_HOST", "0.0.0.0")
    port = int(os.environ.get("TILE_BACKEND_PORT", "8787"))
    uvicorn.run(app, host=host, port=port)
