import { useState } from "react";
import { apiPatchSettings } from "./api";

interface SetupModalProps {
  defaultPath: string;
  suggestions: string[];
  onSave: () => void;
}

export function SetupModal({ defaultPath, suggestions, onSave }: SetupModalProps) {
  const [pathInput, setPathInput] = useState(defaultPath);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!pathInput.trim()) {
      setError("Please enter a config path.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await apiPatchSettings({ config_path: pathInput.trim() });
      onSave();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="setup-overlay">
      <div className="setup-modal">
        <h2>Welcome to SteamLaunch</h2>
        <p>SteamLaunch syncs your Apollo / Sunshine game list with your most recently played Steam games.</p>
        <p>Confirm the path to your <code>apps.json</code> config file to get started.</p>
        <label>
          Config path
          <input
            list="setup-path-suggestions"
            className="config-path-input"
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleSave(); }}
            spellCheck={false}
            autoFocus
          />
          <datalist id="setup-path-suggestions">
            {suggestions.map((s) => <option key={s} value={s} />)}
          </datalist>
        </label>
        {error && <p className="setup-error">{error}</p>}
        <button className="btn-primary" onClick={handleSave} disabled={saving || !pathInput.trim()}>
          {saving ? "Saving…" : "Save & Continue"}
        </button>
      </div>
    </div>
  );
}
