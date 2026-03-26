import { apiPatchSettings } from "./api";

interface SettingsPanelProps {
  configPathInput: string;
  setConfigPathInput: (v: string) => void;
  suggestions: string[];
  autoSync: boolean;
  setAutoSync: (v: boolean) => void;
  showDebug: boolean;
  setShowDebug: (v: boolean) => void;
}

export function SettingsPanel({
  configPathInput, setConfigPathInput, suggestions,
  autoSync, setAutoSync,
  showDebug, setShowDebug,
}: SettingsPanelProps) {
  return (
    <div className="settings-panel">
      <label>
        Config path:
        <input
          list="config-path-suggestions"
          className="config-path-input"
          value={configPathInput}
          onChange={(e) => setConfigPathInput(e.target.value)}
          onBlur={() => apiPatchSettings({ config_path: configPathInput }).catch(() => {})}
          onKeyDown={(e) => e.key === "Enter" && apiPatchSettings({ config_path: configPathInput }).catch(() => {})}
          spellCheck={false}
        />
        <datalist id="config-path-suggestions">
          {suggestions.map((s) => <option key={s} value={s} />)}
        </datalist>
      </label>
      <label className="auto-sync-toggle">
        <input
          type="checkbox"
          checked={autoSync}
          onChange={(e) => {
            setAutoSync(e.target.checked);
            apiPatchSettings({ auto_sync: e.target.checked }).catch(() => {});
          }}
        />
        Auto-sync when game list changes
      </label>
      <label className="debug-toggle">
        <input
          type="checkbox"
          checked={showDebug}
          onChange={(e) => {
            setShowDebug(e.target.checked);
            apiPatchSettings({ show_debug: e.target.checked }).catch(() => {});
          }}
        />
        Show debug information
      </label>
    </div>
  );
}
