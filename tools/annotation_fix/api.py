"""HTTP service wrapper.

    uvicorn annotation_fix.api:app --port 8687

Exists so the Scalabel server can call the correction step at project creation
without hosting Python image processing itself: the ``auto_correct`` checkbox
posts the parsed item file here and imports whatever comes back.

The endpoint is a thin shell over ``process_document`` -- all behaviour lives in
``core``, so the CLI and this service cannot drift apart.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from .core import Options, process_document

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - optional dependency
    raise ImportError(
        "the HTTP service needs fastapi and uvicorn: pip install fastapi uvicorn"
    ) from exc


class CorrectRequest(BaseModel):
    """One correction request."""

    document: Union[Dict[str, Any], List[Dict[str, Any]]] = Field(
        ..., description="Scalabel export document, or a bare list of frames"
    )
    image_root: str = Field("", description="prefix for relative image paths")
    clamp: bool = Field(True, description="pull out-of-ROI vertices inside")
    connect: bool = Field(True, description="join near-touching endpoints")
    tolerance: float = Field(15.0, description="connect radius, image px")
    threshold: int = Field(10, description="black-padding threshold")
    min_angle: float = Field(0.0, description="junction angle guard, 0 disables")
    inset: float = Field(1.5, description="px to nudge clamped vertices inward")
    flag_distance: float = Field(50.0, description="report corrections beyond this")
    cache_dir: Optional[str] = Field(None, description="ROI mask cache directory")


class CorrectResponse(BaseModel):
    """The corrected document plus the run report."""

    document: Union[Dict[str, Any], List[Dict[str, Any]]]
    report: Dict[str, Any]


app = FastAPI(
    title="Scalabel annotation fix",
    description="ROI clamping and polyline auto-connect for imported annotations.",
    version="0.1.0",
)


@app.get("/health")
def health() -> Dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/correct", response_model=CorrectResponse)
def correct(request: CorrectRequest) -> CorrectResponse:
    """Apply both corrections and return the updated document.

    Clamping needs the images, so ``image_root`` must point at a path this
    service can read. With ``clamp: false`` the connect stage runs on geometry
    alone and no images are touched.
    """
    options = Options(
        clamp=request.clamp,
        connect=request.connect,
        threshold=request.threshold,
        tolerance=request.tolerance,
        min_angle=request.min_angle,
        inset=request.inset,
        flag_distance=request.flag_distance,
        image_root=request.image_root,
        cache_dir=request.cache_dir,
    )

    try:
        corrected, report = process_document(request.document, options)
    except Exception as exc:  # pragma: no cover - surfaced to the caller
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")

    return CorrectResponse(document=corrected, report=report.to_dict())
