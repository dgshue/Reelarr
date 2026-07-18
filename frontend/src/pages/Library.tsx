import { useEffect, useState } from "react";
import PageHeader from "../components/PageHeader";
import { getJson, MediaRequest } from "../api";

export default function Library() {
  const [items, setItems] = useState<MediaRequest[]>([]);

  useEffect(() => {
    getJson<MediaRequest[]>("/library").then(setItems).catch(() => setItems([]));
  }, []);

  return (
    <>
      <PageHeader title="Library" subtitle="Identified clips, matched to your library" />
      {items.length === 0 ? (
        <div className="empty-state">
          <i className="fa-solid fa-film" />
          <p>
            Nothing here yet — share an Instagram Reel, TikTok, or Facebook video link with one of
            your configured Sources and it'll show up once identified.
          </p>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 16 }}>
          {items.map((item) => (
            <div key={item.id} className="panel" style={{ padding: 8, textAlign: "center" }}>
              {item.poster_url ? (
                <img src={item.poster_url} alt={item.title ?? ""} style={{ width: "100%", borderRadius: 3 }} />
              ) : (
                <div style={{ aspectRatio: "2/3", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <i className="fa-solid fa-film" style={{ fontSize: 32, opacity: 0.3 }} />
                </div>
              )}
              <div style={{ marginTop: 6, fontSize: 13 }}>
                {item.title} {item.year ? `(${item.year})` : ""}
              </div>
              <span className={`status-pill ${item.media_type === "movie" ? "info" : "warning"}`}>
                {item.media_type}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
