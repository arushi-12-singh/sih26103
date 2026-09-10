"use client";

import { useParams } from "next/navigation";
import Dashboard from "../../page";

export default function ProjectPage() {
  const params = useParams<{ projectId: string }>();
  return <Dashboard projectId={params.projectId} />;
}
