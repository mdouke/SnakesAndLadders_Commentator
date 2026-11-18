import { useCallback, useEffect, useState } from "react";

import {
  type CommentarySettingsChange,
  type SpeechSettingsChange,
  getCommentaryRequests,
  getCommentarySettings,
  getSpeechSettings,
  listVoices,
  previewSpeech,
  regenerateCommentary,
  stopSpeech,
  updateCommentarySettings,
  updateSpeechSettings
} from "./api";
import type {
  CommentaryRequestRecord,
  CommentarySettings,
  CommentaryState,
  GameState,
  RecognitionState,
  SpeechSettings,
  SpeechState,
  VoiceInfo
} from "./types";

const fallbackReasons: Record<string, string> = {
  empty: "応答が空",
  blocked_word: "不適切な言葉",
  too_long: "長すぎる応答",
  truncated: "途中で切れた応答",
  provider_timeout: "タイムアウト",
  provider_connection: "接続できない",
  provider_rate_limited: "利用上限",
  provider_auth: "認証エラー（APIキー）",
  provider_status: "サービスのエラー",
  provider_refused: "応答を断られた",
  provider_unavailable: "パッケージ未導入",
  provider_error: "呼び出しエラー"
};

export function fallbackReasonLabel(reason: string | null): string {
  return reason ? fallbackReasons[reason] ?? reason : "";
}

/** Only commentary that belongs to the game on screen. */
function currentCommentary(
  commentary: CommentaryState | null, game: GameState | null
): CommentaryState | null {
  return commentary && game && commentary.gameId === game.id ? commentary : null;
}

export function AdminCommentary({
  commentary, game, speech, onError
}: {
  commentary: CommentaryState | null;
  game: GameState;
  speech: SpeechState | null;
  onError: (message: string | null) => void;
}) {
  const current = currentCommentary(commentary, game);
  const [pending, setPending] = useState(false);
  let body: string;
  if (!current || current.status === "idle") body = "実況コメントはここに表示されます";
  else if (current.status === "generating") body = `ターン${current.turnNumber}の実況を準備しています…`;
  else if (current.status === "failed") body = "実況を作成できませんでした";
  else body = current.text ?? "";
  const canRegenerate = game.turns.length > 0
    && !["aborted", "suspended"].includes(game.status) && !pending;
  const speaking = speech?.status === "speaking";

  const regenerate = async () => {
    setPending(true);
    try {
      onError(null);
      await regenerateCommentary(game.turns[game.turns.length - 1]?.id);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "再生成できませんでした");
    } finally {
      setPending(false);
    }
  };

  return (
    <section className={`commentary panel ${current?.status ?? "idle"}`} aria-live="polite">
      <h2>実況</h2>
      <p>{body}</p>
      {current?.status === "ready" && (
        <p className="muted">
          ターン{current.turnNumber}
          ・{current.provider}{current.model && current.model !== "template" ? `（${current.model}）` : ""}
          ・{Math.round(current.durationMs ?? 0)}ms
          {current.revision ? `・再生成${current.revision}回目` : ""}
          {current.source === "fallback" && (
            <span className="badge warning">代替文：{fallbackReasonLabel(current.fallbackReason)}</span>
          )}
        </p>
      )}
      {speech?.status === "error" && <p className="error">読み上げに失敗しました: {speech.error}</p>}
      <div className="button-row">
        <button className="secondary small" disabled={!canRegenerate} onClick={regenerate}>
          実況を再生成
        </button>
        {speaking && (
          <button className="secondary small" onClick={() => stopSpeech().catch(() => undefined)}>
            読み上げを止める
          </button>
        )}
        {speaking && <span className="muted">読み上げ中…</span>}
      </div>
    </section>
  );
}

// -- player screen ---------------------------------------------------------------

type PlayerPhase = "waiting" | "recognizing" | "generating" | "speaking" | "paused" | "idle";

const phaseLabels: Record<PlayerPhase, string> = {
  idle: "待機中",
  waiting: "サイコロを振ってください",
  recognizing: "サイコロを読み取っています",
  generating: "実況を準備しています",
  speaking: "実況中",
  paused: "一時停止中"
};

export function playerPhase(
  game: GameState | null,
  recognition: RecognitionState | null,
  commentary: CommentaryState | null,
  speech: SpeechState | null
): PlayerPhase {
  if (!game) return "idle";
  if (game.status === "paused") return "paused";
  const current = currentCommentary(commentary, game);
  if (speech?.status === "speaking" && speech.kind === "commentary"
    && current && speech.requestId === current.requestId) return "speaking";
  if (current?.status === "generating") return "generating";
  if (game.status !== "in_progress") return "idle";
  if (recognition?.running && recognition.gameReady
    && ["moving", "stationary", "candidate", "needs_confirmation"].includes(recognition.state)) {
    return "recognizing";
  }
  return "waiting";
}

function rankingText(game: GameState): string {
  return [...game.players]
    .filter((player) => player.rank !== null)
    .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99))
    .map((player) => `${player.rank}位 ${player.displayName}`)
    .join("、");
}

/** Start and end messages; null while the game is simply running. */
export function gameMessage(game: GameState | null): string | null {
  if (!game) return "管理者がゲームを準備しています";
  const first = game.players.find((player) => player.id === game.currentPlayerId);
  switch (game.status) {
    case "ready":
      return "まもなくゲームが始まります";
    case "in_progress":
      return game.turns.length === 0 && first
        ? `ゲーム開始！最初は${first.displayName}さんの番です`
        : null;
    case "completed":
      return `ゲーム終了！ ${rankingText(game)}`;
    case "suspended":
      return "ゲームを中断しています。続きはまたあとで";
    case "aborted":
      return "このゲームは終了しました";
    default:
      return null;
  }
}

export function PlayerStatus({
  game, recognition, commentary, speech, connected
}: {
  game: GameState | null;
  recognition: RecognitionState | null;
  commentary: CommentaryState | null;
  speech: SpeechState | null;
  connected: boolean;
}) {
  const phase = playerPhase(game, recognition, commentary, speech);
  const current = game?.players.find((player) => player.id === game.currentPlayerId);
  const busy = phase === "recognizing" || phase === "generating" || phase === "speaking";
  let label = phaseLabels[phase];
  if (phase === "waiting" && current) label = `${current.displayName}さん、サイコロを振ってください`;
  const problem = !connected
    ? "サーバーに再接続しています…"
    : recognition?.running && recognition.state === "camera_error"
      ? "カメラを確認しています。少しお待ちください"
      : null;
  return (
    <div className="player-status">
      {phase !== "idle" && (
        <p className={`phase-chip ${phase}`} role="status">
          {busy && <span className="dots" aria-hidden="true"><i /><i /><i /></span>}
          {label}
        </p>
      )}
      {problem && <p className="connection-warning">{problem}</p>}
    </div>
  );
}

export function PlayerCommentary({
  commentary, game
}: {
  commentary: CommentaryState | null;
  game: GameState | null;
}) {
  const current = currentCommentary(commentary, game);
  const message = gameMessage(game);
  let body: string | null = null;
  let className = "idle";
  if (current?.status === "ready" && current.text) {
    body = current.text;
    className = "ready";
  } else if (current?.status === "generating") {
    body = "実況を準備しています…";
    className = "generating";
  } else if (current?.status === "failed") {
    body = "実況はひと休み中です";
    className = "failed";
  }
  // The end message stays visible next to the final comment.
  const showMessage = message && (body === null || game?.status === "completed");
  return (
    <section className={`commentary large ${className}`} aria-live="polite">
      {showMessage && <p className="game-message">{message}</p>}
      {body && <p>{body}</p>}
      {!showMessage && !body && <p>実況コメントはここに表示されます</p>}
    </section>
  );
}

// -- settings --------------------------------------------------------------------

const providerLabels = { fake: "テンプレート（APIを使わない）", claude: "Claude", openai: "OpenAI" };
const lengthLabels = { short: "短い", medium: "ふつう", long: "長い" };
const styleLabels = { lively: "盛り上げる", calm: "落ち着いた", kids: "子ども向け" };

export function CommentarySettingsPanel({ onError }: { onError: (message: string | null) => void }) {
  const [settings, setSettings] = useState<CommentarySettings | null>(null);
  const [speech, setSpeech] = useState<SpeechSettings | null>(null);
  const [voices, setVoices] = useState<VoiceInfo[]>([]);
  const [showAllVoices, setShowAllVoices] = useState(false);
  const [sample, setSample] = useState("");
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(() => {
    getCommentarySettings().then(setSettings).catch(() => undefined);
    getSpeechSettings().then(setSpeech).catch(() => undefined);
    listVoices().then(({ voices: found }) => setVoices(found)).catch(() => undefined);
  }, []);
  useEffect(load, [load]);

  const save = async (change: CommentarySettingsChange) => {
    try {
      onError(null);
      setSettings(await updateCommentarySettings(change));
      setSaved("実況の設定を保存しました");
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "設定を保存できませんでした");
    }
  };
  const saveSpeech = async (change: SpeechSettingsChange) => {
    try {
      onError(null);
      setSpeech(await updateSpeechSettings(change));
      setSaved("読み上げの設定を保存しました");
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "設定を保存できませんでした");
    }
  };

  if (!settings || !speech) return null;
  const availability = settings.providers[settings.provider];
  const language = settings.language === "en" ? "en" : "ja";
  const shownVoices = showAllVoices
    ? voices
    : voices.filter((voice) => voice.locale.toLowerCase().startsWith(language));

  return (
    <details className="panel settings-panel">
      <summary>実況・読み上げの設定<span className="muted">
        {settings.enabled ? providerLabels[settings.provider] : "実況オフ"}
        ・{speech.enabled ? speech.voice ?? "標準の音声" : "読み上げオフ"}
      </span></summary>

      <h3>実況</h3>
      <div className="settings-grid">
        <label className="check">
          <input type="checkbox" checked={settings.enabled}
            onChange={(event) => save({ enabled: event.target.checked })} />
          実況する
        </label>
        <label>生成方法
          <select value={settings.provider}
            onChange={(event) => save({ provider: event.target.value as CommentarySettings["provider"] })}>
            {(["fake", "claude", "openai"] as const).map((provider) => (
              <option key={provider} value={provider}>{providerLabels[provider]}</option>
            ))}
          </select>
        </label>
        {settings.provider === "claude" && (
          <ModelField label="Claudeのモデル" value={settings.claudeModel}
            onSave={(claudeModel) => save({ claudeModel })} />
        )}
        {settings.provider === "openai" && (
          <ModelField label="OpenAIのモデル" value={settings.openaiModel}
            onSave={(openaiModel) => save({ openaiModel })} />
        )}
        <label>言語
          <select value={settings.language}
            onChange={(event) => save({ language: event.target.value as CommentarySettings["language"] })}>
            <option value="ja">日本語</option>
            <option value="en">英語</option>
          </select>
        </label>
        <label>長さ
          <select value={settings.length}
            onChange={(event) => save({ length: event.target.value as CommentarySettings["length"] })}>
            {(["short", "medium", "long"] as const).map((value) => (
              <option key={value} value={value}>{lengthLabels[value]}</option>
            ))}
          </select>
        </label>
        <label>話し方
          <select value={settings.style}
            onChange={(event) => save({ style: event.target.value as CommentarySettings["style"] })}>
            {(["lively", "calm", "kids"] as const).map((value) => (
              <option key={value} value={value}>{styleLabels[value]}</option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={settings.useNames}
            onChange={(event) => save({ useNames: event.target.checked })} />
          プレイヤーの名前を使う（オフにすると「プレイヤー1」などと呼びます）
        </label>
        <label>タイムアウト（秒）
          <input type="number" min={1} max={60} value={settings.timeoutSeconds}
            onChange={(event) => save({ timeoutSeconds: Number(event.target.value) })} />
        </label>
        <label>再試行の回数
          <input type="number" min={0} max={3} value={settings.maxRetries}
            onChange={(event) => save({ maxRetries: Number(event.target.value) })} />
        </label>
      </div>
      {settings.provider !== "fake" && availability && (
        <p className={availability.installed && availability.credentialsConfigured ? "muted" : "error"}>
          {!availability.installed
            ? `${providerLabels[settings.provider]}のパッケージがインストールされていません。`
            : availability.credentialsConfigured
              ? "APIキーは var/secrets.env または環境変数から読み込んでいます（画面やDBには保存しません）。"
              : `APIキー（${settings.provider === "claude" ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY"}）が見つかりません。`
                + "var/secrets.env に書いてサーバーを起動し直してください。設定しないと、実況はテンプレートの代替文になります。"}
        </p>
      )}
      <p className="muted">
        失敗・タイムアウト・不適切な応答のときは、出目と位置だけを使ったテンプレートの文に置き換えます。
      </p>

      <h3>読み上げ</h3>
      {!speech.available ? (
        <p className="muted">この環境では読み上げを利用できません（macOS の say コマンドが必要です）。</p>
      ) : (
        <>
          <div className="settings-grid">
            <label className="check">
              <input type="checkbox" checked={speech.enabled}
                onChange={(event) => saveSpeech({ enabled: event.target.checked })} />
              実況を読み上げる
            </label>
            <label>音声
              <select value={speech.voice ?? ""}
                onChange={(event) => saveSpeech({ voice: event.target.value || null })}>
                <option value="">標準の音声</option>
                {shownVoices.map((voice) => (
                  <option key={voice.name} value={voice.name}>{voice.name}（{voice.locale}）</option>
                ))}
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={showAllVoices}
                onChange={(event) => setShowAllVoices(event.target.checked)} />
              ほかの言語の音声も表示
            </label>
            <label>速さ{speech.rate ? `（${speech.rate}）` : "（標準）"}
              <span className="range-row">
                <input type="range" min={80} max={400} step={10} value={speech.rate ?? 180}
                  onChange={(event) => setSpeech({ ...speech, rate: Number(event.target.value) })}
                  onPointerUp={() => saveSpeech({ rate: speech.rate })}
                  onKeyUp={() => saveSpeech({ rate: speech.rate })} />
                <button className="secondary small" disabled={speech.rate === null}
                  onClick={() => saveSpeech({ rate: null })}>標準に戻す</button>
              </span>
            </label>
            <label>音量（{Math.round(speech.volume * 100)}%）
              <input type="range" min={0} max={1} step={0.05} value={speech.volume}
                onChange={(event) => setSpeech({ ...speech, volume: Number(event.target.value) })}
                onPointerUp={() => saveSpeech({ volume: speech.volume })}
                onKeyUp={() => saveSpeech({ volume: speech.volume })} />
            </label>
          </div>
          <div className="button-row">
            <input className="sample-input" value={sample} maxLength={300}
              placeholder="試聴する文（空欄なら定型文）" aria-label="試聴する文"
              onChange={(event) => setSample(event.target.value)} />
            <button className="secondary" onClick={() => previewSpeech({
              text: sample || undefined, voice: speech.voice, rate: speech.rate, volume: speech.volume
            }).catch((reason) => onError(reason instanceof Error ? reason.message : "試聴できませんでした"))}>
              試聴
            </button>
            <button className="secondary" onClick={() => stopSpeech().catch(() => undefined)}>止める</button>
          </div>
          <p className="muted">
            プレイヤー名の読み方は、ゲーム作成時の「読み方」が読み上げに使われます。
            声の高さは標準の音声では調整できないため、Phase 8 の試聴で扱いを決めます。
          </p>
        </>
      )}
      {saved && <p className="notice">{saved}</p>}
    </details>
  );
}

function ModelField({
  label, value, onSave
}: {
  label: string;
  value: string;
  onSave: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <label>{label}
      <span className="range-row">
        <input value={draft} maxLength={100} onChange={(event) => setDraft(event.target.value)} />
        <button className="secondary small" disabled={!draft.trim() || draft === value}
          onClick={() => onSave(draft.trim())}>保存</button>
      </span>
    </label>
  );
}

// -- request log -----------------------------------------------------------------

const statusLabels: Record<CommentaryRequestRecord["status"], string> = {
  pending: "生成中",
  displayed: "表示中",
  discarded: "破棄（古い）",
  superseded: "置き換え済み"
};

export function CommentaryLogPanel({
  game, commentary
}: {
  game: GameState;
  commentary: CommentaryState | null;
}) {
  const [open, setOpen] = useState(false);
  const [records, setRecords] = useState<CommentaryRequestRecord[]>([]);
  const marker = `${commentary?.requestId}:${commentary?.status}`;
  useEffect(() => {
    if (!open) return;
    getCommentaryRequests(game.id).then(({ requests }) => setRecords(requests)).catch(() => undefined);
  }, [open, game.id, marker]);

  return (
    <details className="panel" onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}>
      <summary>実況の記録<span className="muted">プロンプト・応答・所要時間（最新20件）</span></summary>
      {records.length === 0 ? <p className="muted">まだ記録はありません。</p> : (
        <div className="table-scroll">
          <table className="turn-table">
            <thead>
              <tr><th>開始</th><th>状態</th><th>生成</th><th>モデル</th><th>時間</th><th>試行</th><th>文</th></tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <tr key={record.id}>
                  <td>{new Date(record.startedAt).toLocaleTimeString()}</td>
                  <td>{statusLabels[record.status]}{record.trigger === "regenerate" ? "・再生成" : ""}</td>
                  <td>
                    {record.outcome === "fallback"
                      ? `代替文（${fallbackReasonLabel(record.fallbackReason)}）`
                      : record.outcome === "generated" ? record.provider : record.outcome ?? "—"}
                  </td>
                  <td>{record.model ?? "—"}</td>
                  <td>{record.durationMs === null ? "—" : `${Math.round(record.durationMs)}ms`}</td>
                  <td>{record.attempts}</td>
                  <td className="log-text">
                    <details>
                      <summary>{record.finalText ?? record.errorMessage ?? "—"}</summary>
                      <p className="muted">応答全文</p>
                      <pre>{record.responseText ?? "（応答なし）"}</pre>
                      {record.errors.length > 0 && (
                        <>
                          <p className="muted">エラー</p>
                          <pre>{record.errors.map((item) => `${item.code}: ${item.message}`).join("\n")}</pre>
                        </>
                      )}
                      <p className="muted">プロンプト（{record.promptVersion}）</p>
                      <pre>{record.systemPrompt}{"\n\n"}{record.userPrompt}</pre>
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}
