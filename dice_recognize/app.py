"""Tkinter GUI for live dice recognition."""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

from dice_detector import DiceDetector


class DiceRecognizerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("サイコロ出目認識 - OpenCV")
        self.root.geometry("1180x760")
        self.root.minsize(900, 620)
        self.root.configure(bg="#10151c")
        self.detector = DiceDetector()
        self.capture: cv2.VideoCapture | None = None
        self.running = False
        self.current_frame = None
        self.source_frame = None
        self.photo = None
        self.last_time = time.perf_counter()
        self.fps = 0.0

        self.status_var = tk.StringVar(value="画像、動画、またはカメラを選択してください")
        self.result_var = tk.StringVar(value="検出数: 0    合計: 0")
        self.detail_var = tk.StringVar(value="出目: —")
        self.parameter_vars: dict[str, tk.DoubleVar] = {}
        self.parameter_value_labels: dict[str, ttk.Label] = {}
        self.auto_threshold_var = tk.BooleanVar(value=True)
        self.white_threshold_var = tk.DoubleVar(value=180)
        self.show_mask_var = tk.BooleanVar(value=False)
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Toolbar.TFrame", background="#18212b")
        style.configure("TButton", padding=(12, 8), font=("TkDefaultFont", 11))
        style.configure("Title.TLabel", background="#10151c", foreground="#f2f5f8", font=("TkDefaultFont", 22, "bold"))
        style.configure("Result.TLabel", background="#152219", foreground="#66ef8b", font=("TkDefaultFont", 20, "bold"), padding=12)
        style.configure("Detail.TLabel", background="#18212b", foreground="#dbe7f3", font=("TkDefaultFont", 13), padding=10)
        style.configure("Status.TLabel", background="#0b0f14", foreground="#99a8b8", padding=8)

        header = ttk.Frame(self.root, style="Toolbar.TFrame", padding=12)
        header.pack(fill=tk.X)
        ttk.Button(header, text="画像を開く", command=self.open_image).pack(side=tk.LEFT, padx=4)
        ttk.Button(header, text="動画を開く", command=self.open_video).pack(side=tk.LEFT, padx=4)
        ttk.Button(header, text="カメラ開始", command=self.open_camera).pack(side=tk.LEFT, padx=4)
        ttk.Button(header, text="停止", command=self.stop).pack(side=tk.LEFT, padx=4)
        ttk.Button(header, text="結果を保存", command=self.save_result).pack(side=tk.LEFT, padx=(20, 4))
        ttk.Button(header, text="初期値に戻す", command=self.reset_parameters).pack(side=tk.RIGHT, padx=4)

        title = ttk.Label(self.root, text="OpenCV サイコロ出目認識", style="Title.TLabel")
        title.pack(anchor=tk.W, padx=18, pady=(14, 8))

        summary = ttk.Frame(self.root, style="Toolbar.TFrame")
        summary.pack(fill=tk.X, padx=18, pady=(0, 10))
        ttk.Label(summary, textvariable=self.result_var, style="Result.TLabel").pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(summary, textvariable=self.detail_var, style="Detail.TLabel").pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        content = ttk.Frame(self.root, style="Toolbar.TFrame")
        content.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 10))

        self.image_label = tk.Label(
            content, bg="#050709", fg="#8390a0", text="入力待ち", font=("TkDefaultFont", 18)
        )
        self.image_label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_parameter_panel(content)
        ttk.Label(self.root, textvariable=self.status_var, style="Status.TLabel").pack(fill=tk.X, side=tk.BOTTOM)

    def _build_parameter_panel(self, parent) -> None:
        panel = tk.Frame(parent, bg="#18212b", width=290, padx=14, pady=12)
        panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        panel.pack_propagate(False)
        tk.Label(
            panel, text="検出パラメータ", bg="#18212b", fg="#f2f5f8", font=("TkDefaultFont", 15, "bold")
        ).pack(anchor=tk.W, pady=(0, 4))
        tk.Label(
            panel,
            text="画像に合わせて調整できます",
            bg="#18212b",
            fg="#99a8b8",
            font=("TkDefaultFont", 10),
        ).pack(anchor=tk.W, pady=(0, 10))

        threshold_box = tk.Frame(panel, bg="#222f3d", padx=8, pady=7)
        threshold_box.pack(fill=tk.X, pady=(0, 8))
        ttk.Checkbutton(
            threshold_box,
            text="白領域しきい値を自動決定",
            variable=self.auto_threshold_var,
            command=self._threshold_mode_changed,
        ).pack(anchor=tk.W)
        threshold_row = tk.Frame(threshold_box, bg="#222f3d")
        threshold_row.pack(fill=tk.X, pady=(5, 0))
        tk.Label(
            threshold_row, text="明るさしきい値", bg="#222f3d", fg="#dbe7f3", font=("TkDefaultFont", 10)
        ).pack(side=tk.LEFT)
        self.white_threshold_label = ttk.Label(threshold_row, text="自動", width=9, anchor=tk.E)
        self.white_threshold_label.pack(side=tk.RIGHT)
        self.white_threshold_scale = ttk.Scale(
            threshold_box,
            from_=0,
            to=255,
            variable=self.white_threshold_var,
            command=self._white_threshold_changed,
        )
        self.white_threshold_scale.pack(fill=tk.X, pady=(2, 3))
        ttk.Checkbutton(
            threshold_box,
            text="二値化マスクを表示",
            variable=self.show_mask_var,
            command=self._preview_changed,
        ).pack(anchor=tk.W)
        self.white_threshold_scale.state(["disabled"])

        parameters = [
            ("min_die_area_ratio", "サイコロ最小面積 (%)", 0.05, 5.0, 0.30, 0.05, 100.0),
            ("max_die_area_ratio", "サイコロ最大面積 (%)", 5.0, 90.0, 45.0, 1.0, 100.0),
            ("max_die_aspect", "最大縦横比", 1.05, 2.0, 1.45, 0.05, 1.0),
            ("min_rectangularity", "四角形らしさ", 0.40, 0.95, 0.68, 0.01, 1.0),
            ("min_solidity", "輪郭の充実度", 0.50, 0.99, 0.88, 0.01, 1.0),
            ("min_pip_area_ratio", "黒点最小面積 (%)", 0.02, 2.0, 0.50, 0.02, 100.0),
            ("max_pip_area_ratio", "黒点最大面積 (%)", 1.0, 15.0, 7.50, 0.25, 100.0),
            ("min_pip_circularity", "黒点の円形度", 0.20, 0.90, 0.50, 0.02, 1.0),
        ]
        self.parameter_specs = parameters
        for key, label, minimum, maximum, default, resolution, divisor in parameters:
            row = tk.Frame(panel, bg="#18212b")
            row.pack(fill=tk.X, pady=(2, 0))
            tk.Label(row, text=label, bg="#18212b", fg="#dbe7f3", font=("TkDefaultFont", 10)).pack(side=tk.LEFT)
            value_label = ttk.Label(row, text="", width=6, anchor=tk.E)
            value_label.pack(side=tk.RIGHT)
            variable = tk.DoubleVar(value=default)
            self.parameter_vars[key] = variable
            self.parameter_value_labels[key] = value_label
            scale = ttk.Scale(
                panel,
                from_=minimum,
                to=maximum,
                variable=variable,
                command=lambda value, k=key, r=resolution, d=divisor: self._parameter_changed(k, value, r, d),
            )
            scale.pack(fill=tk.X, pady=(0, 5))
            self._update_parameter_label(key, default, resolution)

        tk.Label(
            panel,
            text="面積を下げると小さい対象を拾いやすく、\n形状値を下げると判定が緩くなります。",
            justify=tk.LEFT,
            bg="#18212b",
            fg="#7f91a3",
            font=("TkDefaultFont", 9),
        ).pack(anchor=tk.W, pady=(10, 0))

    def _threshold_mode_changed(self) -> None:
        if self.auto_threshold_var.get():
            self.detector.white_threshold = None
            self.white_threshold_scale.state(["disabled"])
            self.white_threshold_label.configure(text="自動")
        else:
            value = int(round(self.white_threshold_var.get()))
            self.detector.white_threshold = value
            self.white_threshold_scale.state(["!disabled"])
            self.white_threshold_label.configure(text=str(value))
        self._reprocess_still_image()

    def _white_threshold_changed(self, value: str) -> None:
        rounded = int(round(float(value)))
        if not self.auto_threshold_var.get():
            self.detector.white_threshold = rounded
            self.white_threshold_label.configure(text=str(rounded))
            self._reprocess_still_image()

    def _preview_changed(self) -> None:
        self._reprocess_still_image()

    def _reprocess_still_image(self) -> None:
        if self.source_frame is not None and not self.running:
            self.process_frame(self.source_frame, remember_source=False)

    def _parameter_changed(self, key: str, value: str, resolution: float, divisor: float) -> None:
        rounded = round(float(value) / resolution) * resolution
        setattr(self.detector, key, rounded / divisor)
        self._update_parameter_label(key, rounded, resolution)
        self._reprocess_still_image()

    def _update_parameter_label(self, key: str, value: float, resolution: float) -> None:
        decimals = 2 if resolution < 0.1 else (1 if resolution < 1 else 0)
        self.parameter_value_labels[key].configure(text=f"{value:.{decimals}f}")

    def reset_parameters(self) -> None:
        for key, _label, _minimum, _maximum, default, resolution, divisor in self.parameter_specs:
            self.parameter_vars[key].set(default)
            setattr(self.detector, key, default / divisor)
            self._update_parameter_label(key, default, resolution)
        self.auto_threshold_var.set(True)
        self.white_threshold_var.set(180)
        self.detector.white_threshold = None
        self.white_threshold_scale.state(["disabled"])
        self.white_threshold_label.configure(text="自動")
        self.show_mask_var.set(False)
        self._reprocess_still_image()
        self.status_var.set("検出パラメータを初期値に戻しました")

    def open_image(self) -> None:
        path = filedialog.askopenfilename(
            title="画像を選択", filetypes=[("画像", "*.png *.jpg *.jpeg *.bmp *.webp"), ("すべて", "*.*")]
        )
        if not path:
            return
        self.stop()
        frame = cv2.imread(path)
        if frame is None:
            messagebox.showerror("読み込みエラー", "画像を読み込めませんでした。")
            return
        self.status_var.set(f"画像: {path}")
        self.process_frame(frame)

    def open_video(self) -> None:
        path = filedialog.askopenfilename(
            title="動画を選択", filetypes=[("動画", "*.mp4 *.avi *.mov *.mkv *.m4v"), ("すべて", "*.*")]
        )
        if path:
            self._start_capture(path, f"動画: {path}")

    def open_camera(self) -> None:
        self._start_capture(0, "カメラ入力")

    def _start_capture(self, source, label: str) -> None:
        self.stop()
        capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            capture.release()
            messagebox.showerror("入力エラー", "映像ソースを開けませんでした。")
            return
        self.capture = capture
        self.running = True
        self.last_time = time.perf_counter()
        self.status_var.set(label)
        self._read_next()

    def _read_next(self) -> None:
        if not self.running or self.capture is None:
            return
        ok, frame = self.capture.read()
        if not ok:
            self.stop()
            self.status_var.set("映像の再生が終了しました")
            return
        now = time.perf_counter()
        elapsed = max(now - self.last_time, 1e-6)
        instant = 1.0 / elapsed
        self.fps = instant if self.fps == 0 else 0.9 * self.fps + 0.1 * instant
        self.last_time = now
        self.process_frame(frame)
        self.root.after(15, self._read_next)

    def process_frame(self, frame, remember_source: bool = True) -> None:
        if remember_source:
            self.source_frame = frame.copy()
        detections, mask = self.detector.detect(frame)
        annotated = self.detector.annotate(frame, detections)
        self.current_frame = annotated
        values = [die.value for die in detections]
        self.result_var.set(f"検出数: {len(values)}    合計: {sum(values)}")
        self.detail_var.set("出目: " + ("  /  ".join(map(str, values)) if values else "—"))
        if self.running:
            self.status_var.set(f"リアルタイム認識中    {self.fps:.1f} FPS")
        if self.auto_threshold_var.get():
            self.white_threshold_label.configure(text=f"自動 {self.detector.last_white_threshold:.0f}")
        display = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) if self.show_mask_var.get() else annotated
        if self.show_mask_var.get():
            display = self.detector.annotate(display, detections)
        self._show_frame(display)

    def _show_frame(self, frame) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        max_w = max(self.image_label.winfo_width() - 20, 320)
        max_h = max(self.image_label.winfo_height() - 20, 240)
        image.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(image)
        self.image_label.configure(image=self.photo, text="")

    def save_result(self) -> None:
        if self.current_frame is None:
            messagebox.showinfo("保存", "先に画像または映像を認識してください。")
            return
        path = filedialog.asksaveasfilename(
            title="検出結果を保存", defaultextension=".png", filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")]
        )
        if path and cv2.imwrite(path, self.current_frame):
            self.status_var.set(f"保存しました: {path}")
        elif path:
            messagebox.showerror("保存エラー", "画像を保存できませんでした。")

    def stop(self) -> None:
        self.running = False
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def close(self) -> None:
        self.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    DiceRecognizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
