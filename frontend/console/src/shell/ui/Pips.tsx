/** A 0–4 rating as four short bars and a word, or "no evidence". */
export const RATING_WORDS = ["Clearly lacking", "Weak", "Adequate", "Good, with specifics", "Strong, specific, owned"];

export function Pips({ rating, words = RATING_WORDS, none = "No evidence" }: { rating: number | null; words?: string[]; none?: string }) {
  if (rating === null) return <span className="pips-none">{none}</span>;
  return (
    <span className="pips" role="img" aria-label={`${rating} of 4: ${words[rating]}`}>
      {[1, 2, 3, 4].map((n) => (
        <span key={n} className={n <= rating ? "pip on" : "pip"} />
      ))}
      <span className="pips-word">{words[rating]}</span>
    </span>
  );
}
