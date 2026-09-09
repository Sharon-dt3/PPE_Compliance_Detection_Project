import { FormEvent, JSX, useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const SAFETY_NOTICE =
  "This POC provides indicative safety-support signals. It is not an employee productivity-monitoring system and must not be used for autonomous disciplinary or employment decisions.";

// "supabase" requires VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY; any other/missing
// configuration falls back to the local demonstration role selector.
const AUTH_MODE = import.meta.env.VITE_AUTH_MODE === "supabase" ? "supabase" : "demo";
const supabaseClient: SupabaseClient | null =
  AUTH_MODE === "supabase" && import.meta.env.VITE_SUPABASE_URL && import.meta.env.VITE_SUPABASE_ANON_KEY
    ? createClient(import.meta.env.VITE_SUPABASE_URL, import.meta.env.VITE_SUPABASE_ANON_KEY)
    : null;

type Role =
  | "safety_supervisor"
  | "hse_manager"
  | "administrator"
  | "model_evaluator"
  | "governance_reviewer"
  | "demonstration_viewer";

type Zone = {
  id: string;
  name: string;
  description: string;
  policy: {
    version: number;
    helmet_required: boolean;
    vest_required: boolean;
    persistence_frames: number;
    deduplication_seconds: number;
  };
};

type Source = { id: string; name: string; zone_id: string };

type Job = {
  id: string;
  source_id: string;
  zone_id: string;
  filename: string;
  status: string;
  submitted_at: string;
  completed_at: string | null;
  compliant_count: number;
  non_compliant_count: number;
  unknown_count: number;
  message: string;
  failure_code: string | null;
};

type Alert = {
  id: string;
  source_id: string;
  zone_id: string;
  status: "open" | "acknowledged" | "resolved" | "expired" | "cancelled";
  failed_requirement: string;
  confidence: number;
  occurrence_count: number;
  created_at: string;
  acknowledged_at: string | null;
  acknowledgement_note: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
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

type TrendPoint = {
  date: string;
  observed: number;
  compliant: number;
  non_compliant: number;
  unknown: number;
  compliance_rate: number | null;
};

type AlertMetrics = {
  total: number;
  states: Record<string, number>;
  average_acknowledgement_minutes: number | null;
  average_resolution_minutes: number | null;
  disclaimer: string;
};

type FrameSummary = {
  frame_index: number;
  person_count: number;
  compliant_count: number;
  non_compliant_count: number;
  unknown_count: number;
  confidence_summary: Record<string, number | null>;
};

type ModelEvaluation = {
  id: string;
  provider: string;
  model_name: string;
  model_version: string;
  dataset_reference: string;
  licence_status: string;
  approval_state: string;
  class_metrics: Record<string, Record<string, number>>;
  latency_ms: number | null;
  effective_sampling_rate: number | null;
  limitations: string;
  created_at: string;
};

type Me = { reference: string; role: Role };

type AuditEvent = {
  id: string;
  event_type: string;
  entity_type: string;
  entity_id: string;
  actor_role: string;
  detail: string;
  created_at: string;
};

type View =
  | "dashboard"
  | "media"
  | "media-new"
  | "alerts"
  | "sources"
  | "zones"
  | "policies"
  | "models"
  | "model-evaluation"
  | "retention"
  | "audit";

type NavigationItem = {
  view: View;
  label: string;
  roles: Role[];
};

const ROLE_LABELS: Record<Role, string> = {
  safety_supervisor: "Safety Supervisor",
  hse_manager: "HSE Manager",
  administrator: "System Administrator",
  model_evaluator: "Model Evaluator",
  governance_reviewer: "Privacy / Governance Reviewer",
  demonstration_viewer: "Demonstration Viewer",
};

const NAVIGATION: NavigationItem[] = [
  { view: "dashboard", label: "Dashboard", roles: ["hse_manager", "administrator", "governance_reviewer", "demonstration_viewer"] },
  { view: "media", label: "Media jobs", roles: ["safety_supervisor", "hse_manager", "administrator"] },
  { view: "media-new", label: "New upload", roles: ["safety_supervisor", "hse_manager", "administrator"] },
  { view: "alerts", label: "Safety alerts", roles: ["safety_supervisor", "hse_manager", "administrator"] },
  { view: "sources", label: "Sources", roles: ["administrator"] },
  { view: "zones", label: "Zones", roles: ["administrator"] },
  { view: "policies", label: "Policies", roles: ["administrator"] },
  { view: "models", label: "Model registry", roles: ["administrator", "model_evaluator"] },
  { view: "model-evaluation", label: "Model evaluation", roles: ["model_evaluator"] },
  { view: "retention", label: "Retention", roles: ["administrator", "governance_reviewer"] },
  { view: "audit", label: "Audit records", roles: ["administrator", "governance_reviewer"] },
];

const routeFor = (view: View): string => {
  const routes: Record<View, string> = {
    dashboard: "/dashboard",
    media: "/media",
    "media-new": "/media/new",
    alerts: "/alerts",
    sources: "/admin/sources",
    zones: "/admin/zones",
    policies: "/admin/policies",
    models: "/admin/models",
    "model-evaluation": "/model-evaluation",
    retention: "/admin/retention",
    audit: "/admin/audit",
  };
  return routes[view];
};

const viewForPath = (path: string): View | null => {
  const match = (Object.keys({
    dashboard: true,
    media: true,
    "media-new": true,
    alerts: true,
    sources: true,
    zones: true,
    policies: true,
    models: true,
    "model-evaluation": true,
    retention: true,
    audit: true,
  }) as View[]).find((view) => routeFor(view) === path);
  return match ?? null;
};

const initialRole = (): Role | null => {
  const savedRole = window.sessionStorage.getItem("ppe-demo-role");
  return savedRole && savedRole in ROLE_LABELS ? (savedRole as Role) : null;
};

/**
 * Resolve the header proving the caller's identity for one request.
 *
 * Demo mode sends the labeled-non-production `X-Demo-Role` header. Supabase mode never
 * sends a client-asserted role at all: it forwards the signed session access token, and the
 * server independently resolves the caller's role from its own role-assignment table.
 */
const resolveAuthHeader = async (role: Role): Promise<Record<string, string>> => {
  if (AUTH_MODE === "supabase" && supabaseClient) {
    const { data } = await supabaseClient.auth.getSession();
    return data.session ? { Authorization: `Bearer ${data.session.access_token}` } : {};
  }
  return { "X-Demo-Role": role };
};

const fetchMe = async (accessToken: string): Promise<Me> => {
  const response = await fetch(`${API_URL}/api/v1/me`, { headers: { Authorization: `Bearer ${accessToken}` } });
  if (!response.ok) throw new Error("Unable to resolve your assigned safety-platform role.");
  return response.json() as Promise<Me>;
};

const request = async <T,>(role: Role, path: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      ...(await resolveAuthHeader(role)),
      ...(options?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "The safety service did not complete the request." }));
    throw new Error(typeof body.detail === "string" ? body.detail : "The safety service did not complete the request.");
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
};

// PUBLIC_INTERFACE
function ComplianceChart({ report }: { report: Report | null }) {
  /** Render an accessible aggregate-only chart with no individual or biometric data. */
  const chartElement = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartElement.current || !report) return undefined;

    const chart = echarts.init(chartElement.current);
    chart.setOption({
      aria: { enabled: true, description: "Aggregate PPE observation breakdown." },
      color: ["#087f5b", "#c92a2a", "#d97706"],
      tooltip: { trigger: "item", valueFormatter: (value: number | string) => `${value} observations` },
      series: [{
        type: "pie",
        radius: ["48%", "76%"],
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
function TrendChart({ points }: { points: TrendPoint[] }) {
  /** Render an aggregate daily safety-compliance trend without identity-level observations. */
  const chartElement = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartElement.current) return undefined;

    const chart = echarts.init(chartElement.current);
    chart.setOption({
      aria: { enabled: true, description: "Aggregate daily safety compliance trend." },
      color: ["#087f5b", "#d97706"],
      tooltip: { trigger: "axis" },
      grid: { left: 46, right: 22, top: 25, bottom: 38 },
      legend: { data: ["Compliance rate", "Unknown observations"] },
      xAxis: { type: "category", data: points.map((point) => point.date), axisLabel: { rotate: 30 } },
      yAxis: [
        { type: "value", name: "Rate (%)", min: 0, max: 100 },
        { type: "value", name: "Unknown" },
      ],
      series: [
        { name: "Compliance rate", type: "line", smooth: true, data: points.map((point) => point.compliance_rate ?? 0) },
        { name: "Unknown observations", type: "bar", yAxisIndex: 1, data: points.map((point) => point.unknown) },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [points]);

  return <div ref={chartElement} className="chart" role="img" aria-label="Aggregate daily compliance trend chart" />;
}

// PUBLIC_INTERFACE
function Login({ onLogin }: { onLogin: (role: Role) => void }) {
  /** Render the local POC role-selection entry point without representing it as production authentication. */
  const [role, setRole] = useState<Role>("safety_supervisor");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onLogin(role);
  };

  return (
    <main className="login-layout">
      <section className="login-card" aria-labelledby="login-title">
        <p className="eyebrow">Safety decision-support POC</p>
        <h1 id="login-title">PPE Compliance Detection</h1>
        <p className="subtitle">Use a role to demonstrate the server-enforced workflows and restrictions provided by this local POC.</p>
        <div className="notice compact">
          <strong>Demonstration access only.</strong> Production access must use a verified OIDC/OAuth identity and server-side role assignment. Do not use this selector as a production login mechanism.
        </div>
        <form onSubmit={submit}>
          <label htmlFor="role">POC role
            <select id="role" value={role} onChange={(event) => setRole(event.target.value as Role)}>
              {Object.entries(ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <button type="submit">Enter safety POC</button>
        </form>
        <p className="muted safety-copy">{SAFETY_NOTICE}</p>
      </section>
    </main>
  );
}

// PUBLIC_INTERFACE
function SupabaseLogin({ onAuthenticated }: { onAuthenticated: (me: Me) => void }) {
  /** Render real OIDC sign-in; the server independently resolves the caller's role afterwards. */
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!supabaseClient) return;
    setSubmitting(true);
    setError(null);
    try {
      const { data, error: signInError } = await supabaseClient.auth.signInWithPassword({ email, password });
      if (signInError || !data.session) throw new Error(signInError?.message ?? "Sign-in failed.");
      onAuthenticated(await fetchMe(data.session.access_token));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to sign in.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-layout">
      <section className="login-card" aria-labelledby="login-title">
        <p className="eyebrow">Safety decision-support POC</p>
        <h1 id="login-title">PPE Compliance Detection</h1>
        <p className="subtitle">Sign in with your verified safety-platform identity. Your role is assigned and enforced server-side.</p>
        <form onSubmit={(event) => void submit(event)}>
          <label htmlFor="email">Work email
            <input id="email" type="email" required autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <label htmlFor="password">Password
            <input id="password" type="password" required autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          {error && <p className="notice compact" role="alert">{error}</p>}
          <button disabled={submitting} type="submit">{submitting ? "Signing in…" : "Sign in"}</button>
        </form>
        <p className="muted safety-copy">{SAFETY_NOTICE}</p>
      </section>
    </main>
  );
}

// PUBLIC_INTERFACE
function App() {
  /** Render the authenticated role-aware PPE safety POC interface and permitted workflows. */
  const [role, setRole] = useState<Role | null>(initialRole);
  const [view, setView] = useState<View>(() => viewForPath(window.location.pathname) ?? "dashboard");
  const [zones, setZones] = useState<Zone[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [alertMetrics, setAlertMetrics] = useState<AlertMetrics | null>(null);
  const [evaluations, setEvaluations] = useState<ModelEvaluation[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [frames, setFrames] = useState<FrameSummary[]>([]);
  const [notice, setNotice] = useState("Select a permitted workflow.");
  const [loading, setLoading] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [file, setFile] = useState<File | null>(null);

  const allowedNavigation = useMemo(() => NAVIGATION.filter((item) => role && item.roles.includes(role)), [role]);
  const selectedZone = useMemo(
    () => zones.find((zone) => zone.id === sources.find((source) => source.id === sourceId)?.zone_id),
    [sourceId, sources, zones],
  );

  const navigate = (nextView: View) => {
    setView(nextView);
    window.history.pushState({}, "", routeFor(nextView));
  };

  const loadForRole = async (activeRole: Role) => {
    setLoading(true);
    try {
      const zonePromise = request<Zone[]>(activeRole, "/api/v1/zones");
      const sourcesAllowed = ["safety_supervisor", "hse_manager", "administrator"].includes(activeRole);
      const dashboardAllowed = ["hse_manager", "administrator", "governance_reviewer", "demonstration_viewer"].includes(activeRole);
      const alertsAllowed = ["safety_supervisor", "hse_manager", "administrator"].includes(activeRole);

      const [zoneData, sourceData, jobData, alertData, reportData, trendData, metricsData] = await Promise.all([
        zonePromise,
        sourcesAllowed ? request<Source[]>(activeRole, "/api/v1/sources") : Promise.resolve([]),
        sourcesAllowed ? request<Job[]>(activeRole, "/api/v1/media-jobs") : Promise.resolve([]),
        alertsAllowed ? request<Alert[]>(activeRole, "/api/v1/alerts") : Promise.resolve([]),
        dashboardAllowed ? request<Report>(activeRole, "/api/v1/reports/compliance") : Promise.resolve(null),
        dashboardAllowed ? request<TrendPoint[]>(activeRole, "/api/v1/reports/compliance/trend") : Promise.resolve([]),
        dashboardAllowed ? request<AlertMetrics>(activeRole, "/api/v1/reports/alerts") : Promise.resolve(null),
      ]);

      setZones(zoneData);
      setSources(sourceData);
      setJobs(jobData);
      setAlerts(alertData);
      setReport(reportData);
      setTrend(trendData);
      setAlertMetrics(metricsData);
      setSourceId((current) => current || sourceData[0]?.id || "");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to load this permitted POC workflow.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!role) return;
    void loadForRole(role);
  }, [role]);

  useEffect(() => {
    if (AUTH_MODE !== "supabase" || !supabaseClient) return undefined;
    let active = true;
    void (async () => {
      const { data } = await supabaseClient!.auth.getSession();
      if (!active || !data.session) return;
      try {
        const me = await fetchMe(data.session.access_token);
        setRole(me.role);
      } catch {
        await supabaseClient!.auth.signOut();
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const handleHistory = () => {
      const pathView = viewForPath(window.location.pathname);
      if (pathView) setView(pathView);
    };
    window.addEventListener("popstate", handleHistory);
    return () => window.removeEventListener("popstate", handleHistory);
  }, []);

  useEffect(() => {
    if (!role || !jobs.some((job) => ["queued", "validating", "processing"].includes(job.status))) return undefined;
    const timer = window.setInterval(() => void loadForRole(role), 3000);
    return () => window.clearInterval(timer);
  }, [jobs, role]);

  const changeRole = (nextRole: Role) => {
    window.sessionStorage.setItem("ppe-demo-role", nextRole);
    setRole(nextRole);
    const defaultView = NAVIGATION.find((item) => item.roles.includes(nextRole))?.view ?? "dashboard";
    navigate(defaultView);
    setNotice(`${ROLE_LABELS[nextRole]} demonstration role selected.`);
  };

  const handleSupabaseAuthenticated = (me: Me) => {
    setRole(me.role);
    const defaultView = NAVIGATION.find((item) => item.roles.includes(me.role))?.view ?? "dashboard";
    navigate(defaultView);
    setNotice(`Signed in as ${ROLE_LABELS[me.role]}.`);
  };

  const logout = () => {
    if (AUTH_MODE === "supabase" && supabaseClient) {
      void supabaseClient.auth.signOut();
    } else {
      window.sessionStorage.removeItem("ppe-demo-role");
    }
    setRole(null);
    window.history.pushState({}, "", "/login");
  };

  const submitUpload = async (event: FormEvent) => {
    event.preventDefault();
    if (!role || !sourceId || !file) {
      setNotice("Choose a configured source and a JPEG, PNG, MP4, or MOV file.");
      return;
    }

    setLoading(true);
    try {
      const data = new FormData();
      data.append("file", file);
      const job = await request<Job>(role, `/api/v1/media-jobs?source_id=${encodeURIComponent(sourceId)}`, { method: "POST", body: data });
      setNotice(`Processing job created: ${job.message}`);
      setFile(null);
      await loadForRole(role);
      navigate("media");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to submit the media job.");
    } finally {
      setLoading(false);
    }
  };

  const openJob = async (job: Job) => {
    if (!role) return;
    setSelectedJob(job);
    setFrames([]);
    setLoading(true);
    try {
      setFrames(await request<FrameSummary[]>(role, `/api/v1/media-jobs/${job.id}/frames`));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Frame summaries are unavailable.");
    } finally {
      setLoading(false);
    }
  };

  const updateAlert = async (alert: Alert, action: "acknowledgements" | "resolve") => {
    if (!role) return;
    const actionLabel = action === "resolve" ? "resolution outcome" : "safety intervention";
    const note = window.prompt(`Record the non-identifying ${actionLabel}:`);
    if (!note?.trim()) return;

    setLoading(true);
    try {
      await request<Alert>(role, `/api/v1/alerts/${alert.id}/${action}`, {
        method: "POST",
        body: JSON.stringify({ note: note.trim() }),
      });
      setNotice(action === "resolve" ? "Safety alert resolved after human review." : "Safety alert acknowledged and intervention recorded.");
      await loadForRole(role);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The alert update could not be completed.");
    } finally {
      setLoading(false);
    }
  };

  const createSource = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!role) return;
    const form = new FormData(event.currentTarget);
    setLoading(true);
    try {
      await request<Source>(role, "/api/v1/sources", {
        method: "POST",
        body: JSON.stringify({ name: form.get("name"), zone_id: form.get("zone_id"), enabled: true }),
      });
      event.currentTarget.reset();
      setNotice("Camera source configuration created and audited.");
      await loadForRole(role);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to create the source.");
    } finally {
      setLoading(false);
    }
  };

  const createZone = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!role) return;
    const form = new FormData(event.currentTarget);
    setLoading(true);
    try {
      await request<Zone>(role, "/api/v1/zones", {
        method: "POST",
        body: JSON.stringify({ name: form.get("name"), description: form.get("description"), enabled: true }),
      });
      event.currentTarget.reset();
      setNotice("Safety zone and initial policy created and audited.");
      await loadForRole(role);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to create the safety zone.");
    } finally {
      setLoading(false);
    }
  };

  const createPolicy = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!role) return;
    const form = new FormData(event.currentTarget);
    const zoneId = String(form.get("zone_id"));
    setLoading(true);
    try {
      await request(role, `/api/v1/zones/${zoneId}/policy`, {
        method: "PATCH",
        body: JSON.stringify({
          helmet_required: form.get("helmet_required") === "on",
          vest_required: form.get("vest_required") === "on",
          confidence_threshold: Number(form.get("confidence_threshold")),
          persistence_frames: Number(form.get("persistence_frames")),
          deduplication_seconds: Number(form.get("deduplication_seconds")),
          evidence_retention_hours: Number(form.get("evidence_retention_hours")),
          active: true,
        }),
      });
      setNotice("New immutable zone policy version created and activated.");
      await loadForRole(role);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to create the policy version.");
    } finally {
      setLoading(false);
    }
  };

  const loadEvaluations = async () => {
    if (!role) return;
    setLoading(true);
    try {
      setEvaluations(await request<ModelEvaluation[]>(role, "/api/v1/model-evaluations"));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Model evaluations are unavailable.");
    } finally {
      setLoading(false);
    }
  };

  const loadAudit = async () => {
    if (!role) return;
    setLoading(true);
    try {
      setAuditEvents(await request<AuditEvent[]>(role, "/api/v1/audit-events"));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Audit records are unavailable.");
    } finally {
      setLoading(false);
    }
  };

  const exportAggregate = async () => {
    if (!role) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_URL}/api/v1/reports/exports`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await resolveAuthHeader(role)) },
        body: JSON.stringify({}),
      });
      if (!response.ok) throw new Error("The aggregate export could not be generated.");
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "aggregate-compliance-report.csv";
      anchor.click();
      URL.revokeObjectURL(url);
      setNotice("Aggregate-only CSV export created and audited.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The aggregate export could not be generated.");
    } finally {
      setLoading(false);
    }
  };

  if (!role) {
    return AUTH_MODE === "supabase"
      ? <SupabaseLogin onAuthenticated={handleSupabaseAuthenticated} />
      : <Login onLogin={changeRole} />;
  }

  const canAccessView = allowedNavigation.some((item) => item.view === view);
  const activeView = canAccessView ? view : allowedNavigation[0]?.view ?? "dashboard";

  const renderDashboard = () => (
    <>
      <PageHeader title="Aggregate safety dashboard" description="Safety compliance rates are calculated as compliant ÷ (compliant + non-compliant). Unknown observations are displayed separately and excluded from the headline denominator." />
      <section className="metrics" aria-label="Aggregate compliance metrics">
        <Metric label="Compliance rate" value={report?.compliance_rate === null || report?.compliance_rate === undefined ? "—" : `${report.compliance_rate}%`} />
        <Metric label="Assessed observations" value={report ? report.compliant + report.non_compliant : 0} detail={report ? `${report.compliant} compliant / ${report.non_compliant} non-compliant` : undefined} />
        <Metric label="Unknown observations" value={report?.unknown ?? 0} detail="Not treated as violations" />
        <Metric label="Open safety alerts" value={report?.open_alerts ?? 0} />
      </section>
      <section className="grid">
        <article className="panel">
          <h2>Compliance breakdown</h2>
          <ComplianceChart report={report} />
          <p className="muted">Only aggregate safety observations are charted; no worker profiles or identity-level histories exist.</p>
        </article>
        <article className="panel">
          <h2>Compliance over time</h2>
          {trend.length ? <TrendChart points={trend} /> : <EmptyState title="No trend data yet" text="Complete a privacy-safe media processing job to add aggregate trend observations." />}
        </article>
      </section>
      <section className="grid">
        <article className="panel">
          <h2>Safety alert review</h2>
          <dl className="definition-list">
            <div><dt>All alerts</dt><dd>{alertMetrics?.total ?? 0}</dd></div>
            <div><dt>Acknowledged</dt><dd>{alertMetrics?.states.acknowledged ?? 0}</dd></div>
            <div><dt>Resolved</dt><dd>{alertMetrics?.states.resolved ?? 0}</dd></div>
            <div><dt>Average acknowledgement time</dt><dd>{formatMinutes(alertMetrics?.average_acknowledgement_minutes)}</dd></div>
            <div><dt>Average resolution time</dt><dd>{formatMinutes(alertMetrics?.average_resolution_minutes)}</dd></div>
          </dl>
        </article>
        <article className="panel">
          <h2>Data-quality safeguards</h2>
          <ul className="safety-list">
            <li>Unknown observations are never converted into violations.</li>
            <li>Alerts require explicit failure evidence and configurable multi-frame persistence.</li>
            <li>Aggregate rates exclude unknown observations from the denominator.</li>
            <li>Evidence is privacy-processed and is automatically time-limited.</li>
          </ul>
          {["hse_manager", "administrator", "governance_reviewer"].includes(role) && <button type="button" onClick={() => void exportAggregate()}>Export aggregate report</button>}
        </article>
      </section>
    </>
  );

  const renderMedia = () => (
    <>
      <PageHeader title="Private media processing" description="Manual CCTV image and video uploads only. Live RTSP/VMS ingestion is intentionally outside this POC." action={<button type="button" onClick={() => navigate("media-new")}>New media job</button>} />
      {selectedJob && <section className="panel detail-panel">
        <div className="panel-heading"><div><h2>{selectedJob.filename}</h2><p className="muted">Privacy-safe sampled-frame summaries only.</p></div><button className="secondary-button" type="button" onClick={() => setSelectedJob(null)}>Close detail</button></div>
        <div className="metrics compact-metrics">
          <Metric label="Compliant" value={selectedJob.compliant_count} />
          <Metric label="Non-compliant" value={selectedJob.non_compliant_count} />
          <Metric label="Unknown" value={selectedJob.unknown_count} />
        </div>
        {frames.length ? <table><thead><tr><th>Frame</th><th>Observed population</th><th>Compliant</th><th>Non-compliant</th><th>Unknown</th></tr></thead><tbody>
          {frames.map((frame) => <tr key={frame.frame_index}><td>{frame.frame_index}</td><td>{frame.person_count}</td><td>{frame.compliant_count}</td><td>{frame.non_compliant_count}</td><td>{frame.unknown_count}</td></tr>)}
        </tbody></table> : <EmptyState title="No frame observations" text="Frame summaries become available after media processing begins." />}
      </section>}
      <section className="panel">
        <h2>Recent processing jobs</h2>
        {jobs.length === 0 ? <EmptyState title="No media jobs" text="Create a private processing job to demonstrate the upload-to-review workflow." /> : <div className="jobs">
          {jobs.map((job) => <article className="job" key={job.id}>
            <div><Status status={job.status} /><h3>{job.filename}</h3><p>{formatDate(job.submitted_at)} · Compliant: {job.compliant_count} · Non-compliant: {job.non_compliant_count} · Unknown: {job.unknown_count}</p><p className="muted">{job.message}</p></div>
            <div className="button-row"><button className="secondary-button" type="button" onClick={() => void openJob(job)}>View summary</button>{["queued", "validating", "processing"].includes(job.status) && <button type="button" onClick={() => void cancelJob(role, job, loadForRole, setNotice, setLoading)}>Cancel</button>}</div>
          </article>)}
        </div>}
      </section>
    </>
  );

  const renderNewMedia = () => (
    <>
      <PageHeader title="Create private media job" description="Upload only approved POC CCTV stills or clips. Files are validated before private asynchronous processing." />
      <section className="panel narrow-panel">
        <form onSubmit={submitUpload}>
          <label htmlFor="source">Camera source
            <select id="source" value={sourceId} onChange={(event) => setSourceId(event.target.value)} required>
              <option value="">Choose a configured source</option>
              {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
            </select>
          </label>
          {selectedZone && <div className="policy"><strong>{selectedZone.name} · policy v{selectedZone.policy.version}</strong><span>{selectedZone.policy.helmet_required ? "Helmet required" : "Helmet optional"}; {selectedZone.policy.vest_required ? "high-visibility vest required" : "vest optional"}.</span><span>Persistent evidence requires {selectedZone.policy.persistence_frames} sampled frames.</span></div>}
          <label htmlFor="media-file">CCTV still or recorded clip
            <input id="media-file" accept="image/jpeg,image/png,video/mp4,video/quicktime" type="file" required onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          </label>
          <p className="muted">Supported formats: JPEG, PNG, MP4, and MOV. Suspicious, malformed, mismatched, and oversized media is rejected safely.</p>
          <button disabled={loading} type="submit">{loading ? "Submitting…" : "Create processing job"}</button>
        </form>
      </section>
    </>
  );

  const renderAlerts = () => (
    <>
      <PageHeader title="Safety alert queue" description="Every alert is a human-review safety signal. It does not identify, profile, rank, or discipline any person." />
      <section className="panel">
        {alerts.length === 0 ? <EmptyState title="No safety alerts" text="No sustained PPE non-compliance alerts have been created in this POC session." /> : <div className="alerts">
          {alerts.map((alert) => <article className="alert" key={alert.id}>
            <div>
              <Status status={alert.status} />
              <h3>{alert.failed_requirement}</h3>
              <p>Confidence: {(alert.confidence * 100).toFixed(0)}% · {formatDate(alert.created_at)} · Confirmed observations: {alert.occurrence_count}</p>
              <p className="muted">{alert.evidence_message}</p>
              {alert.evidence_available ? <EvidenceLink alertId={alert.id} role={role} onError={setNotice} /> : <EvidenceExpired />}
              {alert.acknowledgement_note && <p><strong>Intervention:</strong> {alert.acknowledgement_note}</p>}
              {alert.resolution_note && <p><strong>Resolution:</strong> {alert.resolution_note}</p>}
            </div>
            {role === "safety_supervisor" && <div className="button-stack">
              {alert.status === "open" && <button type="button" onClick={() => void updateAlert(alert, "acknowledgements")}>Acknowledge</button>}
              {["open", "acknowledged"].includes(alert.status) && <button className="secondary-button" type="button" onClick={() => void updateAlert(alert, "resolve")}>Resolve</button>}
            </div>}
          </article>)}
        </div>}
      </section>
    </>
  );

  const renderSources = () => (
    <>
      <PageHeader title="Camera source configuration" description="Sources are manual-upload labels for the POC. They do not enable live feeds, access control, or person tracking." />
      <section className="grid">
        <article className="panel"><h2>Configured sources</h2>{sources.length ? <table><thead><tr><th>Name</th><th>Zone</th></tr></thead><tbody>{sources.map((source) => <tr key={source.id}><td>{source.name}</td><td>{zones.find((zone) => zone.id === source.zone_id)?.name ?? "Unavailable zone"}</td></tr>)}</tbody></table> : <EmptyState title="No sources configured" text="Create a source before submitting POC media." />}</article>
        <article className="panel"><h2>Add source</h2><form onSubmit={createSource}><label>Name<input name="name" required minLength={3} maxLength={120} /></label><label>Safety zone<select name="zone_id" required><option value="">Choose zone</option>{zones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}</select></label><button disabled={loading} type="submit">Create source</button></form></article>
      </section>
    </>
  );

  const renderZones = () => (
    <>
      <PageHeader title="Safety zone configuration" description="Zones define safety context and versioned PPE policy. They contain no employee, identity, or biometric data." />
      <section className="grid">
        <article className="panel"><h2>Configured zones</h2>{zones.length ? <table><thead><tr><th>Zone</th><th>Policy</th><th>Persistence</th></tr></thead><tbody>{zones.map((zone) => <tr key={zone.id}><td><strong>{zone.name}</strong><br /><span className="muted">{zone.description}</span></td><td>v{zone.policy.version}: {zone.policy.helmet_required ? "Helmet" : "No helmet rule"} · {zone.policy.vest_required ? "Vest" : "No vest rule"}</td><td>{zone.policy.persistence_frames} frames</td></tr>)}</tbody></table> : <EmptyState title="No zones configured" text="Create a safety zone to begin policy configuration." />}</article>
        <article className="panel"><h2>Add safety zone</h2><form onSubmit={createZone}><label>Name<input name="name" required minLength={3} maxLength={120} /></label><label>Description<textarea name="description" required minLength={3} maxLength={1000} /></label><button disabled={loading} type="submit">Create zone</button></form></article>
      </section>
    </>
  );

  const renderPolicies = () => (
    <>
      <PageHeader title="Versioned PPE policies" description="Creating a policy produces a new immutable version. Configuration defaults are explicit and are not treated as a disciplinary rule." />
      <section className="grid">
        <article className="panel"><h2>Active policies</h2>{zones.map((zone) => <div className="policy policy-row" key={zone.id}><strong>{zone.name} · version {zone.policy.version}</strong><span>Helmet: {zone.policy.helmet_required ? "required" : "not required"}</span><span>Vest: {zone.policy.vest_required ? "required" : "not required"}</span><span>Persistence: {zone.policy.persistence_frames} frames · Deduplication: {zone.policy.deduplication_seconds}s</span></div>)}</article>
        <article className="panel"><h2>Create policy version</h2><form onSubmit={createPolicy}><label>Safety zone<select name="zone_id" required><option value="">Choose zone</option>{zones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}</select></label><label className="check-label"><input name="helmet_required" type="checkbox" defaultChecked /> Helmet required</label><label className="check-label"><input name="vest_required" type="checkbox" defaultChecked /> Hi-vis vest required</label><label>Confidence threshold<input name="confidence_threshold" type="number" min="0" max="1" step="0.01" defaultValue="0.25" required /></label><label>Persistence frames<input name="persistence_frames" type="number" min="1" max="60" defaultValue="3" required /></label><label>Deduplication seconds<input name="deduplication_seconds" type="number" min="0" max="86400" defaultValue="60" required /></label><label>Evidence retention hours<input name="evidence_retention_hours" type="number" min="24" max="72" defaultValue="48" required /></label><button disabled={loading} type="submit">Create and activate version</button></form></article>
      </section>
    </>
  );

  const renderModels = () => (
    <>
      <PageHeader title="Model registry and limitations" description="POC model evaluations must be read alongside their class-level metrics, licensing, limitations, and approval status. No single headline accuracy is used." action={<button className="secondary-button" type="button" onClick={() => void loadEvaluations()}>Load evaluations</button>} />
      <section className="panel">
        {!evaluations.length ? <EmptyState title="No evaluations loaded" text="Load approved model evaluation metadata to review POC capability and limitations." /> : <div className="cards">
          {evaluations.map((evaluation) => <article className="model-card" key={evaluation.id}><Status status={evaluation.approval_state} /><h2>{evaluation.model_name} <span className="muted">v{evaluation.model_version}</span></h2><p>{evaluation.provider} · {evaluation.licence_status}</p><p><strong>Dataset:</strong> {evaluation.dataset_reference}</p><p><strong>Latency:</strong> {evaluation.latency_ms ?? "Not recorded"} ms · <strong>Sampling:</strong> {evaluation.effective_sampling_rate ?? "Not recorded"} FPS</p><h3>Class-level results</h3><table><thead><tr><th>Class</th><th>Precision</th><th>Recall</th><th>F1</th></tr></thead><tbody>{Object.entries(evaluation.class_metrics).map(([label, metrics]) => <tr key={label}><td>{label}</td><td>{metrics.precision ?? "—"}</td><td>{metrics.recall ?? "—"}</td><td>{metrics.f1 ?? "—"}</td></tr>)}</tbody></table><p className="muted"><strong>Limitations:</strong> {evaluation.limitations}</p></article>)}
        </div>}
      </section>
    </>
  );

  const renderModelEvaluation = () => <><PageHeader title="Model evaluation workflow" description="The evaluator role can record approved benchmark metadata through the API. This POC UI provides transparent review of stored class-level results and limitations." action={<button type="button" onClick={() => { navigate("models"); void loadEvaluations(); }}>Review evaluations</button>} /><section className="panel"><h2>Evaluation guardrails</h2><ul className="safety-list"><li>Use approved labeled datasets only.</li><li>Record per-class precision, recall, F1, latency, unknown rate, and camera-angle limitations.</li><li>Keep experimental and POC-only models clearly marked as not production-approved.</li><li>Do not use evaluation data for worker identification or performance assessment.</li></ul></section></>;

  const renderRetention = () => <><PageHeader title="Retention and deletion controls" description="Raw uploaded media is deleted after processing according to approved retention. Privacy-processed evidence expires after its configured 24–72 hour period; aggregate metrics and audit records follow their own governance policy." /><section className="grid"><article className="panel"><h2>Automated controls</h2><ul className="safety-list"><li>Scheduled cleanup removes expired raw private media.</li><li>Expired evidence is deleted and becomes inaccessible through the reviewer API.</li><li>Expired evidence is displayed as unavailable, never as a broken image.</li><li>Retention outcomes are intended to be auditable by the governance workflow.</li></ul></article><article className="panel"><h2>POC limitation</h2><p className="muted">Retention configuration is represented through active zone-policy evidence periods. This POC does not provide a self-service global retention editor or storage administration interface.</p></article></section></>;

  const renderAudit = () => <><PageHeader title="Restricted audit records" description="Audit records omit raw media, direct evidence URLs, biometric material, worker identities, and HR data." action={<button className="secondary-button" type="button" onClick={() => void loadAudit()}>Load audit records</button>} /><section className="panel">{!auditEvents.length ? <EmptyState title="No audit records loaded" text="Load recent restricted audit events to review permitted safety operations." /> : <table><thead><tr><th>When</th><th>Event</th><th>Entity</th><th>Role</th><th>Detail</th></tr></thead><tbody>{auditEvents.map((event) => <tr key={event.id}><td>{formatDate(event.created_at)}</td><td>{event.event_type}</td><td>{event.entity_type}</td><td>{event.actor_role}</td><td>{event.detail}</td></tr>)}</tbody></table>}</section></>;

  const pages: Record<View, () => JSX.Element> = {
    dashboard: renderDashboard,
    media: renderMedia,
    "media-new": renderNewMedia,
    alerts: renderAlerts,
    sources: renderSources,
    zones: renderZones,
    policies: renderPolicies,
    models: renderModels,
    "model-evaluation": renderModelEvaluation,
    retention: renderRetention,
    audit: renderAudit,
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div><p className="eyebrow">Safety POC</p><h1>PPE Compliance</h1></div>
        <nav aria-label="Primary navigation">
          {allowedNavigation.map((item) => <button className={`nav-item ${activeView === item.view ? "active" : ""}`} key={item.view} type="button" onClick={() => navigate(item.view)}>{item.label}</button>)}
        </nav>
        <div className="session-panel">
          {AUTH_MODE === "demo"
            ? <label htmlFor="active-role">Demonstration role<select id="active-role" value={role} onChange={(event) => changeRole(event.target.value as Role)}>{Object.entries(ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            : <p className="muted">Signed in as {ROLE_LABELS[role]}.</p>}
          <button className="text-button" type="button" onClick={logout}>{AUTH_MODE === "supabase" ? "Sign out" : "End POC session"}</button>
        </div>
      </aside>
      <main className="content">
        <header className="topbar"><div><span className="role-badge">{ROLE_LABELS[role]}</span><p className="muted">Authenticated POC view; permissions are enforced by the API.</p></div><button className="secondary-button" type="button" disabled={loading} onClick={() => void loadForRole(role)}>{loading ? "Refreshing…" : "Refresh"}</button></header>
        <section className="notice" aria-live="polite">{notice}</section>
        {pages[activeView]()}
        <footer className="safety-footer">{SAFETY_NOTICE}</footer>
      </main>
    </div>
  );
}

// PUBLIC_INTERFACE
function PageHeader({ title, description, action }: { title: string; description: string; action?: JSX.Element }) {
  /** Render a consistent accessible heading for a role-permitted safety workflow page. */
  return <header className="page-header"><div><h1>{title}</h1><p className="subtitle">{description}</p></div>{action}</header>;
}

// PUBLIC_INTERFACE
function Metric({ label, value, detail }: { label: string; value: string | number; detail?: string }) {
  /** Render one transparent aggregate safety metric with optional numerator or denominator context. */
  return <article className="metric-card"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</article>;
}

// PUBLIC_INTERFACE
function Status({ status }: { status: string }) {
  /** Render a text-bearing status indicator so state is never conveyed by color alone. */
  return <span className={`status ${status}`}>{status.replaceAll("_", " ")}</span>;
}

// PUBLIC_INTERFACE
function EmptyState({ title, text }: { title: string; text: string }) {
  /** Render a meaningful empty state for asynchronous or currently unpopulated POC records. */
  return <div className="empty-state"><strong>{title}</strong><p>{text}</p></div>;
}

// PUBLIC_INTERFACE
function EvidenceExpired() {
  /** Render the configured retention state when evidence cannot be displayed safely. */
  return <div className="evidence-expired"><strong>Evidence expired</strong><span>This evidence is no longer available under the configured retention policy.</span></div>;
}

// PUBLIC_INTERFACE
function EvidenceLink({ alertId, role, onError }: { alertId: string; role: Role; onError: (message: string) => void }) {
  /** Retrieve protected evidence only after opening a separate authorized API request. */
  const [url, setUrl] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);

  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);

  const viewEvidence = async () => {
    try {
      const response = await fetch(`${API_URL}/api/v1/alerts/${alertId}/evidence`, { headers: await resolveAuthHeader(role) });
      if (!response.ok) {
        setExpired(true);
        throw new Error("Privacy-processed evidence is unavailable or expired.");
      }
      const evidenceUrl = URL.createObjectURL(await response.blob());
      setUrl(evidenceUrl);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Evidence is unavailable.");
    }
  };

  if (expired) return <EvidenceExpired />;
  return <div className="evidence-control">{url ? <img alt="Face-blurred PPE safety alert evidence" className="evidence-image" src={url} /> : <button className="text-button" type="button" onClick={() => void viewEvidence()}>View privacy-processed evidence</button>}</div>;
}

const formatDate = (value: string) => new Date(value).toLocaleString();
const formatMinutes = (value: number | null | undefined) => value === null || value === undefined ? "Not available" : `${value.toFixed(1)} min`;

const cancelJob = async (
  role: Role,
  job: Job,
  refresh: (activeRole: Role) => Promise<void>,
  setNotice: (value: string) => void,
  setLoading: (value: boolean) => void,
) => {
  setLoading(true);
  try {
    await request<Job>(role, `/api/v1/media-jobs/${job.id}/cancel`, { method: "POST" });
    setNotice("Pending media job cancelled before finalizing a safety result.");
    await refresh(role);
  } catch (error) {
    setNotice(error instanceof Error ? error.message : "Unable to cancel the pending media job.");
  } finally {
    setLoading(false);
  }
};

createRoot(document.getElementById("root")!).render(<App />);
