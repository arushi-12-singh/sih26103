"use client";

import { useState } from "react";
import { ArrowUpRight, Gauge } from "lucide-react";
import { useRouter } from "next/navigation";
import { Sidebar } from "../page";

export default function ScenarioPage() {
  const router = useRouter();
  const [active, setActive] = useState("Scenario lab");
  return <div className="app-shell"><Sidebar active={active} setActive={setActive} /><main className="main-content"><header className="topbar"><div className="breadcrumb"><strong>SCENARIO LAB</strong></div></header><div className="content-wrap"><header className="intro"><div><span className="eyebrow"><Gauge size={13} /> DECISION SUPPORT</span><h1>Scenario lab</h1><p>Open a project intelligence report to adjust its intervention estimates.</p></div></header><section className="signal-panel state-page-panel"><div className="signal-icon"><Gauge size={17} /></div><span className="eyebrow">PROJECT REQUIRED</span><h3>Choose a project before simulating.</h3><p>The scenario simulator is available inside each project intelligence report and remains labeled as model estimates only.</p><button className="dark-button" onClick={() => router.push("/dashboard")}>Open dashboard <ArrowUpRight size={14} /></button></section></div></main></div>;
}
