import { useState } from "react";

/** The Servarr Test-button idiom (spec §2): one call validates the connection
 * and, on success, hands back any live-populate payload (root folders,
 * quality profiles, model lists) via onResult. Save stays disabled until a
 * test has succeeded — enforce that in the parent form. */
export default function TestButton({
  endpoint,
  payload,
  onResult,
}: {
  endpoint: string;
  payload: () => unknown;
  onResult?: (data: Record<string, unknown>) => void;
}) {
  const [state, setState] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const [error, setError] = useState<string>("");

  async function runTest() {
    setState("testing");
    setError("");
    try {
      const resp = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload()),
      });
      const data = await resp.json();
      if (resp.ok && data.ok) {
        setState("ok");
        onResult?.(data);
      } else {
        setState("fail");
        setError(String(data.error ?? data.detail ?? `HTTP ${resp.status}`));
      }
    } catch (e) {
      setState("fail");
      setError(String(e));
    }
  }

  return (
    <span>
      <button type="button" className="btn" onClick={runTest} disabled={state === "testing"}>
        <i className={`fa-solid ${state === "testing" ? "fa-spinner fa-spin" : "fa-vial"}`} />
        Test
      </button>
      {state === "ok" && (
        <span className="test-result ok">
          <i className="fa-solid fa-check" /> Connection OK
        </span>
      )}
      {state === "fail" && (
        <span className="test-result fail">
          <i className="fa-solid fa-xmark" /> {error}
        </span>
      )}
    </span>
  );
}
