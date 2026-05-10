#!/bin/bash
# 通过 GitHub API 推送本地提交
# 用法: ./git-push.sh
# 绕过 SSH(端口22封锁) 和 HTTPS git(token不支持git操作)
set -e
cd "$(dirname "$0")"

TOKEN="${GITHUB_TOKEN:?请设置 GITHUB_TOKEN 环境变量}"
REPO="daxian10086/JinDX"
API_IP="140.82.113.5"
BRANCH="master"

CURL="curl -4 --connect-timeout 15 -s --resolve api.github.com:443:${API_IP}"
H_AUTH="Authorization: token ${TOKEN}"
H_ACCEPT="Accept: application/vnd.github+json"

echo "=== JinDX GitHub Push (via API) ==="

# 获取远程 HEAD 和 tree
REMOTE_SHA=$( $CURL "https://api.github.com/repos/${REPO}/git/ref/heads/${BRANCH}" \
  -H "$H_AUTH" -H "$H_ACCEPT" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('object',{}).get('sha',''))" )

REMOTE_TREE=$( $CURL "https://api.github.com/repos/${REPO}/git/commits/${REMOTE_SHA}" \
  -H "$H_AUTH" -H "$H_ACCEPT" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('tree',{}).get('sha',''))" )

LOCAL_TREE=$(git rev-parse HEAD^{tree})
echo "远程 tree: ${REMOTE_TREE:0:7}"
echo "本地 tree: ${LOCAL_TREE:0:7}"

if [ "$LOCAL_TREE" = "$REMOTE_TREE" ]; then
    echo "内容一致，无需推送。"
    exit 0
fi

echo "变更内容，开始推送..."
export REMOTE_SHA REMOTE_TREE TOKEN REPO API_IP BRANCH

python3 << 'PYEOF'
import json, os, subprocess

TOKEN = os.environ["TOKEN"]
REPO = os.environ["REPO"]
API_IP = os.environ["API_IP"]
BRANCH = os.environ["BRANCH"]
REMOTE_SHA = os.environ["REMOTE_SHA"]
REMOTE_TREE_SHA = os.environ["REMOTE_TREE"]

H_AUTH = f"Authorization: token {TOKEN}"
CURL_BASE = ["curl", "-4", "--connect-timeout", "15", "-s",
             "--resolve", f"api.github.com:443:{API_IP}",
             "-H", H_AUTH,
             "-H", "Accept: application/vnd.github+json"]

def api(method, path, data=None):
    cmd = CURL_BASE + ["-X", method, f"https://api.github.com{path}",
                        "-H", "Content-Type: application/json"]
    if data:
        cmd += ["-d", json.dumps(data)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"API 调用失败: {method} {path}\n{r.stderr}")
    resp = json.loads(r.stdout)
    if "sha" not in resp and "message" in resp:
        raise SystemExit(f"API 错误: {resp['message']}\n请求: {method} {path}\n数据: {json.dumps(data)[:200] if data else 'none'}")
    return resp

# 获取远程 tree 内容
remote_tree = api("GET", f"/repos/{REPO}/git/trees/{REMOTE_TREE_SHA}?recursive=1")

# 获取本地完整 tree 列表（格式: <mode> <type> <sha>\t<path>）
local_tree_output = subprocess.run(
    ["git", "ls-tree", "-r", "HEAD^{tree}"],
    capture_output=True, text=True
)
local_files = {}  # path -> (blob_sha, mode)
for line in local_tree_output.stdout.strip().splitlines():
    meta, path = line.split('\t', 1)
    mode, ftype, sha = meta.split()
    local_files[path] = (sha, mode)

# 对比远程 tree 找出完整差异
remote_files = {item["path"]: item for item in remote_tree.get("tree", [])}
changed = {}   # path -> (blob_sha, mode) — 新增或变更
deleted = set()
for path, (sha, mode) in local_files.items():
    if path not in remote_files or remote_files[path]["sha"] != sha:
        changed[path] = (sha, mode)
for path in remote_files:
    if path not in local_files:
        deleted.add(path)

print(f"变更: {list(changed.keys())} 删除: {list(deleted)}")

# 上传所有变更文件的 blob 到 GitHub（本地 blob SHA 在服务器上不存在）
blob_map = {}  # path -> server blob SHA
for path, (local_blob, mode) in changed.items():
    if path in deleted:
        continue
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    try:
        with open(filepath) as f:
            content = f.read()
    except FileNotFoundError:
        print(f"  跳过(文件不存在): {path}")
        continue
    result = api("POST", f"/repos/{REPO}/git/blobs",
                 {"content": content, "encoding": "utf-8"})
    blob_map[path] = (result["sha"], mode)
    print(f"  上传: {path} -> {result['sha'][:7]}")

# 构建新 tree — 先处理远程已有的文件
remote_paths = set()
tree_items = []
for item in remote_tree.get("tree", []):
    remote_paths.add(item["path"])
    if item["path"] in deleted:
        tree_items.append({"path": item["path"], "mode": item["mode"], "type": "blob", "sha": None})
        continue
    entry = {"path": item["path"], "mode": item["mode"], "type": item["type"]}
    if item["path"] in blob_map:
        entry["sha"] = blob_map[item["path"]][0]
    else:
        entry["sha"] = item["sha"]
    tree_items.append(entry)

# 添加远程不存在的新文件
for path, (blob, mode) in blob_map.items():
    if path not in remote_paths:
        tree_items.append({"path": path, "mode": mode, "type": "blob", "sha": blob})

new_tree = api("POST", f"/repos/{REPO}/git/trees", {
    "base_tree": REMOTE_TREE_SHA,
    "tree": tree_items
})
print(f"新 tree:  {new_tree['sha'][:7]}")

# 创建 commit
commit_msg = subprocess.run(
    ["git", "log", "--format=%B", "-1", "HEAD"],
    capture_output=True, text=True
).stdout.strip()

new_commit = api("POST", f"/repos/{REPO}/git/commits", {
    "message": commit_msg,
    "tree": new_tree["sha"],
    "parents": [REMOTE_SHA]
})
print(f"新 commit: {new_commit['sha'][:7]}")

# 更新 ref
api("PATCH", f"/repos/{REPO}/git/refs/heads/{BRANCH}", {
    "sha": new_commit["sha"],
    "force": False
})
print(f"\n推送完成! https://github.com/{REPO}")
PYEOF
