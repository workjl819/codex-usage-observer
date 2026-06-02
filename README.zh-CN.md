# Codex Usage Observer

Codex Usage Observer 是一个本地独立工具，用来从 Codex session transcript
中采集每次请求的 usage 数据，统一写入 SQLite 数据库，并提供一个本地报表页面。

它的核心采集逻辑不依赖 Codex plugin，因此只要本机存在
`~/.codex/sessions/` 下的 session JSONL 文件，就可以跨项目汇总 Codex 请求数据。

## 采集内容

每个已完成的 Codex turn 会记录：

- prompt 文本
- 项目路径和项目名称
- 模型
- 开始时间和完成时间
- 总耗时和首 token 时间
- input、cached input、output、reasoning、total token
- primary 和 secondary rate-limit 快照

在报表页面中，`Rate Limit` 显示该请求完成时的 5h 和 weekly used percent。
`Usage Remaining` 列显示 `100 - used_percent`。
`5h Delta` 和 `Weekly Delta` 显示相对上一条已完成请求的可见百分比变化。

## 文件说明

- `collector.py`：扫描 `~/.codex/sessions/**/*.jsonl` 并写入 SQLite
- `dashboard.py`：启动本地 dashboard 和 JSON API
- `web/index.html`：dashboard 页面
- `state/usage.db`：运行时生成的 SQLite 数据库

## 使用方式

用一个命令启动采集和 dashboard：

```bash
python3 start.py
```

启动后会先刷新 SQLite 数据库，然后在下面的地址打开 dashboard：

```text
http://127.0.0.1:8765
```

运行期间，程序会每 5 秒重新扫描 Codex sessions，dashboard 也会以相同间隔自动刷新。

如需分别运行采集和页面服务，也可以执行：

```bash
python3 collector.py
python3 dashboard.py
```

dashboard 支持：

- 按项目名称筛选
- 按模型名称筛选
- 查看最近请求明细

## 说明

- 当前 MVP 只使用 `~/.codex/sessions/**/*.jsonl` 作为数据源。
- 重复运行 collector 是安全的，数据会按 `turn_id` upsert。
- dashboard 读取同一个 SQLite 数据库，不依赖 Codex plugin。
