# WeChat Content Pipeline

把 PDF、Markdown 或纯文本资料转化为可审阅、可恢复的微信公众号六图内容与原生贴图草稿。

这不是一个只负责写文案的提示词集合，而是一套分阶段执行的内容生产流水线：先建立选题与来源映射，再独立重写内容和六图脚本，随后按需生成图片，最后通过微信官方 API 保存到公众号草稿箱，并对整批结果进行审计。

> 安全边界：本项目中的“发布”只指保存为公众号原生 newspic 草稿。项目不提供正式发表、群发或 freepublish 能力。

## 包含的 Skills

- `wechat-content-pipeline`：通用的来源到公众号六图草稿流水线；
- `wechat-white-minimal-article`：中文显示名“公众号白底极简风格公众号文章”，专门创作“每天介绍一个 AI 产品：{产品名}”栏目文章，固定白底极简六图，并校验后台完整标题不得缩写。

## 适合解决什么问题

- 从长 PDF 或知识资料中规划一批不重复的公众号选题；
- 为每篇内容保留来源依据，避免选题与原资料失去对应关系；
- 独立重写公众号内容，避免复制原文、截图、版式或第三方水印；
- 固定输出 1 张封面图和 5 张内容图，形成可复用的六图结构；
- 批量调用 APIMart 生图，并在中断后从已有结果继续；
- 使用微信公众号官方接口创建原生贴图草稿；
- 用 manifest、MediaID 缓存和审计报告识别失败、重复或待修复项。

## 适合与不适合

适合：

- 有明确知识来源，需要连续拆成多篇六图内容；
- 希望先人工审核选题，再按批次生成正文和图片；
- 需要把内容安全地保存到公众号草稿箱；
- 任务可能中断，需要从已有图片和草稿状态继续；
- 希望把一次内容项目沉淀成可复用流程。

不适合：

- 直接复制或改写受版权保护的公众号文章；
- 去除已有图片上的水印后重新发布；
- 无人工审核地自动正式发表或群发；
- 只需要临时生成一张配图、不需要批次状态；
- 用于绕过公众号平台权限、审核或安全限制。

## 完整工作流

    PDF / Markdown / TXT
            |
            v
    资料切片与来源编号
            |
            v
    选题矩阵 --人工审核--> 正文与六图指令
                                |
                                v
                       生图任务清单（dry-run）
                                |
                         明确授权后执行
                                v
                           3:4 六图素材
                                |
                                v
                      草稿任务清单（dry-run）
                                |
                         明确授权后执行
                                v
                      微信原生 newspic 草稿
                                |
                                v
                       批次审计与断点恢复

## 核心设计

### 内容与来源可追溯

选题不是从资料中随机摘句。每个选题都绑定来源切片、目标读者、单一痛点、单一承诺和读者交付物。后续正文和六图指令继承这份映射，便于审核内容是否偏离原始知识。

### 生成与账号写入默认不执行

选题与内容阶段可以直接运行；付费生图和公众号写入阶段默认只生成任务清单。只有显式加入 --execute-images 或 --save-drafts 才会产生外部操作。

### 批处理可恢复

所有关键状态都会落盘。图片按 SHA-256 缓存永久素材 MediaID，草稿返回标识会在回查前持久化。任务中断后重跑同一阶段即可继续，不需要从第一篇重新开始。

### 草稿而非正式发表

流水线只把结果保存到公众号草稿箱，保留人工预览、修改和最终决策。代码中没有正式发表与群发端点。

## 每篇内容的输出契约

每个文章目录必须包含：

    <文章目录>/
    ├── 来源卡.md
    ├── 公众号终稿_图片标注版.md
    ├── 贴图指令.md
    └── 贴图/
        ├── 01-封面.png
        ├── 02-内容.png
        ├── 03-内容.png
        ├── 04-内容.png
        ├── 05-内容.png
        └── 06-内容.png

六图职责建议：

1. 封面：明确痛点、结果或反常识判断；
2. 现状：让读者快速代入问题；
3. 原因：解释问题为什么发生；
4. 方法：给出核心框架或操作路径；
5. 示例：展示具体做法、清单或对比；
6. 钩子：总结行动，并承接下一篇内容。

## 仓库结构

    wechat-content-pipeline/
    ├── skill/wechat-content-pipeline/
    │   ├── SKILL.md
    │   ├── agents/openai.yaml
    │   ├── references/
    │   └── scripts/
    ├── skill/wechat-white-minimal-article/
    │   ├── SKILL.md
    │   ├── agents/openai.yaml
    │   ├── assets/公众号文章模板.md
    │   ├── references/
    │   └── scripts/
    ├── examples/source.md
    ├── tests/test_pipeline.py
    ├── 本地使用说明.md
    ├── install-local.ps1
    ├── requirements.txt
    └── .env.example

核心入口是 skill/wechat-content-pipeline/scripts/pipeline.py。Skill 负责告诉 Codex 何时读取规则、在哪些阶段停下来审核，以及如何安全恢复；Python 脚本负责稳定执行可重复的机械步骤。

## 安装

环境要求：Windows PowerShell、Python 3.10 或更高版本。选题和写作需要模型 API Key；生图需要 APIMart API Key；保存公众号草稿需要微信公众号开发凭据或有效 access token。

    git clone https://github.com/wochihuolg/wechat-content-pipeline-fde-case.git wechat-content-pipeline
    cd wechat-content-pipeline
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    Copy-Item .env.example .env
    .\install-local.ps1
    .\install-local.ps1 -SkillName wechat-white-minimal-article

编辑本机 .env，不要把它提交到 Git。

## 在 Codex 中调用

安装 Skill 后，可以直接用自然语言描述目标。建议同时提供来源路径、批次数量、输出目录和当前允许执行到哪个阶段。

示例：

    使用 $wechat-content-pipeline，把 D:\资料\guide.pdf 拆成 10 个不重复的公众号六图选题。
    先只生成选题矩阵，不生图、不写公众号，输出到 D:\output\guide-batch-01。

    使用 $wechat-content-pipeline，继续完善 D:\output\guide-batch-01 中已审核的选题。
    生成正文和六图指令，保持独立重写，不使用来源截图、排版或水印。

    使用 $wechat-content-pipeline，检查 D:\output\guide-batch-01 的生图任务。
    先 dry-run，报告待生成数量、缺失项和预计外部调用数，不要实际生图。

    使用 $wechat-content-pipeline，审计 D:\output\guide-batch-01。
    检查每篇是否六图、草稿标识是否唯一，并确认 published 为 0。

    使用 $wechat-white-minimal-article，把 D:\资料\ai-products.md 写成 10 篇白底极简 AI 产品拆解。
    后台标题必须完整使用“每天介绍一个 AI 产品：{产品名}”，先生成文章和六图指令，不要生图。

    使用 $wechat-white-minimal-article，检查并原位修正 D:\output\ai-products 中已有公众号草稿的错误短标题。
    保留原 MediaID，使用 draft/update 修正并逐篇回查，不要创建重复草稿。

Codex 应在选题审核、付费生图和公众号写入这三个节点明确停下来。用户只授权前一阶段时，不应自动进入后一阶段。

## 快速开始

    $scripts = ".\skill\wechat-content-pipeline\scripts"
    $source = "D:\资料\guide.pdf"
    $series = ".\output\guide-series"

### 第一步：规划选题

    python "$scripts\pipeline.py" topics --source $source --series-dir $series --count 10 --env-file ".\.env"

审核 topic_plan.json 和 选题矩阵.md，重点检查重复承诺、来源失配、事实时效和六图可展开性。

### 第二步：生成正文与六图指令

    python "$scripts\pipeline.py" content --source $source --series-dir $series --env-file ".\.env"

完整文章目录会自动跳过。只有明确需要重做时才加入 --force。

### 第三步：生成图片

先 dry-run，确认任务数量：

    python "$scripts\pipeline.py" images --series-dir $series --env-file ".\.env"

确认后执行付费生图：

    python "$scripts\pipeline.py" images --series-dir $series --env-file ".\.env" --execute-images

### 第四步：保存公众号草稿

先构建并检查草稿清单：

    python "$scripts\pipeline.py" drafts --series-dir $series --env-file ".\.env"

确认公众号账号和 IP 白名单后保存原生贴图草稿：

    python "$scripts\pipeline.py" drafts --series-dir $series --env-file ".\.env" --save-drafts

### 第五步：审计

    python "$scripts\pipeline.py" audit --series-dir $series

审计要求包括：选题数、文章目录数和草稿数一致；每篇正好六图；草稿标识唯一；failed=0、needs_update=0、published=0。

更完整的命令与故障恢复方式见 [本地使用说明.md](本地使用说明.md)。

## 状态文件与断点恢复

| 文件 | 作用 |
|---|---|
| topic_plan.json | 选题、来源映射和内容状态 |
| image_generation_manifest.json | 生图任务、输出路径和失败状态 |
| 公众号贴图草稿清单.json | 待保存的标题、摘要和六图顺序 |
| 公众号贴图草稿结果.json | MediaID、草稿标识、保存或修复状态 |
| 审计报告 | 批次数量、重复项、缺失项和发布边界 |

恢复原则是“重跑同一阶段”，不是清空重来。不要在失败后删除 manifest，也不要在已有草稿标识时重新创建重复草稿。

## 常见故障

- 40164：当前出口 IP 未加入公众号后台白名单；配置后重跑草稿阶段。
- 标题显示字面量 Unicode：保留原草稿 MediaID，以 UTF-8 JSON 原位更新。
- 生图中断：保留 image_generation_manifest.json，不加 --force 继续运行。
- 草稿创建成功但回查失败：保留草稿结果文件，重跑时修复同一草稿。
- 只想检查本地产物：执行审计脚本并加入 --allow-local-only。

## 扩展到其他渠道

内容规划、六图契约、状态文件和审计逻辑与渠道无关。若要扩展到其他图文平台，优先新增独立 channel adapter，不要把渠道字段写进选题模型。这样可以继续复用：

- 来源切片与选题去重；
- 内容和图片任务契约；
- dry-run 与显式执行门；
- 图片哈希缓存；
- 外部对象标识持久化；
- 批次审计和失败恢复。

多用户、多账号或高并发场景下，可把本地 JSON 状态迁移到数据库与任务队列，并把图片迁移到对象存储；单用户几十篇的批次仍优先保持当前轻量结构。

## 安全与内容边界

本仓库不会提交或输出：

- API Key、AppSecret、access token 或账号标识；
- 公众号草稿 ID、MediaID 或出口 IP；
- 用户原始资料、生成图片或账号内容；
- 原文长段复制、原截图、原排版或第三方水印；
- 正式发表、群发与 freepublish 功能。

## 测试

    python -m unittest discover -s tests -v

当前测试覆盖资料按标题切片、选题去重、六图契约、完整栏目标题、标题映射阻断和完整批次审计。
