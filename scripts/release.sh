#!/usr/bin/env bash
# 发布新版本：测试 → 更新 main.py 版本号 → 提交并打标签 → 推送 → 等待 GitHub Actions 构建 → 核对 GHCR 镜像。
# 用法：scripts/release.sh [版本号]   未指定版本号时在当前版本上递增 patch（如 2.0.1 → 2.0.2）。
# 推送 main 与 vX.Y.Z 标签会各触发一次构建，分别更新 main 与 X.Y.Z / X.Y 镜像标签。
set -euo pipefail

cd "$(dirname "$0")/.."
WORKFLOW=docker-publish.yml
IMAGE=sor85/comic-api
PLATFORMS="linux/amd64 linux/arm64 linux/arm/v7"

die() { echo "错误：$*" >&2; exit 1; }
for tool in git gh jq curl node; do command -v "$tool" >/dev/null || die "缺少命令 $tool"; done

[ "$(git branch --show-current)" = main ] || die "只能在 main 分支发布"
[ -z "$(git status --porcelain)" ] || die "工作区有未提交的改动"
git fetch -q origin main --tags
[ "$(git rev-list --count HEAD..origin/main)" = 0 ] || die "本地 main 落后于 origin/main，先拉取"

current=$(sed -nE 's/.*version="([0-9]+\.[0-9]+\.[0-9]+)".*/\1/p' main.py)
[ -n "$current" ] || die "main.py 中找不到版本号"
if [ $# -ge 1 ]; then
  version=${1#v}
else
  IFS=. read -r major minor patch <<<"$current"
  version="$major.$minor.$((patch + 1))"
fi
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "版本号格式应为 X.Y.Z：$version"
tag="v$version"
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && die "标签 $tag 已存在"

echo "==> 运行测试"
python=python3; [ -x .venv/bin/python ] && python=.venv/bin/python
node --test tests/*-ui.test.cjs >/dev/null || die "前端测试未通过"
"$python" -m pytest tests -q >/dev/null || die "pytest 未通过"

echo "==> 版本 $current → $version"
perl -pi -e "s/version=\"\Q$current\E\"/version=\"$version\"/" main.py
git add main.py
git commit -q -m "chore：版本更新至 $version"
git tag -a "$tag" -m "$tag"
sha=$(git rev-parse HEAD)

echo "==> 推送 main 与 $tag"
git push -q --atomic origin main "$tag"

# 两次推送各对应一个工作流运行：headBranch 分别为 main 与标签名。
echo "==> 等待 GitHub Actions 构建（$sha）"
runs=""
for _ in $(seq 1 60); do
  runs=$(gh run list --workflow "$WORKFLOW" --commit "$sha" --json databaseId,headBranch \
    --jq '[.[]|select(.headBranch=="main" or .headBranch=="'"$tag"'")]|unique_by(.headBranch)|.[].databaseId')
  [ "$(printf '%s\n' "$runs" | grep -c .)" -ge 2 ] && break
  sleep 5
done
[ -n "$runs" ] || die "未找到对应的工作流运行"
for id in $runs; do
  gh run watch "$id" --exit-status --interval 30 >/dev/null || die "构建失败：$(gh run view "$id" --json url --jq .url)"
  echo "    运行 $id 成功"
done

echo "==> 核对 GHCR 镜像"
token=$(curl -fsS "https://ghcr.io/token?scope=repository:$IMAGE:pull" | jq -r .token)
accept="application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json"
registry() { curl -fsSL -H "Authorization: Bearer $token" -H "Accept: $accept" "https://ghcr.io/v2/$IMAGE/$1"; }
for ref in main "$version"; do
  index=$(registry "manifests/$ref") || die "镜像 ghcr.io/$IMAGE:$ref 不存在或无法匿名读取"
  archs=$(jq -r '.manifests[]|select(.platform.os=="linux")|"linux/\(.platform.architecture)\(if .platform.variant then "/"+.platform.variant else "" end)"' <<<"$index" | sort | tr '\n' ' ')
  for platform in $PLATFORMS; do [[ " $archs" == *" $platform "* ]] || die "$ref 缺少架构 $platform"; done
  digest=$(jq -r '[.manifests[]|select(.platform.os=="linux")][0].digest' <<<"$index")
  config=$(registry "manifests/$digest" | jq -r .config.digest)
  revision=$(registry "blobs/$config" | jq -r '.config.Labels["org.opencontainers.image.revision"]')
  [ "$revision" = "$sha" ] || die "$ref 对应提交为 $revision，不是 $sha"
  echo "    ghcr.io/$IMAGE:$ref  提交 ${sha:0:7}  架构 $archs"
done
echo "==> 发布完成：ghcr.io/$IMAGE:$version（同时更新 main、${version%.*}、sha-${sha:0:7}）"
