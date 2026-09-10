"use client";

import { useState, useEffect } from "react";
import dynamic from "next/dynamic";
import { AlertTriangle, Check, Compass, Layers, MapPin, RefreshCw, ShieldAlert, ShieldCheck, Sliders, Zap } from "lucide-react";
import { checkGisCollision, type GISCollisionResponse, type ZoneCollision } from "@/lib/prediction-api";

// Dynamically import GISMap to prevent SSR window issues with Leaflet
const GISMap = dynamic(() => import("./GISMap"), { ssr: false });

const PRESET_PROJECTS = [
  { id: "PROJ-UP-01", name: "Dudhwa Highway Corridor Expansion", state: "Uttar Pradesh", sector: "Roads", lat: 28.45, lng: 80.65 },
  { id: "PROJ-UK-02", name: "Corbett Rail Line Doubling", state: "Uttarakhand", sector: "Railways", lat: 29.55, lng: 78.95 },
  { id: "PROJ-MH-03", name: "Mumbai Metro Line 3 Extension (Aarey)", state: "Maharashtra", sector: "Urban Development", lat: 19.16, lng: 72.88 },
  { id: "PROJ-RJ-04", name: "Keoladeo Water Pipeline Augmentation", state: "Rajasthan", sector: "Water Resources", lat: 27.16, lng: 77.52 },
  { id: "PROJ-KA-05", name: "Western Ghats Power Transmission Line", state: "Karnataka", sector: "Power", lat: 13.45, lng: 75.35 },
  { id: "PROJ-OR-06", name: "Chilika Port Connectivity Road", state: "Odisha", sector: "Ports", lat: 19.65, lng: 85.35 },
];

const ZONE_CATEGORIES = [
  "National Park",
  "Tiger Reserve",
  "Wildlife Sanctuary",
  "Ramsar Wetland",
  "Eco-Sensitive Zone",
  "Reserved Forest",
];

export default function GISCollisionChecker() {
  const [selectedPreset, setSelectedPreset] = useState(PRESET_PROJECTS[0]);
  const [lat, setLat] = useState(PRESET_PROJECTS[0].lat);
  const [lng, setLng] = useState(PRESET_PROJECTS[0].lng);
  const [bufferKm, setBufferKm] = useState(5.0);
  const [selectedCategories, setSelectedCategories] = useState<string[]>(ZONE_CATEGORIES);

  const [gisResult, setGisResult] = useState<GISCollisionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runCollisionCheck = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await checkGisCollision({
        project_id: selectedPreset.id,
        latitude: lat,
        longitude: lng,
        buffer_distance_km: bufferKm,
        zone_categories: selectedCategories,
      });
      setGisResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "GIS collision check failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void runCollisionCheck();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lat, lng, bufferKm, selectedCategories]);

  const handlePresetSelect = (presetId: string) => {
    const found = PRESET_PROJECTS.find(p => p.id === presetId);
    if (found) {
      setSelectedPreset(found);
      setLat(found.lat);
      setLng(found.lng);
    }
  };

  const toggleCategory = (cat: string) => {
    if (selectedCategories.includes(cat)) {
      if (selectedCategories.length > 1) {
        setSelectedCategories(selectedCategories.filter(c => c !== cat));
      }
    } else {
      setSelectedCategories([...selectedCategories, cat]);
    }
  };

  return (
    <div className="gis-workspace">
      {/* Header Banner */}
      <header className="gis-header">
        <div>
          <div className="eyebrow"><span className="live-dot" /> SPATIAL BOUNDARY ENGINE</div>
          <h1>GIS Buffer Collision & Environmental Checking</h1>
          <p>Analyze infrastructure project buffer boundaries against legally protected environmental zones across India.</p>
        </div>
        <div className="gis-header-actions">
          <button className="dark-button" onClick={runCollisionCheck} disabled={loading}>
            <RefreshCw size={14} className={loading ? "spin" : ""} /> {loading ? "Computing..." : "Re-calculate spatial buffer"}
          </button>
        </div>
      </header>

      {/* Control Grid & Map Layout */}
      <div className="gis-layout-grid">
        {/* Left Column: Input Controls & Filter Panel */}
        <div className="gis-control-panel">
          {/* Project Selector */}
          <div className="panel-box">
            <h3><MapPin size={15} /> Select Existing Infrastructure Project</h3>
            <div className="preset-selector">
              {PRESET_PROJECTS.map(p => (
                <button
                  key={p.id}
                  className={selectedPreset.id === p.id ? "preset-btn active" : "preset-btn"}
                  onClick={() => handlePresetSelect(p.id)}
                >
                  <strong>{p.name}</strong>
                  <small>{p.sector} · {p.state}</small>
                </button>
              ))}
            </div>
          </div>

          {/* Coordinate Inputs */}
          <div className="panel-box">
            <h3><Compass size={15} /> Project Point Coordinates (WGS84)</h3>
            <div className="coord-inputs">
              <div>
                <label>Latitude (°N)</label>
                <input
                  type="number"
                  step="0.0001"
                  min="6.0"
                  max="37.5"
                  value={lat}
                  onChange={e => setLat(Number(e.target.value))}
                />
              </div>
              <div>
                <label>Longitude (°E)</label>
                <input
                  type="number"
                  step="0.0001"
                  min="68.0"
                  max="97.5"
                  value={lng}
                  onChange={e => setLng(Number(e.target.value))}
                />
              </div>
            </div>
          </div>

          {/* Buffer Radius Slider */}
          <div className="panel-box">
            <div className="slider-header">
              <h3><Sliders size={15} /> Buffer Distance Radius</h3>
              <b className="radius-badge">{bufferKm} km</b>
            </div>
            <input
              type="range"
              min="0.5"
              max="25.0"
              step="0.5"
              value={bufferKm}
              onChange={e => setBufferKm(Number(e.target.value))}
              className="buffer-slider"
            />
            <div className="slider-scale">
              <span>0.5 km</span>
              <span>5 km</span>
              <span>15 km</span>
              <span>25 km</span>
            </div>
          </div>

          {/* Protected Zone Category Filters */}
          <div className="panel-box">
            <h3><Layers size={15} /> Protected Boundary Filter Layers</h3>
            <div className="category-checkboxes">
              {ZONE_CATEGORIES.map(cat => (
                <label key={cat} className="checkbox-item">
                  <input
                    type="checkbox"
                    checked={selectedCategories.includes(cat)}
                    onChange={() => toggleCategory(cat)}
                  />
                  <span>{cat}</span>
                </label>
              ))}
            </div>
          </div>
        </div>

        {/* Right Column: Collision Results & Map */}
        <div className="gis-main-view">
          {/* Executive Summary Card */}
          {gisResult && (
            <div className={`gis-status-banner ${gisResult.highest_severity.toLowerCase()}`}>
              <div className="status-icon">
                {gisResult.has_collision ? <ShieldAlert size={26} /> : <ShieldCheck size={26} />}
              </div>
              <div className="status-info">
                <div className="status-top">
                  <span className={`risk-pill ${gisResult.highest_severity.toLowerCase()}`}>
                    <i /> {gisResult.has_collision ? `${gisResult.highest_severity} COLLISION DETECTED` : "BUFFER CLEAR"}
                  </span>
                  {gisResult.clearance_required && (
                    <span className="clearance-badge">
                      <Zap size={12} /> MANDATORY MOEFCC / NBWL CLEARANCE REQUIRED
                    </span>
                  )}
                </div>
                <h2>{gisResult.summary}</h2>
              </div>
            </div>
          )}

          {error && (
            <div className="prediction-error">
              <AlertTriangle size={16} /> {error}
            </div>
          )}

          {/* Interactive Leaflet Map Container */}
          <div className="map-card-wrapper">
            <div className="map-card-header">
              <span>INTERACTIVE LEAFLET GEOSPATIAL MAP</span>
              <small>Project marker (blue) with {bufferKm} km metric buffer circle and protected area polygons</small>
            </div>
            <div className="map-frame">
              <GISMap
                center={{ lat, lng }}
                bufferDistanceKm={bufferKm}
                geojsonLayers={gisResult?.geojson_layers}
                hasCollision={gisResult?.has_collision}
              />
            </div>
          </div>

          {/* Collision Results Table */}
          {gisResult && gisResult.collisions.length > 0 && (
            <div className="collision-table-panel">
              <div className="table-panel-title">
                <h3>INTERSECTING PROTECTED BOUNDARIES ({gisResult.collisions.length})</h3>
                <p>Distances computed via UTM projection to prevent spatial distortion.</p>
              </div>
              <div className="table-wrap">
                <table className="gis-table">
                  <thead>
                    <tr>
                      <th>BOUNDARY NAME</th>
                      <th>CATEGORY</th>
                      <th>STATE</th>
                      <th>DISTANCE TO BOUNDARY</th>
                      <th>OVERLAP AREA</th>
                      <th>REQUIRED CLEARANCE</th>
                      <th>SEVERITY</th>
                    </tr>
                  </thead>
                  <tbody>
                    {gisResult.collisions.map(col => (
                      <tr key={col.zone_id}>
                        <td>
                          <strong>{col.zone_name}</strong>
                          <small>{col.designation}</small>
                        </td>
                        <td><span className="cat-tag">{col.zone_category}</span></td>
                        <td>{col.state}</td>
                        <td>
                          <b>{col.is_direct_intersection ? "0.0 km (Direct Intersection)" : `${col.distance_to_boundary_km} km`}</b>
                        </td>
                        <td>{col.intersection_area_sq_km > 0 ? `${col.intersection_area_sq_km} sq km` : "Proximity Buffer"}</td>
                        <td><small className="clearance-text">{col.clearance_type_required}</small></td>
                        <td>
                          <span className={`risk-pill ${col.severity.toLowerCase()}`}>
                            <i /> {col.severity}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
