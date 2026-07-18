import { NavLink, useLocation } from "react-router-dom";

type Item = { to: string; icon: string; label: string; badge?: number };
type Group = Item & { children?: Item[] };

// Nav structure per spec §1 (mapped from Radarr/Sonarr's actual routes).
const NAV: Group[] = [
  { to: "/library", icon: "fa-film", label: "Library" },
  {
    to: "/activity",
    icon: "fa-clock-rotate-left",
    label: "Activity",
    children: [
      { to: "/activity/queue", icon: "fa-list", label: "Queue" },
      { to: "/activity/history", icon: "fa-history", label: "History" },
      { to: "/activity/blocklist", icon: "fa-ban", label: "Blocklist" },
    ],
  },
  { to: "/pending", icon: "fa-circle-question", label: "Pending Confirmation" },
  {
    to: "/settings",
    icon: "fa-gears",
    label: "Settings",
    children: [
      { to: "/settings/fulfillment", icon: "fa-download", label: "Fulfillment" },
      { to: "/settings/identification", icon: "fa-wand-magic-sparkles", label: "Identification" },
      { to: "/settings/sources", icon: "fa-comments", label: "Sources" },
      { to: "/settings/connect", icon: "fa-bell", label: "Connect" },
      { to: "/settings/metadata", icon: "fa-database", label: "Metadata" },
      { to: "/settings/tags", icon: "fa-tags", label: "Tags" },
      { to: "/settings/general", icon: "fa-sliders", label: "General" },
      { to: "/settings/ui", icon: "fa-palette", label: "UI" },
    ],
  },
  {
    to: "/system",
    icon: "fa-laptop-medical",
    label: "System",
    children: [
      { to: "/system/status", icon: "fa-heart-pulse", label: "Status" },
      { to: "/system/tasks", icon: "fa-list-check", label: "Tasks" },
      { to: "/system/backup", icon: "fa-box-archive", label: "Backup" },
      { to: "/system/updates", icon: "fa-arrows-rotate", label: "Updates" },
      { to: "/system/events", icon: "fa-bolt", label: "Events" },
      { to: "/system/logs", icon: "fa-file-lines", label: "Log Files" },
    ],
  },
];

export default function Sidebar() {
  const location = useLocation();

  return (
    <aside className="sidebar">
      <div className="brand">
        <i className="fa-solid fa-clapperboard" />
        Reelarr
      </div>
      <nav>
        {NAV.map((group) => {
          const groupActive = location.pathname.startsWith(group.to);
          return (
            <div key={group.to}>
              <NavLink
                to={group.children ? group.children[0].to : group.to}
                className={() => `nav-item${groupActive ? " active" : ""}`}
              >
                <i className={`fa-solid ${group.icon}`} />
                {group.label}
                {/* Health issues surface as a count badge here (no toasts — spec §1).
                    TODO: wire to /api/v1/system/status health array. */}
                {group.badge ? <span className="badge">{group.badge}</span> : null}
              </NavLink>
              {group.children && groupActive && (
                <div className="sub-nav">
                  {group.children.map((child) => (
                    <NavLink
                      key={child.to}
                      to={child.to}
                      className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
                    >
                      <i className={`fa-solid ${child.icon}`} />
                      {child.label}
                    </NavLink>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
