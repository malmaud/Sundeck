export interface Game {
  app_id: number;
  name: string;
  thumbnail: string;
  last_played: number;
}

export interface Settings {
  config_path: string;
  needs_setup: boolean;
  suggestions: string[];
  excluded_games: number[];
  included_games: number[];
  show_debug: boolean;
  count: number;
  auto_sync: boolean;
  run_at_startup: boolean;
  desktop_position: "start" | "end";
  has_desktop_app: boolean;
}

export interface Status {
  msg: string;
  type: "loading" | "error" | "success";
}

export interface LogEntry {
  timestamp: number;
  kind: "manual" | "auto";
  success: boolean;
  message: string;
  detail: string;
}

export interface CardAction {
  label: string;
  className: string;
  title: string;
  onClick: () => void;
}

export interface GameCardProps {
  game: Game;
  action: CardAction;
  showDebug: boolean;
}

export const OTHER_INITIAL_LIMIT = 24;
