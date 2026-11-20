# Vendored: Daily call SDK

`daily-js-0.94.0.esm.js` is `dist/daily-esm.js` from the npm package `@daily-co/daily-js`
at **exactly 0.94.0**. It is copied here so the candidate's page loads its call SDK from
our own origin. The page's Content-Security-Policy allows scripts from `'self'` and Daily's
own hosts only, so no CDN is used.

To update: pick the new version, run `npm pack @daily-co/daily-js@<version>`, copy
`package/dist/daily-esm.js` to a new `daily-js-<version>.esm.js` file here, point the imports
in `static/candidate/*.js` at it, and delete the old file. The tests check the pinned version
and the import path.

The licence is in `LICENSE` beside this file.
