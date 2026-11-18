import { FileText, Upload, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { Button } from "./Button";

export const DOCUMENT_TYPES = ".pdf,.docx,.txt,.md";

/** Drop a file or choose one. Shows the chosen file with a way to take it back. */
export function FileDrop({
  file,
  onFile,
  labelId,
  prompt = "Drop a PDF, Word or text file here",
}: {
  file: File | null;
  onFile: (file: File | null) => void;
  labelId: string;
  prompt?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  function drop(event: DragEvent) {
    event.preventDefault();
    setOver(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) onFile(dropped);
  }

  return (
    <div
      className={["dropzone", over ? "over" : "", file ? "has-file" : ""].join(" ")}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      {file ? (
        <div className="file-chip">
          <FileText size={20} aria-hidden="true" />
          <span className="file-name">{file.name}</span>
          <Button variant="ghost" size="sm" icon={<X size={15} />} onClick={() => onFile(null)} aria-label={`Remove ${file.name}`}>
            Remove
          </Button>
        </div>
      ) : (
        <div className="drop-prompt">
          <Upload size={22} aria-hidden="true" className="drop-icon" />
          <p>{prompt}</p>
          <Button size="sm" icon={<FileText size={15} />} onClick={() => input.current?.click()}>
            Choose a file
          </Button>
        </div>
      )}
      <input
        ref={input}
        type="file"
        accept={DOCUMENT_TYPES}
        aria-labelledby={labelId}
        className="visually-hidden"
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
    </div>
  );
}
