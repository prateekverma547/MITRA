import { Table2, BarChart3 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Button } from "../ui/Button";

/**
 * A chart with its table twin. Every value a chart shows is also readable in
 * the table, so nothing depends on hovering or on telling colours apart.
 */
export function ChartCard({
  title,
  caption,
  table,
  children,
  action,
}: {
  title: string;
  caption?: string;
  table: ReactNode;
  children: ReactNode;
  action?: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="card-viz">
      <header className="card-viz-head">
        <div>
          <h2 className="card-title">{title}</h2>
          {caption && <p className="card-caption">{caption}</p>}
        </div>
        <div className="card-actions">
          {action}
          <Button
            size="sm"
            variant="ghost"
            icon={asTable ? <BarChart3 size={15} /> : <Table2 size={15} />}
            onClick={() => setAsTable((v) => !v)}
            aria-pressed={asTable}
          >
            {asTable ? "Chart" : "Table"}
          </Button>
        </div>
      </header>
      <div className="card-viz-body">{asTable ? <div className="viz-table">{table}</div> : children}</div>
    </section>
  );
}
