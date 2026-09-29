#!/usr/bin/env bash
# 用法：update-repos.sh <TASK_DIR> [branch]
# 三个仓库必须全部干净才开始拉取；任一仓库有未提交改动则整体不动。
set -euo pipefail
. "$(dirname "$0")/common.sh"

TASK_DIR=$(resolve_task_dir "${1:-}")
BRANCH="${2:-master}"

dirty=0
for repo in manager workspace web; do
  if [ -n "$(git -C "$TASK_DIR/$repo" status --porcelain)" ]; then
    echo "未提交改动：$TASK_DIR/$repo" >&2
    git -C "$TASK_DIR/$repo" status --short | head -20 >&2
    dirty=1
  fi
done
[ "$dirty" -eq 0 ] || die "存在未提交改动，已停止，不做任何拉取"

for repo in manager workspace web; do
  repo_dir="$TASK_DIR/$repo"
  info "$repo -> $BRANCH"
  before=$(git -C "$repo_dir" rev-parse --short HEAD)
  # -q：fetch 的分支/tag 列表和 pull 的 diffstat 可达数百行，只输出摘要
  git -C "$repo_dir" fetch -q --prune origin
  git -C "$repo_dir" switch -q "$BRANCH"
  # 用已 fetch 的 origin/<branch> 快进，不再二次 fetch：
  # 实测 `pull -q --ff-only origin <branch>` 偶发 "Cannot fast-forward to multiple branches"
  git -C "$repo_dir" merge -q --ff-only "origin/$BRANCH"
  after=$(git -C "$repo_dir" rev-parse --short HEAD)
  if [ "$before" = "$after" ]; then
    echo "  已是最新：$(git -C "$repo_dir" log -1 --oneline)"
  else
    n=$(git -C "$repo_dir" rev-list --count "$before..$after" 2>/dev/null || echo '?')
    echo "  $before -> $after（$n 个提交）$(git -C "$repo_dir" log -1 --format=%s)"
  fi
done
