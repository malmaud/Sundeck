import type { LogEntry } from "./types";

interface LogPanelProps {
  logEntries: LogEntry[];
  refreshLog: () => void;
}

export function LogPanel({ logEntries, refreshLog }: LogPanelProps) {
  return (
    <div className="log-panel">
      <div className="log-panel-header">
        <span>Activity Log</span>
        <button className="btn-secondary" onClick={refreshLog}>Refresh</button>
      </div>
      {logEntries.length === 0
        ? <div className="log-empty">No activity yet.</div>
        : <div className="log-entries">
            {logEntries.map((e, i) => (
              <div key={i} className="log-entry" title={e.detail || undefined}>
                <span className="log-time">
                  {new Date(e.timestamp * 1000).toLocaleString()}
                </span>
                <span className={`log-kind ${e.kind}`}>{e.kind}</span>
                <span className={`log-status ${e.success ? "success" : "error"}`}>
                  {e.success ? "✓" : "✗"}
                </span>
                <span className="log-msg">{e.message}</span>
              </div>
            ))}
          </div>
      }
    </div>
  );
}
