import { useEffect, useState } from "react";
import PageHeader from "../../components/PageHeader";
import TestButton from "../../components/TestButton";
import { API, getJson, putJson } from "../../api";

export default function Metadata() {
  const [key, setKey] = useState("");
  const [validated, setValidated] = useState(false);

  useEffect(() => {
    getJson<{ tmdb_api_key: string }>("/settings/metadata")
      .then((s) => setKey(s.tmdb_api_key))
      .catch(() => {});
  }, []);

  return (
    <>
      <PageHeader title="Metadata" subtitle="TMDB" />
      <div className="panel">
        <h2><i className="fa-solid fa-database" /> The Movie Database</h2>
        <div className="form-row">
          <label>API Key</label>
          <input value={key} onChange={(e) => { setKey(e.target.value); setValidated(false); }} />
        </div>
        <p className="form-hint">Free tier — create one at themoviedb.org → Settings → API.</p>
        <TestButton
          endpoint={`${API}/settings/metadata/test`}
          payload={() => ({ tmdb_api_key: key })}
          onResult={() => setValidated(true)}
        />
      </div>
      <div className="btn-row">
        <button className="btn primary" disabled={!validated} onClick={() => putJson("/settings/metadata", { tmdb_api_key: key })}>
          <i className="fa-solid fa-floppy-disk" /> Save
        </button>
      </div>
    </>
  );
}
