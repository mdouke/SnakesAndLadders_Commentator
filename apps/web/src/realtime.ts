import { useEffect, useRef, useState } from "react";

import type {
  CommentaryState, GameState, RealtimeMessage, RecognitionState, SpeechState
} from "./types";

export interface RealtimeState {
  connected: boolean;
  game: GameState | null;
  /** False until the first game snapshot arrives on the current connection. */
  gameLoaded: boolean;
  recognition: RecognitionState | null;
  commentary: CommentaryState | null;
  speech: SpeechState | null;
}

const RECONNECT_MIN_MS = 500;
const RECONNECT_MAX_MS = 5000;

function socketUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}

/**
 * Keep the latest of two game states. HTTP responses and WebSocket messages can
 * arrive in either order, so an older stateVersion of the same game is ignored.
 */
export function newerGame(
  current: GameState | null, next: GameState | null
): GameState | null {
  if (current && next && current.id === next.id && next.stateVersion < current.stateVersion) {
    return current;
  }
  return next;
}

export function useRealtime(): RealtimeState {
  const [state, setState] = useState<RealtimeState>({
    connected: false, game: null, gameLoaded: false, recognition: null, commentary: null,
    speech: null
  });
  const lastSequence = useRef(0);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: number | undefined;
    let delay = RECONNECT_MIN_MS;
    let stopped = false;

    const connect = () => {
      socket = new WebSocket(socketUrl());
      socket.onopen = () => {
        delay = RECONNECT_MIN_MS;
        // Sequence numbers restart from the server's view on each connection;
        // the first messages are fresh snapshots that replace local state.
        lastSequence.current = 0;
        setState((current) => ({ ...current, connected: true }));
      };
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as RealtimeMessage;
        if (message.sequence <= lastSequence.current) return;
        lastSequence.current = message.sequence;
        setState((current) => {
          switch (message.type) {
            case "game":
              return { ...current, game: message.payload, gameLoaded: true };
            case "recognition":
              return { ...current, recognition: message.payload };
            case "commentary":
              return { ...current, commentary: message.payload };
            case "speech":
              return { ...current, speech: message.payload };
          }
        });
      };
      socket.onclose = () => {
        setState((current) => ({ ...current, connected: false }));
        if (stopped) return;
        timer = window.setTimeout(connect, delay);
        delay = Math.min(delay * 2, RECONNECT_MAX_MS);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      socket?.close();
    };
  }, []);

  return state;
}
