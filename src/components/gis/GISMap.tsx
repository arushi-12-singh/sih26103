"use client";

import { useEffect } from "react";
import { MapContainer, TileLayer, Circle, Marker, Popup, GeoJSON, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

// Fix default Leaflet icon paths in Next.js
const defaultIcon = L.icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});
L.Marker.prototype.options.icon = defaultIcon;

function RecenterMap({ lat, lng }: { lat: number; lng: number }) {
  const map = useMap();
  useEffect(() => {
    map.setView([lat, lng], map.getZoom());
  }, [lat, lng, map]);
  return null;
}

type GISMapProps = {
  center: { lat: number; lng: number };
  bufferDistanceKm: number;
  geojsonLayers?: any;
  hasCollision?: boolean;
};

export default function GISMap({ center, bufferDistanceKm, geojsonLayers, hasCollision }: GISMapProps) {
  const position: [number, number] = [center.lat, center.lng];
  const bufferRadiusMeters = bufferDistanceKm * 1000;

  const getStyleForFeature = (feature: any) => {
    const sev = feature?.properties?.collision_severity;
    const cat = feature?.properties?.category;

    if (sev === "CRITICAL") {
      return { color: "#be4d3c", fillColor: "#fae8e3", fillOpacity: 0.55, weight: 2 };
    }
    if (sev === "HIGH") {
      return { color: "#c16b3f", fillColor: "#fbede0", fillOpacity: 0.45, weight: 2 };
    }
    if (sev === "WARNING") {
      return { color: "#aa893d", fillColor: "#f7f1dc", fillOpacity: 0.35, weight: 1.5 };
    }

    // Default category styling
    switch (cat) {
      case "Tiger Reserve":
      case "National Park":
        return { color: "#bd4f3c", fillColor: "#fae8e3", fillOpacity: 0.3, weight: 1.5 };
      case "Ramsar Wetland":
        return { color: "#4f8a63", fillColor: "#e7f1e8", fillOpacity: 0.3, weight: 1.5 };
      case "Wildlife Sanctuary":
      case "Eco-Sensitive Zone":
        return { color: "#ad893e", fillColor: "#f7f1df", fillOpacity: 0.3, weight: 1.5 };
      default:
        return { color: "#547e9b", fillColor: "#e7eff4", fillOpacity: 0.25, weight: 1 };
    }
  };

  const onEachFeature = (feature: any, layer: L.Layer) => {
    const props = feature?.properties || {};
    if (props.name) {
      const content = `
        <div style="font-family: sans-serif; font-size: 11px; padding: 4px;">
          <strong style="color: #27382e; font-size: 12px; display: block; margin-bottom: 3px;">${props.name}</strong>
          <span style="color: #7d8a81; display: block; font-weight: 600;">Category: ${props.category || 'Protected Zone'}</span>
          <span style="color: #8b968f; display: block; margin-top: 2px;">State: ${props.state || 'N/A'}</span>
          ${props.clearance_type_required ? `<div style="margin-top: 6px; font-size: 10px; color: #be4d3c; font-weight: 600;">Clearance: ${props.clearance_type_required}</div>` : ''}
        </div>
      `;
      layer.bindPopup(content);
    }
  };

  return (
    <div style={{ height: "100%", width: "100%", position: "relative", minHeight: "420px", borderRadius: "6px", overflow: "hidden" }}>
      <MapContainer center={position} zoom={9} style={{ height: "100%", width: "100%" }} scrollWheelZoom={true}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <RecenterMap lat={center.lat} lng={center.lng} />

        {/* Project Location Marker */}
        <Marker position={position}>
          <Popup>
            <div style={{ fontFamily: "sans-serif", fontSize: "11px" }}>
              <strong style={{ display: "block", color: "#27382e" }}>Project Coordinates</strong>
              <span>Lat: {center.lat.toFixed(4)}, Lng: {center.lng.toFixed(4)}</span>
            </div>
          </Popup>
        </Marker>

        {/* Buffer Circle */}
        <Circle
          center={position}
          radius={bufferRadiusMeters}
          pathOptions={{
            color: hasCollision ? "#c95740" : "#4a8a69",
            fillColor: hasCollision ? "#f0d9d3" : "#d3e1d4",
            fillOpacity: 0.2,
            weight: 2,
            dashArray: "6, 6",
          }}
        />

        {/* GeoJSON Protected Area Boundaries Overlay */}
        {geojsonLayers && geojsonLayers.features && (
          <GeoJSON
            key={JSON.stringify(geojsonLayers)}
            data={geojsonLayers}
            style={getStyleForFeature}
            onEachFeature={onEachFeature}
          />
        )}
      </MapContainer>
    </div>
  );
}
