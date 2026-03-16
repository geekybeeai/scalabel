"""Project-wide named constants.

All magic numbers used in processing logic are centralised here.
Import only what you need in each module.
"""

# ---------------------------------------------------------------------------
# LANE / DILATION
# ---------------------------------------------------------------------------

# Default dilation width (pixels) applied to lane-type annotations so that
# thin poly-lines become filled regions suitable for segmentation masks.
DEFAULT_LANE_WIDTH = 8

# ---------------------------------------------------------------------------
# MASK BINARISATION
# ---------------------------------------------------------------------------

# Pixel intensity threshold used when converting a rendered greyscale mask
# to a binary mask.  Lowered from the naive 128 to 50 so that anti-aliased
# edges produced by matplotlib are included rather than silently discarded.
MASK_BINARY_THRESHOLD = 50

# Maximum pixel intensity value (white) used with cv2.threshold and
# cv2.drawContours.
PIXEL_MAX_VALUE = 255

# ---------------------------------------------------------------------------
# POLYGON / CONTOUR QUALITY CONTROLS
# ---------------------------------------------------------------------------

# Minimum number of vertices an approxPolyDP result must have to be
# considered a valid polygon.  Triangles (3 pts) are the smallest valid shape.
MIN_POLYGON_VERTICES = 3

# Minimum number of flat coordinates (x,y pairs) a polygon list must contain
# to be stored in the COCO output.  6 = 3 coordinate pairs = triangle.
MIN_POLYGON_COORDS = 6

# Lenient lower bound used when preserving even degenerate polygons: keeps
# anything with at least 1 point (2 coordinate values).
MIN_POLYGON_COORDS_LENIENT = 2

# Number of coordinate values in a rectangle polygon
# (4 corners × 2 values each).
RECT_POLYGON_COORD_COUNT = 8

# Minimum number of contour points required by cv2.minAreaRect.
MIN_CONTOUR_POINTS_FOR_MIN_AREA_RECT = 5

# ---------------------------------------------------------------------------
# APPROXPOLYDP EPSILON FALLBACK
# ---------------------------------------------------------------------------

# Scale factor applied to the original epsilon when retrying approxPolyDP
# with a tighter tolerance on thin/elongated contours.
APPROX_POLY_EPSILON_REDUCTION = 0.1

# Absolute floor for the reduced epsilon so it never collapses to zero.
APPROX_POLY_EPSILON_MIN = 0.5

# ---------------------------------------------------------------------------
# ANNOTATION AREA
# ---------------------------------------------------------------------------

# Minimum area value assigned to any annotation.  Prevents zero-area entries
# which can cause division-by-zero in downstream evaluation tools.
MIN_ANNOTATION_AREA = 0.1

# ---------------------------------------------------------------------------
# CONTOUR FILTERING (tile splitting)
# ---------------------------------------------------------------------------

# Minimum contour area (pixels²) kept when splitting annotations into tiles.
# Contours smaller than this are considered noise and discarded.
MIN_TILE_CONTOUR_AREA = 50

# ---------------------------------------------------------------------------
# JSON OUTPUT
# ---------------------------------------------------------------------------

# Indentation level used when serialising COCO JSON files to disk.
JSON_INDENT = 2

# ---------------------------------------------------------------------------
# TILE CONFIGURATION
# ---------------------------------------------------------------------------

# Width and height (pixels) of each output tile.
TILE_WIDTH = 1250
TILE_HEIGHT = 1180

# ---------------------------------------------------------------------------
# MATPLOTLIB RENDERING
# ---------------------------------------------------------------------------

# DPI used when rendering poly2d annotations to a binary mask via matplotlib.
MATPLOTLIB_DPI = 100

# ---------------------------------------------------------------------------
# CONTOUR APPROXIMATION
# ---------------------------------------------------------------------------

# Multiplier for cv2.arcLength used to compute the epsilon passed to
# cv2.approxPolyDP.  Smaller values produce tighter polygon approximations.
CONTOUR_EPSILON_FACTOR = 0.001
