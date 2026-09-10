"""Central configuration for the GIS Spatial Analysis Engine (Feature 5).

Every threshold, weight, and classification rule the engine uses lives here. Nothing in
app/services/spatial_analysis_service.py hardcodes a distance, a percentage, or a
severity -- it reads them from this module, so the methodology can be retuned (or
replaced per deployment) without touching the geometry code.

The engine is pure, deterministic computational geometry: the same coordinates and the
same boundary dataset always produce the same answer. No model, no LLM, no text
matching, no heuristics over names.
"""

from __future__ import annotations

# --- Buffer ------------------------------------------------------------------------
# The buffer radius is supplied per request; these bound what is accepted. 0 is allowed
# and means "test the bare point" (DIRECT_COLLISION and NEARBY still apply).
DEFAULT_BUFFER_METERS: float = 1_000.0
MIN_BUFFER_METERS: float = 0.0
MAX_BUFFER_METERS: float = 100_000.0

# Quadrant segments used when generating the buffer circle. Higher is a closer circle:
# 16 gives a 64-sided polygon, whose area is within ~0.13% of a true circle. Fixed here
# rather than left to Shapely's default so buffer areas -- and therefore overlap
# percentages -- are reproducible across Shapely versions.
BUFFER_QUAD_SEGMENTS: int = 16

# --- Proximity (the NEARBY band) ---------------------------------------------------
# A boundary that the buffer does not touch is still reported when it lies within the
# proximity threshold. The effective threshold is the LARGER of an absolute floor and a
# multiple of the request's own buffer, so a large buffer automatically widens the
# advisory band instead of being capped by a fixed number.
NEARBY_THRESHOLD_METERS: float = 5_000.0
NEARBY_BUFFER_MULTIPLIER: float = 2.0

# --- Projection --------------------------------------------------------------------
# "aeqd": an azimuthal-equidistant CRS centred on the project point. Distance from that
#         point -- the primary measurement this engine reports -- is exact, and the
#         metre buffer is a true circle. Area distortion grows with distance from the
#         centre but is negligible at the scales involved (well under 1% within ~100 km).
# "utm":  the point's UTM zone. Conformal with ~0.04-0.1% scale error; preferred when
#         results must line up with an existing UTM-based workflow. Degrades near zone
#         edges for boundaries spanning zones.
#
# Either way a metre buffer is NEVER constructed in degrees. Degrees are used only for
# the bounding-box prefilter that feeds the spatial index, and that envelope is derived
# by projecting the real metre buffer back to WGS84 -- never by treating metres as
# degrees.
PROJECTION_STRATEGY: str = "aeqd"

# The CRS everything is stored and exchanged in (matches gis_config.STORAGE_CRS).
GEOGRAPHIC_CRS: str = "EPSG:4326"

# Distance (metres) within which the project point is treated as lying ON a boundary
# rather than outside it. Coordinate transforms are floating-point, so a point that is
# mathematically on a boundary's edge lands a fraction of a picometre off it, and which
# side is arbitrary. Anything below this is far under any meaningful survey precision, so
# resolving it toward "inside" makes the conservative classification (DIRECT_COLLISION)
# reliable instead of down to rounding.
EDGE_TOLERANCE_METERS: float = 1e-6

# --- Collision classification ------------------------------------------------------
# Precedence when summarizing several boundaries into one overall verdict: the most
# severe collision type present wins. Ascending.
COLLISION_TYPE_PRECEDENCE: tuple[str, ...] = ("CLEAR", "NEARBY", "BUFFER_COLLISION", "DIRECT_COLLISION")

# --- Severity ----------------------------------------------------------------------
# Severity is an integer rank, computed once, from three additive terms:
#
#     rank = COLLISION_TYPE_BASE_RANK[type]
#          + CATEGORY_SENSITIVITY[category]
#          + overlap escalation (OVERLAP_ESCALATION_RULES)
#
# clamped into SEVERITY_BY_RANK. Additive integer ranks keep the rule auditable: given a
# result you can reconstruct exactly which term produced the severity, which a blended
# floating-point score would not allow.
COLLISION_TYPE_BASE_RANK: dict[str, int] = {
    "DIRECT_COLLISION": 3,
    "BUFFER_COLLISION": 2,
    "NEARBY": 1,
}

# How much a category's protection status escalates severity. Statutorily strict
# designations (national parks, tiger reserves, sanctuaries, Ramsar sites) add a rank;
# advisory or general-restriction areas do not.
CATEGORY_SENSITIVITY: dict[str, int] = {
    "NATIONAL_PARK": 1,
    "TIGER_RESERVE": 1,
    "WILDLIFE_SANCTUARY": 1,
    "RAMSAR_WETLAND": 1,
    "ECO_SENSITIVE_ZONE": 0,
    "FOREST": 0,
    "OTHER_RESTRICTED_ZONE": 0,
}
CATEGORY_SENSITIVITY_DEFAULT: int = 0

# Ascending (buffer_overlap_percentage, additional_rank). A large share of the project's
# buffer sitting inside a restricted area is materially worse than grazing its edge.
OVERLAP_ESCALATION_RULES: tuple[tuple[float, int], ...] = ((25.0, 1), (60.0, 2))

SEVERITY_BY_RANK: dict[int, str] = {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "CRITICAL"}
MIN_SEVERITY_RANK: int = min(SEVERITY_BY_RANK)
MAX_SEVERITY_RANK: int = max(SEVERITY_BY_RANK)
SEVERITY_PRECEDENCE: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# --- Clearance ---------------------------------------------------------------------
# Whether a boundary's collision flags the project as needing environmental clearance.
# A boundary triggers the flag when its collision type is in the first set, OR its
# category is in the second (categories where even proximity warrants a formal check).
CLEARANCE_REQUIRED_COLLISION_TYPES: frozenset[str] = frozenset({"DIRECT_COLLISION", "BUFFER_COLLISION"})
CLEARANCE_ON_PROXIMITY_CATEGORIES: frozenset[str] = frozenset(
    {"NATIONAL_PARK", "TIGER_RESERVE", "WILDLIFE_SANCTUARY", "ECO_SENSITIVE_ZONE"}
)

# --- Output ------------------------------------------------------------------------
# Rounding applied to reported measurements. Fixed so repeated identical requests return
# byte-identical numbers.
DISTANCE_DECIMALS: int = 2
AREA_DECIMALS: int = 2
PERCENTAGE_DECIMALS: int = 4


def _validate() -> None:
    if not MIN_BUFFER_METERS <= DEFAULT_BUFFER_METERS <= MAX_BUFFER_METERS:
        raise ValueError("DEFAULT_BUFFER_METERS must lie within [MIN_BUFFER_METERS, MAX_BUFFER_METERS].")
    if PROJECTION_STRATEGY not in {"aeqd", "utm"}:
        raise ValueError(f"PROJECTION_STRATEGY must be 'aeqd' or 'utm', got {PROJECTION_STRATEGY!r}.")
    if sorted(SEVERITY_BY_RANK) != list(range(MIN_SEVERITY_RANK, MAX_SEVERITY_RANK + 1)):
        raise ValueError("SEVERITY_BY_RANK must map a contiguous range of ranks.")
    if tuple(SEVERITY_BY_RANK[rank] for rank in sorted(SEVERITY_BY_RANK)) != SEVERITY_PRECEDENCE:
        raise ValueError("SEVERITY_BY_RANK and SEVERITY_PRECEDENCE disagree on severity ordering.")
    thresholds = [threshold for threshold, _ in OVERLAP_ESCALATION_RULES]
    if thresholds != sorted(thresholds):
        raise ValueError("OVERLAP_ESCALATION_RULES must be in ascending threshold order.")
    if set(COLLISION_TYPE_BASE_RANK) | {"CLEAR"} != set(COLLISION_TYPE_PRECEDENCE):
        raise ValueError("COLLISION_TYPE_BASE_RANK and COLLISION_TYPE_PRECEDENCE cover different collision types.")


_validate()
