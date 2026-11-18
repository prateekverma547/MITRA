/** Shimmering placeholders shaped like the page that is about to appear. */
export function Skeleton({ width = "100%", height = 14, radius = 6 }: { width?: string | number; height?: number; radius?: number }) {
  return <span className="skeleton" style={{ width, height, borderRadius: radius }} aria-hidden="true" />;
}

export function PageSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="page" role="status" aria-label="Loading">
      <div className="skeleton-stack">
        <Skeleton width="38%" height={34} radius={8} />
        <Skeleton width="52%" />
      </div>
      <div className="skeleton-card">
        {Array.from({ length: rows }, (_, i) => (
          <div className="skeleton-row" key={i}>
            <Skeleton width="34%" />
            <Skeleton width="18%" />
            <Skeleton width="26%" />
          </div>
        ))}
      </div>
    </div>
  );
}
