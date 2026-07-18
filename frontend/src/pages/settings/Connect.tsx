import PageHeader from "../../components/PageHeader";

/** Settings → Connect: outbound notifications (spec §6). Same idiom as
 * Radarr's Connect page — a list of configured targets plus an Add modal.
 * TODO: target CRUD UI; the backend dispatch + per-target Test already exist
 * (POST /api/v1/settings/connect/test). */
export default function Connect() {
  const targets = ["Discord", "Webhook", "Pushover", "Slack", "ntfy", "Gotify", "Apprise", "Telegram"];
  return (
    <>
      <PageHeader title="Connect" subtitle="Outbound notifications" />
      <div className="empty-state">
        <i className="fa-solid fa-bell" />
        <p>No connections configured yet.</p>
        <p style={{ fontSize: 12 }}>Supported targets: {targets.join(", ")} (Apprise covers 100+ more services).</p>
        <button className="btn primary" disabled title="TODO: add-connection modal">
          <i className="fa-solid fa-plus" /> Add Connection
        </button>
      </div>
    </>
  );
}
