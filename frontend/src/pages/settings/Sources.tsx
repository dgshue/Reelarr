import { useState } from "react";
import PageHeader from "../../components/PageHeader";
import TestButton from "../../components/TestButton";
import { API } from "../../api";

/** Settings → Sources: multi-channel intake (spec §4). Each channel is
 * independently configured with its own allowlist. Telegram is fully wired;
 * Discord/Slack are structural; WhatsApp is opt-in/advanced. */

const CHANNELS = [
  {
    type: "telegram",
    icon: "fa-paper-plane",
    name: "Telegram",
    note: "Bot token via BotFather. Inline-button confirmations.",
    fields: [{ key: "bot_token", label: "Bot Token" }],
    allowlistLabel: "Allowed Chat IDs (comma-separated)",
    advanced: false,
  },
  {
    type: "discord",
    icon: "fa-headset",
    name: "Discord",
    note: "Bot via the Developer Portal. Test loads your guild list.",
    fields: [{ key: "bot_token", label: "Bot Token" }],
    allowlistLabel: "Allowed Channel IDs (comma-separated)",
    advanced: false,
  },
  {
    type: "slack",
    icon: "fa-hashtag",
    name: "Slack",
    note: "Socket Mode — no public HTTPS endpoint needed.",
    fields: [
      { key: "bot_token", label: "Bot Token (xoxb-...)" },
      { key: "app_token", label: "App-Level Token (xapp-...)" },
    ],
    allowlistLabel: "Allowed Channel IDs (comma-separated)",
    advanced: false,
  },
  {
    type: "whatsapp",
    icon: "fa-phone",
    name: "WhatsApp (Evolution API)",
    note:
      "ADVANCED / UNOFFICIAL — uses the unofficial Baileys engine via a self-hosted Evolution API. " +
      "Real ban risk: use a dedicated secondary number. Confirmations use numbered replies, not buttons.",
    fields: [
      { key: "base_url", label: "Evolution API URL" },
      { key: "api_key", label: "API Key" },
      { key: "instance", label: "Instance Name" },
    ],
    allowlistLabel: "Allowed Phone Numbers (comma-separated)",
    advanced: true,
  },
];

export default function Sources() {
  const [configs, setConfigs] = useState<Record<string, Record<string, string>>>({});

  const setField = (channel: string, key: string, value: string) =>
    setConfigs((c) => ({ ...c, [channel]: { ...c[channel], [key]: value } }));

  return (
    <>
      <PageHeader title="Sources" subtitle="Intake channels — where shared links come from" />
      {CHANNELS.map((ch) => (
        <div className="panel" key={ch.type}>
          <h2>
            <i className={`fa-solid ${ch.icon}`} /> {ch.name}{" "}
            {ch.advanced && <span className="status-pill warning">advanced</span>}
          </h2>
          <p className="form-hint" style={{ margin: "0 0 12px" }}>{ch.note}</p>
          {ch.fields.map((f) => (
            <div className="form-row" key={f.key}>
              <label>{f.label}</label>
              <input
                value={configs[ch.type]?.[f.key] ?? ""}
                onChange={(e) => setField(ch.type, f.key, e.target.value)}
              />
            </div>
          ))}
          <div className="form-row">
            <label>{ch.allowlistLabel}</label>
            <input
              value={configs[ch.type]?.allowlist ?? ""}
              onChange={(e) => setField(ch.type, "allowlist", e.target.value)}
            />
          </div>
          <TestButton endpoint={`${API}/settings/sources/${ch.type}/test`} payload={() => ({ config: configs[ch.type] ?? {} })} />
          {/* TODO: persist via PUT /settings/sources/channels/{type} and show
              live-populated guild/channel dropdowns from the Test payload. */}
        </div>
      ))}
    </>
  );
}
