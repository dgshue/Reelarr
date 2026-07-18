export default function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="page-header">
      <h1>{title}</h1>
      {subtitle && <span className="subtitle">{subtitle}</span>}
    </div>
  );
}
