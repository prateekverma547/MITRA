// Track H. Who signs in, what they can do, and the business units they work in.
// Org admins only. Every change here is in the audit log.

import { Building2, KeyRound, Pencil, Plus, Power, Search, Settings2, Undo2, UserPlus, Users, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { ROLE_LABELS, ROLES, ago, initials, type Role } from "../../contracts";
import { Button } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { useToast } from "../../shell/ui/Toast";
import { OneTimePassword } from "./OneTimePassword";
import { day, type AdminBU, type AdminUser, type UserList } from "./types";
import "./admin.css";

const ROLE_HELP: Record<Role, string> = {
  org_admin: "Sees everything, read-only, and runs these admin screens.",
  hr: "Screens CVs, runs the HR round, books the MITRA interview round.",
  bu_head: "Raises job requests and decides on their own unit's candidates.",
};

interface Shown {
  who: string;
  email: string;
  password: string;
}

// ---- inviting -------------------------------------------------------------------------------------

function Invite({ units, onDone, onCancel }: { units: AdminBU[]; onDone: (user: AdminUser, password: string) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("hr");
  const [bu, setBu] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const made = await api.post<{ user: AdminUser; temporary_password: string }>("/admin/users", {
        name, email, role, bu_id: role === "bu_head" ? bu || null : null,
      });
      onDone(made.user, made.temporary_password);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel admin-form" onSubmit={submit}>
      <div className="field">
        <label htmlFor="inv-name">Name</label>
        <input id="inv-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus required />
      </div>
      <div className="field">
        <label htmlFor="inv-email">Work email</label>
        <input id="inv-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </div>
      <div className="field">
        <label htmlFor="inv-role">Role</label>
        <select id="inv-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABELS[r] === "Organisation" ? "Org admin" : ROLE_LABELS[r]}
            </option>
          ))}
        </select>
      </div>
      {role === "bu_head" && (
        <div className="field">
          <label htmlFor="inv-bu">Business unit</label>
          <select id="inv-bu" value={bu} onChange={(e) => setBu(e.target.value)} required>
            <option value="">Choose a unit</option>
            {units.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>
        </div>
      )}
      <p className="muted small" style={{ gridColumn: "1 / -1" }}>
        {ROLE_HELP[role]} You get a temporary password to give them; nothing is emailed.
      </p>
      {error && <p className="error" style={{ gridColumn: "1 / -1" }}>{error}</p>}
      <div className="actions">
        <Button type="submit" variant="primary" icon={<UserPlus size={16} />} loading={busy} disabled={!name.trim() || !email.trim() || (role === "bu_head" && !bu)}>
          Create the account
        </Button>
        <Button variant="ghost" icon={<X size={16} />} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

// ---- one person, being managed ---------------------------------------------------------------------

function Manage({ user, units, onChanged, onPassword }: { user: AdminUser; units: AdminBU[]; onChanged: () => void; onPassword: (p: Shown) => void }) {
  const toast = useToast();
  const [role, setRole] = useState<Role>(user.role);
  const [bu, setBu] = useState(user.bu_id ?? "");
  const [busy, setBusy] = useState<"save" | "power" | "reset" | null>(null);
  const [confirm, setConfirm] = useState<"power" | "reset" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dirty = role !== user.role || (role === "bu_head" && bu !== (user.bu_id ?? ""));

  async function run(kind: "save" | "power" | "reset") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "reset") {
        const r = await api.post<{ temporary_password: string }>(`/admin/users/${user.id}/reset-password`);
        onPassword({ who: user.name, email: user.email, password: r.temporary_password });
        toast(`New temporary password for ${user.name}`);
      } else {
        const body = kind === "save" ? { role, bu_id: role === "bu_head" ? bu || null : null } : { active: !user.active };
        await api.post(`/admin/users/${user.id}`, body);
        toast(kind === "save" ? `${user.name}: saved` : user.active ? `${user.name} deactivated` : `${user.name} reactivated`);
      }
      setConfirm(null);
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="edit-panel">
      {user.is_you ? (
        <p className="muted small">This is your own account. Another admin can change your role or deactivate you.</p>
      ) : (
        <div className="admin-inline">
          <label className="visually-hidden" htmlFor={`role-${user.id}`}>
            Role
          </label>
          <select id={`role-${user.id}`} value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r === "org_admin" ? "Org admin" : ROLE_LABELS[r]}
              </option>
            ))}
          </select>
          {role === "bu_head" && (
            <>
              <label className="visually-hidden" htmlFor={`bu-${user.id}`}>
                Business unit
              </label>
              <select id={`bu-${user.id}`} value={bu} onChange={(e) => setBu(e.target.value)}>
                <option value="">Choose a unit</option>
                {units.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                  </option>
                ))}
              </select>
            </>
          )}
          <Button variant="primary" size="sm" loading={busy === "save"} disabled={!dirty || (role === "bu_head" && !bu)} onClick={() => run("save")}>
            Save role
          </Button>
        </div>
      )}
      <hr className="edit-divider" />
      {confirm === "power" ? (
        <div className="admin-inline">
          <span>{user.active ? `Deactivate ${user.name}? They are signed out at once and cannot sign in.` : `Reactivate ${user.name}? Their old password works again.`}</span>
          <Button variant={user.active ? "danger" : "primary"} size="sm" icon={<Power size={15} />} loading={busy === "power"} onClick={() => run("power")}>
            {user.active ? "Deactivate" : "Reactivate"}
          </Button>
          <Button variant="ghost" size="sm" icon={<Undo2 size={15} />} onClick={() => setConfirm(null)}>
            Keep as is
          </Button>
        </div>
      ) : confirm === "reset" ? (
        <div className="admin-inline">
          <span>Replace {user.name}'s password? Their current one stops working.</span>
          <Button variant="primary" size="sm" icon={<KeyRound size={15} />} loading={busy === "reset"} onClick={() => run("reset")}>
            Reset password
          </Button>
          <Button variant="ghost" size="sm" icon={<Undo2 size={15} />} onClick={() => setConfirm(null)}>
            Keep it
          </Button>
        </div>
      ) : (
        <div className="admin-inline">
          <Button size="sm" icon={<KeyRound size={15} />} onClick={() => setConfirm("reset")}>
            Reset password
          </Button>
          {!user.is_you && (
            <Button variant={user.active ? "ghost" : "secondary"} size="sm" icon={<Power size={15} />} onClick={() => setConfirm("power")}>
              {user.active ? "Deactivate" : "Reactivate"}
            </Button>
          )}
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

// ---- the people tab ------------------------------------------------------------------------------------

function PeopleTab({ units }: { units: AdminBU[] }) {
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<UserList | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState(params.get("q") ?? "");
  const [inviting, setInviting] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [shown, setShown] = useState<Shown | null>(null);
  const q = params.get("q") ?? "";
  const role = params.get("role");
  const bu = params.get("bu");
  const status = params.get("status") ?? "all";

  const set = useCallback(
    (changes: Record<string, string | null>) => {
      const next = new URLSearchParams(params);
      for (const [k, v] of Object.entries(changes)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  useEffect(() => {
    if (text === q) return;
    const timer = window.setTimeout(() => set({ q: text.trim() || null }), 250);
    return () => window.clearTimeout(timer);
  }, [text, q, set]);

  const load = useCallback(() => {
    setRefreshing(true);
    const query = new URLSearchParams({ status });
    if (q) query.set("q", q);
    if (role) query.set("role", role);
    if (bu) query.set("bu", bu);
    api
      .get<UserList>(`/admin/users?${query}`)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  }, [q, role, bu, status]);

  useEffect(load, [load]);

  if (error && !data) return <p className="error">{error}</p>;
  if (!data) return <PageSkeleton rows={5} />;

  return (
    <div className="section">
      <div className="toolbar">
        <label className="search-box">
          <Search size={17} aria-hidden="true" />
          <input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search by name or email" aria-label="Search people" />
          {text && (
            <button type="button" className="search-clear" aria-label="Clear search" onClick={() => setText("")}>
              <X size={15} />
            </button>
          )}
        </label>
        <label className="select">
          <span className="visually-hidden">Business unit</span>
          <select value={bu ?? ""} onChange={(e) => set({ bu: e.target.value || null })}>
            <option value="">Every unit</option>
            {units.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>
        </label>
        {!inviting && (
          <Button variant="primary" icon={<UserPlus size={17} />} onClick={() => setInviting(true)}>
            Invite someone
          </Button>
        )}
      </div>

      {inviting && (
        <Invite
          units={units}
          onCancel={() => setInviting(false)}
          onDone={(user, password) => {
            setInviting(false);
            setShown({ who: user.name, email: user.email, password });
            load();
          }}
        />
      )}
      {shown && <OneTimePassword {...shown} onClose={() => setShown(null)} />}

      <div className="filters" role="group" aria-label="Filters">
        <div className="chips" role="group" aria-label="Role">
          <button type="button" className="chip-btn" aria-pressed={!role} onClick={() => set({ role: null })}>
            Every role<span className="chip-n">{data.counts.active}</span>
          </button>
          {ROLES.map((r) => (
            <button key={r} type="button" className="chip-btn" aria-pressed={role === r} onClick={() => set({ role: role === r ? null : r })}>
              {r === "org_admin" ? "Org admins" : r === "hr" ? "HR" : "BU heads"}
              <span className="chip-n">{data.counts.by_role[r]}</span>
            </button>
          ))}
        </div>
        <div className="segmented" role="group" aria-label="Status">
          {(["all", "active", "inactive"] as const).map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => set({ status: s === "all" ? null : s })}>
              {s === "all" ? "Everyone" : s === "active" ? "Active" : "Deactivated"}
            </button>
          ))}
        </div>
      </div>

      <div className={refreshing ? "dash-body refreshing" : "dash-body"}>
        {data.users.length === 0 ? (
          <div className="empty">
            <p className="empty-title">Nobody matches</p>
            <p className="muted">Try a different search, or clear a filter.</p>
          </div>
        ) : (
          <div className="table-wrap admin-table">
            <table className="table table-fixed">
              <colgroup>
                <col className="col-person" />
                <col />
                <col />
                <col />
                <col className="col-actions" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Person</th>
                  <th scope="col">Role</th>
                  <th scope="col">Last signed in</th>
                  <th scope="col">Account</th>
                  <th scope="col">
                    <span className="visually-hidden">Manage</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.users.map((u) => (
                  <PersonRows
                    key={u.id}
                    user={u}
                    units={units}
                    open={open === u.id}
                    onToggle={() => setOpen(open === u.id ? null : u.id)}
                    onChanged={load}
                    onPassword={setShown}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function PersonRows({
  user, units, open, onToggle, onChanged, onPassword,
}: { user: AdminUser; units: AdminBU[]; open: boolean; onToggle: () => void; onChanged: () => void; onPassword: (p: Shown) => void }) {
  return (
    <>
      <tr className={[user.active ? "" : "is-off", open ? "editing" : ""].join(" ")}>
        <td>
          <span className="person">
            <span className="avatar" aria-hidden="true">
              {initials(user.name)}
            </span>
            <span className="person-text">
              <span className="row-title">
                {user.name}
                {user.is_you && <span className="you">You</span>}
              </span>
              <span className="row-sub">{user.email}</span>
            </span>
          </span>
        </td>
        <td>
          <span className="role-line">
            <span>{user.role === "org_admin" ? "Org admin" : ROLE_LABELS[user.role]}</span>
            {user.bu_name && <span className="row-sub">{user.bu_name}</span>}
          </span>
        </td>
        <td>{user.last_sign_in_at ? <span title={day(user.last_sign_in_at)}>{ago(user.last_sign_in_at)}</span> : <span className="muted">Never</span>}</td>
        <td>
          <span className={`mark tone-${user.active ? "good" : "closed"}`}>{user.active ? "Active" : "Deactivated"}</span>
        </td>
        <td className="cell-actions">
          <Button size="sm" variant={open ? "secondary" : "ghost"} icon={open ? <X size={15} /> : <Settings2 size={15} />} onClick={onToggle} aria-expanded={open}>
            {open ? "Close" : "Manage"}
          </Button>
        </td>
      </tr>
      {open && (
        <tr className="edit-row">
          <td colSpan={5}>
            <Manage user={user} units={units} onChanged={onChanged} onPassword={onPassword} />
          </td>
        </tr>
      )}
    </>
  );
}

// ---- the business units tab ------------------------------------------------------------------------------

function UnitsTab({ units, onChanged }: { units: AdminBU[]; onChanged: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);

  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy("new");
    setError(null);
    try {
      await api.post("/admin/business-units", { name });
      toast(`${name.trim()} added`);
      setName("");
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function rename(e: FormEvent) {
    e.preventDefault();
    if (!renaming) return;
    setBusy(renaming.id);
    setError(null);
    try {
      await api.post(`/admin/business-units/${renaming.id}`, { name: renaming.name });
      toast(`Renamed to ${renaming.name.trim()}`);
      setRenaming(null);
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="section">
      <form className="admin-inline" onSubmit={create}>
        <label className="visually-hidden" htmlFor="new-bu">
          New business unit
        </label>
        <input id="new-bu" value={name} onChange={(e) => setName(e.target.value)} placeholder="New unit, e.g. Design" />
        <Button type="submit" variant="primary" icon={<Plus size={16} />} loading={busy === "new"} disabled={!name.trim()}>
          Add business unit
        </Button>
      </form>
      {error && <p className="error">{error}</p>}
      {units.length === 0 ? (
        <div className="empty">
          <p className="empty-title">No business units yet</p>
          <p className="muted">Add one, then invite its BU head.</p>
        </div>
      ) : (
        <div className="table-wrap admin-table">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Business unit</th>
                <th scope="col">BU heads</th>
                <th scope="col" className="num">
                  Open jobs
                </th>
                <th scope="col" className="num">
                  Candidates
                </th>
                <th scope="col">
                  <span className="visually-hidden">Rename</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {units.map((u) => (
                <tr key={u.id}>
                  <td>
                    {renaming?.id === u.id ? (
                      <form className="admin-inline" onSubmit={rename}>
                        <label className="visually-hidden" htmlFor={`rn-${u.id}`}>
                          New name for {u.name}
                        </label>
                        <input id={`rn-${u.id}`} value={renaming.name} onChange={(e) => setRenaming({ id: u.id, name: e.target.value })} autoFocus />
                        <Button type="submit" size="sm" variant="primary" loading={busy === u.id} disabled={!renaming.name.trim() || renaming.name.trim() === u.name}>
                          Save
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setRenaming(null)}>
                          Cancel
                        </Button>
                      </form>
                    ) : (
                      <span className="row-title">{u.name}</span>
                    )}
                  </td>
                  <td>
                    {u.head_names.length ? (
                      u.head_names.join(", ")
                    ) : (
                      <span className="mark tone-attention">No BU head yet</span>
                    )}
                  </td>
                  <td className="num">{u.open_jobs}</td>
                  <td className="num">{u.candidates}</td>
                  <td className="cell-actions">
                    {renaming?.id !== u.id && (
                      <Button size="sm" variant="ghost" icon={<Pencil size={15} />} onClick={() => setRenaming({ id: u.id, name: u.name })}>
                        Rename
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- the page --------------------------------------------------------------------------------------------

export function People() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "units" ? "units" : "people";
  const [units, setUnits] = useState<AdminBU[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadUnits = useCallback(() => {
    api
      .get<{ business_units: AdminBU[] }>("/admin/business-units")
      .then((d) => setUnits(d.business_units))
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(loadUnits, [loadUnits]);

  const headless = useMemo(() => (units ?? []).filter((u) => u.heads === 0).length, [units]);

  return (
    <div className="page page-wide dash">
      <header className="page-head">
        <div>
          <h1 className="page-title">People</h1>
          <p className="page-sub">Who signs in, what each role can do, and the business units they work in. Every change here is in the audit log.</p>
        </div>
      </header>
      <div className="segmented admin-tabs" role="tablist" aria-label="People or business units">
        <button type="button" role="tab" aria-selected={tab === "people"} aria-pressed={tab === "people"} onClick={() => setParams({}, { replace: true })}>
          <Users size={14} aria-hidden="true" /> People
        </button>
        <button type="button" role="tab" aria-selected={tab === "units"} aria-pressed={tab === "units"} onClick={() => setParams({ tab: "units" }, { replace: true })}>
          <Building2 size={14} aria-hidden="true" /> Business units{headless ? ` (${headless} without a head)` : ""}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {!units ? <PageSkeleton rows={5} /> : tab === "people" ? <PeopleTab units={units} /> : <UnitsTab units={units} onChanged={loadUnits} />}
    </div>
  );
}
