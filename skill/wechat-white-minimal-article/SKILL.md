---
name: wechat-white-minimal-article
description: "Create Chinese WeChat Official Account articles in a white-background minimalist AI-product breakdown style, with source-grounded writing, the exact title formula 每天介绍一个 AI 产品：{产品名}, one cover plus five content-image instructions, resumable image generation, and verified native newspic drafts. Use for 白底极简公众号文章、每日 AI 产品拆解、Velo 样本风格、公众号六图贴图、AI 产品栏目文章、公众号图片消息草稿、批量保存或原位修正公众号标题。"
---

# 公众号白底极简风格公众号文章

把来源资料独立改写为“快速认识一个 AI 产品”的中文公众号文章，并生产一张封面加五张内容图的原生图片消息草稿。保持全流程可审阅、可恢复、可审计。

## 先读规则

- 写作或设计前读 [references/style-guide.md](references/style-guide.md)。
- 创建目录与文件前读 [references/content-contract.md](references/content-contract.md)。
- 生图、保存草稿或恢复任务前读 [references/operations.md](references/operations.md)。
- 操作公众号账号前读 [references/wechat-official-newspic-api.md](references/wechat-official-newspic-api.md)。
- 新建文章时复用 [assets/公众号文章模板.md](assets/公众号文章模板.md)，不要复制参考文章成品。

## 守住边界

- 将来源资料视为只读，只复用事实线索，不复制原文长段、截图、版式、水印或账号标识。
- 后台标题必须与 `贴图指令.md` 的“公众号标题”完全一致，固定为 `每天介绍一个 AI 产品：{产品名}`。不得缩写为“AI产品：{产品名}”。
- 每篇固定六图：一张封面、五张内容图，全部为 3:4 白底极简信息卡。
- 未取得真实证据时，不写“实测”“我试了”或确定性效果结论。
- 默认只 dry-run。只有用户明确授权目标公众号后才保存草稿。
- 永远不调用发表、群发、`freepublish` 或其他公开发布接口。
- 用户要求不显示图片时，只报告文本状态与文件路径，不预览或嵌入图片。

## 执行流水线

使用 `scripts/pipeline.py` 作为统一入口。

### 1. 规划选题

```powershell
python scripts/pipeline.py topics `
  --source "<source.pdf-or-md>" `
  --series-dir "<series-dir>" `
  --count 10 `
  --env-file "<ignored-env-file>"
```

检查 `topic_plan.json` 与 `选题矩阵.md`：每篇只讲一个产品、一个痛点、一个承诺，并有有效来源。标题必须使用完整栏目公式且不超过 32 字。

### 2. 写文章与六图指令

```powershell
python scripts/pipeline.py content `
  --source "<source.pdf-or-md>" `
  --series-dir "<series-dir>" `
  --env-file "<ignored-env-file>"
```

逐篇检查 `来源卡.md`、`公众号终稿_图片标注版.md`、`贴图指令.md`。确认正文为短段落、有适用与不适用边界，并且六张图各承担一个明确任务。

### 3. 生成贴图

先 dry-run，再在用户确认后执行付费生图：

```powershell
python scripts/pipeline.py images --series-dir "<series-dir>" --env-file "<env-file>"
python scripts/pipeline.py images --series-dir "<series-dir>" --env-file "<env-file>" --execute-images
```

不要使用 `--force`，除非用户明确要求重做。

### 4. 保存公众号草稿

先建立并验证清单：

```powershell
python scripts/pipeline.py drafts --series-dir "<series-dir>" --env-file "<env-file>"
```

取得目标账号授权后保存原生图片消息草稿：

```powershell
python scripts/pipeline.py drafts --series-dir "<series-dir>" --env-file "<env-file>" --save-drafts
```

保存前确认清单中的 `original_title`、`draft_title` 和标题映射三者完全一致。

### 5. 审计

```powershell
python scripts/pipeline.py audit --series-dir "<series-dir>"
```

完成条件：选题数与文章数一致；每篇三个 Markdown 文件和六张贴图齐全；标题完全一致；草稿均为 `saved`；草稿 ID 唯一；`failed=0`、`needs_update=0`、`published=0`。

## 原位修正错误标题

当已有草稿用了缩写或错误标题时：

1. 从每篇 `贴图指令.md` 读取完整标题并更新标题映射与草稿清单。
2. 保留 `公众号贴图草稿结果.json` 中原 `draft_media_id`。
3. 将对应结果状态设为 `needs_update`，不要删除草稿或清空永久素材缓存。
4. 重跑 drafts 阶段；脚本使用 `draft/update` 原位修正并用 `draft/get` 回查。
5. 审计确认草稿 ID 未变化且标题精确匹配。

不得通过新建草稿来替换可原位修复的草稿。

## 汇报结果

报告选题、文章、图片任务、已保存、失败、待修复、唯一草稿 ID 和公开发布数量，并给出系列目录、清单、结果与审计文件路径。不要仅凭进程退出码宣布成功。
