# changes/

One file per change, instead of editing `CHANGELOG.md` at its top. Two tracks
editing the top of one file conflict on every merge; separate files never do.

Name: `YYYY-MM-DD-short-slug.md`. Write it the way CHANGELOG.md entries are
written: **What**, **Why**, **How** (files), what was deliberately left undone,
and the commands that confirm it works. Plain language in What and Why.

Whoever promotes `dev` to `stg` folds these into `CHANGELOG.md`, newest first,
and deletes them in the same commit.
