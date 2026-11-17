"""Tk live preview, tuning, and recording tool for physical dice recognition."""

from __future__ import annotations

import tkinter as tk
from functools import partial
from pathlib import Path
from tkinter import ttk
from typing import Any

import cv2
from PIL import Image, ImageTk

from commentator.services.vision.camera import (
    CameraConnectionError,
    CameraDisconnectedError,
)
from commentator.services.vision.recognition import (
    ConfidenceBreakdown,
    RecognitionSnapshot,
)
from commentator.services.vision.settings import (
    NormalizedRoi,
    VisionSettings,
    VisionSettingsStore,
)
from commentator.tools.vision_bench_core import BenchFrame, VisionBenchEngine

STATE_LABELS = {
    "waiting": "待機",
    "moving": "移動中",
    "stationary": "静止",
    "candidate": "候補判定",
    "confirmed": "確定",
    "needs_confirmation": "確認待ち",
    "removal_wait": "除去待ち",
    "recognition_error": "認識エラー",
    "camera_error": "カメラエラー",
}



def _merge_status(confidence: ConfidenceBreakdown) -> str:
    if not confidence.pip_merge_suspected:
        return "なし"
    return "あり→再計数済み" if confidence.pip_recounted else "あり→確認"

class VisionBenchApp:
    def __init__(self, root: tk.Tk, engine: VisionBenchEngine) -> None:
        self.root = root
        self.engine = engine
        self.root.title("サイコロ認識 ライブプレビュー")
        self.root.geometry("1500x950")
        self.root.minsize(1100, 760)
        self._photo_images: dict[str, ImageTk.PhotoImage] = {}
        self._settings_job: str | None = None
        self._normalizing_settings = False

        settings = engine.settings
        self.camera_var = tk.StringVar(value=str(settings.camera_id or 0))
        self.resolution_var = tk.StringVar(
            value=f"{settings.camera_width}x{settings.camera_height}"
        )
        self.auto_exposure_var = tk.BooleanVar(value=settings.exposure is None)
        self.exposure_var = tk.DoubleVar(
            value=-6.0 if settings.exposure is None else settings.exposure
        )
        self.auto_threshold_var = tk.BooleanVar(value=settings.white_threshold is None)
        self.threshold_var = tk.DoubleVar(
            value=float(
                230 if settings.white_threshold is None else settings.white_threshold
            )
        )
        self.auto_pip_threshold_var = tk.BooleanVar(value=settings.pip_threshold is None)
        self.pip_threshold_var = tk.DoubleVar(
            value=float(235 if settings.pip_threshold is None else settings.pip_threshold)
        )
        self.roi_x_var = tk.DoubleVar(value=settings.roi.x)
        self.roi_y_var = tk.DoubleVar(value=settings.roi.y)
        self.roi_width_var = tk.DoubleVar(value=settings.roi.width)
        self.roi_height_var = tk.DoubleVar(value=settings.roi.height)
        self.automatic_threshold_var = tk.DoubleVar(
            value=settings.automatic_threshold
        )
        self.confirmation_threshold_var = tk.DoubleVar(
            value=settings.confirmation_threshold
        )
        self.stationary_frames_var = tk.DoubleVar(
            value=float(settings.stationary_frames)
        )
        self.consecutive_value_frames_var = tk.DoubleVar(
            value=float(settings.consecutive_value_frames)
        )
        self.min_pip_size_consistency_var = tk.DoubleVar(
            value=settings.min_pip_size_consistency
        )
        self.max_single_pip_center_offset_var = tk.DoubleVar(
            value=settings.max_single_pip_center_offset
        )
        self.max_pip_layout_error_var = tk.DoubleVar(
            value=settings.max_pip_layout_error
        )
        self.recognition_apply_var = tk.StringVar(value="認識設定: 反映済み")
        self.mask_visible_var = tk.BooleanVar(value=True)
        self.connection_var = tk.StringVar(value="未接続")
        self.message_var = tk.StringVar(value="カメラを選び、接続してください")
        self.recording_var = tk.StringVar(value="録画停止中")
        self.counter_var = tk.StringVar(
            value="今回の録画  投擲: 0  自動確定: 0  確認候補: 0  正解入力: 0  未入力: 0"
        )
        self.state_var = tk.StringVar(value="状態: —")
        self.metrics_var = tk.StringVar(value="候補: —\n信頼度: —\nmotionScore: —\nFPS: —")
        self.history_var = tk.StringVar(value="まだ判断はありません")

        self._build_ui()
        self._bind_settings()
        self.root.bind("<KeyPress>", self._key_pressed)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(30, self._tick)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.configure("Status.TLabel", font=("TkDefaultFont", 12, "bold"))
        style.configure("State.TLabel", font=("TkDefaultFont", 18, "bold"))

        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="カメラ").pack(side=tk.LEFT)
        self.camera_combo = ttk.Combobox(
            toolbar, textvariable=self.camera_var, values=("0",), width=7
        )
        self.camera_combo.pack(side=tk.LEFT, padx=(4, 8))
        ttk.Button(toolbar, text="一覧更新", command=self._refresh_cameras).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="接続 / 再接続", command=self._connect).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(toolbar, text="設定保存", command=self._save_settings).pack(side=tk.LEFT)
        ttk.Label(toolbar, textvariable=self.connection_var, style="Status.TLabel").pack(
            side=tk.LEFT, padx=14
        )
        self.record_button = ttk.Button(
            toolbar, text="● 録画開始", command=self._toggle_recording
        )
        self.record_button.pack(side=tk.RIGHT)
        ttk.Label(toolbar, textvariable=self.recording_var).pack(side=tk.RIGHT, padx=12)

        body = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8)
        preview_column = ttk.Frame(body)
        side_column = ttk.Frame(body, width=330)
        body.add(preview_column, weight=4)
        body.add(side_column, weight=1)

        full_box = ttk.LabelFrame(preview_column, text="全体ライブ映像（黄色: ROI）")
        full_box.pack(fill=tk.BOTH, expand=True)
        self.full_label = ttk.Label(full_box, anchor=tk.CENTER, text="カメラ未接続")
        self.full_label.pack(fill=tk.BOTH, expand=True)

        detail = ttk.Panedwindow(preview_column, orient=tk.HORIZONTAL)
        detail.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        roi_box = ttk.LabelFrame(detail, text="ROI拡大（緑: 検出 / 赤: 却下）")
        mask_box = ttk.LabelFrame(detail, text="外形マスク（白しきい値）")
        pip_box = ttk.LabelFrame(
            detail, text="目の計数（補正後・緑: 採用 / 赤: 却下）"
        )
        detail.add(roi_box, weight=1)
        detail.add(mask_box, weight=1)
        detail.add(pip_box, weight=1)
        self.roi_label = ttk.Label(roi_box, anchor=tk.CENTER, text="—")
        self.roi_label.pack(fill=tk.BOTH, expand=True)
        self.mask_label = ttk.Label(mask_box, anchor=tk.CENTER, text="—")
        self.mask_label.pack(fill=tk.BOTH, expand=True)
        self.pip_label = ttk.Label(pip_box, anchor=tk.CENTER, text="—")
        self.pip_label.pack(fill=tk.BOTH, expand=True)

        state_box = ttk.LabelFrame(side_column, text="認識状態", padding=10)
        state_box.pack(fill=tk.X)
        ttk.Label(
            state_box,
            textvariable=self.counter_var,
            justify=tk.LEFT,
            font=("TkDefaultFont", 12, "bold"),
            foreground="#075985",
            wraplength=300,
        ).pack(anchor=tk.W, pady=(0, 10))
        ttk.Label(state_box, textvariable=self.state_var, style="State.TLabel").pack(
            anchor=tk.W
        )
        ttk.Label(
            state_box, textvariable=self.metrics_var, justify=tk.LEFT, font=("Menlo", 11)
        ).pack(anchor=tk.W, pady=(8, 0))

        history_box = ttk.LabelFrame(side_column, text="直近の判断・正解入力", padding=10)
        history_box.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        ttk.Label(history_box, textvariable=self.history_var, justify=tk.LEFT).pack(
            anchor=tk.NW
        )

        controls = ttk.LabelFrame(self.root, text="ライブ調整", padding=8)
        controls.pack(fill=tk.X, padx=8, pady=8)
        top_controls = ttk.Frame(controls)
        top_controls.pack(fill=tk.X)
        ttk.Label(top_controls, text="解像度").pack(side=tk.LEFT)
        ttk.Combobox(
            top_controls,
            textvariable=self.resolution_var,
            values=("1920x1080", "1280x720", "640x480"),
            state="readonly",
            width=12,
        ).pack(side=tk.LEFT, padx=(4, 16))
        ttk.Checkbutton(
            top_controls, text="自動露出", variable=self.auto_exposure_var
        ).pack(side=tk.LEFT)

        self.exposure_scale = ttk.Scale(
            top_controls,
            from_=-13.0,
            to=0.0,
            variable=self.exposure_var,
            orient=tk.HORIZONTAL,
            length=140,
        )
        self.exposure_scale.pack(side=tk.LEFT, padx=(4, 4))
        if self.auto_exposure_var.get():
            self.exposure_scale.configure(state=tk.DISABLED)
        self.exposure_label = ttk.Label(
            top_controls, text=f"{self.exposure_var.get():.1f}", width=5
        )
        self.exposure_label.pack(side=tk.LEFT, padx=(0, 16))
        ttk.Checkbutton(
            top_controls, text="白しきい値を自動", variable=self.auto_threshold_var
        ).pack(side=tk.LEFT)
        self.threshold_scale = ttk.Scale(
            top_controls,
            from_=0,
            to=255,
            variable=self.threshold_var,
            orient=tk.HORIZONTAL,
            length=180,
        )
        self.threshold_scale.pack(side=tk.LEFT, padx=6)
        self.threshold_label = ttk.Label(
            top_controls,
            text=f"設定 {round(self.threshold_var.get())}",
            width=18,
        )
        self.threshold_label.pack(side=tk.LEFT)
        if self.auto_threshold_var.get():
            self.threshold_scale.configure(state=tk.DISABLED)
        ttk.Checkbutton(
            top_controls, text="マスク表示", variable=self.mask_visible_var
        ).pack(side=tk.RIGHT)

        pip_controls = ttk.Frame(controls)
        pip_controls.pack(fill=tk.X, pady=(8, 0))
        ttk.Checkbutton(
            pip_controls,
            text="目しきい値を自動（面ごとの大津法）",
            variable=self.auto_pip_threshold_var,
        ).pack(side=tk.LEFT)
        self.pip_threshold_scale = ttk.Scale(
            pip_controls,
            from_=0,
            to=255,
            variable=self.pip_threshold_var,
            orient=tk.HORIZONTAL,
            length=260,
        )
        self.pip_threshold_scale.pack(side=tk.LEFT, padx=6)
        self.pip_threshold_label = ttk.Label(
            pip_controls,
            text=f"設定 {round(self.pip_threshold_var.get())}",
            width=30,
        )
        self.pip_threshold_label.pack(side=tk.LEFT)
        if self.auto_pip_threshold_var.get():
            self.pip_threshold_scale.configure(state=tk.DISABLED)

        roi_controls = ttk.Frame(controls)
        roi_controls.pack(fill=tk.X, pady=(8, 0))
        for label, variable in (
            ("ROI X", self.roi_x_var),
            ("Y", self.roi_y_var),
            ("幅", self.roi_width_var),
            ("高さ", self.roi_height_var),
        ):
            ttk.Label(roi_controls, text=label).pack(side=tk.LEFT)
            ttk.Scale(
                roi_controls,
                from_=0.0 if label in {"ROI X", "Y"} else 0.05,
                to=1.0,
                variable=variable,
                orient=tk.HORIZONTAL,
                length=170,
            ).pack(side=tk.LEFT, padx=(3, 10))

        recognition_controls = ttk.Frame(controls)
        recognition_controls.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(recognition_controls, text="自動確定しきい値").pack(side=tk.LEFT)
        ttk.Scale(
            recognition_controls,
            from_=0.50,
            to=1.00,
            variable=self.automatic_threshold_var,
            orient=tk.HORIZONTAL,
            length=130,
        ).pack(side=tk.LEFT, padx=(3, 3))
        self.automatic_threshold_label = ttk.Label(
            recognition_controls, width=5
        )
        self.automatic_threshold_label.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(recognition_controls, text="確認依頼しきい値").pack(side=tk.LEFT)
        ttk.Scale(
            recognition_controls,
            from_=0.50,
            to=1.00,
            variable=self.confirmation_threshold_var,
            orient=tk.HORIZONTAL,
            length=130,
        ).pack(side=tk.LEFT, padx=(3, 3))
        self.confirmation_threshold_label = ttk.Label(
            recognition_controls, width=5
        )
        self.confirmation_threshold_label.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(
            recognition_controls,
            textvariable=self.recognition_apply_var,
            foreground="#075985",
        ).pack(side=tk.LEFT)

        quality_controls = ttk.Frame(controls)
        quality_controls.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(quality_controls, text="目サイズ均一性の最小値").pack(
            side=tk.LEFT
        )
        ttk.Scale(
            quality_controls,
            from_=0.0,
            to=1.0,
            variable=self.min_pip_size_consistency_var,
            orient=tk.HORIZONTAL,
            length=180,
        ).pack(side=tk.LEFT, padx=(3, 3))
        self.min_pip_size_consistency_label = ttk.Label(
            quality_controls, width=5
        )
        self.min_pip_size_consistency_label.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(
            quality_controls,
            text="採用した目の最小面積 ÷ 最大面積（設定値未満は確認依頼）",
        ).pack(side=tk.LEFT)

        layout_controls = ttk.Frame(controls)
        layout_controls.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(layout_controls, text="1の目の中心距離上限").pack(side=tk.LEFT)
        ttk.Scale(
            layout_controls,
            from_=0.0,
            to=0.5,
            variable=self.max_single_pip_center_offset_var,
            orient=tk.HORIZONTAL,
            length=180,
        ).pack(side=tk.LEFT, padx=(3, 3))
        self.max_single_pip_center_offset_label = ttk.Label(
            layout_controls, width=5
        )
        self.max_single_pip_center_offset_label.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(
            layout_controls,
            text="補正面の一辺で正規化（設定値超過は確認依頼）",
        ).pack(side=tk.LEFT)

        arrangement_controls = ttk.Frame(controls)
        arrangement_controls.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(arrangement_controls, text="目の配置ずれ上限").pack(side=tk.LEFT)
        ttk.Scale(
            arrangement_controls,
            from_=0.0,
            to=0.5,
            variable=self.max_pip_layout_error_var,
            orient=tk.HORIZONTAL,
            length=180,
        ).pack(side=tk.LEFT, padx=(3, 3))
        self.max_pip_layout_error_label = ttk.Label(arrangement_controls, width=5)
        self.max_pip_layout_error_label.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(
            arrangement_controls,
            text="4〜6の目の標準配置からのずれ・目の間隔比（設定値超過は確認依頼）",
        ).pack(side=tk.LEFT)

        timing_controls = ttk.Frame(controls)
        timing_controls.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(timing_controls, text="静止判定フレーム数").pack(side=tk.LEFT)
        ttk.Scale(
            timing_controls,
            from_=1,
            to=15,
            variable=self.stationary_frames_var,
            orient=tk.HORIZONTAL,
            length=180,
        ).pack(side=tk.LEFT, padx=(3, 3))
        self.stationary_frames_label = ttk.Label(timing_controls, width=22)
        self.stationary_frames_label.pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(timing_controls, text="同値連続フレーム数").pack(side=tk.LEFT)
        ttk.Scale(
            timing_controls,
            from_=1,
            to=5,
            variable=self.consecutive_value_frames_var,
            orient=tk.HORIZONTAL,
            length=150,
        ).pack(side=tk.LEFT, padx=(3, 3))
        self.consecutive_value_frames_label = ttk.Label(
            timing_controls, width=22
        )
        self.consecutive_value_frames_label.pack(side=tk.LEFT)
        self._update_recognition_labels(0.0)

        labels = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        labels.pack(fill=tk.X)
        ttk.Label(labels, text="投擲の正解（キー1〜6）:").pack(side=tk.LEFT)
        for value in range(1, 7):
            ttk.Button(
                labels,
                text=str(value),
                width=5,
                command=partial(self._label_roll, value),
            ).pack(side=tk.LEFT, padx=3)
        ttk.Label(labels, textvariable=self.message_var).pack(side=tk.LEFT, padx=16)

    def _bind_settings(self) -> None:
        variables: tuple[tk.Variable, ...] = (
            self.camera_var,
            self.resolution_var,
            self.auto_exposure_var,
            self.exposure_var,
            self.auto_threshold_var,
            self.threshold_var,
            self.auto_pip_threshold_var,
            self.pip_threshold_var,
            self.roi_x_var,
            self.roi_y_var,
            self.roi_width_var,
            self.roi_height_var,
            self.automatic_threshold_var,
            self.confirmation_threshold_var,
            self.stationary_frames_var,
            self.consecutive_value_frames_var,
            self.min_pip_size_consistency_var,
            self.max_single_pip_center_offset_var,
            self.max_pip_layout_error_var,
        )
        for variable in variables:
            variable.trace_add("write", self._schedule_settings_update)

    def _schedule_settings_update(self, *_: object) -> None:
        if self._normalizing_settings:
            return
        self._normalizing_settings = True
        try:
            automatic = round(
                min(1.0, max(0.50, self.automatic_threshold_var.get())), 2
            )
            confirmation = round(
                min(automatic, max(0.50, self.confirmation_threshold_var.get())),
                2,
            )
            stationary = min(15, max(1, round(self.stationary_frames_var.get())))
            consecutive = min(
                5, max(1, round(self.consecutive_value_frames_var.get()))
            )
            pip_consistency = round(
                min(1.0, max(0.0, self.min_pip_size_consistency_var.get())), 2
            )
            pip_center_offset = round(
                min(0.5, max(0.0, self.max_single_pip_center_offset_var.get())),
                2,
            )
            self.automatic_threshold_var.set(automatic)
            self.confirmation_threshold_var.set(confirmation)
            self.stationary_frames_var.set(stationary)
            self.consecutive_value_frames_var.set(consecutive)
            self.min_pip_size_consistency_var.set(pip_consistency)
            self.max_single_pip_center_offset_var.set(pip_center_offset)
            self.max_pip_layout_error_var.set(
                round(min(0.5, max(0.0, self.max_pip_layout_error_var.get())), 2)
            )
        finally:
            self._normalizing_settings = False
        self.threshold_label.configure(text=f"設定 {round(self.threshold_var.get())}")
        self.threshold_scale.configure(
            state=tk.DISABLED if self.auto_threshold_var.get() else tk.NORMAL
        )
        self.pip_threshold_label.configure(
            text=f"設定 {round(self.pip_threshold_var.get())}"
        )
        self.pip_threshold_scale.configure(
            state=(
                tk.DISABLED if self.auto_pip_threshold_var.get() else tk.NORMAL
            )
        )
        self.exposure_label.configure(text=f"{self.exposure_var.get():.1f}")
        self.exposure_scale.configure(
            state=tk.DISABLED if self.auto_exposure_var.get() else tk.NORMAL
        )
        fps = self.engine.last_frame.fps if self.engine.last_frame is not None else 0.0
        self._update_recognition_labels(fps)
        if self._settings_job is not None:
            self.root.after_cancel(self._settings_job)
        self._settings_job = self.root.after(120, self._apply_settings)

    def _settings_from_form(self) -> VisionSettings:
        width_text, height_text = self.resolution_var.get().split("x", maxsplit=1)
        return VisionSettings(
            camera_id=int(self.camera_var.get()),
            roi=NormalizedRoi(
                x=round(self.roi_x_var.get(), 4),
                y=round(self.roi_y_var.get(), 4),
                width=round(self.roi_width_var.get(), 4),
                height=round(self.roi_height_var.get(), 4),
            ),
            white_threshold=(
                None
                if self.auto_threshold_var.get()
                else int(round(self.threshold_var.get()))
            ),
            pip_threshold=(
                None
                if self.auto_pip_threshold_var.get()
                else int(round(self.pip_threshold_var.get()))
            ),
            camera_width=int(width_text),
            camera_height=int(height_text),
            exposure=None if self.auto_exposure_var.get() else self.exposure_var.get(),
            automatic_threshold=round(self.automatic_threshold_var.get(), 2),
            confirmation_threshold=round(self.confirmation_threshold_var.get(), 2),
            stationary_frames=round(self.stationary_frames_var.get()),
            consecutive_value_frames=round(
                self.consecutive_value_frames_var.get()
            ),
            min_pip_size_consistency=round(
                self.min_pip_size_consistency_var.get(), 2
            ),
            max_single_pip_center_offset=round(
                self.max_single_pip_center_offset_var.get(), 2
            ),
            max_pip_layout_error=round(self.max_pip_layout_error_var.get(), 2),
        )

    def _apply_settings(self) -> bool:
        self._settings_job = None
        try:
            self.engine.update_settings(self._settings_from_form())
        except (ValueError, tk.TclError) as error:
            self.message_var.set(f"設定値を確認してください: {error}")
            return False
        if self.engine.recognition_settings_pending:
            self.message_var.set(
                "認識中のため、しきい値と待ち時間は次の待機または除去待ちで反映します（未保存）"
            )
        else:
            self.message_var.set("設定をライブ映像へ反映しました（未保存）")
        return True

    def _refresh_cameras(self) -> None:
        try:
            devices = self.engine.camera_provider.enumerate()
            values = tuple(str(device.id) for device in devices)
            self.camera_combo.configure(values=values or ("0",))
            self.message_var.set(
                f"カメラを{len(devices)}台検出しました" if devices else "カメラが見つかりません"
            )
        except Exception as error:
            self.message_var.set(f"カメラ一覧を取得できません: {error}")

    def _connect(self) -> None:
        if not self._apply_settings():
            return
        try:
            reported = self.engine.connect()
            self.connection_var.set(f"接続中: Camera {self.engine.settings.camera_id or 0}")
            if reported and not all(
                bool(reported.get(key, True))
                for key in (
                    "widthSet",
                    "heightSet",
                    "autoExposureSet",
                    "exposureSet",
                )
            ):
                self.message_var.set("接続しました。一部のカメラ設定は機器が対応していません")
            else:
                self.message_var.set("カメラへ接続しました")
        except (CameraConnectionError, ValueError, cv2.error) as error:
            self._camera_error(str(error))

    def _save_settings(self) -> None:
        if not self._apply_settings():
            return
        try:
            self.engine.save_settings()
            self.message_var.set("var/vision-settings.json へ設定を保存しました")
        except OSError as error:
            self.message_var.set(f"設定を保存できません: {error}")

    def _toggle_recording(self) -> None:
        try:
            if self.engine.recorder.active:
                paths = self.engine.stop_recording()
                self.record_button.configure(text="● 録画開始")
                self.recording_var.set("録画停止中")
                suffix = (
                    f"（未ラベル {self.engine.recorder.unlabeled_rolls}件）"
                    if self.engine.recorder.unlabeled_rolls else ""
                )
                self.message_var.set(f"録画を保存しました: {paths.video.parent}{suffix}")
                self._update_counters()
            else:
                paths = self.engine.start_recording()
                self.record_button.configure(text="■ 録画停止")
                self.recording_var.set("● 録画中")
                self.message_var.set(f"録画中: {paths.video.name}")
                self._update_counters()
        except (OSError, ValueError) as error:
            self.message_var.set(f"録画操作に失敗しました: {error}")

    def _label_roll(self, value: int) -> None:
        try:
            self.engine.label_current(value)
            self.message_var.set(f"現在の投擲の正解を {value} にしました")
            self._update_history()
            self._update_counters()
        except ValueError as error:
            self.message_var.set(str(error))

    def _key_pressed(self, event: tk.Event[Any]) -> None:
        if event.char in "123456" and self.engine.recorder.active:
            focused = self.root.focus_get()
            if not isinstance(focused, (tk.Entry, ttk.Entry, ttk.Combobox)):
                self._label_roll(int(event.char))

    def _tick(self) -> None:
        if self.engine.connected:
            try:
                frame = self.engine.read()
                self._show_frame(frame)
            except CameraDisconnectedError as error:
                self.engine.disconnect()
                self._camera_error(str(error))
            except (ValueError, cv2.error) as error:
                self.message_var.set(f"映像処理エラー: {error}")
        self.root.after(15, self._tick)

    def _show_frame(self, frame: BenchFrame) -> None:
        self._set_image("full", self.full_label, frame.full_preview)
        self._set_image("roi", self.roi_label, frame.roi_preview)
        if self.mask_visible_var.get():
            self._set_image("mask", self.mask_label, frame.mask)
            if frame.pip_debug_preview is None:
                self.pip_label.configure(image="", text="—")
            else:
                self._set_image("pip", self.pip_label, frame.pip_debug_preview)
        else:
            self.mask_label.configure(image="", text="マスク表示はオフです")
            self.pip_label.configure(image="", text="マスク表示はオフです")
        snapshot = frame.result.snapshot
        self.state_var.set(f"状態: {STATE_LABELS.get(snapshot.state.value, snapshot.state.value)}")
        self.metrics_var.set(self._metrics(snapshot, frame))
        self.recognition_apply_var.set(
            "認識設定: 次の待機／除去待ちで反映"
            if self.engine.recognition_settings_pending
            else "認識設定: 反映済み"
        )
        self._update_recognition_labels(frame.fps)
        applied_threshold = round(self.engine.detector.last_white_threshold)
        if self.auto_threshold_var.get():
            self.threshold_label.configure(text=f"自動 / 適用 {applied_threshold}")
        else:
            self.threshold_label.configure(
                text=(
                    f"設定 {round(self.threshold_var.get())} / "
                    f"適用 {applied_threshold}"
                )
            )
        pip_debug = frame.result.pip_debug
        applied_pip_values = ", ".join(
            f"#{item.candidate_index}:{round(item.threshold_value)}"
            for item in pip_debug
        ) or "—"
        if self.auto_pip_threshold_var.get():
            self.pip_threshold_label.configure(
                text=f"自動 / 適用 {applied_pip_values}"
            )
        else:
            self.pip_threshold_label.configure(
                text=(
                    f"設定 {round(self.pip_threshold_var.get())} / "
                    f"適用 {applied_pip_values}"
                )
            )
        self._update_history()
        self._update_counters()

    def _set_image(self, key: str, label: ttk.Label, frame: Any) -> None:
        if frame.ndim == 2:
            image = Image.fromarray(frame, mode="L")
        else:
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        target_width = max(320, label.winfo_width())
        target_height = max(180, label.winfo_height())
        image.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image)
        self._photo_images[key] = photo
        label.configure(image=photo, text="")

    def _metrics(self, snapshot: RecognitionSnapshot, frame: BenchFrame) -> str:
        confidence = snapshot.confidence
        config = self.engine.active_recognition_config
        if confidence is None:
            breakdown = (
                "信頼度: — "
                f"(自動 ≥ {config.automatic_threshold:.2f} / "
                f"確認 ≥ {config.confirmation_threshold:.2f})\n"
                "  detector — / value —\n  still — / position —\n"
                f"  pip size — (確認 < {config.min_pip_size_consistency:.2f})\n"
                f"  pip center — (確認 > {config.max_single_pip_center_offset:.2f})\n"
                f"  pip layout — (確認 > {config.max_pip_layout_error:.2f})\n"
                "  pip merge —"
            )
        else:
            breakdown = (
                f"信頼度: {confidence.total:.3f} "
                f"(自動 ≥ {config.automatic_threshold:.2f} / "
                f"確認 ≥ {config.confirmation_threshold:.2f})\n"
                f"  detector {confidence.detector:.3f} / value {confidence.value_stability:.3f}\n"
                f"  still {confidence.stillness:.3f} / "
                f"position {confidence.position_stability:.3f}\n"
                f"  pip size {confidence.pip_size_consistency:.3f} "
                f"(確認 < {config.min_pip_size_consistency:.2f})\n"
                f"  pip center {confidence.single_pip_center_offset:.3f} "
                f"(確認 > {config.max_single_pip_center_offset:.2f})\n"
                f"  pip layout {confidence.pip_layout_error:.3f} "
                f"(確認 > {config.max_pip_layout_error:.2f})\n"
                f"  pip merge {_merge_status(confidence)}"
            )
        candidate = snapshot.candidate_value if snapshot.candidate_value is not None else "—"
        return (
            f"候補: {candidate}\n{breakdown}\n"
            f"motionScore: {frame.result.motion_score:.4f}\n"
            f"FPS: {frame.fps:.1f}\n"
            f"却下候補: {frame.result.rejected_count}"
        )

    def _update_recognition_labels(self, fps: float) -> None:
        automatic = round(self.automatic_threshold_var.get(), 2)
        confirmation = round(self.confirmation_threshold_var.get(), 2)
        stationary = round(self.stationary_frames_var.get())
        consecutive = round(self.consecutive_value_frames_var.get())
        self.automatic_threshold_label.configure(text=f"{automatic:.2f}")
        self.confirmation_threshold_label.configure(text=f"{confirmation:.2f}")
        self.min_pip_size_consistency_label.configure(
            text=f"{self.min_pip_size_consistency_var.get():.2f}"
        )
        self.max_single_pip_center_offset_label.configure(
            text=f"{self.max_single_pip_center_offset_var.get():.2f}"
        )
        self.max_pip_layout_error_label.configure(
            text=f"{self.max_pip_layout_error_var.get():.2f}"
        )
        self.stationary_frames_label.configure(
            text=self._frame_duration_label(stationary, fps)
        )
        self.consecutive_value_frames_label.configure(
            text=self._frame_duration_label(consecutive, fps)
        )

    @staticmethod
    def _frame_duration_label(frames: int, fps: float) -> str:
        duration = f"{frames / fps:.2f}秒" if fps > 0 else "—"
        return f"{frames}フレーム ≈ {duration}"

    def _update_history(self) -> None:
        self.history_var.set("\n".join(self.engine.history) or "まだ判断はありません")

    def _update_counters(self) -> None:
        recorder = self.engine.recorder
        self.counter_var.set(
            "今回の録画  "
            f"投擲: {recorder.rolls_started}  "
            f"自動確定: {recorder.automatic_confirmations}  "
            f"確認候補: {recorder.review_candidates}  "
            f"正解入力: {recorder.labeled_rolls}  "
            f"未入力: {recorder.rolls_without_label}"
        )

    def _camera_error(self, detail: str) -> None:
        self.connection_var.set("カメラエラー")
        self.message_var.set(
            f"{detail}。macOSのカメラ権限、他アプリの使用状況、USB接続を確認してください"
        )

    def _close(self) -> None:
        try:
            self.engine.close()
        finally:
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    engine = VisionBenchEngine(
        VisionSettingsStore(Path("var/vision-settings.json")),
        Path("recordings/dice-evaluation"),
    )
    VisionBenchApp(root, engine)
    root.mainloop()


if __name__ == "__main__":
    main()
