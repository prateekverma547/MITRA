// What the decisions rest on: HR round transcripts and evaluations, screenings
// and stored recordings, searched by what was actually said or written.

import { FileAudio, FileSearch, MessagesSquare, Play, ScrollText, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button, ButtonLink } from "../../shell/ui/Button";
import { PageSkeleton } from "../../shell/ui/Skeleton";
import { Highlight, Pager, SearchBox, TransparencyHead, clock, day, useFetch, useFilters } from "./common";
import type { RecordItem, Records as RecordsData } from "./types";
import "./transparency.css";

const KINDS = [
  { key: "hr_round", label: "HR rounds", icon: MessagesSquare },
  { key: "screening", label: "Screenings", icon: FileSearch },
  { key: "recording", label: "Recordings", icon: FileAudio },
] as const;

const RECOMMENDS = {
  proceed_to_technical: "Recommended proceeding to the MITRA interview round",
  reject: "Recommended rejecting",
  discuss: "Recommended discussing: the evidence pointed neither way",
};

const SPEAKERS: Record<string, string> = {
  hr: "HR",
  candidate: "Candidate",
  other: "Another voice",
  evaluation: "Evaluation",
  screening: "Screening reasoning",
};

function Matches({ item, term }: { item: RecordItem; term: string }) {
  if (!item.matches.length) return null;
  return (
    <ul className="tx-matches">
      {item.matches.map((m, i) => (
        <li key={i}>
          <span className="tx-match-who">
            {SPEAKERS[m.speaker] ?? m.speaker}
            {m.at_seconds !== null && <span className="line-at">{clock(m.at_seconds)}</span>}
          </span>
          <span className="tx-match-text">
            <Highlight text={m.text} term={term} />
          </span>
        </li>
      ))}
      {item.more_matches > 0 && <li className="muted small">and {item.more_matches} more in this transcript</li>}
    </ul>
  );
}

function Recording({ item }: { item: RecordItem }) {
  const [playing, setPlaying] = useState(false);
  return playing ? (
    <video className="tx-video" src={item.play} controls autoPlay preload="metadata" />
  ) : (
    <Button size="sm" icon={<Play size={15} />} onClick={() => setPlaying(true)}>
      Play the recording
    </Button>
  );
}

function Result({ item, term }: { item: RecordItem; term: string }) {
  const kind = KINDS.find((k) => k.key === item.kind)!;
  const Icon = kind.icon;
  return (
    <li className="tx-record">
      <span className="tx-record-icon" aria-hidden="true">
        <Icon size={18} />
      </span>
      <div className="tx-record-body">
        <div className="tx-record-head">
          <span>
            <Link className="row-title" viewTransition to={`/candidates/${item.candidate.id}`}>
              {item.candidate.name}
            </Link>
            <span className="row-sub">
              {item.job.title}, {item.bu_name}. {day(item.at)}
            </span>
          </span>
          <ButtonLink size="sm" variant="ghost" to={item.open} icon={<ScrollText size={15} />}>
            {item.kind === "screening" ? "Open the screening" : item.kind === "hr_round" ? "Open the HR round" : "Open the round"}
          </ButtonLink>
        </div>
        {item.kind === "hr_round" && (
          <p className="tx-record-line">
            {item.recommendation ? RECOMMENDS[item.recommendation] : "No evaluation yet"}
            {item.turns ? `. Transcript of ${item.turns} turns` : ""}
            {item.attribution === "uncertain" ? ". Voices could not be reliably told apart" : ""}.
          </p>
        )}
        {item.kind === "screening" && item.score_percent != null && (
          <p className="tx-record-line">
            Scored {Math.round(item.score_percent)}%, judged on {Math.round(item.assessed_weight_percent ?? 0)}% of the job's weight.
          </p>
        )}
        {item.kind === "recording" && (
          <>
            <p className="tx-record-line">{item.round === "hr" ? "HR round" : "MITRA interview round"} recording, stored. Opening it is recorded in the audit trail.</p>
            <Recording item={item} />
          </>
        )}
        <Matches item={item} term={term} />
      </div>
    </li>
  );
}

export function Records() {
  const { params, set, clear, page } = useFilters();
  const q = params.get("q") ?? "";
  const kind = params.get("kind");
  const query = new URLSearchParams({ page: String(page), page_size: "20" });
  if (q) query.set("q", q);
  if (kind) query.set("kind", kind);
  const { data, error, refreshing } = useFetch<RecordsData>(`/transparency/records?${query}`);
  const sub = "Transcripts, evaluations, screenings and recordings, searched by what was said or written.";

  if (error && !data) {
    return (
      <div className="page page-wide">
        <TransparencyHead sub={sub} />
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!data) return <PageSkeleton rows={6} />;

  return (
    <div className="page page-wide dash">
      <TransparencyHead sub={sub} />
      <div className="toolbar">
        <SearchBox value={q} onChange={(v) => set({ q: v })} placeholder="Search what was said, or a candidate or job" />
      </div>
      <div className="filters" role="group" aria-label="Filters">
        <div className="chips" role="group" aria-label="Kind of record">
          <button type="button" className="chip-btn" aria-pressed={!kind} onClick={() => set({ kind: null })}>
            Everything
          </button>
          {KINDS.map((k) => (
            <button key={k.key} type="button" className="chip-btn" aria-pressed={kind === k.key} onClick={() => set({ kind: kind === k.key ? null : k.key })}>
              {k.label}
              <span className="chip-n">{data.counts[k.key]}</span>
            </button>
          ))}
        </div>
      </div>

      <p className="notice tx-note">
        MITRA interview transcripts and reports will appear here once the MITRA interview round is connected. Nothing is shown in
        their place until then.
      </p>

      <div className={refreshing ? "dash-body refreshing" : "dash-body"}>
        {data.items.length === 0 ? (
          <div className="empty">
            <p className="empty-title">{q || kind ? "Nothing matches" : "No records yet"}</p>
            <p className="muted">
              {q || kind ? "Try other words, or clear the filter." : "Records appear as candidates are screened and HR rounds are held."}
            </p>
            {(q || kind) && (
              <Button size="sm" icon={<X size={15} />} onClick={clear} className="tx-clear">
                Clear everything
              </Button>
            )}
          </div>
        ) : (
          <ul className="tx-records">
            {data.items.map((item) => (
              <Result key={item.id} item={item} term={q} />
            ))}
          </ul>
        )}
        <Pager total={data.total} page={data.page} pageSize={data.page_size} onPage={(p) => set({ page: String(p) }, false)} />
      </div>
    </div>
  );
}
