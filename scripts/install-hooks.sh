#!/usr/bin/env bash
# 安装 pre-commit 钩子：派生模块、上游快照与 UPSTREAM.md 必须在同一个提交里。
# 用法：scripts/install-hooks.sh [--force]
set -euo pipefail

root=$(git rev-parse --show-toplevel)
hook="$root/.git/hooks/pre-commit"
marker='# upstream-manifest-guard'

if [[ -e "$hook" ]] && ! grep -q "$marker" "$hook" && [[ "${1:-}" != "--force" ]]; then
  echo "已存在别的 pre-commit 钩子：$hook" >&2
  echo '手动把下面这行加进那个钩子，或加 --force 覆盖：' >&2
  echo "  python3 \"$root/scripts/check_upstream_manifest.py\" --staged" >&2
  exit 1
fi

cat > "$hook" <<EOF
#!/usr/bin/env bash
$marker
set -euo pipefail
exec python3 "\$(git rev-parse --show-toplevel)/scripts/check_upstream_manifest.py" --staged
EOF
chmod +x "$hook"
echo "已安装：$hook"
