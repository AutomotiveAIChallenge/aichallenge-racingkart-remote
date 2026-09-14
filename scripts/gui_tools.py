#!/usr/bin/env python3
from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import tkinter.font as tkfont

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

ROOT_DIR = Path(__file__).resolve().parent
REMOTE_DIR = ROOT_DIR
REPO_ROOT = ROOT_DIR.parent

# ランチャ自身の多重起動防止に使う (LN-15)。GUI が2枚開くと、互いの状態を知らないまま
# 同じ zenoh/joy/manager を起動できてしまう。
LAUNCHER_PID_FILE = REPO_ROOT / "output" / "gui-launcher.pid"
# `make remote` (run_remote.bash) が書く PID。setsid のセッションリーダーの PID で、
# そのままプロセスグループ ID でもある。GUI 側の起動・再起動はこれが生きている間
# 拒否する (LN-15)。
REMOTE_PID_FILE = REPO_ROOT / "output" / "remote.pid"

# 遠隔操作の対象にする実車。Zenoh と Manager は複数台をまとめて扱う。
VEHICLE_IDS = ["A2", "A3", "A4", "A6", "A7"]


# --- 多重起動防止まわりの純粋関数 (Tk に依存しないので pytest から直接叩ける) ---


def _read_pid_file(path: Path) -> Optional[int]:
    """PID ファイルを読む。無い・空・数値でない場合は None (stale 扱い)。"""
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    """単一プロセスとして生きているか。シグナルは送らない (sig=0)。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 別ユーザー所有などで確認できないだけで、存在はしている。
        return True
    except OSError:
        return False
    return True


def acquire_launcher_lock(path: Path) -> bool:
    """ランチャ GUI 自身の多重起動を防ぐ (LN-15)。

    既存の PID ファイルが生きたプロセスを指していれば False を返し、呼び出し側は
    起動を諦める。ファイルが無い・空・不正・死んだプロセスを指している (stale) 場合は
    上書きして自分の PID を書き、True を返す。`output/` が無ければ作る。
    """
    existing = _read_pid_file(path)
    if existing is not None and _pid_alive(existing):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n")
    return True


def release_launcher_lock(path: Path) -> None:
    """`acquire_launcher_lock` で書いた PID ファイルを消す。無ければ何もしない。"""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def remote_stack_pid(path: Path) -> Optional[int]:
    """`make remote` (run_remote.bash) のプロセスグループが生きていれば PID を返す。

    Makefile の remote-stop / ps と同じ判定 (`pgrep -g` 相当) を Python 側でも行う。
    setsid で起動しているので PID がそのままプロセスグループ ID になる。ファイルが
    無い・空・不正、またはグループが既に消えている場合は None (動いていないとみなす)。
    """
    pid = _read_pid_file(path)
    if pid is None:
        return None
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    except OSError:
        return None
    return pid


def command_touches_remote_component(command_text: str) -> bool:
    """レンダリング後のコマンドが `remote_component.bash` (zenoh/joy/manager) を
    起動するかどうか (LN-15)。
    """
    return "remote_component.bash" in command_text


# --- ウィンドウ ---
WINDOW_GEOMETRY = "1100x680"
WINDOW_MIN_SIZE = (900, 540)

# --- ログ処理まわりの定数 ---
# chatty な子プロセス (zenoh-bridge, joy, manager など) がログを高速に吐いても
# Tk のメインループを飢餓状態にしないための上限・予算値。
MAX_LOG_LINES = 2000  # 各ログウィジェットが保持する最大行数
LOG_QUEUE_MAXSIZE = 10000  # ログキューの上限。超えたら行を捨てる (producer は絶対にブロックしない)
POLL_BUDGET_SECONDS = 0.01  # 1回の _poll_log_queue にかける壁時計予算 (約10ms)
# 1回の _poll_log_queue で処理する最大件数。大きくすると1回の insert が重くなり、
# その間イベントループが止まる (= 体感のカクつき) ので、描画1回分に見合う量に抑える。
POLL_MAX_ITEMS = 400
POLL_INTERVAL_IDLE_MS = 100  # キューが空になった後の再スケジュール間隔
POLL_INTERVAL_BUSY_MS = 10  # キューにまだ残っている場合の再スケジュール間隔

# --- プロセス停止まわりの定数 ---
STOP_ESCALATE_INTERVAL_MS = 200  # SIGTERM 送信後、生存確認をポーリングする間隔
STOP_ESCALATE_TIMEOUT_MS = 3000  # この時間を過ぎても生きていたら SIGKILL に昇格


# --- Devias Kit Pro: Neon Blue / dark palette (approx) ---
# These values are offline approximations of the "Neon Blue" preset on the dark
# theme (neonBlue + neutral scales). Adjust here if you have exact tokens from
# the design kit.
PALETTE = {
    "bg": "#0B0F19",           # app background   (neutral 950)
    "surface": "#111927",      # cards / frames   (neutral 900)
    "surface_alt": "#1C2536",  # elevated surface (neutral 800)
    "text": "#EDF2F7",         # primary text
    "text_muted": "#9DA4AE",   # secondary text   (neutral 400)
    "border": "#2D3748",       # divider          (neutral 700)
    # Neon Blue core. On a dark ground the hover state gets *lighter*, not darker.
    "primary": "#635BFF",        # Neon Blue (main, neonBlue 500)
    "primary_hover": "#7578FF",  # hover  (neonBlue 400)
    "primary_active": "#4E36F5", # pressed (neonBlue 600)
    "primary_light": "#9CA7FF",  # readable accent text on dark (neonBlue 300)
    "primary_soft": "#1C1553",   # subtle surface tint (neonBlue 950)
    # Danger accents
    "danger": "#F04438",
    "danger_hover": "#F97066",
    "danger_active": "#B42318",
    # Status indicators
    "status_running": "#22C55E",
    "status_pending": "#F59E0B",
}


def apply_devias_theme(root: tk.Tk) -> tuple[ttk.Style, tkfont.Font]:
    """Apply a Devias-like Neon Blue dark theme using ttk.Style.

    This function sets the base theme to 'clam' for consistent styling and
    customizes widgets' colors, fonts, and padding. Buttons receive primary
    (neon blue), outline, and danger variants. Returns the style and the
    fixed-width font so callers can apply it to log widgets (ScrolledText 等)
    directly.
    """
    style = ttk.Style(root)
    # Ensure a predictable style base
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # Root background
    root.configure(bg=PALETTE["bg"])

    # Base fonts
    base_font = tkfont.nametofont("TkDefaultFont")
    base_font.configure(size=10)
    try:
        heading_font = tkfont.nametofont("TkHeadingFont")
        heading_font.configure(size=11, weight="bold")
    except Exception:
        heading_font = tkfont.Font(family=base_font.cget("family"), size=11, weight="bold")
    try:
        fixed_font = tkfont.nametofont("TkFixedFont")
        fixed_font.configure(size=10)
    except Exception:
        fixed_font = tkfont.Font(family="Monospace", size=10)

    # Frames and labels
    style.configure(
        "TFrame",
        background=PALETTE["bg"],
    )
    style.configure(
        "Card.TFrame",
        background=PALETTE["surface"],
        bordercolor=PALETTE["border"],
        relief="flat",
    )
    style.configure(
        "TLabel",
        background=PALETTE["bg"],
        foreground=PALETTE["text"],
        font=base_font,
    )
    style.configure(
        "Muted.TLabel",
        background=PALETTE["bg"],
        foreground=PALETTE["text_muted"],
        font=base_font,
    )
    style.configure(
        "Card.TLabel",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        font=base_font,
    )
    style.configure(
        "CardMuted.TLabel",
        background=PALETTE["surface"],
        foreground=PALETTE["text_muted"],
        font=base_font,
    )
    style.configure(
        "Header.TLabel",
        background=PALETTE["bg"],
        foreground=PALETTE["text"],
        font=heading_font,
    )
    style.configure(
        "TLabelframe",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        bordercolor=PALETTE["border"],
        relief="groove",
    )
    style.configure(
        "TLabelframe.Label",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        font=heading_font,
    )

    # Entry fields
    style.configure(
        "TEntry",
        fieldbackground=PALETTE["surface_alt"],
        background=PALETTE["surface_alt"],
        foreground=PALETTE["text"],
        insertcolor=PALETTE["text"],
        bordercolor=PALETTE["border"],
        lightcolor=PALETTE["primary"],
        darkcolor=PALETTE["border"],
        relief="flat",
        padding=6,
    )

    # Combobox (Vehicle ID). The drop-down list is a classic Tk Listbox, so it
    # needs option_add on top of the ttk style or it stays stubbornly light.
    style.configure(
        "TCombobox",
        fieldbackground=PALETTE["surface_alt"],
        background=PALETTE["surface_alt"],
        foreground=PALETTE["text"],
        insertcolor=PALETTE["text"],
        arrowcolor=PALETTE["text_muted"],
        bordercolor=PALETTE["border"],
        lightcolor=PALETTE["border"],
        darkcolor=PALETTE["border"],
        selectbackground=PALETTE["primary"],
        selectforeground="#FFFFFF",
        padding=5,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PALETTE["surface_alt"])],
        foreground=[("disabled", PALETTE["text_muted"])],
        arrowcolor=[("active", PALETTE["primary_light"])],
        bordercolor=[("focus", PALETTE["primary"])],
    )
    root.option_add("*TCombobox*Listbox.background", PALETTE["surface_alt"])
    root.option_add("*TCombobox*Listbox.foreground", PALETTE["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", PALETTE["primary"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")

    # Checkbutton (Autoscroll toggles above each log pane)
    style.configure(
        "Devias.TCheckbutton",
        background=PALETTE["surface"],
        foreground=PALETTE["text_muted"],
        focusthickness=0,
        indicatorbackground=PALETTE["surface_alt"],
        indicatorforeground=PALETTE["text"],
        bordercolor=PALETTE["border"],
        padding=2,
    )
    style.map(
        "Devias.TCheckbutton",
        background=[("active", PALETTE["surface"])],
        foreground=[("active", PALETTE["text"])],
        indicatorbackground=[
            ("selected", PALETTE["primary"]),
            ("active", PALETTE["border"]),
        ],
        indicatorforeground=[("selected", "#FFFFFF")],
        bordercolor=[("selected", PALETTE["primary"])],
    )

    # Scrollbars. ScrolledText embeds a ttk.Scrollbar, which keeps the light
    # 'clam' defaults unless the base style is overridden.
    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar",
            background=PALETTE["surface_alt"],
            troughcolor=PALETTE["bg"],
            bordercolor=PALETTE["border"],
            arrowcolor=PALETTE["text_muted"],
            lightcolor=PALETTE["surface_alt"],
            darkcolor=PALETTE["surface_alt"],
            relief="flat",
        )
        style.map(
            f"{orient}.TScrollbar",
            background=[("active", PALETTE["border"]), ("pressed", PALETTE["primary"])],
            arrowcolor=[("active", PALETTE["text"])],
        )

    # Buttons - Primary (solid green)
    style.configure(
        "DeviasPrimary.TButton",
        background=PALETTE["primary"],
        foreground="#FFFFFF",
        bordercolor=PALETTE["primary"],
        focusthickness=0,
        padding=(8, 4),
        relief="flat",
    )
    style.map(
        "DeviasPrimary.TButton",
        background=[
            ("active", PALETTE["primary_hover"]),
            ("pressed", PALETTE["primary_active"]),
            ("disabled", PALETTE["border"]),
        ],
        foreground=[("disabled", PALETTE["text_muted"])],
        bordercolor=[
            ("active", PALETTE["primary_hover"]),
            ("pressed", PALETTE["primary_active"]),
            ("disabled", PALETTE["border"]),
        ],
    )

    # Buttons - Outline (green outline on white)
    style.configure(
        "DeviasOutline.TButton",
        background=PALETTE["surface"],
        foreground=PALETTE["primary_light"],
        bordercolor=PALETTE["primary"],
        focusthickness=0,
        padding=(8, 4),
        relief="solid",
        borderwidth=1,
    )
    style.map(
        "DeviasOutline.TButton",
        background=[
            ("active", PALETTE["primary_soft"]),
            ("pressed", PALETTE["primary_soft"]),
        ],
        bordercolor=[
            ("active", PALETTE["primary"]),
            ("pressed", PALETTE["primary"]),
        ],
        foreground=[
            ("disabled", PALETTE["text_muted"]),
        ],
    )

    # Buttons - Danger (stop)
    style.configure(
        "DeviasDanger.TButton",
        background=PALETTE["danger"],
        foreground="#FFFFFF",
        bordercolor=PALETTE["danger"],
        focusthickness=0,
        padding=(8, 4),
        relief="flat",
    )
    style.map(
        "DeviasDanger.TButton",
        background=[
            ("active", PALETTE["danger_hover"]),
            ("pressed", PALETTE["danger_active"]),
            ("disabled", PALETTE["border"]),
        ],
        foreground=[("disabled", PALETTE["text_muted"])],
        bordercolor=[
            ("active", PALETTE["danger_hover"]),
            ("pressed", PALETTE["danger_active"]),
            ("disabled", PALETTE["border"]),
        ],
    )

    # Status indicators above each log pane
    for name, color in (
        ("Status.TLabel", PALETTE["text_muted"]),
        ("StatusRunning.TLabel", PALETTE["status_running"]),
        ("StatusPending.TLabel", PALETTE["status_pending"]),
    ):
        style.configure(name, background=PALETTE["surface"], foreground=color, font=base_font)

    # Separator
    style.configure("TSeparator", background=PALETTE["border"])

    return style, fixed_font

@dataclass
class CommandSpec:
    label: str
    command: str | None = None
    log_key: str | None = None
    requires_vehicles: bool = False
    stop_before: bool = False
    note: str | None = None
    kind: str = "command"  # command, stop, stop_all
    # UI 上の役割。ボタンの色と有効/無効の判定はこれだけを見る。表示ラベルの文字列を
    # 条件に使うと、文言を変えただけで挙動が壊れる。kind とは独立している点に注意。
    role: str = "start"  # start, stop, restart

    def render(self, vehicle_ids: List[str]) -> str:
        if self.kind != "command":
            return ""
        assert self.command is not None
        return self.command.format(vehicle_ids=" ".join(vehicle_ids))

COMMANDS: List[CommandSpec] = [
    CommandSpec(
        label="Start Zenoh",
        command=(
            'REMOTE_COMPONENT_STDIO=1 ./remote_component.bash zenoh '
            '../output/gui-launcher "{vehicle_ids}"'
        ),
        log_key="zenoh",
        requires_vehicles=True,
        note="選択した全車両の zenoh-bridge へ接続します。",
    ),
    CommandSpec(
        label="Stop Zenoh",
        role="stop",
        log_key="zenoh",
        kind="stop",
        note="GUI で起動した Zenoh プロセスを終了します (Ctrl+C 相当)。",
    ),
    CommandSpec(
        label="Restart Zenoh",
        role="restart",
        command=(
            'REMOTE_COMPONENT_STDIO=1 ./remote_component.bash zenoh '
            '../output/gui-launcher "{vehicle_ids}"'
        ),
        log_key="zenoh",
        requires_vehicles=True,
        stop_before=True,
        note="既存プロセス停止後、選択した全車両へ再接続します。",
    ),
    CommandSpec(
        label="Start Joy",
        command=(
            "REMOTE_COMPONENT_STDIO=1 ./remote_component.bash joy "
            "../output/gui-launcher"
        ),
        log_key="joy",
        note="ゲームパッドノードを起動します。",
    ),
    CommandSpec(
        label="Stop Joy",
        role="stop",
        log_key="joy",
        kind="stop",
        note="joy プロセスを停止します。GUI が追跡していない孤児ノードも対象です。",
    ),
    CommandSpec(
        label="Restart Joy",
        role="restart",
        command=(
            "REMOTE_COMPONENT_STDIO=1 ./remote_component.bash joy "
            "../output/gui-launcher"
        ),
        log_key="joy",
        stop_before=True,
        note="joy ノードを再起動します。",
    ),
    CommandSpec(
        label="Start Manager",
        command=(
            "REMOTE_COMPONENT_STDIO=1 ./remote_component.bash manager "
            "../output/gui-launcher {vehicle_ids}"
        ),
        log_key="manager",
        requires_vehicles=True,
        note="選択した全車両を操作する manager を起動します。",
    ),
    CommandSpec(
        label="Stop Manager",
        role="stop",
        log_key="manager",
        kind="stop",
        note="GUI で起動した manager プロセスを終了します (Ctrl+C 相当)。",
    ),
    CommandSpec(
        label="Restart Manager",
        role="restart",
        command=(
            "REMOTE_COMPONENT_STDIO=1 ./remote_component.bash manager "
            "../output/gui-launcher {vehicle_ids}"
        ),
        log_key="manager",
        requires_vehicles=True,
        stop_before=True,
        note="manager を再起動します。",
    ),
]

SPEC_MAP: Dict[str, CommandSpec] = {spec.label: spec for spec in COMMANDS}

# self.processes は GUI 自身が起動したプロセスしか把握できない。前回セッションの
# クラッシュや手動起動で残った同種プロセスは Stop/Restart を押しても消せず、
# Restart Joy が孤児に加えてもう1つ joy_node を起こしてしまう (RC13)。
# 巻き込み事故を避けるため、パターンをここで明示登録した log_key だけ pkill で掃除する。
ORPHAN_KILL_PATTERNS: Dict[str, str] = {
    # ros2 run はラッパーで、孤児になるのは別プロセスの実行ファイル。
    # argv[0] と実行ファイル名の境界を限定し、引数や似た名前には一致させない。
    "joy": r"^/[^[:space:]]*/lib/joy/joy_node([[:space:]]|$)",
}

# 孤児の消滅を待つ上限。pkill はシグナルを送るだけで終了を待たないので、消えるのを
# 見届けてから新プロセスを起こす (Restart は RC12 と同じ約束を守る)。
ORPHAN_REAP_TIMEOUT_S = 3.0
ORPHAN_REAP_INTERVAL_S = 0.1


def kill_orphan_pattern(log_key: str, timeout: float = 3.0) -> bool:
    """log_key に登録された pattern で pkill する。登録が無ければ何もしない。

    戻り値は「一致するプロセスを見つけて killed した (pkill の終了コード 0)」かどうか。
    Tk に依存しないのでテストしやすいよう、GUI クラスから切り出してある。
    """
    pattern = ORPHAN_KILL_PATTERNS.get(log_key)
    if pattern is None:
        return False
    result = subprocess.run(
        ["pkill", "-f", pattern],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode == 0


def wait_for_orphan_gone(
    log_key: str,
    timeout: float = ORPHAN_REAP_TIMEOUT_S,
    interval: float = ORPHAN_REAP_INTERVAL_S,
) -> bool:
    """pkill した孤児が実際に消えるまで待つ。消えたら True、粘ったら False。

    pkill は SIGTERM を送って即座に戻る。待たずに新プロセスを起こすと、一瞬とはいえ
    joy の publisher が2つ並ぶ。バックグラウンドスレッドから呼ぶ前提 (GUI は止めない)。
    """
    pattern = ORPHAN_KILL_PATTERNS.get(log_key)
    if pattern is None:
        return True
    deadline = time.monotonic() + timeout
    while True:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


COLUMN_LAYOUT = [
    ("Zenoh", ["Start Zenoh", "Stop Zenoh", "Restart Zenoh"]),
    ("Joy", ["Start Joy", "Stop Joy", "Restart Joy"]),
    ("Manager", ["Start Manager", "Stop Manager", "Restart Manager"]),
]

# フェーズごとのステータス表示 (テキスト, スタイル名)。
STATUS_BY_PHASE = {
    "pending": ("● waiting…", "StatusPending.TLabel"),
    "stopping": ("● stopping…", "StatusPending.TLabel"),
    "running": ("● running", "StatusRunning.TLabel"),
    "idle": ("● stopped", "Status.TLabel"),
}

# role ごとのボタンスタイル。ラベル文字列で分岐しないための対応表。
BUTTON_STYLE_BY_ROLE = {
    "start": "DeviasPrimary.TButton",
    "stop": "DeviasDanger.TButton",
    "restart": "DeviasOutline.TButton",
}

LOG_AREAS = {
    "zenoh": "Zenoh Log",
    "joy": "Joy Log",
    "manager": "Manager Log",
}

@dataclass
class _ProcessEntry:
    """起動世代 (token) 付きで Popen を保持する。

    Restart などで古いリーダースレッドが新しいプロセス登録後に終了しても、
    token が一致しない限り self.processes から新しいプロセスを消さないようにする (RC2)。
    """

    token: int
    process: subprocess.Popen[str]


class RemoteGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Remote Vehicle Helper")

        # Apply Devias-inspired theme before building UI
        self.style, self.fixed_font = apply_devias_theme(self.root)

        if not REMOTE_DIR.exists():
            messagebox.showerror(
                "Configuration error",
                f"Remote directory not found: {REMOTE_DIR}",
            )
            raise SystemExit(1)

        # 普段使う4台を既定で選ぶ。外した車両は Manager の「全台」と緊急停止の宛先からも外れる。
        self.vehicle_vars = {
            vehicle_id: tk.BooleanVar(value=True) for vehicle_id in VEHICLE_IDS
        }
        self.processes: Dict[str, _ProcessEntry] = {}
        # self.processes への「世代を見てから消す」操作はリーダースレッドとメイン
        # スレッドの双方から走る。get と pop の間に別スレッドが新しいプロセスを
        # 登録すると、世代チェックを通り抜けて新プロセスを消してしまうので排他する。
        self._processes_lock = threading.Lock()
        self.log_queue: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=LOG_QUEUE_MAXSIZE)
        self._log_dropped: Dict[str, int] = {}
        self._next_token = 0
        self._closing = False
        self.buttons: Dict[str, ttk.Button] = {}
        self.status_labels: Dict[str, ttk.Label] = {}
        self.autoscroll_vars: Dict[str, tk.BooleanVar] = {}
        # 適用済みの状態を Python 側に持つ。ttk の cget() は Tcl への往復で、
        # 100ms ごとに全ボタン分を問い合わせると flood 時の描画予算を食い潰す。
        # 予約中の _poll_log_queue の after id。閉じるときに取り消さないと、
        # destroy 済みの root 上でコールバックが発火して Tcl エラーが端末に出る。
        self._poll_after_id: Optional[str] = None
        self._button_state_cache: Dict[str, str] = {}
        self._status_cache: Dict[str, str] = {}

        self.root.geometry(WINDOW_GEOMETRY)
        self.root.minsize(*WINDOW_MIN_SIZE)
        # SIGINT/SIGTERM ハンドラが直接 Tk API を叩かず、ここにフラグだけ立てる (RC10)。
        # 実際の後始末は root.after で定期実行される _poll_log_queue 側から行う。
        self._pending_shutdown = False
        # Restart 系 (stop_before=True) で、旧プロセスの終了待ちの間 True になる (RC12)。
        # このフラグが立っている log_key の Start/Restart ボタンは _refresh_button_states で無効化する。
        self._pending_launch: Dict[str, bool] = {}
        # _poll_stop_escalation で SIGTERM/SIGKILL の生存確認をポーリング中のプロセス。
        # _on_close / _terminate_all がウィンドウを閉じる際、self.processes から既に
        # 取り除かれてしまった (停止処理の途中の) プロセスも確実に畳めるようにするための保険 (RC12)。
        self._escalating: Dict[str, List[subprocess.Popen[str]]] = {}

        self._build_ui()
        self._refresh_button_states()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_after_id = self.root.after(100, self._poll_log_queue)

    def _build_ui(self) -> None:
        # Top banner (subtle spacing)
        container = ttk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.Frame(container, style="TFrame")
        top_frame.pack(fill=tk.X, padx=10, pady=(10, 6))

        ttk.Label(top_frame, text="Vehicles:", style="TLabel").pack(side=tk.LEFT)
        for vehicle_id in VEHICLE_IDS:
            ttk.Checkbutton(
                top_frame,
                text=vehicle_id,
                variable=self.vehicle_vars[vehicle_id],
                style="Devias.TCheckbutton",
            ).pack(side=tk.LEFT, padx=(6, 0))

        self.stop_all_button = ttk.Button(
            top_frame,
            text="Stop All",
            command=self._handle_stop_all,
            width=12,
            style="DeviasDanger.TButton",
        )
        self.stop_all_button.pack(side=tk.RIGHT)

        # SSH User input removed per request.

        preview_frame = ttk.Frame(container, style="Card.TFrame")
        preview_frame.pack(fill=tk.X, padx=10, pady=(0, 6))

        self.directory_label = ttk.Label(preview_frame, text="Directory: -", style="CardMuted.TLabel")
        self.directory_label.pack(anchor=tk.W, padx=8, pady=(4, 0))
        self.command_label = ttk.Label(preview_frame, text="Command: -", style="Card.TLabel")
        self.command_label.pack(anchor=tk.W, padx=8)
        self.note_label = ttk.Label(preview_frame, text="Note: -", style="CardMuted.TLabel")
        self.note_label.pack(anchor=tk.W, padx=8, pady=(0, 4))

        button_container = ttk.Frame(container)
        button_container.pack(fill=tk.X, padx=10, pady=(0, 6))

        for col_idx, (label, buttons) in enumerate(COLUMN_LAYOUT):
            col_frame = ttk.Frame(button_container)
            col_frame.grid(row=0, column=col_idx, padx=6, pady=0, sticky=tk.NSEW)

            ttk.Label(col_frame, text=label, style="Header.TLabel").pack(pady=(0, 4))

            for btn_label in buttons:
                spec = SPEC_MAP[btn_label]
                btn_style = BUTTON_STYLE_BY_ROLE[spec.role]

                button = ttk.Button(
                    col_frame,
                    text=spec.label,
                    command=lambda s=spec: self._handle_command(s),
                    width=18,
                    style=btn_style,
                )
                button.pack(pady=2, fill=tk.X)
                self.buttons[spec.label] = button

        for i in range(len(COLUMN_LAYOUT)):
            button_container.columnconfigure(i, weight=1)

        logs_frame = ttk.Frame(container)
        logs_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.log_widgets: Dict[str, ScrolledText] = {}
        for idx, (key, title) in enumerate(LOG_AREAS.items()):
            frame = ttk.LabelFrame(logs_frame, text=title, style="TLabelframe")
            frame.grid(row=0, column=idx, padx=3, pady=0, sticky=tk.NSEW)
            logs_frame.columnconfigure(idx, weight=1)

            toolbar = ttk.Frame(frame, style="Card.TFrame")
            toolbar.pack(fill=tk.X, padx=4, pady=(0, 2))

            status = ttk.Label(toolbar, text="● stopped", style="Status.TLabel")
            status.pack(side=tk.LEFT)
            self.status_labels[key] = status

            ttk.Button(
                toolbar,
                text="Clear",
                width=6,
                style="DeviasOutline.TButton",
                command=lambda k=key: self._clear_log(k),
            ).pack(side=tk.RIGHT)

            autoscroll_var = tk.BooleanVar(value=True)
            self.autoscroll_vars[key] = autoscroll_var
            ttk.Checkbutton(
                toolbar,
                text="Autoscroll",
                variable=autoscroll_var,
                style="Devias.TCheckbutton",
            ).pack(side=tk.RIGHT, padx=(0, 8))

            text_widget = ScrolledText(
                frame,
                height=10,
                width=32,
                state=tk.DISABLED,
                # 折り返しは Tk の再レイアウトを重くする。ログが高速に流れると
                # 目に見えるカクつきになるので切って、横スクロールで読ませる。
                wrap=tk.NONE,
                background=PALETTE["surface"],
                foreground=PALETTE["text"],
                insertbackground=PALETTE["text"],
                selectbackground=PALETTE["primary"],
                selectforeground="#FFFFFF",
                borderwidth=1,
                relief="solid",
                font=self.fixed_font,
            )
            # ScrolledText が内蔵するのは ttk ではなくクラシックの Scrollbar/Frame なので、
            # ttk.Style ではダーク化されない。ここで直接指定する。
            text_widget.frame.configure(background=PALETTE["surface"])
            text_widget.vbar.configure(
                background=PALETTE["surface_alt"],
                activebackground=PALETTE["border"],
                troughcolor=PALETTE["bg"],
                highlightbackground=PALETTE["bg"],
                highlightcolor=PALETTE["bg"],
                borderwidth=0,
                width=12,
            )
            text_widget.pack(fill=tk.BOTH, expand=True)

            hbar = ttk.Scrollbar(
                frame, orient=tk.HORIZONTAL, command=text_widget.xview
            )
            text_widget.configure(xscrollcommand=hbar.set)
            hbar.pack(fill=tk.X)

            self.log_widgets[key] = text_widget

        logs_frame.rowconfigure(0, weight=1)

    def _handle_stop_single(self, log_key: str) -> None:
        # _process_running() で先に弾くと、GUI が追跡していない孤児 (RC13) は
        # 「動いていない」ことになって _stop_process 自体に届かず、Restart だけ
        # 掃除できて Stop では掃除できないという食い違いになる。追跡の有無に
        # 関わらず _stop_process に任せ、孤児の掃除も同じ経路を通す。
        self._append_log(log_key, '[stop requested]\n')
        self._stop_process(log_key)

    def _handle_command(self, spec: CommandSpec) -> None:
        vehicle_ids = [
            vehicle_id
            for vehicle_id in VEHICLE_IDS
            if self.vehicle_vars[vehicle_id].get()
        ]

        # 停止は log_key で追跡中のプロセスを畳むだけで車両設定を使わない。
        # 車両検証より先に処理し、設定にかかわらず必ず停止できるようにする。
        if spec.kind == "stop":
            if not spec.log_key:
                return
            self._update_preview(REMOTE_DIR, f"[Stop] {spec.log_key}", spec.note or "")
            self._handle_stop_single(spec.log_key)
            self._refresh_button_states()
            return

        if spec.requires_vehicles and not vehicle_ids:
            messagebox.showwarning("入力不足", "対象車両を1台以上選択してください。")
            return

        command_text = spec.render(vehicle_ids)

        # make remote (run_remote.bash) が生きている間は zenoh/joy/manager を起動・
        # 再起動させない (LN-15)。二重起動すると joy publisher が重複し、車両側は
        # どちらが本物か区別できない。
        if command_touches_remote_component(command_text):
            conflict_pid = remote_stack_pid(REMOTE_PID_FILE)
            if conflict_pid is not None:
                messagebox.showwarning(
                    "起動できません",
                    f"make remote が動いています (PID group {conflict_pid})。"
                    "先に make remote-stop してください。",
                )
                return

        working_dir = REMOTE_DIR
        note = spec.note or ""
        self._update_preview(working_dir, command_text, note)

        log_key = spec.log_key
        if log_key is None:
            return

        if spec.stop_before:
            # 旧プロセスの終了を待ってから新プロセスを起動する (RC12)。
            # メインスレッドはブロックしない: _stop_process は非ブロッキングで、
            # 実際の起動 (_launch) は終了確認後に _poll_stop_escalation からコールバックされる。
            self._pending_launch[log_key] = True
            self._append_log(log_key, "[restart: waiting for previous process to exit]\n")
            self._refresh_button_states()
            self._stop_process(
                log_key,
                on_terminated=lambda: self._launch(log_key, command_text, working_dir),
            )
            return

        if self._warn_if_running(log_key):
            return

        self._launch(log_key, command_text, working_dir)

    def _warn_if_running(self, log_key: str) -> bool:
        """既に実行中なら警告ダイアログを出して True を返す。起動系の入口はここを通す。"""
        if not self._process_running(log_key):
            return False
        messagebox.showinfo(
            "Process running",
            f"{LOG_AREAS.get(log_key, log_key)} でコマンドが実行中です。先に停止してください。",
        )
        self._refresh_button_states()
        return True

    def _launch(self, log_key: str, command_text: str, working_dir: Path) -> None:
        """実際に子プロセスを起動する。

        Restart 系 (stop_before=True) では、旧プロセスの終了を確認した後の
        コールバックとしてもここに来る (RC12)。ウィンドウを閉じた後 (`self._closing`)
        に呼ばれた場合は新プロセスを起動せずに no-op で抜ける。
        """
        # 遅延起動 (Restart) の場合、待っている間に Stop All / close で取り消されている
        # ことがある。取り消されていたら起動しない。
        was_pending = self._pending_launch.pop(log_key, None)
        if was_pending is False:
            self._refresh_button_states()
            return
        if self._closing:
            self._refresh_button_states()
            return

        # 通常はボタンが無効化されているので起きないはずだが、念のための保険。
        if self._warn_if_running(log_key):
            return

        try:
            process = subprocess.Popen(
                ["bash", "-lc", command_text],
                cwd=str(working_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                # 起動するのは bash -> スクリプト -> ros2 run -> 実体 の多段で、
                # ros2 run は joy_node を別プロセスとして起こす。専用のプロセス
                # グループに入れておかないと、停止時に親だけが死んで実体が孤児
                # として残り (親が systemd に引き取られる)、GUI から止められなく
                # なる。同じ理由で scripts/run_remote.bash もグループで畳んでいる。
                start_new_session=True,
            )
        except FileNotFoundError:
            messagebox.showerror("Command error", "bash が見つかりませんでした。")
            self._refresh_button_states()
            return
        except Exception as exc:  # pragma: no cover - defensive
            messagebox.showerror("Command error", str(exc))
            self._refresh_button_states()
            return

        self._next_token += 1
        token = self._next_token
        thread = threading.Thread(
            target=self._stream_output,
            args=(log_key, process, token),
            daemon=True,
        )
        with self._processes_lock:
            self.processes[log_key] = _ProcessEntry(token, process)
        thread.start()
        self._append_log(log_key, f"$ {command_text}\n")
        self._refresh_button_states()

    def _enqueue_log(self, log_key: str, line: str) -> None:
        """ログ1行 (または通知) をキューへ積む。キューが満杯でも絶対にブロックしない (RC1)。

        タイムスタンプは「GUI が受け取った時刻」なので、描画が遅れても実時刻がずれない
        よう、表示側ではなくここで付ける。
        """
        line = f"{time.strftime('%H:%M:%S')} {line}"
        try:
            self.log_queue.put_nowait((log_key, line))
        except queue.Full:
            # 通知をここで積もうとしても、満杯だからこそドロップしているので入らない。
            # カウントだけ増やし、報告は消費側 (_poll_log_queue) が行う。
            self._log_dropped[log_key] = self._log_dropped.get(log_key, 0) + 1

    def _stream_output(self, log_key: str, process: subprocess.Popen[str], token: int) -> None:
        assert process.stdout is not None
        try:
            for line in iter(process.stdout.readline, ""):
                self._enqueue_log(log_key, line)
        except ValueError:
            # _stop_process 側で stdout を close した直後などに readline が投げうる (RC5)
            pass
        finally:
            try:
                process.wait()
            except Exception:
                pass
            exit_msg = f"[process exited with code {process.returncode}]\n"
            self._enqueue_log(log_key, exit_msg)
            try:
                process.stdout.close()
            except Exception:
                pass
            # このスレッドが積んだ token と現在の登録が一致する場合のみ取り除く (RC2)。
            # 判定と削除は不可分に行う。
            with self._processes_lock:
                entry = self.processes.get(log_key)
                if entry is not None and entry.token == token:
                    del self.processes[log_key]

    def _stop_process(
        self, log_key: str, on_terminated: Optional[Callable[[], None]] = None
    ) -> None:
        """プロセスを (非ブロッキングで) 停止する。

        `on_terminated` を渡すと、プロセスの消滅を確認できた時点で (SIGTERM だけで
        済んだ場合は即座に、粘った場合は SIGKILL 後の消滅確認を経て) 呼び出す。
        Restart 系 (RC12) が「旧プロセスの終了後に新プロセスを起動する」ために使う。
        """
        with self._processes_lock:
            entry = self.processes.pop(log_key, None)
        if entry is None:
            # 追跡が無くても実機には孤児が残っているかもしれない (RC13)。「Stop」も
            # ここを通すので、追跡していないというだけで掃除をスキップしない。
            self._refresh_button_states()
            self._reap_orphan_async(log_key, on_terminated)
            return
        process = entry.process
        if process.poll() is None:
            self._signal_process_group(process, signal.SIGTERM)
            self._append_log(log_key, "[stop: SIGTERM sent]\n")
            # process.wait() はメインスレッドをブロックするので使わず、after で非同期に監視する (RC3)
            # 登録はボタン状態の更新より先に行う。順序を逆にすると、その瞬間だけ
            # 「実行中でも停止中でもない」と見えて Start が有効に戻ってしまう。
            self._escalating.setdefault(log_key, []).append(process)
            self._refresh_button_states()
            self.root.after(
                STOP_ESCALATE_INTERVAL_MS,
                lambda: self._poll_stop_escalation(log_key, process, time.monotonic(), on_terminated),
            )
        else:
            self._append_log(log_key, "[process terminated]\n")
            self._refresh_button_states()
            if on_terminated is not None:
                on_terminated()

    def _poll_stop_escalation(
        self,
        log_key: str,
        process: subprocess.Popen[str],
        start_time: float,
        on_terminated: Optional[Callable[[], None]] = None,
        sigkill_sent: bool = False,
    ) -> None:
        if process.poll() is not None:
            remaining = [p for p in self._escalating.get(log_key, []) if p is not process]
            if remaining:
                self._escalating[log_key] = remaining
            else:
                self._escalating.pop(log_key, None)
            self._append_log(log_key, "[process terminated]\n")
            self._refresh_button_states()
            if on_terminated is not None:
                on_terminated()
            return
        if not sigkill_sent and time.monotonic() - start_time >= STOP_ESCALATE_TIMEOUT_MS / 1000:
            self._signal_process_group(process, signal.SIGKILL)
            self._append_log(log_key, "[stop: SIGKILL sent]\n")
            sigkill_sent = True
        # SIGKILL を送っただけでは終了したとは限らない (uninterruptible sleep 等) ので、
        # 実際に poll() が None でなくなるまでポーリングを続けてから on_terminated を呼ぶ。
        try:
            self.root.after(
                STOP_ESCALATE_INTERVAL_MS,
                lambda: self._poll_stop_escalation(
                    log_key, process, start_time, on_terminated, sigkill_sent
                ),
            )
        except tk.TclError:
            # ウィンドウが既に破棄されている場合は諦める (_on_close / _terminate_all 側で後始末される)
            pass

    def _reap_orphan_async(
        self, log_key: str, on_terminated: Optional[Callable[[], None]] = None
    ) -> None:
        """GUI が追跡していない同種プロセスを別スレッドで掃除する (RC13)。

        対象は ORPHAN_KILL_PATTERNS に明示登録された log_key だけに限定し、
        無関係なプロセスを巻き込まない。pkill は環境によっては数百ms以上かかりうるので、
        _stop_process の「非ブロッキング」という前提を保つため Tk のメインスレッドでは
        呼ばず、結果は root.after 経由でメインスレッドへ戻す。
        """

        def worker() -> None:
            killed = False
            gone = True
            error: Optional[Exception] = None
            try:
                killed = kill_orphan_pattern(log_key)
                if killed:
                    # pkill はシグナルを送るだけ。消えるのを見届けてから on_terminated
                    # (Restart なら新プロセスの起動) を呼ぶ。
                    gone = wait_for_orphan_gone(log_key)
            except Exception as exc:  # pragma: no cover - defensive
                error = exc

            def finish() -> None:
                if error is not None:
                    self._append_log(log_key, f"[orphan cleanup failed: {error}]\n")
                elif killed:
                    pattern = ORPHAN_KILL_PATTERNS[log_key]
                    if gone:
                        self._append_log(
                            log_key,
                            f"[orphan cleanup: killed stray process matching {pattern!r}]\n",
                        )
                    else:
                        self._append_log(
                            log_key,
                            f"[orphan cleanup: stray process matching {pattern!r} "
                            "did not exit]\n",
                        )
                else:
                    self._append_log(log_key, "[no running process]\n")
                if on_terminated is not None:
                    on_terminated()

            try:
                self.root.after(0, finish)
            except tk.TclError:
                # ウィンドウが既に破棄されている場合は諦める (_on_close / _terminate_all 側で後始末される)
                pass

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _signal_process_group(process: subprocess.Popen[str], sig: int) -> None:
        """子孫ごと畳む。start_new_session=True で作ったグループに送る。"""
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError):
            # グループが既に消えている場合などは、直接の子だけに送って諦める。
            if sig == signal.SIGKILL:
                process.kill()
            else:
                process.terminate()

    def _process_running(self, log_key: str) -> bool:
        entry = self.processes.get(log_key)
        return entry is not None and entry.process.poll() is None

    def _process_phase(self, log_key: Optional[str]) -> str:
        """log_key の現在のフェーズを返す: pending / stopping / running / idle。

        ボタンの有効/無効とステータス表示の双方がこの1箇所を見る。優先順位を
        2箇所に書くと、片方だけ直したときに「ボタンは無効なのに running 表示」と
        いったズレが出る。
        """
        if log_key is None:
            return "idle"
        if self._pending_launch.get(log_key):
            return "pending"
        if self._escalating.get(log_key):
            return "stopping"
        if self._process_running(log_key):
            return "running"
        return "idle"

    def _refresh_button_states(self) -> None:
        """ボタンの有効/無効をプロセスのフェーズに同期する (RC6)。

        pending (起動待ち) と stopping (SIGKILL 昇格待ち) の間は、そのグループを
        一律無効にして誤操作とプロセスグループの重複を防ぐ。running 中は Start だけ
        無効、孤児の掃除に対応する Stop は idle でも有効。それ以外の
        "Stop X" (kind="stop") は未実行なら無効。
        値が変わらない限り configure しない (Tcl 往復を避けるため churn 防止)。
        """
        # フェーズは log_key ごとに1回だけ調べる (poll() はシステムコールなので、
        # ボタンごとに呼ぶと同じ log_key に対して何度も走ってしまう)。
        phase_by_key = {key: self._process_phase(key) for key in LOG_AREAS}
        for label, button in self.buttons.items():
            spec = SPEC_MAP[label]
            phase = phase_by_key.get(spec.log_key, "idle") if spec.log_key else "idle"
            if phase in ("pending", "stopping"):
                desired = tk.DISABLED
            elif spec.kind == "stop":
                # 孤児の掃除に対応する Stop は、追跡中の相手がいなくても使える。
                can_stop = phase == "running" or spec.log_key in ORPHAN_KILL_PATTERNS
                desired = tk.NORMAL if can_stop else tk.DISABLED
            elif spec.role in ("stop", "restart"):
                # stop/restart は GUI が追跡していないプロセス (孤児など) も畳めるので、
                # 実行中かどうかによらず常に有効。
                desired = tk.NORMAL
            else:
                desired = tk.DISABLED if phase != "idle" else tk.NORMAL
            if self._button_state_cache.get(label) != desired:
                self._button_state_cache[label] = desired
                button.configure(state=desired)

        stop_all = getattr(self, "stop_all_button", None)
        if stop_all is not None:
            # 停止処理中や起動待ちでも有効にしておく。無効にすると、ユーザーが後悔した
            # Restart を中断する手段が UI から無くなる。
            any_busy = any(phase != "idle" for phase in phase_by_key.values())
            desired = tk.NORMAL if any_busy else tk.DISABLED
            if self._button_state_cache.get("__stop_all__") != desired:
                self._button_state_cache["__stop_all__"] = desired
                stop_all.configure(state=desired)

        self._refresh_status_indicators(phase_by_key)

    def _refresh_status_indicators(self, phase_by_key: Dict[str, str]) -> None:
        """各ログペインの実行状態インジケータを更新する (RC14)。

        `_refresh_button_states` からフェーズのマップを受け取り、値が変わるときだけ
        configure する。比較には Tk へ問い合わせない Python 側のキャッシュを使う。
        """
        for log_key, label in self.status_labels.items():
            text, style_name = STATUS_BY_PHASE[phase_by_key.get(log_key, "idle")]
            if self._status_cache.get(log_key) != text:
                self._status_cache[log_key] = text
                label.configure(text=text, style=style_name)

    def _clear_log(self, log_key: str) -> None:
        widget = self.log_widgets.get(log_key)
        if not widget:
            return
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.configure(state=tk.DISABLED)

    def _handle_stop_all(self) -> None:
        """追跡中の全プロセスを停止する。ウィンドウは閉じない (RC14)。

        Restart の「旧プロセス終了待ち」中の log_key は self.processes から既に
        外れているので、それも取り消さないと「全部止めて」と言った直後に新しい
        プロセスが立ち上がってしまう。
        """
        self._cancel_pending_launches()
        # リーダースレッドが終了したプロセスを削除しても、走査中の辞書が変化しない
        # ようにロック中にスナップショットを取る。
        with self._processes_lock:
            entries = list(self.processes.items())
        for log_key, entry in entries:
            if entry.process.poll() is not None:
                continue
            self._append_log(log_key, "[stop all requested]\n")
            self._stop_process(log_key)
        self._refresh_button_states()

    def _cancel_pending_launches(self) -> None:
        """予約済みの遅延起動を取り消す。_launch 側はフラグが消えていれば起動しない。"""
        for log_key, pending in list(self._pending_launch.items()):
            if not pending:
                continue
            # キーごと消すと _launch 側で「遅延起動が取り消された」のか「そもそも
            # 直接起動なのか」を区別できないので、False を取り消し済みの印として残す。
            self._pending_launch[log_key] = False
            self._append_log(log_key, "[restart cancelled]\n")

    def _update_preview(self, working_dir: Path, command: str, note: str) -> None:
        self.directory_label.config(text=f"Directory: {working_dir}")
        self.command_label.config(text=f"Command: {command}")
        self.note_label.config(text=f"Note: {note}" if note else "Note: -")

    def _append_log(self, log_key: str, text: str) -> None:
        """バッチ化されたテキストを1回の insert でウィジェットに追記する (RC1)。"""
        widget = self.log_widgets.get(log_key)
        if not widget:
            return
        # 挿入前に「最下部までスクロールされているか」を判定しておく。
        # ユーザーが上にスクロールして読んでいる場合、勝手に末尾へ飛ばさない。
        # Autoscroll を OFF にしている場合は、最下部にいても追従しない (RC14)。
        autoscroll_var = self.autoscroll_vars.get(log_key)
        autoscroll_on = autoscroll_var.get() if autoscroll_var is not None else True
        was_at_bottom = autoscroll_on and widget.yview()[1] >= 0.999
        # どうせ直後に削られる分まで insert するのは無駄なので、バッチが上限を超えて
        # いる場合は末尾 MAX_LOG_LINES 行だけを入れる (flood 時の描画スパイク対策)。
        if text.count("\n") > MAX_LOG_LINES:
            text = "".join(text.splitlines(keepends=True)[-MAX_LOG_LINES:])
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, text)
        # 保持行数の上限を超えたら古い行から削除する
        line_count = int(widget.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            excess = line_count - MAX_LOG_LINES
            widget.delete("1.0", f"{excess + 1}.0")
        if was_at_bottom:
            widget.see(tk.END)
        widget.configure(state=tk.DISABLED)

    def _poll_log_queue(self) -> None:
        if self._closing:
            return
        if self._pending_shutdown:
            # シグナルハンドラが立てたフラグをここ (Tk のイベントループの中) で拾い、
            # ウィンドウを閉じたときと同じ後始末経路 (_on_close) に委譲する (RC10)。
            self._pending_shutdown = False
            self._on_close()
            return
        # 壁時計予算と件数上限の両方でキューを drain する。
        # 同じ log_key の連続する行は1回の _append_log 呼び出し (= 1回の insert) にまとめる。
        deadline = time.monotonic() + POLL_BUDGET_SECONDS
        batches: Dict[str, List[str]] = {}
        count = 0
        while count < POLL_MAX_ITEMS:
            try:
                log_key, line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            batches.setdefault(log_key, []).append(line)
            count += 1
            if time.monotonic() >= deadline:
                break
        # ドロップ数の報告は消費側で行う。producer 側から積もうとしても、満杯だから
        # こそドロップしているので通知自体が入らない。
        for log_key in list(self._log_dropped.keys()):
            dropped = self._log_dropped.pop(log_key, 0)
            if dropped:
                batches.setdefault(log_key, []).append(
                    f"{time.strftime('%H:%M:%S')} [{dropped} lines dropped]\n"
                )
        for log_key, lines in batches.items():
            self._append_log(log_key, "".join(lines))
        # プロセスが自然終了したケース (Stop を押していない) もここで拾ってボタン状態に反映する。
        # 値が変わらない限り configure しないので、100ms ごとに呼んでも負荷は無視できる。
        self._refresh_button_states()

        if self._closing:
            return
        # まだキューに残っている場合は次のイベントループを待たずに早めに再開する
        delay_ms = POLL_INTERVAL_BUSY_MS if not self.log_queue.empty() else POLL_INTERVAL_IDLE_MS
        try:
            self._poll_after_id = self.root.after(delay_ms, self._poll_log_queue)
        except tk.TclError:
            # ウィンドウが破棄済み
            pass

    def _collect_running_processes(self) -> List[subprocess.Popen[str]]:
        """追跡中の全プロセスへ SIGTERM を送り、self.processes を空にして生存プロセス一覧を返す。"""
        still_running: List[subprocess.Popen[str]] = []
        with self._processes_lock:
            entries = list(self.processes.values())
            self.processes.clear()
        for entry in entries:
            if entry is None:
                continue
            process = entry.process
            if process.poll() is None:
                self._signal_process_group(process, signal.SIGTERM)
                still_running.append(process)
        # RC12: Restart の「旧プロセス終了待ち」中は self.processes から既に外れているが、
        # まだ生きている可能性があるプロセスが self._escalating に残っている。
        # ここで回収しないと、ウィンドウを閉じたときにそれらだけ後始末されずに孤児化する。
        for log_key, processes in list(self._escalating.items()):
            self._escalating.pop(log_key, None)
            for process in processes:
                if process.poll() is None and process not in still_running:
                    # 既に SIGTERM 送信済みなので再送はしない。以降の SIGKILL 昇格判断は
                    # 呼び出し元 (_finish_close / _terminate_all) に委ねる。
                    still_running.append(process)
        return still_running

    def _terminate_all(self, blocking: bool) -> None:
        """追跡中の全プロセスを SIGTERM → (猶予後) SIGKILL で畳む (RC4/RC10)。

        blocking=False: ウィンドウを閉じる操作 (`_on_close`) 専用。イベントループがまだ
        生きているので `root.after` による非同期エスカレーションで待つ。
        blocking=True: `main()` の finally やシグナルハンドラ専用。この時点で mainloop は
        既に終了しており GUI は表示されていないため、短いブロッキング `wait()` を許容する。
        """
        still_running = self._collect_running_processes()
        if not blocking:
            self._finish_close(still_running, time.monotonic())
            return

        deadline = time.monotonic() + STOP_ESCALATE_TIMEOUT_MS / 1000
        for process in still_running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                pass
        for process in still_running:
            if process.poll() is None:
                self._signal_process_group(process, signal.SIGKILL)
        for process in still_running:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass

    def _on_close(self) -> None:
        """ウィンドウを閉じるときは、追跡中の全プロセスを SIGTERM → (猶予後) SIGKILL で畳んでから破棄する (RC4)。"""
        self._closing = True
        self._cancel_pending_launches()
        if self._poll_after_id is not None:
            try:
                self.root.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None
        try:
            # 閉じる操作をすぐ視覚的に反映する (RC11): 後始末の完了 (最大3秒) を待つ間もウィンドウを
            # 表示したまま操作を受け付けているように見せない。
            self.root.withdraw()
        except tk.TclError:
            pass
        self._terminate_all(blocking=False)

    def _finish_close(self, processes: List[subprocess.Popen[str]], start_time: float) -> None:
        alive = [p for p in processes if p.poll() is None]
        if alive and time.monotonic() - start_time < STOP_ESCALATE_TIMEOUT_MS / 1000:
            try:
                self.root.after(
                    STOP_ESCALATE_INTERVAL_MS,
                    lambda: self._finish_close(processes, start_time),
                )
                return
            except tk.TclError:
                pass
        for p in alive:
            self._signal_process_group(p, signal.SIGKILL)
        try:
            self.root.destroy()
        except tk.TclError:
            pass

def _show_startup_error(title: str, message: str) -> None:
    """RemoteGui を作る前に出すエラーダイアログ用の、最小限の Tk root。"""
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()


def main() -> None:
    # ランチャ GUI 自身の多重起動を防ぐ (LN-15)。2枚目は互いの状態を知らないまま
    # 同じ zenoh/joy/manager を起動できてしまうため、ここで弾く。
    existing_pid = _read_pid_file(LAUNCHER_PID_FILE)
    if not acquire_launcher_lock(LAUNCHER_PID_FILE):
        _show_startup_error(
            "起動できません",
            f"ランチャは既に起動しています (PID {existing_pid})。そちらを使ってください。",
        )
        raise SystemExit(1)

    try:
        root = tk.Tk()
        app = RemoteGui(root)

        def _handle_termination_signal(signum: int, frame: object) -> None:
            # シグナルハンドラの中で Tk API (root.quit() など) を直接叩くのは避け、
            # フラグを立てるだけにする。実際の後始末は root.after で常時回っている
            # _poll_log_queue がフラグを見て _on_close 経由で行う (RC10)。
            app._pending_shutdown = True

        signal.signal(signal.SIGINT, _handle_termination_signal)
        signal.signal(signal.SIGTERM, _handle_termination_signal)

        try:
            root.mainloop()
        except KeyboardInterrupt:
            # 端末からの Ctrl+C がシグナルハンドラより先に素通りしてきた場合の保険。
            app._closing = True
            try:
                root.withdraw()
            except tk.TclError:
                pass
        finally:
            # Ctrl+C (SIGINT)・SIGTERM・ウィンドウを閉じ忘れた異常系のいずれでも、
            # 子プロセスグループを確実に畳んでからプロセスを終了する (RC10)。
            # _on_close 経由の後始末が既に完了していれば self.processes は空なので、
            # ここは安全に no-op になる。
            app._terminate_all(blocking=True)
    finally:
        # RemoteGui.__init__ が SystemExit で抜けた場合も含め、確保したロックは必ず戻す。
        release_launcher_lock(LAUNCHER_PID_FILE)

if __name__ == "__main__":
    main()
