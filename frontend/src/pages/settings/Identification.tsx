import { useEffect, useState } from "react";
import PageHeader from "../../components/PageHeader";
import TestButton from "../../components/TestButton";
import { API, getJson, putJson } from "../../api";

interface IdentificationSettings {
  litellm_base_url: string;
  litellm_api_key: string;
  text_model: string;
  vision_model: string;
  stt_model: string;
  stt_base_url: string;
  stt_api_key: string;
  enable_vision: boolean;
  frame_count: number;
  max_video_minutes: number;
  max_video_height: number;
}

export default function Identification() {
  const [s, setS] = useState<IdentificationSettings | null>(null);
  const [models, setModels] = useState<string[] | null>(null); // live-populated by Test
  const [validated, setValidated] = useState(false);

  useEffect(() => {
    getJson<IdentificationSettings>("/settings/identification").then(setS).catch(() => {});
  }, []);

  if (!s) return null;
  const set = (patch: Partial<IdentificationSettings>) => { setS({ ...s, ...patch }); setValidated(false); };

  const modelSelect = (value: string, onChange: (v: string) => void) =>
    models ? (
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Select...</option>
        {models.map((m) => <option key={m} value={m}>{m}</option>)}
      </select>
    ) : (
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder="Test to load model list" />
    );

  return (
    <>
      <PageHeader title="Identification" subtitle="AI pipeline configuration (LiteLLM proxy)" />

      <div className="panel">
        <h2><i className="fa-solid fa-server" /> AI Backend</h2>
        <div className="form-row">
          <label>LiteLLM Base URL</label>
          <input value={s.litellm_base_url} onChange={(e) => set({ litellm_base_url: e.target.value })} />
        </div>
        <div className="form-row">
          <label>API Key</label>
          <input value={s.litellm_api_key} onChange={(e) => set({ litellm_api_key: e.target.value })} />
        </div>
        <div className="form-row">
          <label>Text Model</label>
          {modelSelect(s.text_model, (v) => set({ text_model: v }))}
        </div>
        <div className="form-row">
          <label>Vision Model</label>
          {modelSelect(s.vision_model, (v) => set({ vision_model: v }))}
        </div>
        <div className="form-row">
          <label>STT Model</label>
          {modelSelect(s.stt_model, (v) => set({ stt_model: v }))}
        </div>
        <p className="form-hint">
          Test checks the proxy is reachable AND that the configured models exist on it — "configured
          but never ollama-pulled" is reported distinctly from "can't reach the proxy".
        </p>
        <TestButton
          endpoint={`${API}/settings/identification/test`}
          payload={() => s}
          onResult={(data) => { setModels((data.models as string[]) ?? []); setValidated(true); }}
        />
      </div>

      <div className="panel">
        <h2><i className="fa-solid fa-layer-group" /> Tiers</h2>
        <div className="form-row">
          <label>Enable Vision (Tier 3)</label>
          <input
            type="checkbox"
            style={{ flex: "none" }}
            checked={s.enable_vision}
            onChange={(e) => set({ enable_vision: e.target.checked })}
          />
        </div>
        <div className="form-row">
          <label>Frame Count</label>
          <input
            type="number"
            value={s.frame_count}
            onChange={(e) => set({ frame_count: Number(e.target.value) })}
          />
        </div>
        <div className="form-row">
          <label>Max Video Minutes</label>
          <input
            type="number"
            value={s.max_video_minutes}
            onChange={(e) => set({ max_video_minutes: Number(e.target.value) })}
          />
        </div>
      </div>

      <div className="btn-row">
        <button className="btn primary" disabled={!validated} onClick={() => putJson("/settings/identification", s)}>
          <i className="fa-solid fa-floppy-disk" /> Save
        </button>
      </div>
    </>
  );
}
