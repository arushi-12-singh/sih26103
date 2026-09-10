"use client";

import { useState } from "react";
import { AlertTriangle, Map } from "lucide-react";
import { Sidebar } from "../page";

export default function GeospatialPage() {
  const [active, setActive] = useState("Geospatial view");
  return <div className="app-shell"><Sidebar active={active} setActive={setActive} /><main className="main-content"><header className="topbar"><div className="breadcrumb"><strong>GEOSPATIAL VIEW</strong></div></header><div className="content-wrap"><header className="intro"><div><span className="eyebrow"><Map size={13} /> MONITORING</span><h1>Geospatial view</h1><p>Infrastructure map coverage for the current workspace.</p></div></header><section className="signal-panel state-page-panel"><div className="signal-icon"><AlertTriangle size={17} /></div><span className="eyebrow">DATA UNAVAILABLE</span><h3>Geospatial data is not connected.</h3><p>No map or location service is available in the current backend. Project intelligence remains available through the dashboard.</p></section></div></main></div>;
}
