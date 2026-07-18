import PageHeader from "../../components/PageHeader";

export default function Tags() {
  return (
    <>
      <PageHeader title="Tags" />
      <div className="empty-state">
        <i className="fa-solid fa-tags" />
        <p>No tags yet. Tags applied here are passed through to Radarr/Sonarr on add.</p>
        {/* TODO: tag CRUD */}
      </div>
    </>
  );
}
