"use client";

/**
 * GIS Environmental Screening section of the Project Intelligence report.
 *
 * Renders the structured screening signal the backend produced. It computes nothing:
 * status, severity, overlap, and clearance flags all arrive already decided by the
 * spatial engine, and the geometry that produced them stays on the map at /gis-check.
 *
 * Wording: this is SCREENING output, not a permitting determination. The panel shows the
 * backend's approved `advisory` and always renders its `disclaimer` -- neither string is
 * composed here, so the UI cannot drift from the language the backend is required to use.
 */

import Link from "next/link";
import { ArrowUpRight, MapPin, ShieldAlert, ShieldCheck } from "lucide-react";

import { SEVERITY_CLASS, STATUS_LABELS, formatBuffer, formatDistance } from "@/lib/gis-api";
import type { GisScreening } from "@/lib/prediction-api";

export function GisScreeningPanel({
  screening,
  projectId,
}: {
  screening: GisScreening | null;
  projectId: string;
}) {
  const openHref = `/gis-check?projectId=${encodeURIComponent(projectId)}`;

  if (!screening) {
    return (
      <section className="gis-screening-section">
        <div className="section-kicker">
          <span>GIS ENVIRONMENTAL SCREENING</span>
          <span>Feature 4-7</span>
        </div>
        <div className="gis-screening-empty">
          <MapPin size={18} />
          <div>
            <strong>Not screened</strong>
            <p>
              This project has no spatial screening on record. Run a boundary check to add
              environmental evidence to its priority score.
            </p>
          </div>
          <Link href={openHref} className="gis-open-button">
            Open Geospatial View <ArrowUpRight size={14} />
          </Link>
        </div>
      </section>
    );
  }

  const isClear = screening.gis_status === "CLEAR";
  const severityClass = screening.gis_severity ? SEVERITY_CLASS[screening.gis_severity] : "stable";
  const nearest = screening.nearest_boundary;

  return (
    <section className="gis-screening-section">
      <div className="section-kicker">
        <span>GIS ENVIRONMENTAL SCREENING</span>
        <span>{screening.boundaries_checked} boundaries screened</span>
      </div>

      <div className={`gis-screening-head gis-verdict-${severityClass}`}>
        {isClear ? <ShieldCheck size={20} /> : <ShieldAlert size={20} />}
        <div>
          <strong>{screening.advisory}</strong>
          <p>
            {isClear
              ? `No restricted boundary intersects the ${formatBuffer(screening.buffer_meters)} screening buffer.`
              : `${STATUS_LABELS[screening.gis_status]} within the ${formatBuffer(
                  screening.buffer_meters,
                )} screening buffer.`}
          </p>
        </div>
        <Link href={openHref} className="gis-open-button">
          Open Geospatial View <ArrowUpRight size={14} />
        </Link>
      </div>

      <dl className="gis-screening-stats">
        <div>
          <dt>STATUS</dt>
          <dd>{screening.gis_status.replace(/_/g, " ")}</dd>
        </div>
        <div>
          <dt>SEVERITY</dt>
          <dd>
            {screening.gis_severity ? (
              <span className={`risk-pill ${severityClass}`}>
                <i />
                {screening.gis_severity}
              </span>
            ) : (
              "None"
            )}
          </dd>
        </div>
        <div>
          <dt>BUFFER</dt>
          <dd>{formatBuffer(screening.buffer_meters)}</dd>
        </div>
        <div>
          <dt>CONFLICTS</dt>
          <dd>{screening.collision_count}</dd>
        </div>
        <div>
          <dt>BUFFER OVERLAP</dt>
          <dd>
            {screening.max_buffer_overlap_percentage > 0
              ? `${screening.max_buffer_overlap_percentage.toFixed(1)}%`
              : "--"}
          </dd>
        </div>
      </dl>

      <div className="gis-screening-detail">
        <div>
          <span>AFFECTED BOUNDARY</span>
          {nearest ? (
            <>
              <strong>{nearest.name}</strong>
              <small>
                {nearest.category_label} · {formatDistance(nearest.distance_meters)} ·{" "}
                {STATUS_LABELS[nearest.collision_type]}
              </small>
            </>
          ) : (
            <>
              <strong>None</strong>
              <small>No boundary within the screening radius</small>
            </>
          )}
        </div>
        <div>
          <span>CLEARANCE FLAGS</span>
          {screening.clearance_flag_count === 0 ? (
            <>
              <strong>None raised</strong>
              <small>No statutory review triggered by this screening</small>
            </>
          ) : (
            <ul className="gis-clearance-flags">
              {screening.clearance_flags.map((flag) => (
                <li key={flag}>{flag}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {screening.notice && <p className="gis-screening-notice">{screening.notice}</p>}
      <p className="gis-screening-disclaimer">{screening.disclaimer}</p>
    </section>
  );
}
