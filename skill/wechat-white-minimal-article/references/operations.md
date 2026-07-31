# 操作与恢复

## 环境变量

将凭证放入被忽略的本地 env 文件，不要打印或提交值：

- `CONTENT_API_KEY` 或 `APIMART_API_KEY`
- `CONTENT_API_URL`
- `CONTENT_MODEL`
- `APIMART_API_KEY`
- `APIMART_API_BASE`
- `APIMART_IMAGE_MODEL`
- `APIMART_CONCURRENCY`，最大 3
- `WECHAT_ACCESS_TOKEN`，或 `WECHAT_APP_ID` + `WECHAT_APP_SECRET`

## 审阅闸门

1. 生成内容前审阅选题矩阵。
2. 付费生图前审阅文章、来源卡、完整标题和六图文字。
3. 图片阶段先 dry-run，确认任务总数等于文章数乘以 6。
4. 草稿阶段先 dry-run，确认标题、摘要、图片顺序和已有结果。
5. 用户明确授权目标公众号后才保存草稿。

## 恢复

- 内容阶段跳过完整文章目录，除非显式 `--force`。
- 图片阶段按 prompt hash 与落盘文件续传，不盲目重提已生成任务。
- 永久图片 MediaID 按 SHA-256 缓存。
- 草稿返回 MediaID 后立即持久化，再调用 `draft/get` 回查。
- 已有 MediaID 且状态为 `needs_update` 时，使用 `draft/update` 原位修复。
- 微信错误 `40164` 表示出口 IP 未加入白名单；修复白名单后重跑。
- 微信临时 `system error` 且尚未返回草稿 MediaID 时，只重试当前项。

## 安全

- 只调用 `material/add_material`、`draft/add`、`draft/update`、`draft/get` 和必要的 `draft/batchget`。
- 不调用 `freepublish`、群发或发表接口。
- JSON 使用 UTF-8 字节和 `ensure_ascii=False`，防止中文被存为字面量 `\uXXXX`。
- 批次完成后必须运行审计，不以命令退出码代替结果检查。
