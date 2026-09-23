#!/bin/bash
# 航路マップの公開ファイルを、会社サイトのリポジトリの ferrymap/ に反映する
# 使い方: tools/deploy_to_site.sh [会社サイトのリポジトリのパス]
#   省略時は ../kamei-shoten（環境変数 SITE_REPO でも指定可）
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
site="${1:-${SITE_REPO:-$here/../kamei-shoten}}"
[ -d "$site/.git" ] || { echo "会社サイトのリポジトリが見つかりません: $site" >&2; exit 1; }
dest="$site/ferrymap"
mkdir -p "$dest/assets" "$dest/data"
cp "$here/index.html" "$dest/index.html"
cp "$here/assets/style.css" "$here/assets/app.js" "$dest/assets/"
cp "$here/data/routes.js" "$dest/data/routes.js"
echo "反映しました: $dest"
echo "次: cd \"$site\" && git add ferrymap && git commit -m '航路マップを更新' && git push"
