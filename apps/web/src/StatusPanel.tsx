import { useCallback, useEffect, useState } from "react";

import { getSystemStatus, updateLogRetention } from "./api";
import type { ComponentStatus, SystemStatus } from "./types";

const POLL_MS = 5000;

// Status always shows an icon and a word next to the color, never color alone.
const statusMarks: Record<ComponentStatus["status"], { icon: string; text: string }> = {
  ok: { icon: "●", text: "正常" },
  warning: { icon: "▲", text: "注意" },
  error: { icon: "■", text: "エラー" },
  off: { icon: "○", text: "停止" },
  not_configured: { icon: "—", text: "未接続" }
};

function Row({ name, value }: { name: string; value: ComponentStatus }) {
  const mark = statusMarks[value.status];
  return (
    <tr>
      <th scope="row">{name}</th>
      <td><span className={`status-badge ${value.status}`}>{mark.icon} {mark.text}</span></td>
      <td>{value.label}{value.detail && <span className="muted">（{value.detail}）</span>}</td>
    </tr>
  );
}

export function StatusPanel({ connected }: { connected: boolean }) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    getSystemStatus()
      .then((value) => { setStatus(value); setFailed(false); })
      .catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const changeRetention = async (retention: "rotate" | "permanent") => {
    const text = retention === "permanent"
      ? "ログを削除せずに残し続けます（日ごとのファイル）。ディスク容量に注意してください。変更しますか？"
      : "古いログを自動で削除します（日数・サイズで整理）。変更しますか？";
    if (!window.confirm(text)) return;
    try {
      await updateLogRetention(retention);
      setMessage("ログの保存方式を変更しました");
      load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "変更できませんでした");
    }
  };

  return (
    <section className="panel status-panel">
      <h2>システムの状態</h2>
      {failed && <p className="error">サーバーの状態を取得できません。サーバーが起動しているか確認してください。</p>}
      {status && (
        <>
          <table className="status-table">
            <tbody>
              <tr>
                <th scope="row">画面の接続</th>
                <td>
                  <span className={`status-badge ${connected ? "ok" : "error"}`}>
                    {connected ? "● 正常" : "■ 再接続中"}
                  </span>
                </td>
                <td>リアルタイム配信（接続中の画面 {status.realtimeClients}）</td>
              </tr>
              <Row name="保存（データベース）" value={status.database} />
              <Row name="カメラ" value={status.camera} />
              <Row name="実況（LLM）" value={status.commentary} />
              <Row name="読み上げ（TTS）" value={status.tts} />
              <Row name="録音" value={status.recording} />
            </tbody>
          </table>

          <h3>ログ</h3>
          <p>
            保存方式: <strong>{status.logging.retention === "rotate" ? "自動整理（rotate）" : "永久保存（permanent）"}</strong>
            {status.logging.retention === "rotate" && `・${status.logging.retentionDays}日より古いものを削除`}
            <span className="muted">（保存先 {status.logging.directory}）</span>
          </p>
          {status.logging.editable ? (
            <div className="button-row">
              <button className="secondary" disabled={status.logging.retention === "rotate"}
                onClick={() => changeRetention("rotate")}>自動整理にする</button>
              <button className="secondary" disabled={status.logging.retention === "permanent"}
                onClick={() => changeRetention("permanent")}>永久保存にする</button>
            </div>
          ) : (
            <p className="muted">
              {status.logging.locked
                ? "環境変数 COMMENTATOR_LOG_RETENTION で固定されているため、ここでは変更できません。"
                : "このサーバーではログ設定を変更できません。"}
            </p>
          )}
          {message && <p className="notice">{message}</p>}

          <details>
            <summary>最近の警告・エラー（{status.recentProblems.length}件）</summary>
            {status.recentProblems.length === 0 ? <p className="muted">ありません</p> : (
              <ul className="problem-list">
                {status.recentProblems.map((problem, index) => (
                  <li key={`${problem.at}-${index}`}>
                    <span className={`status-badge ${problem.level === "warning" ? "warning" : "error"}`}>
                      {problem.level === "warning" ? "▲ 注意" : "■ エラー"}
                    </span>
                    <time>{new Date(problem.at).toLocaleTimeString()}</time>
                    <span>{problem.message}</span>
                    {problem.errorCode && <code>{problem.errorCode}</code>}
                  </li>
                ))}
              </ul>
            )}
          </details>
        </>
      )}
    </section>
  );
}
