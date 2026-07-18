import { useEffect, useState } from "react";
import PageHeader from "../../components/PageHeader";
import TestButton from "../../components/TestButton";
import { API, getJson, putJson } from "../../api";

interface FulfillmentSettings {
  target: string;
  radarr_url: string;
  radarr_api_key: string;
  radarr_root_folder: string;
  radarr_quality_profile_id: number | null;
  sonarr_url: string;
  sonarr_api_key: string;
  sonarr_root_folder: string;
  sonarr_quality_profile_id: number | null;
  seerr_url: string;
  seerr_api_key: string;
}

interface Option { id: number; name?: string; path?: string }

export default function Fulfillment() {
  const [s, setS] = useState<FulfillmentSettings | null>(null);
  // Live-populated by a successful Test (Seerr's pattern, spec §2): dropdowns
  // stay disabled until Test returns them; Save gated on a validated test.
  const [radarrOpts, setRadarrOpts] = useState<{ rootFolders: Option[]; qualityProfiles: Option[] } | null>(null);
  const [sonarrOpts, setSonarrOpts] = useState<{ rootFolders: Option[]; qualityProfiles: Option[] } | null>(null);
  const [validated, setValidated] = useState(false);

  useEffect(() => {
    getJson<FulfillmentSettings>("/settings/fulfillment").then(setS).catch(() => {});
  }, []);

  if (!s) return null;
  const set = (patch: Partial<FulfillmentSettings>) => { setS({ ...s, ...patch }); setValidated(false); };

  return (
    <>
      <PageHeader title="Fulfillment" subtitle="Where confirmed identifications get sent" />

      <div className="panel">
        <div className="form-row">
          <label>Fulfillment Target</label>
          <select value={s.target} onChange={(e) => set({ target: e.target.value })}>
            <option value="direct">Radarr / Sonarr (direct add)</option>
            <option value="seerr">Overseerr / Jellyseerr (approval-gated request)</option>
          </select>
        </div>
        <p className="form-hint">
          Direct adds straight to Radarr/Sonarr once identification is confirmed. Via Seerr, requests
          go through Overseerr/Jellyseerr's own approval queue instead.
        </p>
      </div>

      {s.target === "direct" && (
        <>
          <div className="panel">
            <h2><i className="fa-solid fa-film" /> Radarr</h2>
            <div className="form-row">
              <label>URL</label>
              <input value={s.radarr_url} onChange={(e) => set({ radarr_url: e.target.value })} />
            </div>
            <div className="form-row">
              <label>API Key</label>
              <input value={s.radarr_api_key} onChange={(e) => set({ radarr_api_key: e.target.value })} />
            </div>
            <div className="form-row">
              <label>Root Folder</label>
              <select
                disabled={!radarrOpts}
                value={s.radarr_root_folder}
                onChange={(e) => set({ radarr_root_folder: e.target.value })}
              >
                <option value="">{radarrOpts ? "Select..." : "Test connection first"}</option>
                {radarrOpts?.rootFolders.map((r) => (
                  <option key={r.id} value={r.path}>{r.path}</option>
                ))}
              </select>
            </div>
            <div className="form-row">
              <label>Quality Profile</label>
              <select
                disabled={!radarrOpts}
                value={s.radarr_quality_profile_id ?? ""}
                onChange={(e) => set({ radarr_quality_profile_id: Number(e.target.value) || null })}
              >
                <option value="">{radarrOpts ? "Select..." : "Test connection first"}</option>
                {radarrOpts?.qualityProfiles.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
            <TestButton
              endpoint={`${API}/settings/fulfillment/test/radarr`}
              payload={() => s}
              onResult={(data) => { setRadarrOpts(data as never); setValidated(true); }}
            />
          </div>

          <div className="panel">
            <h2><i className="fa-solid fa-tv" /> Sonarr</h2>
            <div className="form-row">
              <label>URL</label>
              <input value={s.sonarr_url} onChange={(e) => set({ sonarr_url: e.target.value })} />
            </div>
            <div className="form-row">
              <label>API Key</label>
              <input value={s.sonarr_api_key} onChange={(e) => set({ sonarr_api_key: e.target.value })} />
            </div>
            <div className="form-row">
              <label>Root Folder</label>
              <select
                disabled={!sonarrOpts}
                value={s.sonarr_root_folder}
                onChange={(e) => set({ sonarr_root_folder: e.target.value })}
              >
                <option value="">{sonarrOpts ? "Select..." : "Test connection first"}</option>
                {sonarrOpts?.rootFolders.map((r) => (
                  <option key={r.id} value={r.path}>{r.path}</option>
                ))}
              </select>
            </div>
            <div className="form-row">
              <label>Quality Profile</label>
              <select
                disabled={!sonarrOpts}
                value={s.sonarr_quality_profile_id ?? ""}
                onChange={(e) => set({ sonarr_quality_profile_id: Number(e.target.value) || null })}
              >
                <option value="">{sonarrOpts ? "Select..." : "Test connection first"}</option>
                {sonarrOpts?.qualityProfiles.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
            <TestButton
              endpoint={`${API}/settings/fulfillment/test/sonarr`}
              payload={() => s}
              onResult={(data) => { setSonarrOpts(data as never); setValidated(true); }}
            />
          </div>
        </>
      )}

      {s.target === "seerr" && (
        <div className="panel">
          <h2><i className="fa-solid fa-inbox" /> Overseerr / Jellyseerr</h2>
          <div className="form-row">
            <label>URL</label>
            <input value={s.seerr_url} onChange={(e) => set({ seerr_url: e.target.value })} />
          </div>
          <div className="form-row">
            <label>API Key</label>
            <input value={s.seerr_api_key} onChange={(e) => set({ seerr_api_key: e.target.value })} />
          </div>
          <p className="form-hint">
            Seerr owns root folders, quality profiles, and approval on its side — nothing else to
            configure here.
          </p>
          <TestButton
            endpoint={`${API}/settings/fulfillment/test/seerr`}
            payload={() => s}
            onResult={() => setValidated(true)}
          />
        </div>
      )}

      <div className="btn-row">
        {/* Save stays disabled until a Test has succeeded (isValidated pattern, spec §2) */}
        <button
          className="btn primary"
          disabled={!validated}
          onClick={() => putJson("/settings/fulfillment", s)}
        >
          <i className="fa-solid fa-floppy-disk" /> Save
        </button>
      </div>
    </>
  );
}
