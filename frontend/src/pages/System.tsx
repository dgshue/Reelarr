import { useEffect, useState } from "react";
import { NavLink, useParams } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import { getJson } from "../api";

const TABS = ["status", "tasks", "backup", "updates", "events", "logs"];
const LABELS: Record<string, string> = {
  status: "Status", tasks: "Tasks", backup: "Backup",
  updates: "Updates", events: "Events", logs: "Log Files",
};

interface SystemStatus {
  app_name: string;
  version: string;
  python_version: string;
  started_at: string;
  health: { type: string; message: string }[];
}

interface SystemEvent {
  id: number;
  request_id: number;
  event_type: string;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export default function SystemPage() {
  const { tab = "status" } = useParams();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [events, setEvents] = useState<SystemEvent[]>([]);

  useEffect(() => {
    if (tab === "status") getJson<SystemStatus>("/system/status").then(setStatus).catch(() => {});
    if (tab === "events") getJson<SystemEvent[]>("/system/events").then(setEvents).catch(() => setEvents([]));
  }, [tab]);

  return (
    <>
      <PageHeader title="System" />
      <div className="tab-bar">
        {TABS.map((t) => (
          <NavLink key={t} to={`/system/${t}`} className={({ isActive }) => `tab${isActive ? " active" : ""}`}>
            {LABELS[t]}
          </NavLink>
        ))}
      </div>

      {tab === "status" && status && (
        <>
          <div className="panel">
            <h2>Health</h2>
            {status.health.length === 0 ? (
              <p style={{ color: "var(--success-color)" }}>
                <i className="fa-solid fa-check" /> No issues with your configuration
              </p>
            ) : (
              status.health.map((h, i) => (
                <p key={i}><span className={`status-pill ${h.type}`}>{h.type}</span> {h.message}</p>
              ))
            )}
          </div>
          <div className="panel">
            <h2>About</h2>
            <table className="data">
              <tbody>
                <tr><td>Version</td><td className="mono">{status.version}</td></tr>
                <tr><td>Python</td><td className="mono">{status.python_version}</td></tr>
                <tr><td>Started</td><td>{new Date(status.started_at).toLocaleString()}</td></tr>
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === "events" && (
        events.length === 0 ? (
          <div className="empty-state"><i className="fa-solid fa-bolt" /><p>No events yet.</p></div>
        ) : (
          <table className="data">
            <thead><tr><th>Time</th><th>Request</th><th>Event</th><th>Detail</th></tr></thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td>{new Date(e.created_at).toLocaleString()}</td>
                  <td>#{e.request_id}</td>
                  <td>{e.event_type}</td>
                  <td className="mono" style={{ fontSize: 12 }}>{JSON.stringify(e.detail)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      )}

      {tab === "tasks" && (
        <div className="empty-state"><i className="fa-solid fa-list-check" /><p>No scheduled tasks yet. (TODO: backups, blocklist cleanup, update check.)</p></div>
      )}
      {tab === "backup" && (
        <div className="empty-state">
          <i className="fa-solid fa-box-archive" />
          <p>No backups yet.</p>
          <button className="btn primary" disabled title="TODO: backup service"><i className="fa-solid fa-plus" /> Backup Now</button>
        </div>
      )}
      {tab === "updates" && (
        <div className="empty-state">
          <i className="fa-solid fa-arrows-rotate" />
          <p>Docker-first deployment — updates arrive as new images (Watchtower or manual pull). A health-check flag for "newer image available" is TODO.</p>
        </div>
      )}
      {tab === "logs" && (
        <div className="empty-state"><i className="fa-solid fa-file-lines" /><p>Rotating log files TODO — logs currently go to stdout (docker logs reelarr).</p></div>
      )}
    </>
  );
}
