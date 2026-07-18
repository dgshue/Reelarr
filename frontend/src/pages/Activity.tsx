import { useEffect, useState } from "react";
import { NavLink, useParams } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import { getJson, MediaRequest } from "../api";

const STATUS_PILL: Record<string, string> = {
  fulfilled: "success",
  already_exists: "info",
  failed: "danger",
  dismissed: "warning",
  queued: "info",
  identifying: "info",
  fulfilling: "info",
};

interface BlocklistEntry {
  id: number;
  url: string;
  reason: string | null;
  created_at: string;
}

export default function Activity() {
  const { tab = "queue" } = useParams();
  const [rows, setRows] = useState<MediaRequest[]>([]);
  const [blocklist, setBlocklist] = useState<BlocklistEntry[]>([]);

  useEffect(() => {
    if (tab === "blocklist") {
      getJson<BlocklistEntry[]>("/activity/blocklist").then(setBlocklist).catch(() => setBlocklist([]));
    } else {
      getJson<MediaRequest[]>(`/activity/${tab}`).then(setRows).catch(() => setRows([]));
    }
  }, [tab]);

  return (
    <>
      <PageHeader title="Activity" />
      <div className="tab-bar">
        {["queue", "history", "blocklist"].map((t) => (
          <NavLink key={t} to={`/activity/${t}`} className={({ isActive }) => `tab${isActive ? " active" : ""}`}>
            {t[0].toUpperCase() + t.slice(1)}
          </NavLink>
        ))}
      </div>

      {tab === "blocklist" ? (
        blocklist.length === 0 ? (
          <div className="empty-state">
            <i className="fa-solid fa-ban" />
            <p>No blocklisted links.</p>
          </div>
        ) : (
          <table className="data">
            <thead>
              <tr><th>URL</th><th>Reason</th><th>Added</th></tr>
            </thead>
            <tbody>
              {blocklist.map((b) => (
                <tr key={b.id}>
                  <td className="mono">{b.url}</td>
                  <td>{b.reason}</td>
                  <td>{new Date(b.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <i className="fa-solid fa-clock-rotate-left" />
          <p>{tab === "queue" ? "No in-flight identifications." : "No history yet."}</p>
        </div>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th>Title</th><th>Source</th><th>Tier</th><th>Confidence</th><th>Status</th><th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.title ? `${r.title} ${r.year ? `(${r.year})` : ""}` : <span className="mono">{r.url}</span>}</td>
                <td>{r.source_channel}</td>
                <td>{r.resolved_tier ?? "—"}</td>
                <td>{r.confidence ?? "—"}</td>
                <td><span className={`status-pill ${STATUS_PILL[r.status] ?? "info"}`}>{r.status.replace("_", " ")}</span></td>
                <td>{r.updated_at ? new Date(r.updated_at).toLocaleString() : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
