# R6 + 本地 main 合并提案

## 现状

- 本地主目录 main HEAD = `b256f10`（你的 5 个本地 commit）
- 远程 origin/main HEAD = `f4bf834`（含 R6 v1.0.6）
- 本地领先 5 commits / 落后 5 commits（已分叉）

R6 修改了 `dashboard.py` / `ipos.py` / `snapshots.py` / `main.py`，**和你的本地 commit `51f4af3`（lifecycle+outcomes endpoints）冲突**。

## 我无法直接写入主目录的原因

每次我 `Write` 主目录下的 `src/hk_ipo_agent/api/**/*.py`，几秒内有外部进程（疑似你启动了文件 watcher / 编辑器自动恢复 / 另一个 Claude 会话）把文件回滚到旧版本。**我无法可靠地完成合并**。

因此把 4 个文件的合并提案放在这里供你手动 review + 复制。

## 提案文件（4 个）

| 提案文件 | 目标路径 |
|---|---|
| `dashboard.py` | `src/hk_ipo_agent/api/routers/dashboard.py` |
| `ipos.py` | `src/hk_ipo_agent/api/routers/ipos.py` |
| `snapshots.py` | `src/hk_ipo_agent/api/routers/snapshots.py` |
| `main.py` | `src/hk_ipo_agent/api/main.py` |

## 关键合并决策

### 1. `ipos.py` — 4 个 endpoint 都加 `require_permission(READ_IPO)`
- `list_ipos` / `get_ipo_detail` / `list_ipo_snapshots` / `get_ipo_lifecycle`
- 用统一别名 `_IPODep = Annotated[CurrentUser, Depends(require_permission(Permission.READ_IPO))]`
- R5-3 注：`list_ipo_snapshots`（mounted under `/api/ipos/`）gate 在 `READ_IPO` 而非 `READ_SNAPSHOTS`，因为 URL 资源是 IPO；所有 VIEWER+ 角色两个权限都有，行为等价。

### 2. `snapshots.py` — 7 个 endpoint 都加 `require_permission(READ_SNAPSHOTS)`
- `list_snapshots` / `get_snapshot` / `get_memo_markdown` / `get_memo_pdf` / `get_memo_docx` / `get_snapshot_outcomes` / `recent_outcomes`
- 用统一别名 `_SnapDep = Annotated[CurrentUser, Depends(require_permission(Permission.READ_SNAPSHOTS))]`
- 保留你 commit `72f0cce` 加的 `_adapt_snapshot_for_ui` 和 `outcomes_router`

### 3. `dashboard.py` — `require_permission(READ_DASHBOARD)`
- R6 的修改 + 保留你 `ae991c9` 把 `cost_summary={"today_usd": Decimal("0")}` 改成 `"0"` 字符串

### 4. `main.py` — 双方都保留
- **R6**：环境感知的 LLM 失败处理（prod reraise）+ `_upsert_seed_accounts_into_pg` (R6-7)
- **你的**：`set_registry(PGPredictionRegistry())` 在 lifespan 启动时

二者无冲突 — R5-4 仅在 pipeline 层移除 `set_registry`，API lifespan 仍可调用。

## 你需要做的

1. Review `docs/r6_merge_proposal/` 下的 4 个 `.py` 文件
2. （工作树清理）当前主目录 `git status` 显示 73 个待处理文件（你 39 个 stashed + 我开始合并的痕迹），需要先理清
3. 完成合并后 commit + push

## 后续工作树清理建议

```bash
cd "D:/自定义工具/港股数据分析/港股基石建模/港股基石轮投资模型"

# 1. 先把当前混乱状态 stash 起来（暂存 73 个修改）
git stash push -u -m "post-R6-merge-attempt mess"

# 2. 重新干净 merge
git merge origin/main --no-ff -m "Merge R6 (v1.0.6) into local main"
# (出现 4 conflicts)

# 3. 把 docs/r6_merge_proposal/ 下的 4 个文件覆盖到对应路径
cp .claude/worktrees/optimistic-dhawan-988b7a/docs/r6_merge_proposal/dashboard.py src/hk_ipo_agent/api/routers/
cp .claude/worktrees/optimistic-dhawan-988b7a/docs/r6_merge_proposal/ipos.py src/hk_ipo_agent/api/routers/
cp .claude/worktrees/optimistic-dhawan-988b7a/docs/r6_merge_proposal/snapshots.py src/hk_ipo_agent/api/routers/
cp .claude/worktrees/optimistic-dhawan-988b7a/docs/r6_merge_proposal/main.py src/hk_ipo_agent/api/

# 4. 标记冲突已解决，commit merge
git add src/hk_ipo_agent/api/routers/dashboard.py src/hk_ipo_agent/api/routers/ipos.py src/hk_ipo_agent/api/routers/snapshots.py src/hk_ipo_agent/api/main.py
git commit  # 用默认 merge message

# 5. （可选）pop stash 恢复 39 文件未提交工作
git stash pop  # stash@{0}
```
