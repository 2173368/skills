#!/usr/bin/env bash
# 用法：build-java.sh <TASK_DIR> <manager|workspace>
# 执行 mvn -B clean install -DskipTests，完整日志写到 /tmp，终端只输出 Reactor Summary。
# 构建前必须已停止 Tomcat 容器（见 SKILL.md）。
set -uo pipefail
. "$(dirname "$0")/common.sh"

TASK_DIR=$(resolve_task_dir "${1:-}") || exit 1
PROJECT="${2:-}"
case "$PROJECT" in
  manager)   ARTIFACT="$TASK_DIR/manager/manager-web/target/manager" ;;
  workspace) ARTIFACT="$TASK_DIR/workspace/tycj-main/target/tycj-main" ;;
  *) die "第二个参数必须是 manager 或 workspace" ;;
esac

if [ "$(dk inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null)" = true ] \
   && [ "$(current_docker_task)" = "$TASK_DIR" ]; then
  die "Tomcat 正在使用本任务目录，先执行：cd $DOCKER_DIR && docker compose stop tomcat7"
fi

LOG=$(log_path "$TASK_DIR" "$PROJECT-install")
info "构建 $PROJECT，日志：$LOG"
(cd "$TASK_DIR/$PROJECT" && mvn -B clean install -DskipTests) > "$LOG" 2>&1
status=$?
echo "exit code: $status" >> "$LOG"

# Reactor Summary 是判定依据；com.sun:tools systemPath 的 ERROR 是遗留告警
sed -n '/Reactor Summary/,/BUILD /p' "$LOG" | sed 's/^\[INFO\] //'
summary=$(sed -n '/Reactor Summary/,/BUILD /p' "$LOG" | grep -v 'BUILD ' | grep -E ' (SUCCESS|FAILURE|SKIPPED)( \[|$)')
ok=$(grep -c ' SUCCESS' <<<"$summary")
bad=$(grep -vc ' SUCCESS' <<<"$summary")

if [ "$status" -ne 0 ]; then
  grep -nE '\[ERROR\]' "$LOG" | grep -v 'systemPath' | head -30
  die "$PROJECT 构建失败（exit $status），完整日志：$LOG"
fi
[ "$ok" -gt 0 ] && [ "$bad" -eq 0 ] || die "$PROJECT Reactor 有 $bad 个模块未 SUCCESS"
[ -d "$ARTIFACT" ] || die "产物目录不存在：$ARTIFACT"
info "$PROJECT 构建成功：$ARTIFACT"
