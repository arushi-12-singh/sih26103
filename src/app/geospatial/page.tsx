"use client";

import { useState } from "react";
import { Map } from "lucide-react";
import { Sidebar } from "../page";
import GISCollisionChecker from "@/components/gis/GISCollisionChecker";

export default function GeospatialPage() {
  const [active, setActive] = useState("Geospatial view");
  return <div className="app-shell"><Sidebar active={active} setActive={setActive} /><main className="main-content"><header className="topbar"><div className="breadcrumb"><strong>GEOSPATIAL VIEW</strong></div></header><div className="content-wrap"><header className="intro"><div><span className="eyebrow"><Map size={13} /> MONITORING</span><h1>Geospatial view</h1><p>Infrastructure map coverage for the current workspace.</p></div></header><GISCollisionChecker /></div></main></div>;
}
