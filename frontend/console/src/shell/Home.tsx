// The home screen. One request, read three ways: the server has already scoped
// every number to the person signed in, and each role gets the page built for
// the questions that role asks (home/HRHome, home/BUHome, home/OrgHome).

import { useSession } from "../session";
import { BUHome } from "./home/BUHome";
import { HRHome } from "./home/HRHome";
import { HomeError, useOverview } from "./home/common";
import { OrgHome } from "./home/OrgHome";
import { PageSkeleton } from "./ui/Skeleton";
import { TooltipLayer } from "./viz/Tooltip";

export function Home() {
  const { me } = useSession();
  const state = useOverview();
  if (state.error && !state.data) return <HomeError error={state.error} />;
  if (!state.data || !me) return <PageSkeleton />;
  return (
    <TooltipLayer>
      {me.role === "hr" ? <HRHome state={state} /> : me.role === "bu_head" ? <BUHome state={state} buName={me.bu_name} /> : <OrgHome state={state} />}
    </TooltipLayer>
  );
}
