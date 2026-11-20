// The product's identity and the rules the UI states, fetched once from the backend.
//
// The backend owns these values (shared/branding.py, shared/contracts.py). Fetching them keeps one
// source of truth for the pages, which are plain files with no build step to substitute them.

/** Load the configuration. Rejects with a readable message if the server cannot be reached. */
export async function loadConfig() {
  const response = await fetch("/config", { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Could not load the configuration (${response.status}).`);
  return response.json();
}
