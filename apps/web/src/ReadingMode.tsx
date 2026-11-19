import { useCallback, useEffect, useState } from "react";
import {
  getVlmReadings,
  getVlmSettings,
  testVlmReading,
  updateVlmSettings,
  type VlmSettingsChange
} from "./api";
import type { ReadingMode, RecognitionInfo, VlmReadingRecord, VlmSettings } from "./types";

export const readingModeLabels: Record<ReadingMode, string> = {
  opencv: "OpenCV（今までの方式）",
  vlm: "Claude の画像認識",
  hybrid: "両方（一致したら自動確定）"
};

const readingModeHelp: Record<ReadingMode, string> = {
  opencv: "カメラ画像を手元で処理して目を数えます。ネットワークは使いません。",
  vlm: "サイコロが止まったら画像を1枚 Claude に送って読ませます。確信度が高い時だけ自動確定します。",
  hybrid: "OpenCV と Claude の両方で読み、一致した時だけ自動確定します。食い違った時は確認を求めます。Claude が使えない時は OpenCV だけで判定します。"
};

const modelOptions = [
  { value: "claude-haiku-5-5", label: "Haiku 5.5（速い）" },
  { value: "claude-sonnet-5-5", label: "Sonnet 5.5（標準）" },
  { value: "claude-opus-5-5", label: "Opus 5.5（精度重視）" }
];

const confidenceLabels: Record<string, string> = { high: "高", medium: "中", low: "低" };

const statusLabels: Record<VlmReadingRecord["status"], string> = {
  used: "使用", discarded: "破棄（サイコロが動いた）", failed: "失敗", test: "試し読み"
};

const errorMessages: Record<string, string> = {
  vlm_unreadable: "Claude が目を読めませんでした（傾き・端で切れているなど）。手動で入力してください",
  vlm_timeout: "Claude の応答が時間内に来ませんでした。手動で入力してください",
  vlm_connection: "Claude に接続できませんでした。ネットワークを確認してください",
  vlm_auth: "Claude の認証に失敗しました。var/secrets.env の API キーを確認してください",
  vlm_unavailable: "Claude の画像認識を使えません（パッケージまたは API キーがありません）",
  vlm_rate_limited: "Claude の利用上限に達しました。しばらく待つか方式を OpenCV に切り替えてください"
};

export function recognitionErrorMessage(code: string | null | undefined): string | null {
  if (!code) return null;
  if (errorMessages[code]) return errorMessages[code];
  if (code.startsWith("vlm_")) return `Claude で読み取れませんでした（${code}）。手動で入力してください`;
  return null;
}

function errorText(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

function credentialWarning(settings: VlmSettings): string | null {
  if (settings.readingMode === "opencv") return null;
  if (!settings.installed) return "anthropic パッケージがありません（pip install -e '.[llm]'）。";
  if (!settings.credentialsConfigured) return "API キーがありません。var/secrets.env に ANTHROPIC_API_KEY を書いて起動し直してください。";
  return null;
}

/** Quick switch shown in the recognition panel. */
export function ReadingModeSwitch({
  activeMode, onError
}: {
  activeMode: ReadingMode;
  onError: (message: string | null) => void;
}) {
  const [settings, setSettings] = useState<VlmSettings | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getVlmSettings().then(setSettings).catch(() => undefined);
  }, [activeMode]);

  if (!settings) return null;
  const change = async (readingMode: ReadingMode) => {
    setSaving(true);
    try {
      onError(null);
      setSettings(await updateVlmSettings({ readingMode }));
    } catch (reason) {
      onError(errorText(reason, "読み取り方式を変更できませんでした"));
    } finally {
      setSaving(false);
    }
  };
  const warning = credentialWarning(settings);
  return (
    <div className="reading-mode">
      <label>
        読み取り方式
        <select value={settings.readingMode} disabled={saving}
          onChange={(event) => void change(event.target.value as ReadingMode)}>
          {(Object.keys(readingModeLabels) as ReadingMode[]).map((mode) => (
            <option key={mode} value={mode}>{readingModeLabels[mode]}</option>
          ))}
        </select>
      </label>
      <small>{readingModeHelp[settings.readingMode]}</small>
      {settings.pendingReadingMode && (
        <small className="pending">今の投擲が終わってから切り替わります。</small>
      )}
      {warning && <small className="warning-text">{warning}</small>}
    </div>
  );
}

/** OpenCV and Claude values side by side in the confirmation request. */
export function RecognitionComparison({ recognition }: { recognition: RecognitionInfo | null | undefined }) {
  if (!recognition || recognition.recognizer === "opencv") return null;
  const vlm = recognition.vlm;
  return (
    <p className="recognition-compare">
      {recognition.opencvCandidate !== null && <span>OpenCV: <strong>{recognition.opencvCandidate}</strong></span>}
      <span>
        Claude: {vlm
          ? <><strong>{vlm.value ?? "読めない"}</strong>（確信度 {confidenceLabels[vlm.confidence]}
            {vlm.diceCount !== 1 && `・サイコロ${vlm.diceCount}個`}）{vlm.note && ` ${vlm.note}`}</>
          : `使えませんでした（${recognition.vlmError ?? "不明"}）`}
      </span>
    </p>
  );
}

function ReadingRow({ record }: { record: VlmReadingRecord }) {
  const time = new Date(record.at).toLocaleTimeString();
  return (
    <li>
      <span className="muted">{time}</span>{" "}
      {record.result
        ? <>出目 <strong>{record.result.value ?? "読めない"}</strong>・確信度 {confidenceLabels[record.result.confidence]}
          {record.result.diceCount !== 1 && `・${record.result.diceCount}個`}</>
        : <>エラー {record.error}</>}
      {record.durationMs !== null && `・${(record.durationMs / 1000).toFixed(1)}秒`}
      <span className="muted">（{statusLabels[record.status]}）</span>
      {record.result?.note && <div className="muted small">{record.result.note}</div>}
    </li>
  );
}

/** Claude settings, a test read and recent answers, inside the tuning dialog. */
export function VlmSettingsSection({ running }: { running: boolean }) {
  const [settings, setSettings] = useState<VlmSettings | null>(null);
  const [readings, setReadings] = useState<VlmReadingRecord[]>([]);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getVlmReadings().then(({ readings: found }) => setReadings(found)).catch(() => undefined);
  }, []);

  useEffect(() => {
    getVlmSettings().then(setSettings).catch((reason) => setError(errorText(reason, "設定を読み込めませんでした")));
    refresh();
    const id = window.setInterval(refresh, 3000);
    return () => window.clearInterval(id);
  }, [refresh]);

  if (!settings) return error ? <p className="error">{error}</p> : null;
  const save = async (changes: VlmSettingsChange) => {
    try {
      setError(null);
      setSettings(await updateVlmSettings(changes));
    } catch (reason) {
      setError(errorText(reason, "設定を変更できませんでした"));
    }
  };
  const test = async () => {
    setTesting(true);
    try {
      setError(null);
      await testVlmReading();
    } catch (reason) {
      setError(errorText(reason, "読み取れませんでした"));
    } finally {
      setTesting(false);
      refresh();
    }
  };
  const knownModel = modelOptions.some((option) => option.value === settings.model);
  const warning = credentialWarning({ ...settings, readingMode: "vlm" });

  return (
    <fieldset>
      <legend>読み取り方式・Claude</legend>
      <label className="tuning-field">
        <span className="tuning-label">読み取り方式</span>
        <select value={settings.readingMode}
          onChange={(event) => void save({ readingMode: event.target.value as ReadingMode })}>
          {(Object.keys(readingModeLabels) as ReadingMode[]).map((mode) => (
            <option key={mode} value={mode}>{readingModeLabels[mode]}</option>
          ))}
        </select>
        <small>{readingModeHelp[settings.readingMode]}</small>
        {settings.pendingReadingMode && <small className="pending">今の投擲が終わってから切り替わります。</small>}
      </label>
      {warning && <p className="warning-text small">{warning}</p>}
      <label className="tuning-field">
        <span className="tuning-label">モデル</span>
        <select value={settings.model} onChange={(event) => void save({ model: event.target.value })}>
          {modelOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          {!knownModel && <option value={settings.model}>{settings.model}</option>}
        </select>
      </label>
      <div className="tuning-pair">
        <label className="tuning-field">
          <span className="tuning-label">待ち時間の上限<output>{settings.timeoutSeconds}秒</output></span>
          <input type="range" min={2} max={30} step={1} value={settings.timeoutSeconds}
            onChange={(event) => void save({ timeoutSeconds: Number(event.target.value) })} />
        </label>
        <label className="tuning-field">
          <span className="tuning-label">再試行<output>{settings.maxRetries}回</output></span>
          <input type="range" min={0} max={2} step={1} value={settings.maxRetries}
            onChange={(event) => void save({ maxRetries: Number(event.target.value) })} />
        </label>
      </div>
      <label className="tuning-field check-row">
        <input type="checkbox" checked={settings.saveImages}
          onChange={(event) => void save({ saveImages: event.target.checked })} />
        送った画像を recordings/vlm/ に保存する（評価用）
      </label>
      <div className="button-row">
        <button className="secondary small" disabled={!running || testing} onClick={() => void test()}>
          {testing ? "読み取り中…" : "今の映像を Claude で読む"}
        </button>
        {!running && <small className="muted">認識を開始すると試せます。</small>}
      </div>
      <small className="block">試し読みの結果はゲームには登録しません。1回ごとに API の料金がかかります。</small>
      {error && <p className="error small">{error}</p>}
      {readings.length > 0 && (
        <details open>
          <summary>最近の読み取り</summary>
          <ul className="vlm-readings">
            {readings.slice(0, 6).map((record, index) => <ReadingRow key={`${record.at}-${index}`} record={record} />)}
          </ul>
        </details>
      )}
    </fieldset>
  );
}
