"""レース開始・終了の MQTT 通知。

ROS にも Tk にも依存しない。`mosquitto_pub` を別スレッドで叩くだけで、呼び出し側は
キューに積んで即座に戻る。joy のコールバックを外部 I/O で止めないため (RN-10)。

通知の成否は車両の操作に一切影響しない (RN-12)。ブローカに繋がらなくてもログに残す
だけで、joy の中継は続く。

仕様: docs/spec/race-notification.md
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

from racing_kart_manager_core import race_payload, race_topic

LOGGER = logging.getLogger("race_notifier")

#: publish 1回あたりの待ち時間。ブローカが無反応でもスレッドが詰まらないように切る。
PUBLISH_TIMEOUT_S = 10.0

#: 失敗したときの再試行 (RN-11)
MAX_RETRIES = 2
RETRY_DELAY_S = 1.0

#: 終了時にキューを吐き切るのを待つ時間。make remote-stop は TERM の 5 秒後に KILL
#: するので、それより短くする。待ちきれなければ諦める (RN-12)。
CLOSE_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class BrokerConfig:
    host: str
    port: int = 1883
    username: str = ""
    password: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.host)


def config_from_env(env=None) -> BrokerConfig:
    """.env から読む。認証情報はリポジトリに置かない (RN-01)。

    MQTT_HOST が空なら通知を無効にする。練習走行や .env を用意していない環境でも
    manager は起動できなければならない (RN-12)。
    """
    env = os.environ if env is None else env
    try:
        port = int(env.get("MQTT_PORT") or 1883)
    except ValueError:
        LOGGER.warning("MQTT_PORT を解釈できません。1883 を使います。")
        port = 1883
    return BrokerConfig(
        host=(env.get("MQTT_HOST") or "").strip(),
        port=port,
        username=(env.get("MQTT_USERNAME") or "").strip(),
        password=(env.get("MQTT_PASSWORD") or "").strip(),
    )


def publish_command(config: BrokerConfig, topic: str, payload: str) -> "list[str]":
    """mosquitto_pub のコマンド行。QoS 1・retain 付き。

    retain なので、順位計算側がレース開始より後に購読しても時刻を取りこぼさない。
    """
    command = [
        "mosquitto_pub",
        "-h",
        config.host,
        "-p",
        str(config.port),
        "-t",
        topic,
        "-q",
        "1",
        "-r",
        "-m",
        payload,
    ]
    if config.username:
        command += ["-u", config.username]
        if config.password:
            command += ["-P", config.password]
    return command


class RaceNotifier:
    """レース通知を1本のワーカースレッドで送る。

    publish() はキューに積んで即座に戻る。再試行もワーカーの中で完結する。
    """

    def __init__(self, config: BrokerConfig) -> None:
        self._config = config
        self._queue: "queue.Queue[Optional[tuple[str, str]]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None

        if not config.enabled:
            LOGGER.warning(
                "MQTT_HOST が空です。レース開始・終了の通知を送りません。"
                " .env を確認してください。"
            )
            return
        if shutil.which("mosquitto_pub") is None:
            LOGGER.error(
                "mosquitto_pub が見つかりません。レース通知を送りません。"
                " sudo apt install mosquitto-clients"
            )
            self._config = BrokerConfig(host="")
            return

        LOGGER.info(
            "レース通知: %s:%s へ %s / %s を送ります。",
            config.host,
            config.port,
            race_topic("start"),
            race_topic("finish"),
        )
        # daemon にする。close() が待ちきれなかったとき、インタプリタの終了処理が
        # 同じスレッドを待ち続けてプロセスが落ちなくなるのを防ぐ。
        self._worker = threading.Thread(
            target=self._run, name="race_notifier", daemon=True
        )
        self._worker.start()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def publish(self, event: str, stamp_ns: int) -> None:
        """通知を積む。呼び出し側 (joy のコールバック) をブロックしない (RN-10)。"""
        if not self.enabled:
            return
        self._queue.put((race_topic(event), race_payload(event, stamp_ns)))

    def close(self) -> None:
        """積み残しを送り切ってから閉じる。"""
        if self._worker is None:
            return
        self._queue.put(None)
        self._worker.join(timeout=CLOSE_TIMEOUT_S)
        if self._worker.is_alive():
            LOGGER.warning("レース通知の送信が終わりませんでした。")

    # ------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            topic, payload = item
            self._publish_with_retries(topic, payload)

    def _publish_with_retries(self, topic: str, payload: str) -> None:
        command = publish_command(self._config, topic, payload)
        for attempt in range(1 + MAX_RETRIES):
            if attempt:
                time.sleep(RETRY_DELAY_S)
            reason = self._publish_once(command)
            if reason is None:
                LOGGER.info("%s %s", topic, payload)
                return
            LOGGER.warning(
                "%s の送信に失敗しました (%d/%d): %s",
                topic,
                attempt + 1,
                1 + MAX_RETRIES,
                reason,
            )
        # ここまで来ても操作は止めない (RN-12)
        LOGGER.error("%s を送れませんでした: %s", topic, payload)

    @staticmethod
    def _publish_once(command: "list[str]") -> Optional[str]:
        """成功なら None、失敗なら理由を返す。"""
        try:
            done = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=PUBLISH_TIMEOUT_S,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return f"{PUBLISH_TIMEOUT_S} 秒で応答がありません"
        except OSError as error:
            return str(error)
        if done.returncode != 0:
            return f"exit {done.returncode}: {done.stdout.strip()}"
        return None
