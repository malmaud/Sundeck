import { useState, useEffect, useRef } from "react";
import type { GameCardProps } from "./types";

export function GameCard({ game, action, showDebug }: GameCardProps) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    if (imgRef.current?.complete && imgRef.current.naturalWidth > 0) {
      setImgLoaded(true);
    }
  }, []);

  return (
    <div className="game-card">
      <button
        className={`card-action ${action.className}`}
        onClick={action.onClick}
        title={action.title}
      >
        {action.label}
      </button>
      {!imgError && game.thumbnail
        ? <>
            <img
              ref={imgRef}
              src={game.thumbnail}
              alt={game.name}
              loading="lazy"
              onLoad={() => setImgLoaded(true)}
              onError={() => setImgError(true)}
            />
            {!imgLoaded && <div className="game-thumbnail-placeholder loading" aria-hidden="true" />}
          </>
        : <div className="game-thumbnail-placeholder" aria-hidden="true" />
      }
      <div className="game-name-row">
        <div className="game-name" title={game.name}>{game.name}</div>
      </div>
      {showDebug && <div className="game-id">App ID: {game.app_id}</div>}
      {game.last_played > 0 && (
        <div className="game-last-played">
          {new Date(game.last_played * 1000).toLocaleDateString()}
        </div>
      )}
    </div>
  );
}
