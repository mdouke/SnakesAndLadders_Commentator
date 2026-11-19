import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import {
  getRecognitionDiagnostics,
  getVisionSettings,
  revertVisionSettings,
  saveVisionSettings,
  startRecognition,
  updateVisionSettings,
  type VisionSettingsChange
} from "./api";
import { VlmSettingsSection } from "./ReadingMode";
import type {
  RecognitionCamera,
  RecognitionDiagnostics,
  VisionSettings,
  VisionSettingsState
} from "./types";

type PreviewView = "overlay" | "mask" | "pips";
type Roi = VisionSettings["roi"];

const viewLabels: Record<PreviewView, string> = {
  overlay: "全体（ドラッグで枠を指定）",
  mask: "外形マスク",
  pips: "目の計数"
};

const resolutions = [
  { width: 1920, height: 1080 },
  { width: 1280, height: 720 },
  { width: 640, height: 480 }
];

function mergeChange(base: VisionSettingsChange, next: VisionSettingsChange): VisionSettingsChange {
  return { ...base, ...next, ...(base.roi || next.roi ? { roi: { ...base.roi, ...next.roi } } : {}) };
}

function applyChange(settings: VisionSettings, change: VisionSettingsChange): VisionSettings {
  return { ...settings, ...change, roi: { ...settings.roi, ...change.roi } } as VisionSettings;
}

function round(value: number, digits = 4): number {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function errorText(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

function Slider({
  label, value, min, max, step, onChange, help, suffix, disabled
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  help?: ReactNode;
  suffix?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className="tuning-field">
      <span className="tuning-label">{label}<output>{value}</output>{suffix}</span>
      <input type="range" min={min} max={max} step={step} value={value} disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))} />
      {help && <small>{help}</small>}
    </label>
  );
}

/** A slider whose value can be ``null`` (automatic). */
function AutoSlider({
  label, value, fallback, min, max, step, onChange, help, autoLabel = "自動"
}: {
  label: string;
  value: number | null;
  fallback: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number | null) => void;
  help?: ReactNode;
  autoLabel?: string;
}) {
  const automatic = value === null;
  return (
    <div className="tuning-field">
      <span className="tuning-label">
        {label}<output>{automatic ? autoLabel : value}</output>
        <label className="tuning-auto">
          <input type="checkbox" checked={automatic}
            onChange={(event) => onChange(event.target.checked ? null : fallback)} />
          {autoLabel}
        </label>
      </span>
      <input type="range" min={min} max={max} step={step} value={value ?? fallback}
        disabled={automatic} aria-label={label}
        onChange={(event) => onChange(Number(event.target.value))} />
      {help && <small>{help}</small>}
    </div>
  );
}

function RoiPreview({
  view, running, roi, onRoi
}: {
  view: PreviewView;
  running: boolean;
  roi: Roi;
  onRoi: (roi: Roi) => void;
}) {
  const [tick, setTick] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const imageRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), 400);
    return () => window.clearInterval(timer);
  }, [running]);

  if (!running) return null;

  const point = (event: ReactPointerEvent) => {
    const rect = imageRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return null;
    return {
      x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height))
    };
  };
  const editable = view === "overlay";
  const shown = drag
    ? {
        x: Math.min(drag.x0, drag.x1), y: Math.min(drag.y0, drag.y1),
        width: Math.abs(drag.x1 - drag.x0), height: Math.abs(drag.y1 - drag.y0)
      }
    : roi;

  return (
    <div className={`tuning-preview ${view}${editable ? " editable" : ""}`}
      onPointerDown={(event) => {
        if (!editable) return;
        const start = point(event);
        if (!start) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        setDrag({ x0: start.x, y0: start.y, x1: start.x, y1: start.y });
      }}
      onPointerMove={(event) => {
        if (!drag) return;
        const current = point(event);
        if (current) setDrag({ ...drag, x1: current.x, y1: current.y });
      }}
      onPointerUp={() => {
        if (!drag) return;
        const next = {
          x: round(Math.min(drag.x0, drag.x1)), y: round(Math.min(drag.y0, drag.y1)),
          width: round(Math.abs(drag.x1 - drag.x0)), height: round(Math.abs(drag.y1 - drag.y0))
        };
        setDrag(null);
        if (next.width >= 0.03 && next.height >= 0.03) onRoi(next);
      }}>
      <img ref={imageRef} src={`/api/recognition/preview.jpg?view=${view}&t=${tick}`}
        alt={viewLabels[view]} draggable={false}
        style={{ visibility: loaded ? "visible" : "hidden" }}
        onLoad={() => setLoaded(true)} onError={() => setLoaded(false)} />
      {editable && loaded && (
        <div className="tuning-roi" style={{
          left: `${shown.x * 100}%`, top: `${shown.y * 100}%`,
          width: `${shown.width * 100}%`, height: `${shown.height * 100}%`
        }} />
      )}
    </div>
  );
}

function DiagnosticsView({
  diagnostics, stateLabel
}: {
  diagnostics: RecognitionDiagnostics | null;
  stateLabel: string;
}) {
  if (!diagnostics?.running) return null;
  const white = diagnostics.whiteThreshold;
  return (
    <div className="tuning-diagnostics">
      <dl>
        <dt>認識の状態</dt><dd>{stateLabel}</dd>
        <dt>見つかったサイコロ</dt>
        <dd>
          {diagnostics.detections.length === 0
            ? "なし"
            : diagnostics.detections.map((item) => `${item.value}（形 ${item.confidence.toFixed(2)}）`).join("、")}
          {diagnostics.rejected.length > 0 && `／読めない候補 ${diagnostics.rejected.length}個`}
        </dd>
        <dt>枠の中の明るさ</dt>
        <dd>
          {diagnostics.roiBrightness
            ? `平均 ${diagnostics.roiBrightness.mean.toFixed(0)}／最も明るい部分 ${diagnostics.roiBrightness.p99.toFixed(0)}`
            : "—"}
        </dd>
        <dt>外形しきい値（実際の値）</dt>
        <dd>
          {white?.applied != null ? white.applied.toFixed(0) : "—"}
          {white?.mode === "auto" && "（自動）"}
          {diagnostics.whiteAreaRatio != null && `／白い部分 ${(diagnostics.whiteAreaRatio * 100).toFixed(0)}%`}
        </dd>
        <dt>動き</dt><dd>{diagnostics.motionScore?.toFixed(3) ?? "—"}</dd>
        {diagnostics.tray.enabled && (
          <>
            <dt>トレー</dt>
            <dd>
              {diagnostics.tray.found
                ? `検出済み（${diagnostics.tray.vertices}角形・枠の${Math.round((diagnostics.tray.areaRatio ?? 0) * 100)}%）`
                : "探しています…"}
              {diagnostics.tray.found && diagnostics.tray.lastAttemptFound === false && "／最新の映像では見つからず"}
            </dd>
          </>
        )}
        <dt>映像</dt>
        <dd>
          {diagnostics.frameWidth}×{diagnostics.frameHeight}
          {diagnostics.fps != null && `／${diagnostics.fps.toFixed(1)} FPS`}
        </dd>
      </dl>
      {diagnostics.hints.length > 0 && (
        <ul className="tuning-hints">
          {diagnostics.hints.map((hint) => <li key={hint}>{hint}</li>)}
        </ul>
      )}
    </div>
  );
}

export function VisionTuningDialog({
  running, available, stateLabel, cameras, onClose
}: {
  running: boolean;
  available: boolean;
  stateLabel: string;
  cameras: RecognitionCamera[];
  onClose: () => void;
}) {
  const [state, setState] = useState<VisionSettingsState | null>(null);
  const [draft, setDraft] = useState<VisionSettings | null>(null);
  const [diagnostics, setDiagnostics] = useState<RecognitionDiagnostics | null>(null);
  const [view, setView] = useState<PreviewView>("overlay");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const pending = useRef<VisionSettingsChange | null>(null);
  const timer = useRef<number | null>(null);
  const inFlight = useRef<Promise<void> | null>(null);

  const accept = useCallback((next: VisionSettingsState) => {
    setState(next);
    if (!pending.current) setDraft(next.settings);
  }, []);

  useEffect(() => {
    getVisionSettings().then(accept)
      .catch((reason) => setError(errorText(reason, "設定を読み込めませんでした")));
  }, [accept]);

  useEffect(() => {
    if (!running) {
      setDiagnostics(null);
      return;
    }
    let active = true;
    const poll = () => getRecognitionDiagnostics()
      .then((next) => { if (active) setDiagnostics(next); })
      .catch(() => undefined);
    poll();
    const id = window.setInterval(poll, 700);
    return () => { active = false; window.clearInterval(id); };
  }, [running]);

  const flush = useCallback(async () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    await inFlight.current;
    const changes = pending.current;
    if (!changes) return;
    pending.current = null;
    const request = updateVisionSettings(changes)
      .then((next) => { setError(null); accept(next); })
      .catch(async (reason) => {
        setError(errorText(reason, "設定を反映できませんでした"));
        const current = await getVisionSettings().catch(() => null);
        if (current) { setState(current); setDraft(current.settings); }
      });
    inFlight.current = request;
    await request;
  }, [accept]);

  const change = (next: VisionSettingsChange) => {
    setNotice(null);
    setDraft((current) => (current ? applyChange(current, next) : current));
    pending.current = mergeChange(pending.current ?? {}, next);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { void flush(); }, 250);
  };

  const act = async (operation: () => Promise<VisionSettingsState>, done: string, fallback: string) => {
    setWorking(true);
    try {
      await flush();
      const next = await operation();
      setState(next);
      setDraft(next.settings);
      setError(null);
      setNotice(done);
    } catch (reason) {
      setError(errorText(reason, fallback));
    } finally {
      setWorking(false);
    }
  };

  const close = async () => {
    await flush();
    if (state?.unsaved && !window.confirm(
      "保存していない変更があります。閉じても今の値で認識を続けますが、サーバーを起動し直すと保存した値に戻ります。閉じますか？"
    )) return;
    onClose();
  };

  const start = async () => {
    setWorking(true);
    try {
      await flush();
      await startRecognition(draft?.cameraId ?? undefined);
      setError(null);
    } catch (reason) {
      setError(errorText(reason, "認識を開始できませんでした"));
    } finally {
      setWorking(false);
    }
  };

  const fps = diagnostics?.fps ?? null;
  const seconds = (frames: number) => (fps ? `（約${(frames / fps).toFixed(1)}秒）` : "");

  return (
    <div className="dialog-backdrop" role="presentation">
      <div className="dialog tuning-dialog" role="dialog" aria-modal="true" aria-labelledby="tuning-title">
        <header className="tuning-header">
          <h2 id="tuning-title">サイコロ認識の調整</h2>
          {state?.unsaved && <span className="badge warning">未保存</span>}
          {state?.pendingRecognitionSettings && <span className="badge">今の投擲の後に反映</span>}
        </header>
        <p className="muted">
          変更はすぐに認識へ反映されます。「保存」を押すと、次にサーバーを起動したときもこの値を使います。
        </p>
        {error && <p className="error" role="alert">{error}</p>}
        {notice && <p className="notice" role="status">{notice}</p>}
        {!available ? (
          <p>この環境ではカメラ認識を利用できません。</p>
        ) : !draft ? (
          <p className="muted">読み込み中…</p>
        ) : (
          <div className="tuning-body">
            <div className="tuning-left">
              {running ? (
                <>
                  <div className="tabs" role="tablist">
                    {(Object.keys(viewLabels) as PreviewView[]).map((item) => (
                      <button key={item} role="tab" aria-selected={view === item}
                        className={view === item ? "tab active" : "tab"}
                        onClick={() => setView(item)}>{viewLabels[item]}</button>
                    ))}
                  </div>
                  <RoiPreview view={view} running={running} roi={draft.roi}
                    onRoi={(roi) => change({ roi })} />
                  <p className="muted small">
                    {view === "overlay" && (draft.trayDetection
                      ? "緑の枠がトレーを探す範囲、水色の線が検出したトレーです。トレーの外は暗く表示され、認識にも Claude にも使いません。映像の上をドラッグすると枠を描き直せます。"
                      : "緑の枠が認識する範囲です。映像の上をドラッグすると枠を描き直せます。オレンジがサイコロ、赤は目を読めなかった候補です。")}
                    {view === "mask" && "白い部分がサイコロの面として扱われる部分です。サイコロだけが白い四角になるのが理想です。"}
                    {view === "pips" && "サイコロの面を正面から見た画像と、目として数えた部分です。緑が数えた目、赤は除外した部分です。"}
                  </p>
                </>
              ) : (
                <div className="tuning-start">
                  <p>映像を見ながら調整するには、認識を開始してください（ゲームの進行には影響しません）。</p>
                  <button disabled={working} onClick={() => void start()}>認識を開始</button>
                </div>
              )}
              <DiagnosticsView diagnostics={diagnostics} stateLabel={stateLabel} />
            </div>

            <div className="tuning-controls">
              <VlmSettingsSection running={running} />
              <fieldset>
                <legend>カメラ</legend>
                <label className="tuning-field">
                  <span className="tuning-label">使うカメラ</span>
                  <select value={draft.cameraId ?? ""}
                    onChange={(event) => change({ cameraId: event.target.value === "" ? null : Number(event.target.value) })}>
                    <option value="">未選択</option>
                    {cameras.map((camera) => <option key={camera.id} value={camera.id}>{camera.label}</option>)}
                    {draft.cameraId !== null && !cameras.some((camera) => camera.id === draft.cameraId) && (
                      <option value={draft.cameraId}>Camera {draft.cameraId}</option>
                    )}
                  </select>
                  <small>認識中に変えると、カメラをつなぎ直します。</small>
                </label>
                <label className="tuning-field">
                  <span className="tuning-label">解像度</span>
                  <select value={`${draft.cameraResolution.width}x${draft.cameraResolution.height}`}
                    onChange={(event) => {
                      const [width, height] = event.target.value.split("x").map(Number);
                      change({ cameraResolution: { width, height } });
                    }}>
                    {resolutions.map((item) => (
                      <option key={item.width} value={`${item.width}x${item.height}`}>{item.width}×{item.height}</option>
                    ))}
                  </select>
                </label>
                <AutoSlider label="露出" value={draft.exposure} fallback={-6} min={-13} max={0} step={0.5}
                  onChange={(exposure) => change({ exposure })}
                  help="自動をやめると明るさを固定できます。値の意味はカメラによって違い、対応していないカメラもあります。" />
              </fieldset>

              <fieldset>
                <legend>認識する範囲（枠）</legend>
                <label className="tuning-field check-row">
                  <input type="checkbox" checked={draft.trayDetection}
                    onChange={(event) => change({ trayDetection: event.target.checked })} />
                  トレーを自動で検出する（枠の中から探す）
                </label>
                <small className="block">
                  {draft.trayDetection
                    ? "枠の中で一番大きい暗い部分をトレーとして探し、その形（水色の線）の内側だけを認識と Claude に使います。トレーを動かしても、投擲待ちの間に形を探し直します。枠はトレー全体より少し大きめにしてください。"
                    : "オンにすると、枠の中からトレーの形を探して、机などの写り込みを除きます。"}
                </small>
                <div className="tuning-pair">
                  <Slider label="左端" value={draft.roi.x} min={0} max={0.97} step={0.01}
                    onChange={(x) => change({ roi: { x, width: Math.min(draft.roi.width, round(1 - x)) } })} />
                  <Slider label="上端" value={draft.roi.y} min={0} max={0.97} step={0.01}
                    onChange={(y) => change({ roi: { y, height: Math.min(draft.roi.height, round(1 - y)) } })} />
                  <Slider label="幅" value={draft.roi.width} min={0.03} max={round(1 - draft.roi.x)} step={0.01}
                    onChange={(width) => change({ roi: { width } })} />
                  <Slider label="高さ" value={draft.roi.height} min={0.03} max={round(1 - draft.roi.y)} step={0.01}
                    onChange={(height) => change({ roi: { height } })} />
                </div>
                <button className="secondary small"
                  onClick={() => change({ roi: { x: 0, y: 0, width: 1, height: 1 } })}>映像全体にする</button>
                <small className="block">サイコロを振るトレーの内側全体が入るようにします。端でサイコロが切れると読み間違えます。</small>
              </fieldset>

              <fieldset>
                <legend>画像のしきい値</legend>
                <AutoSlider label="外形しきい値" value={draft.whiteThreshold} fallback={200} min={0} max={255} step={1}
                  onChange={(whiteThreshold) => change({ whiteThreshold })}
                  help="これより明るい部分をサイコロの面として扱います。サイコロが見つからない時は下げ、背景まで白くなる時は上げます。" />
                <AutoSlider label="目しきい値" value={draft.pipThreshold} fallback={200} min={0} max={255} step={1}
                  onChange={(pipThreshold) => change({ pipThreshold })}
                  help="面の中でこれより暗い部分を目として数えます。目を数え落とす時は上げ、影を目と数える時は下げます。" />
              </fieldset>

              <fieldset>
                <legend>判定</legend>
                <Slider label="静止と判断するフレーム数" value={draft.stationaryFrames} min={1} max={15} step={1}
                  suffix={seconds(draft.stationaryFrames)}
                  onChange={(stationaryFrames) => change({ stationaryFrames })}
                  help="少ないほど早く確定しますが、転がり終わる前に読む危険が増えます。" />
                <Slider label="同じ目が続くフレーム数" value={draft.consecutiveValueFrames} min={1} max={5} step={1}
                  onChange={(consecutiveValueFrames) => change({ consecutiveValueFrames })} />
                <Slider label="自動確定の信頼度" value={draft.automaticThreshold} min={0.5} max={1} step={0.01}
                  onChange={(automaticThreshold) => change({
                    automaticThreshold,
                    confirmationThreshold: Math.min(draft.confirmationThreshold, automaticThreshold)
                  })}
                  help="これ以上なら自動で確定します。上げると誤確定は減り、確認が増えます。" />
                <Slider label="確認を求める信頼度" value={draft.confirmationThreshold} min={0.5} max={draft.automaticThreshold} step={0.01}
                  onChange={(confirmationThreshold) => change({ confirmationThreshold })}
                  help="これ以上で自動確定未満なら、管理者画面で確認を求めます。" />
              </fieldset>

              <details>
                <summary>詳細な判定条件</summary>
                <Slider label="目の大きさのそろい具合（下限）" value={draft.minPipSizeConsistency} min={0} max={1} step={0.01}
                  onChange={(minPipSizeConsistency) => change({ minPipSizeConsistency })} />
                <Slider label="1の目の中心からのずれ（上限）" value={draft.maxSinglePipCenterOffset} min={0} max={0.5} step={0.01}
                  onChange={(maxSinglePipCenterOffset) => change({ maxSinglePipCenterOffset })} />
                <Slider label="目の配置のずれ（上限）" value={draft.maxPipLayoutError} min={0} max={1} step={0.01}
                  onChange={(maxPipLayoutError) => change({ maxPipLayoutError })} />
                <Slider label="サイコロの形の信頼度（下限）" value={draft.minDetectorConfidence} min={0} max={1} step={0.01}
                  onChange={(minDetectorConfidence) => change({ minDetectorConfidence })} />
                <small className="block">条件を満たさない時は、自動確定せずに確認を求めます。</small>
              </details>
            </div>
          </div>
        )}
        <div className="button-row dialog-actions">
          <button className="secondary" disabled={working || !state}
            onClick={() => void act(() => revertVisionSettings("defaults"),
              "既定値に戻しました（まだ保存していません）", "既定値に戻せませんでした")}>既定値に戻す</button>
          <button className="secondary" disabled={working || !state?.unsaved || !state.saved}
            onClick={() => void act(() => revertVisionSettings("saved"),
              "保存した値に戻しました", "保存した値に戻せませんでした")}>保存した値に戻す</button>
          <button disabled={working || !state}
            onClick={() => void act(saveVisionSettings, "保存しました", "保存できませんでした")}>保存</button>
          <button className="secondary" disabled={working} onClick={() => void close()}>閉じる</button>
        </div>
      </div>
    </div>
  );
}
