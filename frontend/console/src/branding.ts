/**
 * MIRROR of backend/shared/branding.py (BOT_NAME, COMPANY_NAME).
 *
 * CLAUDE.md: the name lives in shared/branding.py and is never hard-coded in
 * more than one place. The frontend cannot import Python, so this is the one
 * frontend copy; every screen reads it from here. When the candidate app is
 * wired to the backend, the join response should carry these values and this
 * file should become the fallback only (see frontend_findings.md).
 */
export const BOT_NAME = "Mitra";
export const COMPANY_NAME = "Appinventiv";
