#!/usr/bin/env bash
# 用法：preflight.sh <TASK_DIR>
# 构建前只读检查：仓库状态、JDK/Maven/Node、settings.xml、incoming 目录、Docker 指向。
# 发现阻断问题时退出码为 1，告警项只打印不退出。
set -uo pipefail
. "$(dirname "$0")/common.sh"

TASK_DIR=$(resolve_task_dir "${1:-}") || exit 1
fail=0
block() { echo "BLOCK: $*" >&2; fail=1; }
warn() { echo "WARN: $*" >&2; }

info "仓库"
for repo in manager workspace web; do
  d="$TASK_DIR/$repo"
  printf '  %-9s %-20s %s\n' "$repo" "$(git -C "$d" rev-parse --abbrev-ref HEAD)" "$(git -C "$d" log -1 --oneline)"
  [ -z "$(git -C "$d" status --porcelain)" ] || warn "$repo 有未提交改动，确认是否要带着改动部署"
done

info "工具"
jver=$(java -version 2>&1 | grep -m1 ' version ')
echo "  $jver"
grep -q '"1\.8' <<<"$jver" || block "需要 JDK 8"
mvn -version 2>/dev/null | head -1 || block "找不到 mvn"
export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
  . "$NVM_DIR/nvm.sh"
  nvm ls "$NODE_VERSION" >/dev/null 2>&1 || block "nvm 未安装 Node $NODE_VERSION"
  echo "  nvm current: $(nvm current), default: $(nvm alias default 2>/dev/null | head -1)"
else
  block "找不到 nvm：$NVM_DIR/nvm.sh"
fi

info "Maven settings"
SETTINGS="$HOME/.m2/settings.xml"
if [ -f "$SETTINGS" ]; then
  perm=$(stat -c '%a' "$SETTINGS")
  [ "$perm" = 600 ] || warn "settings.xml 权限为 $perm，应为 600"
  # 按 POM 中的 repository name 校验 HTTPS 镜像；注释里的内容不算
  python3 - "$SETTINGS" <<'EOF' || fail=1
import re, sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
ns = {"m": root.tag[1:].split("}")[0]} if root.tag.startswith("{") else {"m": ""}
q = (lambda t: f"m:{t}") if ns["m"] else (lambda t: t)
local = (root.findtext(q("localRepository"), namespaces=ns) or "").strip()
bad = 0
if re.match(r"^[A-Za-z]:|\\\\", local):
    print(f"BLOCK: localRepository 是 Windows 路径：{local}", file=sys.stderr); bad = 1
mirrors = {}
for m in root.iterfind(f".//{q('mirror')}", ns):
    url = (m.findtext(q("url"), namespaces=ns) or "").strip()
    for name in (m.findtext(q("mirrorOf"), namespaces=ns) or "").split(","):
        mirrors.setdefault(name.strip(), []).append(url)
required = ["Whaty Release Repositories", "Whaty Central Repositories", "mvnRepository",
            "Whaty 3rd Repositories Old", "Whaty Public Repositories Old", "Whaty Release Repositories Old"]
for name in required:
    urls = mirrors.get(name, [])
    if not any(u.startswith("https://") for u in urls):
        print(f"BLOCK: 缺少 mirrorOf '{name}' 的 HTTPS 镜像", file=sys.stderr); bad = 1
http = sorted({u for us in mirrors.values() for u in us if u.startswith("http://maven.webtrn.cn")})
if http:
    print(f"WARN: 另有 {len(http)} 个 HTTP 镜像（Maven 3.9 会拦截，未被 POM 使用可忽略）", file=sys.stderr)
if not bad:
    print("  必需的 HTTPS 镜像齐全")
sys.exit(bad)
EOF
  n=$(find "$HOME/.m2/repository" -name '*.lastUpdated' 2>/dev/null | wc -l)
  [ "$n" -eq 0 ] || warn "$n 个 .lastUpdated 失败标记；镜像修正后可删除（保留 jar/pom）"
else
  block "缺少 $SETTINGS"
fi

info "incoming"
INCOMING="$TASK_DIR/manager/manager-web/target/manager/incoming"
if [ ! -e "$INCOMING" ]; then
  # 新检出或已 clean：无残留文件，不影响 mvn clean
  echo "  不存在（新目录或尚未构建），跳过"
else
  [ -w "$INCOMING" ] || block "incoming 当前用户不可写：$INCOMING"
  foreign=$(find "$INCOMING" ! -uid "$(id -u)" -print -quit 2>/dev/null)
  [ -z "$foreign" ] || block "incoming 存在非当前用户文件（mvn clean 会失败），需有权限者修正：$foreign"
  [ -w "$INCOMING" ] && [ -z "$foreign" ] && echo "  可写，所有者正确"
fi

info "Docker"
cur=$(current_docker_task)
echo "  server.xml 当前指向：${cur:-<未识别>}"
[ "$cur" = "$TASK_DIR" ] || warn "Docker 未指向本任务，构建后需运行 switch-docker.sh"
echo "  容器状态：$(dk inspect "$CONTAINER" --format '{{.State.Status}}' 2>/dev/null || echo 不存在)"

[ "$fail" -eq 0 ] && info "preflight 通过" || echo "preflight 未通过" >&2
exit "$fail"
