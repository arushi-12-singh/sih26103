"use client";

/**
 * GIS Environmental Boundary Check.
 *
 * Every value shown here comes from the FastAPI spatial engine via `gisApi` -- the page
 * validates input, renders state, and formats numbers, but performs no geometry,
 * classification, or severity logic of its own. That work belongs to the backend, and
 * duplicating any of it on the client would let the two disagree.
 */

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  Crosshair,
  Gauge,
  History,
  Info,
  LayoutDashboard,
  Loader2,
  Map as MapIcon,
  Search,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Signal,
  WifiOff,
} from "lucide-react";

import {
  BOUNDARY_CATEGORIES,
  CATEGORY_LABELS,
  GisAuthError,
  GisNetworkError,
  GisNotFoundError,
  GisServerError,
  GisValidationError,
  SEVERITY_CLASS,
  STATUS_HEADLINES,
  STATUS_LABELS,
  formatArea,
  formatBuffer,
  formatDistance,
  gisApi,
  type AssessmentRecord,
  type BoundaryCategory,
  type CollisionCheckResponse,
  type GeoJsonFeatureCollection,
} from "@/lib/gis-api";
import { BUFFER_PRESETS, GIS_PROJECTS } from "@/lib/gis-projects";
import {
  DEFAULT_LAYERS,
  LAYER_OPTIONS,
  MapLegend,
  type LayerKey,
  type LayerVisibility,
} from "@/components/gis/map-legend";

// Leaflet reads `window` on import, so the map is client-only.
const BoundaryMap = dynamic(() => import("@/components/gis/boundary-map"), {
  ssr: false,
  loading: () => <div className="gis-map-placeholder">Loading map…</div>,
});

const MANUAL = "MANUAL";

type ErrorState =
  | { kind: "validation"; message: string; fields: string[] }
  | { kind: "auth"; message: string }
  | { kind: "server"; message: string }
  | { kind: "network"; message: string };

function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">
          <Activity size={17} />
        </span>
        <span>PAIMANA</span>
        <small>INTELLIGENCE</small>
      </div>
      <div className="workspace-label">
        WORKSPACE <ChevronDown size={13} />
      </div>
      <div className="workspace-name">
        National Infrastructure <span className="online-dot" />
      </div>
      <nav>
        <p className="nav-label">MONITORING</p>
        <Link href="/" className="nav-item">
          <LayoutDashboard size={17} />
          Overview
        </Link>
        <Link href="/" className="nav-item">
          <ClipboardList size={17} />
          Project portfolio
        </Link>
        <button type="button" className="nav-item">
          <AlertTriangle size={17} />
          Risk signals
          <b>7</b>
        </button>
        <Link href="/gis-check" className="nav-item active" aria-current="page">
          <MapIcon size={17} />
          Geospatial view
        </Link>
        <p className="nav-label second">DECISIONS</p>
        <Link href="/" className="nav-item">
          <ShieldCheck size={17} />
          Interventions
        </Link>
        <Link href="/" className="nav-item">
          <Gauge size={17} />
          Scenario lab
        </Link>
      </nav>
      <div className="sidebar-bottom">
        <button className="nav-item" type="button">
          <Settings size={17} />
          Workspace settings
        </button>
        <div className="user-mini">
          <span>AS</span>
          <div>
            <strong>Ananya Sharma</strong>
            <small>Portfolio director</small>
          </div>
        </div>
      </div>
    </aside>
  );
}

function GisCheckWorkspace() {
  // Deep link from the Project Intelligence report: /gis-check?projectId=EFC-04.
  // Resolved once, at mount, as the initial selection -- after that the dropdown owns
  // the value, so a later re-render cannot fight the user's choice.
  const searchParams = useSearchParams();
  const requestedProjectId = searchParams.get("projectId");
  const initialProject =
    GIS_PROJECTS.find((project) => project.id === requestedProjectId) ?? GIS_PROJECTS[0];

  const [projectId, setProjectId] = useState<string>(initialProject.id);
  const [latitude, setLatitude] = useState<string>(String(initialProject.latitude ?? ""));
  const [longitude, setLongitude] = useState<string>(String(initialProject.longitude ?? ""));
  const [bufferMeters, setBufferMeters] = useState<number>(2000);
  const [customBuffer, setCustomBuffer] = useState<string>("2000");
  const [useCustomBuffer, setUseCustomBuffer] = useState(false);
  const [categories, setCategories] = useState<BoundaryCategory[]>([]);

  const [isChecking, setIsChecking] = useState(false);
  const [result, setResult] = useState<CollisionCheckResponse | null>(null);
  const [error, setError] = useState<ErrorState | null>(null);
  const [highlightedId, setHighlightedId] = useState<string | null>(null);

  const [contextCollection, setContextCollection] = useState<GeoJsonFeatureCollection | null>(null);
  const [layers, setLayers] = useState<LayerVisibility>(DEFAULT_LAYERS);
  const [history, setHistory] = useState<AssessmentRecord[] | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [saveNote, setSaveNote] = useState<string | null>(null);

  const resultRef = useRef<HTMLDivElement | null>(null);

  const selectedProject = useMemo(
    () => GIS_PROJECTS.find((project) => project.id === projectId) ?? null,
    [projectId],
  );

  /** Selecting a project with a surveyed location fills the coordinates automatically. */
  const onSelectProject = (value: string) => {
    setProjectId(value);
    setResult(null);
    setError(null);
    setHistory(null);
    setSaveNote(null);
    const project = GIS_PROJECTS.find((item) => item.id === value);
    if (project?.latitude != null && project?.longitude != null) {
      setLatitude(String(project.latitude));
      setLongitude(String(project.longitude));
    } else if (value !== MANUAL) {
      // A project without a surveyed location: clear the fields so the user enters them.
      setLatitude("");
      setLongitude("");
    }
  };

  const toggleCategory = (category: BoundaryCategory) => {
    setCategories((current) =>
      current.includes(category) ? current.filter((item) => item !== category) : [...current, category],
    );
  };

  const effectiveBuffer = useCustomBuffer ? Number(customBuffer) : bufferMeters;

  /** Client-side checks mirror the backend's, so obvious mistakes never need a round trip. */
  const validate = (buffer: number = effectiveBuffer): ErrorState | null => {
    const fields: string[] = [];
    const messages: string[] = [];
    const lat = Number(latitude);
    const lon = Number(longitude);

    if (latitude.trim() === "" || Number.isNaN(lat)) {
      fields.push("latitude");
      messages.push("Latitude is required and must be a number");
    } else if (lat < -90 || lat > 90) {
      fields.push("latitude");
      messages.push("Latitude must be between -90 and 90");
    }

    if (longitude.trim() === "" || Number.isNaN(lon)) {
      fields.push("longitude");
      messages.push("Longitude is required and must be a number");
    } else if (lon < -180 || lon > 180) {
      fields.push("longitude");
      messages.push("Longitude must be between -180 and 180");
    }

    if (Number.isNaN(buffer) || buffer < 0) {
      fields.push("buffer_meters");
      messages.push("Buffer must be a positive distance in metres");
    } else if (buffer > 100_000) {
      fields.push("buffer_meters");
      messages.push("Buffer must not exceed 100,000 m");
    }

    return fields.length ? { kind: "validation", message: messages.join(". "), fields } : null;
  };

  const toErrorState = (caught: unknown): ErrorState => {
    if (caught instanceof GisValidationError)
      return { kind: "validation", message: caught.message, fields: caught.fields };
    if (caught instanceof GisAuthError) return { kind: "auth", message: caught.message };
    if (caught instanceof GisNetworkError) return { kind: "network", message: caught.message };
    if (caught instanceof GisServerError) return { kind: "server", message: caught.message };
    return { kind: "server", message: caught instanceof Error ? caught.message : "Unexpected error" };
  };

  const runCheck = useCallback(async (bufferOverride?: number) => {
    const buffer = bufferOverride ?? effectiveBuffer;
    const invalid = validate(buffer);
    if (invalid) {
      setError(invalid);
      setResult(null);
      return;
    }

    setIsChecking(true);
    setError(null);
    setSaveNote(null);
    try {
      const response = await gisApi.checkCollision({
        project_id: projectId === MANUAL ? null : projectId,
        latitude: Number(latitude),
        longitude: Number(longitude),
        buffer_meters: buffer,
        categories: categories.length ? categories : null,
      });
      setResult(response);
    } catch (caught) {
      setError(toErrorState(caught));
      setResult(null);
    } finally {
      setIsChecking(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, latitude, longitude, effectiveBuffer, categories]);

  // The context layer -- every stored boundary, so the map shows the surrounding
  // regulated landscape and not only whatever the last check happened to flag. Fetched
  // once; a failure here is not surfaced as an error because it degrades the map's
  // background, not the analysis the user asked for.
  useEffect(() => {
    let cancelled = false;
    gisApi
      .getBoundaries({ include_geometry: true, limit: 500 })
      .then((response) => {
        if (!cancelled) setContextCollection(response.geojson);
      })
      .catch(() => {
        if (!cancelled) setContextCollection(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Move focus to the result region once an analysis finishes, so keyboard and screen
  // reader users land on the answer instead of hunting for it.
  useEffect(() => {
    if (result && resultRef.current) resultRef.current.focus();
  }, [result]);

  /**
   * Changing the buffer changes the question, so the answer is recomputed by the
   * backend -- never reinterpreted, scaled, or reused on the client. The new radius is
   * passed straight through rather than read back from state, so the request always
   * carries the value the user just picked. Only re-runs once a first result exists, so
   * the initial analysis stays an explicit action.
   */
  const applyBuffer = (meters: number, custom: boolean) => {
    setUseCustomBuffer(custom);
    if (custom) setCustomBuffer(String(meters));
    else setBufferMeters(meters);
    if (result && Number.isFinite(meters)) void runCheck(meters);
  };

  const loadHistory = async () => {
    if (!selectedProject || projectId === MANUAL) return;
    try {
      const response = await gisApi.getAssessmentHistory(projectId, 5);
      setHistory(response.assessments);
    } catch (caught) {
      // A project with no recorded assessments is an empty state, not a failure.
      if (caught instanceof GisNotFoundError) setHistory([]);
      else setError(toErrorState(caught));
    }
  };

  const saveAssessment = async () => {
    if (!result || projectId === MANUAL) return;
    setIsSaving(true);
    setSaveNote(null);
    try {
      await gisApi.createAssessment({
        project_id: projectId,
        latitude: result.project_location.latitude,
        longitude: result.project_location.longitude,
        buffer_meters: result.buffer_meters,
        categories: categories.length ? categories : null,
      });
      setSaveNote("Assessment recorded against this project.");
      await loadHistory();
    } catch (caught) {
      setError(toErrorState(caught));
    } finally {
      setIsSaving(false);
    }
  };

  const fieldInvalid = (field: string) => error?.kind === "validation" && error.fields.includes(field);
  const statusKey = result?.overall_status ?? null;
  const severityClass = result?.overall_severity ? SEVERITY_CLASS[result.overall_severity] : "stable";

  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main-content">
        <header className="topbar">
          <div className="mobile-brand">
            <MapIcon size={20} />
            <strong>PAIMANA</strong>
          </div>
          <div className="breadcrumb">
            <Link href="/" className="back-link">
              <ArrowLeft size={13} /> Portfolio
            </Link>{" "}
            / <strong>GIS BOUNDARY CHECK</strong>
          </div>
          <div className="top-actions">
            <button className="icon-button search-trigger" type="button">
              <Search size={18} />
              <span>Search projects</span>
              <kbd>Cmd K</kbd>
            </button>
            <div className="avatar">AS</div>
          </div>
        </header>

        <div className="gis-wrap">
          <header className="gis-header">
            <div>
              <div className="eyebrow">
                <span className="live-dot" /> SPATIAL SCREENING
              </div>
              <h1>GIS Environmental Boundary Check</h1>
              <p>
                Screen infrastructure projects for potential spatial conflicts with protected and
                regulated geographic zones.
              </p>
            </div>
          </header>

          <div className="gis-layout">
            {/* ----------------------------- Control panel ----------------------------- */}
            <section className="gis-controls" aria-label="Analysis parameters">
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void runCheck();
                }}
              >
                <div className="gis-field">
                  <label htmlFor="gis-project">Existing project</label>
                  <div className="gis-select-wrap">
                    <select
                      id="gis-project"
                      value={projectId}
                      onChange={(event) => onSelectProject(event.target.value)}
                    >
                      {GIS_PROJECTS.map((project) => (
                        <option key={project.id} value={project.id}>
                          {project.name} · {project.id}
                        </option>
                      ))}
                      <option value={MANUAL}>Manual coordinate entry</option>
                    </select>
                    <ChevronDown size={14} aria-hidden />
                  </div>
                  <small>
                    {selectedProject
                      ? selectedProject.latitude != null
                        ? `${selectedProject.sector} · ${selectedProject.state} · coordinates auto-filled`
                        : `${selectedProject.sector} · ${selectedProject.state} · no surveyed location, enter coordinates below`
                      : "Enter coordinates manually below"}
                  </small>
                </div>

                <div className="gis-field-row">
                  <div className="gis-field">
                    <label htmlFor="gis-lat">Latitude</label>
                    <input
                      id="gis-lat"
                      inputMode="decimal"
                      value={latitude}
                      onChange={(event) => setLatitude(event.target.value)}
                      placeholder="26.8467"
                      aria-invalid={fieldInvalid("latitude")}
                      className={fieldInvalid("latitude") ? "invalid" : undefined}
                    />
                  </div>
                  <div className="gis-field">
                    <label htmlFor="gis-lon">Longitude</label>
                    <input
                      id="gis-lon"
                      inputMode="decimal"
                      value={longitude}
                      onChange={(event) => setLongitude(event.target.value)}
                      placeholder="80.9462"
                      aria-invalid={fieldInvalid("longitude")}
                      className={fieldInvalid("longitude") ? "invalid" : undefined}
                    />
                  </div>
                </div>

                <fieldset className="gis-fieldset">
                  <legend>Buffer distance</legend>
                  <div className="gis-buffer-options" role="group">
                    {BUFFER_PRESETS.map((preset) => (
                      <button
                        key={preset.meters}
                        type="button"
                        className={!useCustomBuffer && bufferMeters === preset.meters ? "gis-chip active" : "gis-chip"}
                        aria-pressed={!useCustomBuffer && bufferMeters === preset.meters}
                        onClick={() => applyBuffer(preset.meters, false)}
                      >
                        {preset.label}
                      </button>
                    ))}
                    <button
                      type="button"
                      className={useCustomBuffer ? "gis-chip active" : "gis-chip"}
                      aria-pressed={useCustomBuffer}
                      onClick={() => setUseCustomBuffer(true)}
                      title="Enter an exact buffer radius in metres"
                    >
                      Custom
                    </button>
                  </div>
                  {useCustomBuffer && (
                    <div className="gis-custom-buffer">
                      <label htmlFor="gis-buffer-custom">Custom buffer (metres)</label>
                      <input
                        id="gis-buffer-custom"
                        inputMode="numeric"
                        value={customBuffer}
                        onChange={(event) => setCustomBuffer(event.target.value)}
                        onBlur={(event) => {
                          // On commit, not on every keystroke: re-running mid-typing would
                          // fire an analysis for "5" on the way to "5000".
                          const meters = Number(event.target.value);
                          if (result && Number.isFinite(meters)) void runCheck(meters);
                        }}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") event.currentTarget.blur();
                        }}
                        aria-invalid={fieldInvalid("buffer_meters")}
                        className={fieldInvalid("buffer_meters") ? "invalid" : undefined}
                      />
                    </div>
                  )}
                </fieldset>

                <fieldset className="gis-fieldset">
                  <legend>Boundary categories</legend>
                  <p className="gis-hint">All categories are screened unless you narrow the scan.</p>
                  <div className="gis-category-grid">
                    {BOUNDARY_CATEGORIES.map((category) => (
                      <label key={category} className="gis-checkbox">
                        <input
                          type="checkbox"
                          checked={categories.includes(category)}
                          onChange={() => toggleCategory(category)}
                        />
                        <span>{CATEGORY_LABELS[category]}</span>
                      </label>
                    ))}
                  </div>
                  {categories.length > 0 && (
                    <button type="button" className="gis-link-button" onClick={() => setCategories([])}>
                      Clear {categories.length} filter{categories.length > 1 ? "s" : ""}
                    </button>
                  )}
                </fieldset>

                <button className="gis-primary-button" type="submit" disabled={isChecking}>
                  {isChecking ? <Loader2 size={15} className="gis-spin" /> : <Crosshair size={15} />}
                  {isChecking ? "Analyzing…" : "Check Boundaries"}
                </button>
              </form>

              {error && (
                <div className={`gis-alert gis-alert-${error.kind}`} role="alert">
                  {error.kind === "network" ? (
                    <WifiOff size={15} />
                  ) : error.kind === "auth" ? (
                    <ShieldAlert size={15} />
                  ) : (
                    <AlertTriangle size={15} />
                  )}
                  <div>
                    <strong>
                      {error.kind === "validation"
                        ? "Check your input"
                        : error.kind === "auth"
                          ? "GIS service not authorised"
                          : error.kind === "network"
                            ? "Cannot reach the GIS service"
                            : "GIS service error"}
                    </strong>
                    <p>{error.message}</p>
                    {error.kind !== "validation" && (
                      <button type="button" className="gis-link-button" onClick={() => void runCheck()}>
                        Retry
                      </button>
                    )}
                  </div>
                </div>
              )}
            </section>

            {/* ------------------------------- Map + results ------------------------------- */}
            <section className="gis-results" aria-label="Analysis result">
              <div className="gis-map-panel">
                <div className="gis-map-head">
                  <h2>Spatial context</h2>
                  <div className="gis-layer-toggles" role="group" aria-label="Layer visibility">
                    {LAYER_OPTIONS.map((option) => (
                      <label key={option.key} className="gis-layer-toggle">
                        <input
                          type="checkbox"
                          checked={layers[option.key]}
                          onChange={() =>
                            setLayers((current) => ({
                              ...current,
                              [option.key as LayerKey]: !current[option.key],
                            }))
                          }
                        />
                        <span>{option.label}</span>
                      </label>
                    ))}
                  </div>
                </div>
                <div className="gis-map-frame">
                  <BoundaryMap
                    collection={result?.geojson ?? null}
                    contextCollection={contextCollection}
                    latitude={result ? result.project_location.latitude : Number(latitude) || null}
                    longitude={result ? result.project_location.longitude : Number(longitude) || null}
                    highlightedId={highlightedId}
                    layers={layers}
                    onLayersChange={setLayers}
                    containsDemoData={
                      result?.contains_demo_data ??
                      (contextCollection?.features.some((f) => f.properties?.is_demo === true) ?? false)
                    }
                  />
                  {isChecking && (
                    <div className="gis-map-overlay" role="status">
                      <Loader2 size={20} className="gis-spin" />
                      <span>Analyzing geographic boundaries…</span>
                    </div>
                  )}
                </div>
                <MapLegend
                  presentCategories={[
                    ...(result?.collisions ?? []).map((c) => c.category),
                    ...((contextCollection?.features ?? [])
                      .map((f) => f.properties?.category)
                      .filter((c): c is string => typeof c === "string") as never[]),
                  ]}
                />
              </div>

              <div
                className="gis-result-panel"
                ref={resultRef}
                tabIndex={-1}
                aria-live="polite"
                aria-busy={isChecking}
              >
                {isChecking && (
                  <div className="gis-state gis-state-loading">
                    <Loader2 size={22} className="gis-spin" />
                    <p>Analyzing geographic boundaries…</p>
                    <small>Running exact geometry intersection against the boundary dataset.</small>
                  </div>
                )}

                {!isChecking && !result && !error && (
                  <div className="gis-state gis-state-empty">
                    <MapIcon size={22} />
                    <p>No analysis run yet</p>
                    <small>
                      Select a project or enter coordinates, choose a buffer distance, then run
                      Check Boundaries.
                    </small>
                  </div>
                )}

                {!isChecking && !result && error && error.kind !== "validation" && (
                  <div className="gis-state gis-state-empty">
                    <Info size={22} />
                    <p>No result to display</p>
                    <small>Resolve the error shown in the control panel, then run the check again.</small>
                  </div>
                )}

                {!isChecking && result && statusKey && (
                  <>
                    <div className={`gis-verdict gis-verdict-${severityClass}`}>
                      <div className="gis-verdict-head">
                        {statusKey === "CLEAR" ? <ShieldCheck size={20} /> : <ShieldAlert size={20} />}
                        <div>
                          <strong>
                            {statusKey === "CLEAR"
                              ? "CLEAR"
                              : `${result.overall_severity} RISK`}
                          </strong>
                          <p>{STATUS_HEADLINES[statusKey]}</p>
                        </div>
                        <span className={`risk-pill ${severityClass}`}>
                          <i />
                          {STATUS_LABELS[statusKey]}
                        </span>
                      </div>

                      <dl className="gis-verdict-stats">
                        <div>
                          <dt>Buffer</dt>
                          <dd>{formatBuffer(result.buffer_meters)}</dd>
                        </div>
                        <div>
                          <dt>Boundaries checked</dt>
                          <dd>{result.boundaries_checked}</dd>
                        </div>
                        <div>
                          <dt>Collisions</dt>
                          <dd>{result.collision_count}</dd>
                        </div>
                        <div>
                          <dt>Clearance flags</dt>
                          <dd>{result.clearance_flags.length}</dd>
                        </div>
                        <div>
                          <dt>Severity</dt>
                          <dd>{result.overall_severity ?? "None"}</dd>
                        </div>
                      </dl>
                      <p className="gis-summary">{result.summary}</p>
                    </div>

                    {result.clearance_flags.length > 0 && (
                      <div className="gis-block">
                        <h3>Clearance flags</h3>
                        <ul className="gis-flag-list">
                          {result.clearance_flags.map((flag) => (
                            <li key={flag.boundary_id}>
                              <span className={`risk-pill ${SEVERITY_CLASS[flag.severity]}`}>
                                <i />
                                {flag.severity}
                              </span>
                              <div>
                                <strong>{flag.boundary_name}</strong>
                                <p>{flag.reason}</p>
                              </div>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <div className="gis-block">
                      <h3>
                        Detected boundaries
                        <span>{result.collisions.length}</span>
                      </h3>
                      {result.collisions.length === 0 ? (
                        <div className="gis-state gis-state-clear">
                          <CheckCircle2 size={20} />
                          <p>No boundaries intersect or lie near this location</p>
                          <small>
                            Screened {result.boundaries_checked} boundaries within{" "}
                            {formatBuffer(result.proximity_threshold_meters)} of the project point.
                          </small>
                        </div>
                      ) : (
                        <div className="gis-table" role="table">
                          <div className="gis-table-head" role="row">
                            <span role="columnheader">BOUNDARY</span>
                            <span role="columnheader">TYPE</span>
                            <span role="columnheader">DISTANCE</span>
                            <span role="columnheader">OVERLAP</span>
                            <span role="columnheader">SEVERITY</span>
                          </div>
                          {result.collisions.map((collision) => (
                            <div
                              key={collision.boundary_id}
                              className="gis-table-row"
                              role="row"
                              tabIndex={0}
                              onMouseEnter={() => setHighlightedId(collision.boundary_id)}
                              onMouseLeave={() => setHighlightedId(null)}
                              onFocus={() => setHighlightedId(collision.boundary_id)}
                              onBlur={() => setHighlightedId(null)}
                            >
                              <span role="cell" className="gis-boundary-name">
                                <strong>{collision.boundary_name}</strong>
                                <small>
                                  {collision.category_label}
                                  {collision.district ? ` · ${collision.district}` : ""}
                                  {collision.is_demo ? " · demo data" : ""}
                                </small>
                              </span>
                              <span role="cell" className="gis-collision-type">
                                {STATUS_LABELS[collision.collision_type]}
                              </span>
                              <span role="cell">{formatDistance(collision.distance_meters)}</span>
                              <span role="cell">
                                {collision.buffer_overlap_percentage > 0
                                  ? `${collision.buffer_overlap_percentage.toFixed(1)}%`
                                  : "--"}
                                <small>{formatArea(collision.intersection_area_sqm)}</small>
                              </span>
                              <span role="cell">
                                <span className={`risk-pill ${SEVERITY_CLASS[collision.severity]}`}>
                                  <i />
                                  {collision.severity}
                                </span>
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    <div className="gis-actions">
                      {projectId !== MANUAL && (
                        <button
                          type="button"
                          className="gis-secondary-button"
                          onClick={() => void saveAssessment()}
                          disabled={isSaving}
                        >
                          {isSaving ? <Loader2 size={14} className="gis-spin" /> : <ClipboardList size={14} />}
                          Record assessment
                        </button>
                      )}
                      {projectId !== MANUAL && (
                        <button type="button" className="gis-secondary-button" onClick={() => void loadHistory()}>
                          <History size={14} />
                          Assessment history
                        </button>
                      )}
                      {saveNote && (
                        <span className="gis-save-note">
                          <CheckCircle2 size={13} /> {saveNote}
                        </span>
                      )}
                    </div>

                    {history !== null && (
                      <div className="gis-block">
                        <h3>
                          Assessment history
                          <span>{history.length}</span>
                        </h3>
                        {history.length === 0 ? (
                          <div className="gis-state gis-state-empty compact">
                            <Info size={18} />
                            <p>No assessments recorded for this project yet</p>
                          </div>
                        ) : (
                          <ul className="gis-history">
                            {history.map((record) => (
                              <li key={record.id}>
                                <span className={`risk-pill ${record.overall_severity ? SEVERITY_CLASS[record.overall_severity] : "stable"}`}>
                                  <i />
                                  {record.overall_status === "CLEAR" ? "CLEAR" : record.overall_severity}
                                </span>
                                <div>
                                  <strong>{STATUS_LABELS[record.overall_status]}</strong>
                                  <small>
                                    {formatBuffer(record.buffer_meters)} buffer ·{" "}
                                    {record.intersecting_boundaries.length} boundaries ·{" "}
                                    {new Date(record.created_at).toLocaleString()} · {record.created_by}
                                  </small>
                                </div>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}

                    {result.notice && (
                      <p className="gis-notice">
                        <Signal size={13} /> {result.notice}
                      </p>
                    )}
                    <p className="gis-provenance">
                      Computed in {result.projected_crs.startsWith("+proj") ? "a local azimuthal-equidistant CRS" : result.projected_crs}
                      {" · "}
                      {result.candidates_examined} of {result.boundaries_checked} boundaries required exact
                      geometry testing
                    </p>
                  </>
                )}
              </div>
            </section>
          </div>
        </div>
      </main>
    </div>
  );
}

export default function GisCheckPage() {
  return (
    <Suspense fallback={<div className="gis-map-placeholder">Loading GIS workspace…</div>}>
      <GisCheckWorkspace />
    </Suspense>
  );
}
