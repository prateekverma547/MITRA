// Feature registration. Every folder in src/features/ exports a `feature` from
// its routes.tsx, and is picked up here by glob, so adding a screen never
// means editing a shared file (docs/DEV_PLAN.md section 6). Track H owns the
// shell; each feature folder belongs to one track.

import type { ReactNode } from "react";
import type { Role } from "../contracts";

export interface FeatureRoute {
  /** Hash path, e.g. "/jobs/:jobId". */
  path: string;
  element: ReactNode;
  /** Public routes render without signing in (welcome, sign-in). */
  public?: boolean;
  /** Roles that may open it. Omitted means every signed-in role. */
  roles?: Role[];
}

export interface NavItem {
  to: string;
  label: string;
  roles: Role[];
  /** Position in the sidebar, lowest first. Home is 0. */
  order: number;
}

export interface Feature {
  routes: FeatureRoute[];
  nav?: NavItem[];
}

const modules = import.meta.glob<{ feature: Feature }>("../features/*/routes.tsx", { eager: true });

export const features: Feature[] = Object.values(modules).map((module) => module.feature);

export const featureRoutes: FeatureRoute[] = features.flatMap((feature) => feature.routes);

export function navFor(role: Role): NavItem[] {
  return features
    .flatMap((feature) => feature.nav ?? [])
    .filter((item) => item.roles.includes(role))
    .sort((a, b) => a.order - b.order);
}
