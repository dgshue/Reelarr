import { useEffect, useState } from "react";
import PageHeader from "../../components/PageHeader";
import { getJson } from "../../api";

interface GeneralSettings {
  url_base: string;
  api_key: string;
  auth_method: string;
  log_level: string;
}

export default function General() {
  const [s, setS] = useState<GeneralSettings | null>(null);

  useEffect(() => {
    getJson<GeneralSettings>("/settings/general").then(setS).catch(() => {});
  }, []);

  if (!s) return null;

  return (
    <>
      <PageHeader title="General" />
      <div className="panel">
        <h2><i className="fa-solid fa-shield-halved" /> Security</h2>
        <div className="form-row">
          <label>Authentication</label>
          <select value={s.auth_method} onChange={(e) => setS({ ...s, auth_method: e.target.value })}>
            <option value="forms">Forms (Login Page)</option>
            <option value="disabled_for_local">Disabled for Local Addresses</option>
          </select>
        </div>
        <div className="form-row">
          <label>API Key</label>
          {/* Servarr idiom: plain-text read-only, auto-select on focus, Copy +
              confirm-gated Reset, "requires restart" (spec §1) */}
          <input readOnly value={s.api_key || "(generated on first run)"} onFocus={(e) => e.target.select()} className="mono" />
          <button className="btn" style={{ marginLeft: 8 }} onClick={() => navigator.clipboard.writeText(s.api_key)}>
            <i className="fa-solid fa-copy" />
          </button>
          <button className="btn" style={{ marginLeft: 4, color: "var(--danger-color)" }} disabled title="TODO: confirm modal">
            <i className="fa-solid fa-rotate" /> Reset
          </button>
        </div>
        <p className="form-hint">Changing the API key requires a restart. Webhook calls authenticate with it regardless of UI auth mode.</p>
      </div>

      <div className="panel">
        <h2><i className="fa-solid fa-globe" /> Host</h2>
        <div className="form-row">
          <label>URL Base</label>
          <input value={s.url_base} onChange={(e) => setS({ ...s, url_base: e.target.value })} placeholder="/reelarr" />
        </div>
        <p className="form-hint">
          For reverse-proxy subpath deployment. If real-time updates stall behind nginx/Caddy, check
          the WebSocket upgrade headers on the proxy.
        </p>
        <div className="form-row">
          <label>Log Level</label>
          <select value={s.log_level} onChange={(e) => setS({ ...s, log_level: e.target.value })}>
            <option value="info">Info</option>
            <option value="debug">Debug</option>
            <option value="trace">Trace</option>
          </select>
        </div>
      </div>
      {/* TODO: Save (PUT /settings/general) once auth/API-key backend lands */}
    </>
  );
}
