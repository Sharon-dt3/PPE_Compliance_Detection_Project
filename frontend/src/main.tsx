import { FormEvent, JSX, useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const SAFETY_NOTICE =
  "This POC provides indicative safety-support signals. It is not an employee productivity-monitoring system and must not be used for autonomous disciplinary or employment decisions.";

// Mirrors PolicyRequest's allowed_labels set (backend app/main.py) -- keep in sync.
const CLASS_THRESHOLD_LABELS = ["no_helmet", "no_vest", "helmet", "vest", "gloves", "glasses", "person"] as const;

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
  preview_available: boolean;
};

type Alert = {
  id: string;
  source_id: string;
  zone_id: string;
  event_type: string;
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

type ReportingSettings = { shift_schedule: Record<string, [number, number]>; updated_at: string };

type DashboardFilters = { zoneId: string; sourceId: string; shift: string; alertStatus: string; alertStartDate: string; alertEndDate: string };
const EMPTY_DASHBOARD_FILTERS: DashboardFilters = { zoneId: "", sourceId: "", shift: "", alertStatus: "", alertStartDate: "", alertEndDate: "" };
const ALERT_STATUS_OPTIONS = ["open", "acknowledged", "resolved", "expired", "cancelled"] as const;

type FrameSummary = {
  frame_index: number;
  person_count: number;
  compliant_count: number;
  non_compliant_count: number;
  unknown_count: number;
  confidence_summary: Record<string, { count: number; min_confidence: number; max_confidence: number }>;
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

type PlatformUser = {
  id: string;
  auth_subject: string;
  role: Role;
  enabled: boolean;
  created_at: string;
};

type RetentionSettings = {
  raw_media_retention_hours: number;
  frame_observation_retention_hours: number;
  updated_at: string;
};

type InferenceSettings = {
  detection_provider: string;
  demo_mode: boolean;
  hf_model_repository: string;
  hf_model_filename: string;
  local_model_path: string;
  detection_confidence_threshold: number;
  updated_at: string;
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
  | "users"
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
  { view: "dashboard", label: "Dashboard", roles: ["safety_supervisor", "hse_manager", "administrator", "governance_reviewer", "demonstration_viewer"] },
  { view: "media", label: "Media jobs", roles: ["safety_supervisor", "hse_manager", "administrator"] },
  { view: "media-new", label: "New upload", roles: ["safety_supervisor", "hse_manager", "administrator"] },
  { view: "alerts", label: "Safety alerts", roles: ["safety_supervisor", "hse_manager", "administrator", "demonstration_viewer"] },
  { view: "sources", label: "Sources", roles: ["administrator"] },
  { view: "zones", label: "Zones", roles: ["administrator"] },
  { view: "policies", label: "Policies", roles: ["administrator"] },
  { view: "models", label: "Model registry", roles: ["administrator", "model_evaluator"] },
  { view: "model-evaluation", label: "Model evaluation", roles: ["model_evaluator"] },
  { view: "retention", label: "Retention", roles: ["administrator", "governance_reviewer"] },
  { view: "users", label: "Users & roles", roles: ["administrator"] },
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
    users: "/admin/users",
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
    users: true,
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
      color: ["#16a34a", "#e11d48", "#f59e0b"],
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
      color: ["#16a34a", "#f59e0b"],
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
  const [openAlertCount, setOpenAlertCount] = useState(0);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const [shiftSchedule, setShiftSchedule] = useState<Record<string, [number, number]>>({});
  const [dashboardFilters, setDashboardFilters] = useState<DashboardFilters>(EMPTY_DASHBOARD_FILTERS);
  const [evaluations, setEvaluations] = useState<ModelEvaluation[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [retentionSettings, setRetentionSettings] = useState<RetentionSettings | null>(null);
  const [inferenceSettings, setInferenceSettings] = useState<InferenceSettings | null>(null);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [editingSourceId, setEditingSourceId] = useState<string | null>(null);
  const [editingZoneId, setEditingZoneId] = useState<string | null>(null);
  const [frames, setFrames] = useState<FrameSummary[]>([]);
  const [notice, setNotice] = useState("Select a permitted workflow.");
  const [noticeKind, setNoticeKind] = useState<"success" | "error" | "info">("info");
  const [loading, setLoading] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [file, setFile] = useState<File | null>(null);

  const allowedNavigation = useMemo(() => NAVIGATION.filter((item) => role && item.roles.includes(role)), [role]);
  const selectedZone = useMemo(
    () => zones.find((zone) => zone.id === sources.find((source) => source.id === sourceId)?.zone_id),
    [sourceId, sources, zones],
  );
  const jobsInFlight = useMemo(
    () => jobs.some((job) => ["queued", "validating", "processing"].includes(job.status)),
    [jobs],
  );
  const jobConfidenceSummary = useMemo(() => {
    const aggregate: Record<string, { count: number; min_confidence: number; max_confidence: number }> = {};
    for (const frame of frames) {
      for (const [label, stats] of Object.entries(frame.confidence_summary)) {
        const existing = aggregate[label];
        if (!existing) aggregate[label] = { ...stats };
        else {
          existing.count += stats.count;
          existing.min_confidence = Math.min(existing.min_confidence, stats.min_confidence);
          existing.max_confidence = Math.max(existing.max_confidence, stats.max_confidence);
        }
      }
    }
    return aggregate;
  }, [frames]);

  const navigate = (nextView: View) => {
    setView(nextView);
    window.history.pushState({}, "", routeFor(nextView));
  };

  const notifySuccess = (message: string) => {
    setNotice(message);
    setNoticeKind("success");
  };
  const notifyInfo = (message: string) => {
    setNotice(message);
    setNoticeKind("info");
  };
  const notifyError = (error: unknown, fallback: string) => {
    setNotice(error instanceof Error ? error.message : fallback);
    setNoticeKind("error");
  };
  const notifyErrorMessage = (message: string) => {
    setNotice(message);
    setNoticeKind("error");
  };

  const loadForRole = async (activeRole: Role, filters: DashboardFilters = dashboardFilters) => {
    setLoading(true);
    try {
      const zonePromise = request<Zone[]>(activeRole, "/api/v1/zones");
      const sourcesAllowed = ["safety_supervisor", "hse_manager", "administrator"].includes(activeRole);
      const dashboardAllowed = ["safety_supervisor", "hse_manager", "administrator", "governance_reviewer", "demonstration_viewer"].includes(activeRole);
      const alertsAllowed = ["safety_supervisor", "hse_manager", "administrator", "demonstration_viewer"].includes(activeRole);

      // Compliance/trend accept zone, camera, and shift; alert metrics only accept zone and
      // camera (the backend's alert_totals has no shift dimension) -- built as two query
      // strings rather than sending a shift param the alerts endpoint would silently ignore.
      const complianceParams = new URLSearchParams();
      if (filters.zoneId) complianceParams.set("zone_id", filters.zoneId);
      if (filters.sourceId) complianceParams.set("source_id", filters.sourceId);
      if (filters.shift) complianceParams.set("shift", filters.shift);
      const complianceQuery = complianceParams.toString() ? `?${complianceParams.toString()}` : "";

      const alertParams = new URLSearchParams();
      if (filters.zoneId) alertParams.set("zone_id", filters.zoneId);
      if (filters.sourceId) alertParams.set("source_id", filters.sourceId);
      const alertQuery = alertParams.toString() ? `?${alertParams.toString()}` : "";

      // The alert queue itself additionally supports status and creation-date filtering,
      // which the aggregate /reports/alerts totals endpoint does not accept -- a separate
      // query string, rather than appending unsupported params to alertQuery above.
      const alertListParams = new URLSearchParams(alertParams);
      if (filters.alertStatus) alertListParams.set("status", filters.alertStatus);
      if (filters.alertStartDate) alertListParams.set("start_at", `${filters.alertStartDate}T00:00:00`);
      if (filters.alertEndDate) alertListParams.set("end_at", `${filters.alertEndDate}T23:59:59`);
      const alertListQuery = alertListParams.toString() ? `?${alertListParams.toString()}` : "";

      const [zoneData, sourceData, jobData, alertData, reportData, trendData, metricsData, reportingSettings] = await Promise.all([
        zonePromise,
        sourcesAllowed ? request<Source[]>(activeRole, "/api/v1/sources") : Promise.resolve([]),
        sourcesAllowed ? request<Job[]>(activeRole, "/api/v1/media-jobs") : Promise.resolve([]),
        alertsAllowed ? request<Alert[]>(activeRole, `/api/v1/alerts${alertListQuery}`) : Promise.resolve([]),
        dashboardAllowed ? request<Report>(activeRole, `/api/v1/reports/compliance${complianceQuery}`) : Promise.resolve(null),
        dashboardAllowed ? request<TrendPoint[]>(activeRole, `/api/v1/reports/compliance/trend${complianceQuery}`) : Promise.resolve([]),
        dashboardAllowed ? request<AlertMetrics>(activeRole, `/api/v1/reports/alerts${alertQuery}`) : Promise.resolve(null),
        dashboardAllowed ? request<ReportingSettings>(activeRole, "/api/v1/settings/reporting").catch(() => null) : Promise.resolve(null),
      ]);

      setZones(zoneData);
      setSources(sourceData);
      setJobs(jobData);
      setAlerts(alertData);
      setReport(reportData);
      setTrend(trendData);
      setAlertMetrics(metricsData);
      if (reportingSettings) setShiftSchedule(reportingSettings.shift_schedule);
      setSourceId((current) => current || sourceData[0]?.id || "");
      setLastUpdatedAt(new Date());
    } catch (error) {
      notifyError(error, "Unable to load this permitted POC workflow.");
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
    if (!role || !jobsInFlight) return undefined;
    const timer = window.setInterval(() => void loadForRole(role, dashboardFilters), 3000);
    return () => window.clearInterval(timer);
  }, [jobsInFlight, role, dashboardFilters]);

  // The response loop's "who receives an alert" answer for this POC: rather than an
  // external push channel (email/webhook both need infrastructure this environment doesn't
  // have configured), any client with the app open polls a live open-alert count, shown as
  // a badge on the Safety alerts nav item -- independent of whether a media job happens to
  // be in flight, unlike the poll above.
  useEffect(() => {
    if (!role || !NAVIGATION.find((item) => item.view === "alerts")?.roles.includes(role)) return undefined;
    const poll = () => {
      request<{ open_count: number }>(role, "/api/v1/alerts/open-count")
        .then((result) => setOpenAlertCount(result.open_count))
        .catch(() => undefined);
    };
    poll();
    const timer = window.setInterval(poll, 10_000);
    return () => window.clearInterval(timer);
  }, [role]);

  const changeRole = (nextRole: Role) => {
    window.sessionStorage.setItem("ppe-demo-role", nextRole);
    setRole(nextRole);
    const defaultView = NAVIGATION.find((item) => item.roles.includes(nextRole))?.view ?? "dashboard";
    navigate(defaultView);
    notifyInfo(`${ROLE_LABELS[nextRole]} demonstration role selected.`);
  };

  const handleSupabaseAuthenticated = (me: Me) => {
    setRole(me.role);
    const defaultView = NAVIGATION.find((item) => item.roles.includes(me.role))?.view ?? "dashboard";
    navigate(defaultView);
    notifySuccess(`Signed in as ${ROLE_LABELS[me.role]}.`);
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
      notifyErrorMessage("Choose a configured source and a JPEG, PNG, MP4, or MOV file.");
      return;
    }

    setLoading(true);
    try {
      const data = new FormData();
      data.append("file", file);
      const job = await request<Job>(role, `/api/v1/media-jobs?source_id=${encodeURIComponent(sourceId)}`, { method: "POST", body: data });
      notifySuccess(`Processing job created: ${job.message}`);
      setFile(null);
      await loadForRole(role);
      navigate("media");
    } catch (error) {
      notifyError(error, "Unable to submit the media job.");
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
      notifyError(error, "Frame summaries are unavailable.");
    } finally {
      setLoading(false);
    }
  };

  const updateAlert = async (alert: Alert, action: "acknowledgements" | "resolve" | "cancel") => {
    if (!role) return;
    const actionLabel = action === "resolve" ? "resolution outcome" : action === "cancel" ? "cancellation reason" : "safety intervention";
    // The note is optional (FR-ALERT-03): only an explicit Cancel of this prompt aborts the
    // action entirely. An OK with no text still submits, with no note recorded -- matching
    // what the API itself accepts.
    const note = window.prompt(`Record the non-identifying ${actionLabel} (optional -- leave blank and press OK to skip):`);
    if (note === null) return;

    setLoading(true);
    try {
      await request<Alert>(role, `/api/v1/alerts/${alert.id}/${action}`, {
        method: "POST",
        body: JSON.stringify({ note: note.trim() || null }),
      });
      notifySuccess(
        action === "resolve"
          ? "Safety alert resolved after human review."
          : action === "cancel"
            ? "Safety alert cancelled as raised in error."
            : "Safety alert acknowledged and intervention recorded.",
      );
      await loadForRole(role);
    } catch (error) {
      notifyError(error, "The alert update could not be completed.");
    } finally {
      setLoading(false);
    }
  };

  const createSource = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!role) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setLoading(true);
    try {
      await request<Source>(role, "/api/v1/sources", {
        method: "POST",
        body: JSON.stringify({ name: form.get("name"), zone_id: form.get("zone_id"), enabled: true }),
      });
      formElement.reset();
      notifySuccess("Camera source configuration created and audited.");
      await loadForRole(role);
    } catch (error) {
      notifyError(error, "Unable to create the source.");
    } finally {
      setLoading(false);
    }
  };

  const createZone = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!role) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setLoading(true);
    try {
      await request<Zone>(role, "/api/v1/zones", {
        method: "POST",
        body: JSON.stringify({ name: form.get("name"), description: form.get("description"), enabled: true }),
      });
      formElement.reset();
      notifySuccess("Safety zone and initial policy created and audited.");
      await loadForRole(role);
    } catch (error) {
      notifyError(error, "Unable to create the safety zone.");
    } finally {
      setLoading(false);
    }
  };

  const updateSource = async (event: FormEvent<HTMLFormElement>, sourceId: string) => {
    event.preventDefault();
    if (!role) return;
    const form = new FormData(event.currentTarget);
    setLoading(true);
    try {
      await request<Source>(role, `/api/v1/sources/${sourceId}`, {
        method: "PATCH",
        body: JSON.stringify({ name: form.get("name"), zone_id: form.get("zone_id") }),
      });
      notifySuccess("Camera source configuration updated and audited.");
      setEditingSourceId(null);
      await loadForRole(role);
    } catch (error) {
      notifyError(error, "Unable to update the source.");
    } finally {
      setLoading(false);
    }
  };

  const updateZone = async (event: FormEvent<HTMLFormElement>, zoneId: string) => {
    event.preventDefault();
    if (!role) return;
    const form = new FormData(event.currentTarget);
    setLoading(true);
    try {
      await request<Zone>(role, `/api/v1/zones/${zoneId}`, {
        method: "PATCH",
        body: JSON.stringify({ name: form.get("name"), description: form.get("description") }),
      });
      notifySuccess("Safety zone configuration updated and audited.");
      setEditingZoneId(null);
      await loadForRole(role);
    } catch (error) {
      notifyError(error, "Unable to update the safety zone.");
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
      const classConfidenceThresholds: Record<string, number> = {};
      for (const label of CLASS_THRESHOLD_LABELS) {
        const raw = form.get(`threshold_${label}`);
        if (raw !== null && raw !== "") classConfidenceThresholds[label] = Number(raw);
      }
      await request(role, `/api/v1/zones/${zoneId}/policy`, {
        method: "PATCH",
        body: JSON.stringify({
          helmet_required: form.get("helmet_required") === "on",
          vest_required: form.get("vest_required") === "on",
          confidence_threshold: Number(form.get("confidence_threshold")),
          class_confidence_thresholds: classConfidenceThresholds,
          persistence_frames: Number(form.get("persistence_frames")),
          deduplication_seconds: Number(form.get("deduplication_seconds")),
          evidence_retention_hours: Number(form.get("evidence_retention_hours")),
          active: true,
        }),
      });
      notifySuccess("New immutable zone policy version created and activated.");
      await loadForRole(role);
    } catch (error) {
      notifyError(error, "Unable to create the policy version.");
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
      notifyError(error, "Model evaluations are unavailable.");
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
      notifyError(error, "Audit records are unavailable.");
    } finally {
      setLoading(false);
    }
  };

  const loadUsers = async () => {
    if (!role) return;
    setLoading(true);
    try {
      setUsers(await request<PlatformUser[]>(role, "/api/v1/users"));
    } catch (error) {
      notifyError(error, "Role assignments are unavailable.");
    } finally {
      setLoading(false);
    }
  };

  const createUser = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!role) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setLoading(true);
    try {
      await request<PlatformUser>(role, "/api/v1/users", {
        method: "POST",
        body: JSON.stringify({ auth_subject: form.get("auth_subject"), role: form.get("role"), enabled: true }),
      });
      formElement.reset();
      notifySuccess("Role assignment created and audited.");
      await loadUsers();
    } catch (error) {
      notifyError(error, "Unable to create the role assignment.");
    } finally {
      setLoading(false);
    }
  };

  const updateUserRole = async (user: PlatformUser, nextRole: Role) => {
    if (!role) return;
    setLoading(true);
    try {
      await request<PlatformUser>(role, `/api/v1/users/${user.id}`, { method: "PATCH", body: JSON.stringify({ role: nextRole }) });
      notifySuccess(`Role updated for ${user.auth_subject}.`);
      await loadUsers();
    } catch (error) {
      notifyError(error, "Unable to update this role assignment.");
    } finally {
      setLoading(false);
    }
  };

  const toggleUserEnabled = async (user: PlatformUser) => {
    if (!role) return;
    setLoading(true);
    try {
      await request<PlatformUser>(role, `/api/v1/users/${user.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !user.enabled }) });
      notifySuccess(user.enabled ? `Access disabled for ${user.auth_subject}.` : `Access re-enabled for ${user.auth_subject}.`);
      await loadUsers();
    } catch (error) {
      notifyError(error, "Unable to update this role assignment.");
    } finally {
      setLoading(false);
    }
  };

  const loadRetentionSettings = async () => {
    if (!role) return;
    setLoading(true);
    try {
      setRetentionSettings(await request<RetentionSettings>(role, "/api/v1/settings/retention"));
    } catch (error) {
      notifyError(error, "Retention settings are unavailable.");
    } finally {
      setLoading(false);
    }
  };

  const updateRetentionSettingsForm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!role) return;
    const form = new FormData(event.currentTarget);
    setLoading(true);
    try {
      setRetentionSettings(
        await request<RetentionSettings>(role, "/api/v1/settings/retention", {
          method: "PATCH",
          body: JSON.stringify({
            raw_media_retention_hours: Number(form.get("raw_media_retention_hours")),
            frame_observation_retention_hours: Number(form.get("frame_observation_retention_hours")),
          }),
        }),
      );
      notifySuccess("Retention settings updated and audited.");
    } catch (error) {
      notifyError(error, "Unable to update retention settings.");
    } finally {
      setLoading(false);
    }
  };

  const loadInferenceSettings = async () => {
    if (!role) return;
    setLoading(true);
    try {
      setInferenceSettings(await request<InferenceSettings>(role, "/api/v1/settings/inference"));
    } catch (error) {
      notifyError(error, "Inference provider configuration is unavailable.");
    } finally {
      setLoading(false);
    }
  };

  const updateInferenceSettingsForm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!role) return;
    const form = new FormData(event.currentTarget);
    setLoading(true);
    try {
      setInferenceSettings(
        await request<InferenceSettings>(role, "/api/v1/settings/inference", {
          method: "PATCH",
          body: JSON.stringify({
            detection_provider: form.get("detection_provider"),
            demo_mode: form.get("demo_mode") === "on",
            hf_model_repository: form.get("hf_model_repository"),
            hf_model_filename: form.get("hf_model_filename"),
            local_model_path: form.get("local_model_path"),
            detection_confidence_threshold: Number(form.get("detection_confidence_threshold")),
          }),
        }),
      );
      notifySuccess("Inference provider configuration updated and audited.");
    } catch (error) {
      notifyError(error, "Unable to update the inference provider configuration.");
    } finally {
      setLoading(false);
    }
  };

  const approveEvidenceForDemo = async (alert: Alert) => {
    if (!role) return;
    setLoading(true);
    try {
      await request<Alert>(role, `/api/v1/alerts/${alert.id}/evidence/approve-demo`, { method: "POST" });
      notifySuccess("Evidence approved for demonstration-viewer access.");
    } catch (error) {
      notifyError(error, "Unable to approve this evidence for demonstration viewing.");
    } finally {
      setLoading(false);
    }
  };

  const retryAlertEvidence = async (alert: Alert) => {
    if (!role) return;
    setLoading(true);
    try {
      const updated = await request<Alert>(role, `/api/v1/alerts/${alert.id}/evidence/retry`, { method: "POST" });
      if (updated.evidence_available) notifySuccess("Privacy-processed evidence generated successfully.");
      else notifyInfo("Evidence is still unavailable; mandatory privacy processing did not complete.");
      await loadForRole(role);
    } catch (error) {
      notifyError(error, "Unable to retry evidence generation for this alert.");
    } finally {
      setLoading(false);
    }
  };

  const updateDashboardFilters = (nextFilters: DashboardFilters) => {
    setDashboardFilters(nextFilters);
    if (role) void loadForRole(role, nextFilters);
  };

  const exportAggregate = async () => {
    if (!role) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_URL}/api/v1/reports/exports`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await resolveAuthHeader(role)) },
        body: JSON.stringify({
          zone_id: dashboardFilters.zoneId || undefined,
          source_id: dashboardFilters.sourceId || undefined,
          shift: dashboardFilters.shift || undefined,
        }),
      });
      if (!response.ok) throw new Error("The aggregate export could not be generated.");
      const content = await response.blob();
      const url = URL.createObjectURL(content);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "aggregate-compliance-report.csv";
      anchor.click();
      URL.revokeObjectURL(url);
      notifySuccess("Aggregate-only CSV export created and audited.");
    } catch (error) {
      notifyError(error, "The aggregate export could not be generated.");
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
      <p className="muted live-status" aria-live="polite">
        {jobsInFlight
          ? <><span className="live-dot" aria-hidden="true" /> Live — refreshing every 3 seconds while clips are processing.</>
          : lastUpdatedAt ? `Last updated ${lastUpdatedAt.toLocaleTimeString()}. No clips currently processing.` : "Loading…"}
      </p>
      <section className="panel filter-panel" aria-label="Dashboard filters">
        <label>Zone
          <select
            value={dashboardFilters.zoneId}
            onChange={(event) => updateDashboardFilters({ ...dashboardFilters, zoneId: event.target.value })}
          >
            <option value="">All zones</option>
            {zones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}
          </select>
        </label>
        <label>Camera
          <select
            value={dashboardFilters.sourceId}
            onChange={(event) => updateDashboardFilters({ ...dashboardFilters, sourceId: event.target.value })}
          >
            <option value="">All cameras</option>
            {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
          </select>
        </label>
        <label>Shift
          <select
            value={dashboardFilters.shift}
            onChange={(event) => updateDashboardFilters({ ...dashboardFilters, shift: event.target.value })}
          >
            <option value="">All shifts</option>
            {Object.keys(shiftSchedule).map((shiftName) => <option key={shiftName} value={shiftName}>{shiftName}</option>)}
          </select>
        </label>
        {(dashboardFilters.zoneId || dashboardFilters.sourceId || dashboardFilters.shift) && (
          <button className="secondary-button" type="button" onClick={() => updateDashboardFilters(EMPTY_DASHBOARD_FILTERS)}>
            Clear filters
          </button>
        )}
        <p className="muted">Shift filtering applies to compliance rate and trend only; alert totals below are scoped by zone and camera.</p>
      </section>
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
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Recent alert log &amp; annotated evidence inspector</h2>
            <p className="muted">The five most recent detect → alert → evidence-snapshot events. Evidence images are face-blurred before they can ever be stored.</p>
          </div>
          {allowedNavigation.some((item) => item.view === "alerts") && (
            <button className="secondary-button" type="button" onClick={() => navigate("alerts")}>Open full alert queue</button>
          )}
        </div>
        {alerts.length === 0 ? <EmptyState title="No safety alerts" text="No sustained PPE non-compliance alerts have been created in this POC session." /> : <div className="alerts">
          {[...alerts]
            .sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime())
            .slice(0, 5)
            .map((alert) => <article className="alert" key={alert.id}>
              <div>
                <Status status={alert.status} />
                <p className="event-type">{alert.event_type}</p>
                <h3>{alert.failed_requirement}</h3>
                <p>Confidence: {(alert.confidence * 100).toFixed(0)}% · {formatDate(alert.created_at)} · Confirmed observations: {alert.occurrence_count}</p>
                <p className="muted">{alert.evidence_message}</p>
                {alert.evidence_available ? <EvidenceLink alertId={alert.id} role={role} onError={notifyErrorMessage} /> : <EvidenceExpired />}
                {role !== "demonstration_viewer" && <RuleExplanation alertId={alert.id} role={role} onError={notifyErrorMessage} />}
                {alert.acknowledgement_note && <p><strong>Intervention:</strong> {alert.acknowledgement_note}</p>}
                {alert.resolution_note && <p><strong>Resolution:</strong> {alert.resolution_note}</p>}
              </div>
            </article>)}
        </div>}
      </section>
    </>
  );

  const renderMedia = () => (
    <>
      <PageHeader title="Private media processing" description="Manual CCTV image and video uploads only. Live RTSP/VMS ingestion is intentionally outside this POC." action={<button type="button" onClick={() => navigate("media-new")}>New media job</button>} />
      {selectedJob && <section className="panel detail-panel">
        <div className="panel-heading"><div><h2>{selectedJob.filename}</h2><p className="muted">Privacy-safe sampled-frame summaries only.</p></div><button className="secondary-button" type="button" onClick={() => setSelectedJob(null)}>Close detail</button></div>
        {role && (selectedJob.preview_available
          ? <JobPreviewLink jobId={selectedJob.id} role={role} onError={notifyErrorMessage} />
          : <EmptyState title="No preview available" text="A face-blurred preview is generated for every completed job; this one hasn't finished processing yet, its preview expired, or mandatory privacy processing could not run." />)}
        <div className="metrics compact-metrics">
          <Metric label="Compliant" value={selectedJob.compliant_count} />
          <Metric label="Non-compliant" value={selectedJob.non_compliant_count} />
          <Metric label="Unknown" value={selectedJob.unknown_count} />
        </div>
        {Object.keys(jobConfidenceSummary).length > 0 && (
          <div className="confidence-summary">
            <h3>Detection confidence (whole job)</h3>
            <ul className="confidence-list">
              {Object.entries(jobConfidenceSummary).map(([label, stats]) => (
                <li key={label}>
                  <span className="confidence-label">{label}</span>
                  <span className="muted"> · {stats.count} detection{stats.count === 1 ? "" : "s"} · </span>
                  <span className="confidence-range">{(stats.min_confidence * 100).toFixed(0)}%–{(stats.max_confidence * 100).toFixed(0)}%</span>
                </li>
              ))}
            </ul>
            <p className="muted">Range across every sampled frame in this job. A rule only fires as a violation once its zone's configured confidence threshold is cleared -- see the Zones page.</p>
          </div>
        )}
        <p className="muted">
          These totals count only <strong>persistent</strong> violations -- a requirement failing across this
          zone's configured number of consecutive sampled frames. The table below shows each individual sampled
          frame's own raw observation, which can show a violation that never persisted long enough to appear in
          the totals above (a single-image upload has exactly one frame, so it can never reach a multi-frame
          threshold on its own). Both are correct; they answer different questions.
        </p>
        {frames.length ? <table><thead><tr><th>Frame</th><th>Observed population</th><th>Compliant</th><th>Non-compliant</th><th>Unknown</th><th>Confidence by class</th></tr></thead><tbody>
          {frames.map((frame) => <tr key={frame.frame_index}>
            <td>{frame.frame_index}</td>
            <td>{frame.person_count}</td>
            <td>{frame.compliant_count}</td>
            <td>{frame.non_compliant_count}</td>
            <td>{frame.unknown_count}</td>
            <td>{Object.entries(frame.confidence_summary).length
              ? Object.entries(frame.confidence_summary).map(([label, stats]) => `${label} ${(stats.min_confidence * 100).toFixed(0)}–${(stats.max_confidence * 100).toFixed(0)}%`).join(", ")
              : "—"}</td>
          </tr>)}
        </tbody></table> : <EmptyState title="No frame observations" text="Frame summaries become available after media processing begins." />}
      </section>}
      <section className="panel">
        <h2>Recent processing jobs</h2>
        {jobs.length === 0 ? <EmptyState title="No media jobs" text="Create a private processing job to demonstrate the upload-to-review workflow." /> : <div className="jobs">
          {jobs.map((job) => <article className="job" key={job.id}>
            <div><Status status={job.status} /><h3>{job.filename}</h3><p>{sources.find((source) => source.id === job.source_id)?.name ?? "Unknown source"} · {formatDate(job.submitted_at)} · Compliant: {job.compliant_count} · Non-compliant: {job.non_compliant_count} · Unknown: {job.unknown_count}</p><p className="muted">{job.message}</p></div>
            <div className="button-row"><button className="secondary-button" type="button" onClick={() => void openJob(job)}>View summary</button>{["queued", "validating", "processing"].includes(job.status) && <button type="button" onClick={() => void cancelJob(role, job, loadForRole, notifyInfo, notifyError, setLoading)}>Cancel</button>}{job.status === "failed" && <button type="button" onClick={() => void retryJob(role, job, loadForRole, notifyInfo, notifyError, setLoading)}>Retry</button>}</div>
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
      <section className="panel filter-panel" aria-label="Alert queue filters">
        <label>Zone
          <select
            value={dashboardFilters.zoneId}
            onChange={(event) => updateDashboardFilters({ ...dashboardFilters, zoneId: event.target.value })}
          >
            <option value="">All zones</option>
            {zones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}
          </select>
        </label>
        <label>Status
          <select
            value={dashboardFilters.alertStatus}
            onChange={(event) => updateDashboardFilters({ ...dashboardFilters, alertStatus: event.target.value })}
          >
            <option value="">All statuses</option>
            {ALERT_STATUS_OPTIONS.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <label>From
          <input
            type="date"
            value={dashboardFilters.alertStartDate}
            onChange={(event) => updateDashboardFilters({ ...dashboardFilters, alertStartDate: event.target.value })}
          />
        </label>
        <label>To
          <input
            type="date"
            value={dashboardFilters.alertEndDate}
            onChange={(event) => updateDashboardFilters({ ...dashboardFilters, alertEndDate: event.target.value })}
          />
        </label>
        {(dashboardFilters.zoneId || dashboardFilters.alertStatus || dashboardFilters.alertStartDate || dashboardFilters.alertEndDate) && (
          <button
            className="secondary-button"
            type="button"
            onClick={() => updateDashboardFilters({ ...dashboardFilters, zoneId: "", alertStatus: "", alertStartDate: "", alertEndDate: "" })}
          >
            Clear filters
          </button>
        )}
        {dashboardFilters.sourceId && (
          <p className="muted">
            Also scoped to the camera filter set on the Dashboard page.{" "}
            <button className="text-button" type="button" onClick={() => updateDashboardFilters({ ...dashboardFilters, sourceId: "" })}>
              Clear camera filter
            </button>
          </p>
        )}
      </section>
      <section className="panel">
        {alerts.length === 0 ? <EmptyState title="No safety alerts" text="No sustained PPE non-compliance alerts have been created in this POC session." /> : <div className="alerts">
          {alerts.map((alert) => <article className="alert" key={alert.id}>
            <div>
              <Status status={alert.status} />
              <h3>{alert.failed_requirement}</h3>
              <p>Confidence: {(alert.confidence * 100).toFixed(0)}% · {formatDate(alert.created_at)} · Confirmed observations: {alert.occurrence_count}</p>
              <p className="muted">{alert.evidence_message}</p>
              {alert.evidence_available ? <EvidenceLink alertId={alert.id} role={role} onError={notifyErrorMessage} /> : <EvidenceExpired />}
              {alert.acknowledgement_note && <p><strong>Intervention:</strong> {alert.acknowledgement_note}</p>}
              {alert.resolution_note && <p><strong>Resolution:</strong> {alert.resolution_note}</p>}
            </div>
            {role === "safety_supervisor" && <div className="button-stack">
              {alert.status === "open" && <button type="button" onClick={() => void updateAlert(alert, "acknowledgements")}>Acknowledge</button>}
              {["open", "acknowledged"].includes(alert.status) && <button className="secondary-button" type="button" onClick={() => void updateAlert(alert, "resolve")}>Resolve</button>}
              {alert.status === "open" && <button className="secondary-button" type="button" onClick={() => void updateAlert(alert, "cancel")}>Cancel (raised in error)</button>}
              {alert.evidence_available && <button className="secondary-button" type="button" onClick={() => void approveEvidenceForDemo(alert)}>Approve evidence for demo viewing</button>}
              {!alert.evidence_available && <button className="secondary-button" type="button" onClick={() => void retryAlertEvidence(alert)}>Retry evidence generation</button>}
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
        <article className="panel"><h2>Configured sources</h2>{sources.length ? <table><thead><tr><th>Name</th><th>Zone</th><th></th></tr></thead><tbody>{sources.map((source) => editingSourceId === source.id ? (
          <tr key={source.id}>
            <td colSpan={3}>
              <form className="inline-edit-form" onSubmit={(event) => void updateSource(event, source.id)}>
                <input name="name" required minLength={3} maxLength={120} defaultValue={source.name} />
                <select name="zone_id" required defaultValue={source.zone_id}>
                  {zones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}
                </select>
                <button disabled={loading} type="submit">Save</button>
                <button className="secondary-button" type="button" onClick={() => setEditingSourceId(null)}>Cancel</button>
              </form>
            </td>
          </tr>
        ) : (
          <tr key={source.id}>
            <td>{source.name}</td>
            <td>{zones.find((zone) => zone.id === source.zone_id)?.name ?? "Unavailable zone"}</td>
            <td><button className="text-button" type="button" onClick={() => setEditingSourceId(source.id)}>Edit</button></td>
          </tr>
        ))}</tbody></table> : <EmptyState title="No sources configured" text="Create a source before submitting POC media." />}</article>
        <article className="panel"><h2>Add source</h2><form onSubmit={createSource}><label>Name<input name="name" required minLength={3} maxLength={120} /></label><label>Safety zone<select name="zone_id" required><option value="">Choose zone</option>{zones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}</select></label><button disabled={loading} type="submit">Create source</button></form></article>
      </section>
    </>
  );

  const renderZones = () => (
    <>
      <PageHeader title="Safety zone configuration" description="Zones define safety context and versioned PPE policy. They contain no employee, identity, or biometric data." />
      <section className="grid">
        <article className="panel"><h2>Configured zones</h2>{zones.length ? <table><thead><tr><th>Zone</th><th>Policy</th><th>Persistence</th><th></th></tr></thead><tbody>{zones.map((zone) => editingZoneId === zone.id ? (
          <tr key={zone.id}>
            <td colSpan={4}>
              <form className="inline-edit-form" onSubmit={(event) => void updateZone(event, zone.id)}>
                <input name="name" required minLength={3} maxLength={120} defaultValue={zone.name} />
                <textarea name="description" required minLength={3} maxLength={1000} defaultValue={zone.description} />
                <button disabled={loading} type="submit">Save</button>
                <button className="secondary-button" type="button" onClick={() => setEditingZoneId(null)}>Cancel</button>
              </form>
            </td>
          </tr>
        ) : (
          <tr key={zone.id}>
            <td><strong>{zone.name}</strong><br /><span className="muted">{zone.description}</span></td>
            <td>v{zone.policy.version}: {zone.policy.helmet_required ? "Helmet" : "No helmet rule"} · {zone.policy.vest_required ? "Vest" : "No vest rule"}</td>
            <td>{zone.policy.persistence_frames} frames</td>
            <td><button className="text-button" type="button" onClick={() => setEditingZoneId(zone.id)}>Edit</button></td>
          </tr>
        ))}</tbody></table> : <EmptyState title="No zones configured" text="Create a safety zone to begin policy configuration." />}</article>
        <article className="panel"><h2>Add safety zone</h2><form onSubmit={createZone}><label>Name<input name="name" required minLength={3} maxLength={120} /></label><label>Description<textarea name="description" required minLength={3} maxLength={1000} /></label><button disabled={loading} type="submit">Create zone</button></form></article>
      </section>
    </>
  );

  const renderPolicies = () => (
    <>
      <PageHeader title="Versioned PPE policies" description="Creating a policy produces a new immutable version. Configuration defaults are explicit and are not treated as a disciplinary rule." />
      <section className="grid">
        <article className="panel"><h2>Active policies</h2>{zones.map((zone) => <div className="policy policy-row" key={zone.id}><strong>{zone.name} · version {zone.policy.version}</strong><span>Helmet: {zone.policy.helmet_required ? "required" : "not required"}</span><span>Vest: {zone.policy.vest_required ? "required" : "not required"}</span><span>Persistence: {zone.policy.persistence_frames} frames · Deduplication: {zone.policy.deduplication_seconds}s</span></div>)}</article>
        <article className="panel">
          <h2>Create policy version</h2>
          <form onSubmit={createPolicy}>
            <label>Safety zone<select name="zone_id" required><option value="">Choose zone</option>{zones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}</select></label>
            <label className="check-label"><input name="helmet_required" type="checkbox" defaultChecked /> Helmet required</label>
            <label className="check-label"><input name="vest_required" type="checkbox" defaultChecked /> Hi-vis vest required</label>
            <label>Confidence threshold<input name="confidence_threshold" type="number" min="0" max="1" step="0.01" defaultValue="0.25" required /></label>
            <fieldset className="threshold-overrides">
              <legend>Per-class confidence overrides (optional)</legend>
              <p className="muted">
                Leave a field blank to use the flat confidence threshold above for that class. Raising the
                threshold for a negative class (e.g. no_helmet, no_vest) reduces false-positive violations
                from a single weak detection outweighing a much stronger correct one for the same person.
              </p>
              <div className="threshold-grid">
                {CLASS_THRESHOLD_LABELS.map((label) => (
                  <label key={label}>{label}
                    <input name={`threshold_${label}`} type="number" min="0" max="1" step="0.01" placeholder="uses flat threshold" />
                  </label>
                ))}
              </div>
              <p className="muted">
                gloves and glasses have no effect yet: no currently licensable model outputs these classes
                (see the Model registry's limitations for the primary model for why). Setting a threshold here
                does not enable detection.
              </p>
            </fieldset>
            <label>Persistence frames<input name="persistence_frames" type="number" min="1" max="60" defaultValue="3" required /></label>
            <label>Deduplication seconds<input name="deduplication_seconds" type="number" min="0" max="86400" defaultValue="60" required /></label>
            <label>Evidence retention hours<input name="evidence_retention_hours" type="number" min="24" max="72" defaultValue="48" required /></label>
            <button disabled={loading} type="submit">Create and activate version</button>
          </form>
        </article>
      </section>
    </>
  );

  const renderModels = () => (
    <>
      <PageHeader title="Model registry and limitations" description="POC model evaluations must be read alongside their class-level metrics, licensing, limitations, and approval status. No single headline accuracy is used." action={<button className="secondary-button" type="button" onClick={() => void loadEvaluations()}>Load evaluations</button>} />
      <section className="panel">
        <div className="panel-heading"><h2>Active inference provider configuration</h2><button className="secondary-button" type="button" onClick={() => void loadInferenceSettings()}>{inferenceSettings ? "Refresh" : "Load configuration"}</button></div>
        {!inferenceSettings ? <EmptyState title="Configuration not loaded" text="Load the active detection-provider configuration to review or change it." /> : role === "administrator" ? (
          <form onSubmit={updateInferenceSettingsForm} className="settings-form">
            <label className="check-label"><input name="demo_mode" type="checkbox" defaultChecked={inferenceSettings.demo_mode} key={`demo-${inferenceSettings.updated_at}`} /> Demo mode (deterministic, no real model)</label>
            <label>Detection provider<select name="detection_provider" defaultValue={inferenceSettings.detection_provider} key={`provider-${inferenceSettings.updated_at}`}><option value="demo">demo</option><option value="ultralytics">ultralytics</option></select></label>
            <label>Hugging Face repository<input name="hf_model_repository" defaultValue={inferenceSettings.hf_model_repository} key={`repo-${inferenceSettings.updated_at}`} maxLength={200} /></label>
            <label>Hugging Face filename<input name="hf_model_filename" defaultValue={inferenceSettings.hf_model_filename} key={`file-${inferenceSettings.updated_at}`} maxLength={200} /></label>
            <label>Local model path (overrides Hugging Face when set)<input name="local_model_path" defaultValue={inferenceSettings.local_model_path} key={`local-${inferenceSettings.updated_at}`} maxLength={500} /></label>
            <label>Confidence threshold<input name="detection_confidence_threshold" type="number" min="0" max="1" step="0.01" defaultValue={inferenceSettings.detection_confidence_threshold} key={`conf-${inferenceSettings.updated_at}`} required /></label>
            <p className="muted">Real inference stays disabled while demo mode is checked. A misconfigured model fails the next media job safely rather than substituting another provider.</p>
            <button disabled={loading} type="submit">Save configuration</button>
          </form>
        ) : (
          <dl className="definition-list">
            <div><dt>Mode</dt><dd>{inferenceSettings.demo_mode ? "Demo (deterministic)" : "Real inference"}</dd></div>
            <div><dt>Provider</dt><dd>{inferenceSettings.detection_provider}</dd></div>
            <div><dt>Model</dt><dd>{inferenceSettings.local_model_path || `${inferenceSettings.hf_model_repository}/${inferenceSettings.hf_model_filename}`}</dd></div>
            <div><dt>Confidence threshold</dt><dd>{inferenceSettings.detection_confidence_threshold}</dd></div>
          </dl>
        )}
      </section>
      <section className="panel">
        {!evaluations.length ? <EmptyState title="No evaluations loaded" text="Load approved model evaluation metadata to review POC capability and limitations." /> : <div className="cards">
          {evaluations.map((evaluation) => <article className="model-card" key={evaluation.id}><Status status={evaluation.approval_state} /><h2>{evaluation.model_name} <span className="muted">v{evaluation.model_version}</span></h2><p>{evaluation.provider} · {evaluation.licence_status}</p><p><strong>Dataset:</strong> {evaluation.dataset_reference}</p><p><strong>Latency:</strong> {evaluation.latency_ms ?? "Not recorded"} ms · <strong>Sampling:</strong> {evaluation.effective_sampling_rate ?? "Not recorded"} FPS</p><h3>Class-level results</h3><table><thead><tr><th>Class</th><th>Precision</th><th>Recall</th><th>F1</th></tr></thead><tbody>{Object.entries(evaluation.class_metrics).map(([label, metrics]) => <tr key={label}><td>{label}</td><td>{metrics.precision ?? "—"}</td><td>{metrics.recall ?? "—"}</td><td>{metrics.f1 ?? "—"}</td></tr>)}</tbody></table><p className="muted"><strong>Limitations:</strong> {evaluation.limitations}</p></article>)}
        </div>}
      </section>
    </>
  );

  const renderModelEvaluation = () => <><PageHeader title="Model evaluation workflow" description="The evaluator role can record approved benchmark metadata through the API. This POC UI provides transparent review of stored class-level results and limitations." action={<button type="button" onClick={() => { navigate("models"); void loadEvaluations(); }}>Review evaluations</button>} /><section className="panel"><h2>Evaluation guardrails</h2><ul className="safety-list"><li>Use approved labeled datasets only.</li><li>Record per-class precision, recall, F1, latency, unknown rate, and camera-angle limitations.</li><li>Keep experimental and POC-only models clearly marked as not production-approved.</li><li>Do not use evaluation data for worker identification or performance assessment.</li></ul></section></>;

  const renderRetention = () => (
    <>
      <PageHeader title="Retention and deletion controls" description="Raw uploaded media is deleted after processing according to approved retention. Privacy-processed evidence expires after its configured 24–72 hour period, set per zone policy version; aggregate metrics and audit records follow their own governance policy." />
      <section className="grid">
        <article className="panel">
          <h2>Automated controls</h2>
          <ul className="safety-list">
            <li>Scheduled cleanup removes expired raw private media.</li>
            <li>Expired evidence is deleted and becomes inaccessible through the reviewer API.</li>
            <li>Expired evidence is displayed as unavailable, never as a broken image.</li>
            <li>Retention outcomes are intended to be auditable by the governance workflow.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-heading"><h2>Global retention settings</h2><button className="secondary-button" type="button" onClick={() => void loadRetentionSettings()}>{retentionSettings ? "Refresh" : "Load settings"}</button></div>
          {!retentionSettings ? <EmptyState title="Settings not loaded" text="Load the active global retention settings to review or change them." /> : role === "administrator" ? (
            <form onSubmit={updateRetentionSettingsForm} className="settings-form">
              <label>Raw media retention (hours)<input name="raw_media_retention_hours" type="number" min="1" max="168" defaultValue={retentionSettings.raw_media_retention_hours} key={`raw-${retentionSettings.updated_at}`} required /></label>
              <label>Frame/person observation retention (hours)<input name="frame_observation_retention_hours" type="number" min="1" max="168" defaultValue={retentionSettings.frame_observation_retention_hours} key={`frame-${retentionSettings.updated_at}`} required /></label>
              <p className="muted">Face-blurred evidence retention is not set here: it stays per zone policy version, 24–72 hours.</p>
              <button disabled={loading} type="submit">Save retention settings</button>
            </form>
          ) : (
            <dl className="definition-list">
              <div><dt>Raw media retention</dt><dd>{retentionSettings.raw_media_retention_hours} hours</dd></div>
              <div><dt>Frame/person observation retention</dt><dd>{retentionSettings.frame_observation_retention_hours} hours</dd></div>
              <div><dt>Last updated</dt><dd>{formatDate(retentionSettings.updated_at)}</dd></div>
            </dl>
          )}
        </article>
      </section>
    </>
  );

  const renderUsers = () => (
    <>
      <PageHeader title="Users and role assignments" description="No role is ever trusted from a signed-in identity's own claim. Only a role assignment created here determines what a verified identity is permitted to do." />
      <section className="grid">
        <article className="panel">
          <div className="panel-heading"><h2>Configured role assignments</h2><button className="secondary-button" type="button" onClick={() => void loadUsers()}>{users.length ? "Refresh" : "Load users"}</button></div>
          {!users.length ? <EmptyState title="No role assignments loaded" text="Load configured role assignments, or create the first one for a verified identity-provider subject." /> : <table>
            <thead><tr><th>Identity subject</th><th>Role</th><th>Access</th><th>Created</th><th></th></tr></thead>
            <tbody>
              {users.map((user) => <tr key={user.id}>
                <td>{user.auth_subject}</td>
                <td><select value={user.role} onChange={(event) => void updateUserRole(user, event.target.value as Role)}>{Object.entries(ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></td>
                <td><Status status={user.enabled ? "enabled" : "disabled"} /></td>
                <td>{formatDate(user.created_at)}</td>
                <td><button className="text-button" type="button" onClick={() => void toggleUserEnabled(user)}>{user.enabled ? "Disable" : "Enable"}</button></td>
              </tr>)}
            </tbody>
          </table>}
        </article>
        <article className="panel">
          <h2>Assign a role</h2>
          <form onSubmit={createUser}>
            <label>Identity-provider subject (JWT `sub`)<input name="auth_subject" required minLength={1} maxLength={128} /></label>
            <label>Role<select name="role" required defaultValue="safety_supervisor">{Object.entries(ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <button disabled={loading} type="submit">Create role assignment</button>
          </form>
          <p className="muted">Find the subject in your identity provider's user record (for Supabase: Authentication → Users → the User UID).</p>
        </article>
      </section>
    </>
  );

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
    users: renderUsers,
    audit: renderAudit,
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div><p className="eyebrow">Safety POC</p><h1>PPE Compliance</h1></div>
        <nav aria-label="Primary navigation">
          {allowedNavigation.map((item) => (
            <button className={`nav-item ${activeView === item.view ? "active" : ""}`} key={item.view} type="button" onClick={() => navigate(item.view)}>
              {item.label}
              {item.view === "alerts" && openAlertCount > 0 && <span className="nav-badge" aria-label={`${openAlertCount} open safety alerts`}>{openAlertCount}</span>}
            </button>
          ))}
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
        <section
          className={`notice notice-${noticeKind}`}
          role={noticeKind === "error" ? "alert" : "status"}
          aria-live={noticeKind === "error" ? "assertive" : "polite"}
        >
          {notice}
        </section>
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

type RuleResult = {
  id: string;
  requirement: string;
  persistent: boolean;
  non_compliant_count: number;
  confidence: number | null;
  created_at: string;
};

// PUBLIC_INTERFACE
function RuleExplanation({ alertId, role, onError }: { alertId: string; role: Role; onError: (message: string) => void }) {
  /** Explain, from durable per-frame rule-evaluation records, exactly why this alert fired. */
  const [results, setResults] = useState<RuleResult[] | null>(null);
  const [open, setOpen] = useState(false);

  const loadResults = async () => {
    setOpen(true);
    if (results !== null) return;
    try {
      setResults(await request<RuleResult[]>(role, `/api/v1/alerts/${alertId}/rule-results`));
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to load the rule explanation for this alert.");
      setOpen(false);
    }
  };

  if (!open) return <button className="text-button" type="button" aria-expanded="false" onClick={() => void loadResults()}>Why was this flagged?</button>;
  return (
    <div className="rule-explanation">
      <div className="panel-heading">
        <h4>Why this alert fired</h4>
        <button className="text-button" type="button" aria-expanded="true" onClick={() => setOpen(false)}>Hide</button>
      </div>
      {results === null ? <p className="muted">Loading rule evaluations…</p> : results.length === 0 ? (
        <p className="muted">No durable rule-evaluation records exist for this alert.</p>
      ) : (
        <table>
          <thead><tr><th>When</th><th>Requirement</th><th>Persistent</th><th>Peak violating count</th><th>Confidence</th></tr></thead>
          <tbody>
            {results.map((result) => <tr key={result.id}>
              <td>{formatDate(result.created_at)}</td>
              <td>{result.requirement}</td>
              <td>{result.persistent ? "Yes -- met this zone's persistence threshold" : "Not yet -- held for future frames"}</td>
              <td>{result.non_compliant_count}</td>
              <td>{result.confidence === null ? "--" : `${(result.confidence * 100).toFixed(0)}%`}</td>
            </tr>)}
          </tbody>
        </table>
      )}
      <p className="muted">Each row is a durable record of one job's evaluation against this exact rule -- including jobs that only merged into this alert's occurrence count without creating it.</p>
    </div>
  );
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

function JobPreviewLink({ jobId, role, onError }: { jobId: string; role: Role; onError: (message: string) => void }) {
  /** Show what the detector actually found for this job -- every completed job, not only
   * ones that produced a confirmed alert. Same privacy-processed, face-blurred image the
   * alert evidence view uses, fetched from the job-scoped endpoint instead. */
  const [url, setUrl] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);

  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);

  const viewPreview = async () => {
    try {
      const response = await fetch(`${API_URL}/api/v1/media-jobs/${jobId}/preview`, { headers: await resolveAuthHeader(role) });
      if (!response.ok) {
        setExpired(true);
        throw new Error("A preview is unavailable or expired for this job.");
      }
      const previewUrl = URL.createObjectURL(await response.blob());
      setUrl(previewUrl);
    } catch (error) {
      onError(error instanceof Error ? error.message : "The job preview is unavailable.");
    }
  };

  if (expired) return <EvidenceExpired />;
  return <div className="evidence-control">{url ? <img alt="Face-blurred, annotated preview of what the detector found in this job" className="evidence-image" src={url} /> : <button className="text-button" type="button" onClick={() => void viewPreview()}>View what the detector found</button>}</div>;
}

const formatDate = (value: string) => new Date(value).toLocaleString();
const formatMinutes = (value: number | null | undefined) => value === null || value === undefined ? "Not available" : `${value.toFixed(1)} min`;

const cancelJob = async (
  role: Role,
  job: Job,
  refresh: (activeRole: Role) => Promise<void>,
  notifyInfo: (value: string) => void,
  notifyError: (error: unknown, fallback: string) => void,
  setLoading: (value: boolean) => void,
) => {
  setLoading(true);
  try {
    await request<Job>(role, `/api/v1/media-jobs/${job.id}/cancel`, { method: "POST" });
    notifyInfo("Pending media job cancelled before finalizing a safety result.");
    await refresh(role);
  } catch (error) {
    notifyError(error, "Unable to cancel the pending media job.");
  } finally {
    setLoading(false);
  }
};

const retryJob = async (
  role: Role,
  job: Job,
  refresh: (activeRole: Role) => Promise<void>,
  notifyInfo: (value: string) => void,
  notifyError: (error: unknown, fallback: string) => void,
  setLoading: (value: boolean) => void,
) => {
  setLoading(true);
  try {
    await request<Job>(role, `/api/v1/media-jobs/${job.id}/retry`, { method: "POST" });
    notifyInfo("Failed media job re-queued for retry.");
    await refresh(role);
  } catch (error) {
    notifyError(error, "Unable to retry this media job.");
  } finally {
    setLoading(false);
  }
};

createRoot(document.getElementById("root")!).render(<App />);
