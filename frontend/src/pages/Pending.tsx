import { useEffect, useState } from "react";
import PageHeader from "../components/PageHeader";
import { getJson, MediaRequest } from "../api";

export default function Pending() {
  const [rows, setRows] = useState<MediaRequest[]>([]);

  useEffect(() => {
    getJson<MediaRequest[]>("/pending").then(setRows).catch(() => setRows([]));
  }, []);

  return (
    <>
      <PageHeader
        title="Pending Confirmation"
        subtitle="Low-confidence identifications waiting on a reply from the originating channel"
      />
      {rows.length === 0 ? (
        <div className="empty-state">
          <i className="fa-solid fa-circle-question" />
          <p>Nothing awaiting confirmation.</p>
        </div>
      ) : (
        rows.map((r) => (
          <div className="panel" key={r.id}>
            <h2 className="mono" style={{ fontSize: 13 }}>{r.url}</h2>
            <p style={{ color: "var(--text-muted)", fontSize: 13 }}>
              Awaiting a reply on {r.source_channel}. Candidates:
            </p>
            <ul>
              {(r.candidates ?? []).map((c, i) => (
                <li key={i}>
                  {c.title} ({c.year ?? "?"}) — {c.media_type}
                </li>
              ))}
            </ul>
            {/* TODO: confirm/dismiss buttons — backend endpoints exist but 501
                until the RequestProcessor is wired for UI-side confirmation. */}
          </div>
        ))
      )}
    </>
  );
}
