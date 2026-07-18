export const API = "/api/v1";

export async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(`${API}${path}`);
  if (!resp.ok) throw new Error(`GET ${path} -> ${resp.status}`);
  return resp.json();
}

export async function putJson<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${API}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`PUT ${path} -> ${resp.status}`);
  return resp.json();
}

export interface MediaRequest {
  id: number;
  url: string;
  platform: string | null;
  source_channel: string;
  status: string;
  resolved_tier: string | null;
  confidence: string | null;
  title: string | null;
  year: number | null;
  media_type: string | null;
  tmdb_id: number | null;
  poster_url: string | null;
  candidates: { title: string; year: number | null; media_type: string }[] | null;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
}
