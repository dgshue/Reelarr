import { useState } from "react";
import PageHeader from "../../components/PageHeader";

export default function Ui() {
  const [theme, setTheme] = useState(document.documentElement.dataset.theme ?? "dark");
  const [colorImpaired, setColorImpaired] = useState(false);

  const applyTheme = (t: string) => {
    setTheme(t);
    document.documentElement.dataset.theme = t === "auto"
      ? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
      : t;
    // TODO: persist via PUT /settings/ui
  };

  return (
    <>
      <PageHeader title="UI" />
      <div className="panel">
        <h2><i className="fa-solid fa-palette" /> Style</h2>
        <div className="form-row">
          <label>Theme</label>
          <select value={theme} onChange={(e) => applyTheme(e.target.value)}>
            <option value="dark">Dark (default)</option>
            <option value="light">Light</option>
            <option value="auto">Auto</option>
          </select>
        </div>
        <div className="form-row">
          <label>Color-Impaired Mode</label>
          <input
            type="checkbox"
            style={{ flex: "none" }}
            checked={colorImpaired}
            onChange={(e) => setColorImpaired(e.target.checked)}
          />
        </div>
        <p className="form-hint">Color-impaired mode alters status colors for better contrast. TODO: apply palette swap.</p>
      </div>
    </>
  );
}
