import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

type Zone = {
  id: string;
  name: string;
  description: string;
  policy: { version: number; helmet_required: boolean; vest_required: boolean; persistence_frames: number };
};
type Source = { id: string; name: string; zone_id: string };
type Job = {
  id: string;
  filename: string;
  status: string;
  submitted_at: string;
  completed_at: string | null;
  compliant_count: number;
  non_compliant_count: number;
  unknown_count: number;
  message: string;
};
type Alert = {
  id: string;
  status: "open" | "acknowledged" | "resolved";
  failed_requirement: string;
  confidence: number;
  created_at: string;
  acknowledgement_note?: string;
  evidence_available: boolean;
  evidence_message: string;
};
type Report = {
  observed: number;
  compliant: number;
  non_compliant: number;
  unknown: number;
  compliance_rate: number | null;
  open_alerts: number;
  disclaimer: string;
};

const request = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: { "X-Demo-Role": "safety_supervisor", ...(options?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Service request failed." }));
    throw new Error(body.detail ?? "Service request failed.");
  }
  return response.json() as Promise<T>;
};

// PUBLIC_INTERFACE
function ComplianceBreakdownChart({ report }: { report: Report | null }) {
  /** Render an aggregate-only Apache ECharts compliance breakdown visualization. */
  const chartElement = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartElement.current || !report) return undefined;
    const chart = echarts.init(chartElement.current);
    chart.setOption({
      aria: { enabled: true, description: "Aggregate PPE compliance observation breakdown." },
      color: ["#087f5b", "#c92a2a", "#f08c00"],
      tooltip: { trigger: "item", valueFormatter: (value) => `${value} observations` },
      series: [{
        type: "pie",
        radius: ["48%", "76%"],
        avoidLabelOverlap: true,
        label: { formatter: "{b}: {c}" },
        data: [
          { name: "Compliant", value: report.compliant },
          { name: "Persistent non-compliance", value: report.non_compliant },
          { name: "Unknown", value: report.unknown },
        ],
      }],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [report]);

  return <div ref={chartElement} className="chart" role="img" aria-label="Aggregate compliance breakdown chart" />;
}

// PUBLIC_INTERFACE
function App() {
  /** Render the safety-first POC dashboard, private media workflow, and privacy-gated evidence links. */
  const [zones, setZones] = useState<Zone[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [notice, setNotice] = useState("Select a source and upload an approved CCTV still or clip.");
  const [busy, setBusy] = useState(false);

  const selectedZone = useMemo(
    () => zones.find((zone) => zone.id === sources.find((source) => source.id === sourceId)?.zone_id),
    [sourceId, sources, zones],
  );

  const refresh = async () => {
    try {
      const [zoneData, sourceData, jobData, alertData, reportData] = await Promise.all([
        request<Zone[]>("/api/v1/zones"),
        request<Source[]>("/api/v1/sources"),
        request<Job[]>("/api/v1/media-jobs"),
        request<Alert[]>("/api/v1/alerts"),
        request<Report>("/api/v1/reports/compliance"),
      ]);
      setZones(zoneData);
      setSources(sourceData);
      setJobs(jobData);
      setAlerts(alertData);
      setReport(reportData);
      setSourceId((current) => current || sourceData[0]?.id || "");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to load the POC service.");
    }
  };

  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    const hasActiveJobs = jobs.some((job) => job.status === "queued" || job.status === "processing");
    if (!hasActiveJobs) return undefined;
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [jobs]);

  const submitUpload = async (event: FormEvent) => {
    event.preventDefault();
    if (!sourceId || !file) {
      setNotice("Choose a configured source and a JPEG, PNG, MP4, or MOV file.");
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const job = await request<Job>(`/api/v1/media-jobs?source_id=${sourceId}`, { method: "POST", body: form });
      setNotice(`Job queued: ${job.message}`);
      setFile(null);
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to submit the media job.");
    } finally {
      setBusy(false);
    }
  };

  const acknowledge = async (alertId: string) => {
    const note = window.prompt("Record the safety intervention taken:");
    if (!note?.trim()) return;
    try {
      await request<Alert>(`/api/v1/alerts/${alertId}/acknowledgements`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
      });
      setNotice("Alert acknowledged and intervention recorded.");
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to acknowledge the alert.");
    }
  };

  return (
    <main>
      <header>
        <p className="eyebrow">Safety decision-support POC</p>
        <h1>PPE Compliance Detection</h1>
        <p className="subtitle">Aggregate safety reporting only — no worker identification, tracking, or automated enforcement.</p>
      </header>
      <section className="notice" aria-live="polite">{notice}</section>

      <section className="metrics" aria-label="Aggregate compliance metrics">
        <article><span>Compliance rate</span><strong>{report?.compliance_rate ?? "—"}{report?.compliance_rate !== null && report ? "%" : ""}</strong></article>
        <article><span>Assessed observations</span><strong>{report?.observed ?? 0}</strong></article>
        <article><span>Unknown observations</span><strong>{report?.unknown ?? 0}</strong></article>
        <article><span>Open safety alerts</span><strong>{report?.open_alerts ?? 0}</strong></article>
      </section>

      <section className="grid">
        <article className="panel">
          <h2>Compliance breakdown</h2>
          <ComplianceBreakdownChart report={report} />
          <p className="muted">Only aggregate observations are charted. Unknown observations are never treated as violations.</p>
        </article>
        <article className="panel">
          <h2>Privacy and decision safeguards</h2>
          <ol>
            <li>Jobs are stored privately and processed outside the browser request.</li>
            <li>Explicit PPE evidence is associated to a detected person without persistent tracking.</li>
            <li>Non-compliance requires policy confidence and consecutive sampled-frame confirmation.</li>
            <li>Evidence is shown only after mandatory face blurring succeeds.</li>
          </ol>
          <p className="muted">{report?.disclaimer}</p>
        </article>
      </section>

      <section className="grid">
        <article className="panel">
          <h2>Process CCTV media</h2>
          <p>Manual POC upload only. Live RTSP/VMS integration is not enabled.</p>
          <form onSubmit={submitUpload}>
            <label>Camera source
              <select value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
                {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
              </select>
            </label>
            {selectedZone && <p className="policy"><strong>{selectedZone.name}</strong>: {selectedZone.policy.helmet_required ? "Helmet required" : "Helmet not required"}; {selectedZone.policy.vest_required ? "hi-vis vest required" : "vest not required"}. Confirmation requires {selectedZone.policy.persistence_frames} sampled frames.</p>}
            <label>CCTV still or clip
              <input accept="image/jpeg,image/png,video/mp4,video/quicktime" type="file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
            </label>
            <button disabled={busy} type="submit">{busy ? "Submitting…" : "Create processing job"}</button>
          </form>
        </article>
      </section>

      <section className="panel">
        <h2>Recent processing jobs</h2>
        {jobs.length === 0 ? <p>No media jobs have been submitted.</p> : <div className="jobs">
          {jobs.map((job) => <article className="job" key={job.id}>
            <div><span className={`status ${job.status}`}>{job.status}</span><h3>{job.filename}</h3>
              <p>{new Date(job.submitted_at).toLocaleString()} · Compliant: {job.compliant_count} · Non-compliant: {job.non_compliant_count} · Unknown: {job.unknown_count}</p>
              <p className="muted">{job.message}</p>
            </div>
          </article>)}
        </div>}
      </section>

      <section className="panel">
        <h2>Safety alert queue</h2>
        {alerts.length === 0 ? <p>No alerts have been created in this POC session.</p> : <div className="alerts">
          {alerts.map((alert) => <article className="alert" key={alert.id}>
            <div><span className={`status ${alert.status}`}>{alert.status}</span><h3>{alert.failed_requirement}</h3>
              <p>Confidence: {(alert.confidence * 100).toFixed(0)}% · {new Date(alert.created_at).toLocaleString()}</p>
              <p className="muted">{alert.evidence_message}</p>
              {alert.evidence_available && <a className="evidence-link" href={`${API_URL}/api/v1/alerts/${alert.id}/evidence`} rel="noreferrer" target="_blank">View privacy-processed evidence</a>}
              {alert.acknowledgement_note && <p><strong>Intervention:</strong> {alert.acknowledgement_note}</p>}
            </div>
            {alert.status === "open" && <button onClick={() => void acknowledge(alert.id)} type="button">Acknowledge</button>}
          </article>)}
        </div>}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
