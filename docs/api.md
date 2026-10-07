# REST 与前端通信

本地默认地址：`http://127.0.0.1:8000`。JSON 错误统一包含 `detail`。响应带 `X-Request-ID`，日志中可以按该编号排查。配置与数据库操作均在后端，浏览器不持有模型密钥。

## 认证

主网页访问 `/` 时取得 HttpOnly、SameSite=Strict cookie；修改请求要求同源 Origin。扩展在主网页复制连接码后，用 `Authorization: Bearer <连接码>` 请求。访问 `/api/health` 不需要认证，其余 API 需要认证。Host 仅接受本地主机。

扩展在自己的侧栏上下文发请求，manifest 声明 localhost/127.0.0.1 主机权限。YouTube 中的 content script 只读播放状态、执行用户点击的重播，不拿连接码，不直接调用后端。

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 启动状态 |
| GET | `/api/settings` | 模型名称、缺失配置名、上传限制；不返回模型密钥 |
| GET | `/api/pairing-token` | 已认证用户主动获取本地扩展连接码 |
| GET/POST | `/api/conversations` | 列出/新建聊天 |
| GET | `/api/conversations/{id}/messages` | 已保存聊天回合与来源 |
| POST | `/api/conversations/{id}/messages` | 提交 `{question}`，返回 NDJSON 流 |
| GET/POST | `/api/documents` | 资料目录/上传 multipart 的 `file` |
| GET | `/api/vocabulary` | query、video_id、mastered、favorite 筛选，最多 200 个条目 |
| PATCH | `/api/vocabulary/{id}` | `{mastered: true}` / `{favorite: true}` 等 |
| GET | `/api/videos` | 实际保存过难点的视频标题和条目数量 |
| POST | `/api/videos/{video_id}/subtitles` | `{title, content}`，导入 UTF-8 英文 SRT/VTT 文本 |
| POST | `/api/video-sessions` | `{video_id, title, level, position}`，返回 202 和会话 id |
| GET | `/api/video-sessions/{id}?position=秒数` | 状态、字幕来源、当前位置附近的难点 |
| POST | `/api/video-sessions/{id}/window` | `{position}`，分析当前分钟和下一分钟，返回 202 |
| POST | `/api/video-sessions/{id}/pause` | 停止新的分析任务，不强行中断已发出的外部 HTTP 请求 |
| POST | `/api/video-sessions/{id}/exposures` | `{difficulty_id, position}`，验证所属会话和时间范围后幂等保存 |

`level` 为 `初级` / `中级` / `高级`。首版视频时间范围为 0–14400 秒。输入的是 11 位 YouTube video ID，后端不抓取任意用户提供的 URL。

## 聊天流

`POST messages` 响应为 `application/x-ndjson`，每行一个 JSON。类型：

- `status`：工具与处理进度。
- `text`：当前回答的完整文本快照，前端替换显示，不重复追加。
- `done`：最终 `answer` 和 `sources`，数据库已保存。
- `error`：失败原因；已进入的聊天回合标记失败，不当作成功回答回放。

本机首版限制同时一条聊天或一次资料导入。视频后台任务独立，分析调用单独限并发。浏览器断开聊天流后，已在进行的请求可以完成并保存历史；前端重新打开聊天查看结果，不自动重复提交。

## 字幕与单词本状态

字幕准备：`loading → ready`；窗口分析：`ready → analyzing → ready`；失败为 `failed`，用户暂停/进程重启为 `paused`。重新开始创建新会话，但可以复用字幕和分析缓存。

获取的全文字幕存 SQLite，先合并相邻碎片并保留原时轴。分析模型只生成 cue_id 和表达/含义/解释/译文，程序查回真实时间戳，并过滤原句不存在的表达。每分钟最多 3 条，也允许空列表。

缓存并不代表已经学习。只有侧栏在实际播放推进、对应时间区间、非广告状态下展示卡片，才上报 exposure。后端再次验证所属会话、字幕版本、水平和时间范围；重复上报不重复插入。同表达、同含义的来源合并显示，已掌握表达不再自动提示。

这些检查是单机学习体验逻辑，不是观看证明或考试监考机制。跳过、隐藏侧栏或分析响应较晚而错过的片段不会自动入本；可重播片段。

## Agent 与 Middleware

- 工具：`search_materials`、`web_search`、`search_vocabulary`、`get_video_context`。
- `ModelCallLimitMiddleware`：每轮最多 6 次模型调用。
- `ToolCallLimitMiddleware`：每轮最多 8 次工具调用。
- HTTP Middleware：请求编号/耗时日志、安全响应头；TrustedHostMiddleware 限制主机。
- API 认证使用 FastAPI dependency，错误使用 exception handler。
- 没有 MCP 服务。字幕获取、结构化难点分析、自动保存都是固定的普通 Python 服务流程。
