"use client";

import { useState } from "react";
import { Check, Settings } from "lucide-react";
import { Sidebar } from "../page";

export default function SettingsPage() {
  const [active, setActive] = useState("Workspace settings");
  const [saved, setSaved] = useState(false);
  return <div className="app-shell"><Sidebar active={active} setActive={setActive} /><main className="main-content"><header className="topbar"><div className="breadcrumb"><strong>WORKSPACE SETTINGS</strong></div></header><div className="content-wrap"><header className="intro"><div><span className="eyebrow"><Settings size={13} /> WORKSPACE</span><h1>Workspace settings</h1><p>Manage the local workspace preferences used by this interface.</p></div></header><section className="signal-panel state-page-panel"><div className="signal-icon"><Settings size={17} /></div><span className="eyebrow">WORKSPACE PROFILE</span><h3>National Infrastructure</h3><p>Portfolio director access is active for Ananya Sharma. Authentication and persistence are not connected in this environment.</p><button className="dark-button" onClick={() => setSaved(true)}>{saved ? <><Check size={14} /> Settings acknowledged</> : "Save workspace state"}</button></section></div></main></div>;
}
