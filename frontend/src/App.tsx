import { Navigate, Route, Routes } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import Library from "./pages/Library";
import Activity from "./pages/Activity";
import Pending from "./pages/Pending";
import SettingsFulfillment from "./pages/settings/Fulfillment";
import SettingsIdentification from "./pages/settings/Identification";
import SettingsSources from "./pages/settings/Sources";
import SettingsConnect from "./pages/settings/Connect";
import SettingsMetadata from "./pages/settings/Metadata";
import SettingsTags from "./pages/settings/Tags";
import SettingsGeneral from "./pages/settings/General";
import SettingsUi from "./pages/settings/Ui";
import SystemPage from "./pages/System";

export default function App() {
  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/library" replace />} />
          <Route path="/library" element={<Library />} />
          <Route path="/activity" element={<Navigate to="/activity/queue" replace />} />
          <Route path="/activity/:tab" element={<Activity />} />
          <Route path="/pending" element={<Pending />} />
          <Route path="/settings" element={<Navigate to="/settings/fulfillment" replace />} />
          <Route path="/settings/fulfillment" element={<SettingsFulfillment />} />
          <Route path="/settings/identification" element={<SettingsIdentification />} />
          <Route path="/settings/sources" element={<SettingsSources />} />
          <Route path="/settings/connect" element={<SettingsConnect />} />
          <Route path="/settings/metadata" element={<SettingsMetadata />} />
          <Route path="/settings/tags" element={<SettingsTags />} />
          <Route path="/settings/general" element={<SettingsGeneral />} />
          <Route path="/settings/ui" element={<SettingsUi />} />
          <Route path="/system" element={<Navigate to="/system/status" replace />} />
          <Route path="/system/:tab" element={<SystemPage />} />
        </Routes>
      </main>
    </div>
  );
}
