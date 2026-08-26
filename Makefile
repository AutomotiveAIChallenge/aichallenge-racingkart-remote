# 遠隔操作PC用の Makefile。本体リポジトリ (aichallenge-racingkart) の Makefile と
# 同じ作法で書いている。イメージのビルドは ./docker_build.sh を使う。
SHELL := /bin/bash

.PHONY: remote remote-stop rviz rviz-stop down ps logs

# RViz コンテナのユーザーに使う。output/ の生成物がホストユーザー所有になる。
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
export HOST_UID HOST_GID
# ホストシェルの ROS_DOMAIN_ID が compose 補間や子プロセスに漏れるのを防ぐ。
# 遠隔側は常に 0 で、run_zenoh.bash も 0 に固定している。変える口は用意しない。
unexport ROS_DOMAIN_ID

# ブレーキ試験 (実験用)。指定すると B ボタンで一定ブレーキが入る。
#   make remote VEHICLES="A3" BRAKE_TEST=20
export BRAKE_TEST

TIMESTAMP := $(shell date +%Y%m%d-%H%M%S)

# 遠隔操作PC一式（zenoh ブリッジ + joy + manager）。GUI は manager と同じプロセス。
#   make remote VEHICLES="A2 A3 A7"
# 対象車両に既定値を置かない。GUI の「全台」も緊急停止の宛先もここで決まるため。
# 遠隔側は常に ROS_DOMAIN_ID 0。車両側の domain とは無関係で、車両IDで区別する。
#
# RViz 以外はコンテナに入れずホストで動かす。setsid で端末から切り離すので make が
# 返っても生き残る。setsid によって run_remote.bash がセッションリーダーになり、
# 子も孫も同じプロセスグループに入る。停止はそのグループごと畳む (remote-stop)。
# ホストに ROS 2 Humble と zenoh-bridge-ros2dds が入っていること (README 参照)。
# 前提の確認は remote_component.bash check が持つ。Makefile・ランチャ GUI・起動時の
# 3者が同じものを呼ぶので、確認の内容が散らばらない。
remote:
	@test -n "$(VEHICLES)" || { \
		echo 'Error: VEHICLES を指定してください。  例: make remote VEHICLES="A2 A3 A7"' >&2; \
		exit 1; \
	}
	@./scripts/remote_component.bash check
	@mkdir -p output/$(TIMESTAMP)/remote output/latest
	@ln -sfn "$(PWD)/output/$(TIMESTAMP)/remote" output/latest/remote
	@setsid ./scripts/run_remote.bash "$(VEHICLES)" "$(PWD)/output/$(TIMESTAMP)" \
		</dev/null >/dev/null 2>&1 & echo $$! > output/remote.pid
	@echo "対象車両: $(VEHICLES)"
	@echo "PID: $$(cat output/remote.pid) (zenoh / joy / manager)"
	@echo "ログ: output/latest/remote/"
	@test -z "$(BRAKE_TEST)" || echo "ブレーキ試験: $(BRAKE_TEST)% (B ボタンを押している間)"
	@echo "状態: make ps / ログ: make logs / 停止: make remote-stop"

# プロセスグループごと畳む。PID の前のマイナスがそれ。`ros2 run` は joy_node を
# subprocess で起こすので、親だけ kill すると joy_node が孤児として残る。
remote-stop:
	@pid=$$(cat output/remote.pid 2>/dev/null); \
	if [ -z "$$pid" ]; then \
		echo "output/remote.pid がありません。起動していないようです。"; \
		exit 0; \
	fi; \
	echo "stopping PID group $$pid ..."; \
	kill -TERM -"$$pid" 2>/dev/null || kill -TERM "$$pid" 2>/dev/null || true; \
	for _ in $$(seq 20); do pgrep -g "$$pid" >/dev/null || break; sleep 0.25; done; \
	if pgrep -g "$$pid" >/dev/null; then \
		echo "TERM で終わらないプロセスがあるので KILL します:"; \
		pgrep -g "$$pid" -a; \
		kill -KILL -"$$pid" 2>/dev/null || true; \
		sleep 1; \
	fi; \
	if pgrep -g "$$pid" >/dev/null; then \
		echo "警告: まだ残っています。"; \
		pgrep -g "$$pid" -a; \
	else \
		echo "停止しました。"; \
	fi; \
	rm -f output/remote.pid

# 遠隔監視 RViz
#   make rviz VEHICLE=A3
# 車両トピックは /<VEHICLE_ID>/... の prefix 付きで届く。VEHICLE を渡すと prefix を
# 剥がす中継が立ち、その車両が RViz に映る。未指定だと地図しか出ない。
rviz:
	docker compose stop rviz2
	RVIZ_VEHICLE_ID="$(VEHICLE)" docker compose up -d rviz2

rviz-stop:
	docker compose stop rviz2

down:
	docker compose down --remove-orphans

# ホスト側はプロセスグループの中身をそのまま出す。コンテナは RViz だけ。
# ランチャ GUI から起こしたものは構成要素ごとに pid ファイルを置くので、そちらも出す。
ps:
	@pid=$$(cat output/remote.pid 2>/dev/null); \
	if [ -n "$$pid" ] && kill -0 "$$pid" 2>/dev/null; then \
		echo "remote: up (PID group $$pid)"; \
		pgrep -g "$$pid" -a | sed 's/^/  /'; \
	else \
		echo "remote: down"; \
	fi
	@for f in output/launcher-*.pid; do \
		[ -e "$$f" ] || continue; \
		name=$$(basename "$$f" .pid | sed 's/^launcher-//'); \
		pid=$$(cat "$$f" 2>/dev/null); \
		if [ -n "$$pid" ] && kill -0 "$$pid" 2>/dev/null; then \
			echo "launcher/$$name: up (PID group $$pid)"; \
			pgrep -g "$$pid" -a | sed 's/^/  /'; \
		else \
			echo "launcher/$$name: down (残った pid ファイル: $$f)"; \
		fi; \
	done
	@echo
	@docker compose ps

logs:
	tail -f output/latest/remote/manager.log
