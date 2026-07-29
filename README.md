# WeChat Content Pipeline FDE Case

一个经过真实批量验证的 Codex Skill：把 PDF、Markdown 或文本资料转成公众号选题、原创六图内容、自动生图和公众号原生贴图草稿。

> 安全定义：本项目中的“发布阶段”默认是保存到公众号草稿箱。代码不包含正式发表、群发或 `freepublish` 能力。

## 能力范围

```text
资料输入
  -> 选题矩阵与来源映射
  -> 公众号正文与六图指令
  -> APIMart 生成 3:4 六图
  -> 微信官方 newspic 草稿 API
  -> 结果审计与断点恢复
```

核心 Skill 位于 [skill/wechat-content-pipeline](skill/wechat-content-pipeline)。

## 快速开始

1. 创建虚拟环境并安装依赖。
2. 将 `.env.example` 复制为本机 `.env`，填写所需凭证。
3. 安装或直接从本目录调用 Skill。
4. 按阶段执行并在每个外部操作前审核。

完整命令见 [本地使用说明.md](本地使用说明.md)。

## FDE 案例价值

这个案例不是“写几个提示词”，而是把一个真实业务流程产品化：

- 把不稳定的人工操作拆成可验证的数据契约；
- 在付费生图和账号写入前设置明确审批门；
- 用 manifest、哈希缓存和 MediaID 实现幂等与断点恢复；
- 把 40164 白名单、Unicode 编码和审批服务故障转成可复用处理规则；
- 用 50 篇、300 张图片、50 个唯一草稿标识验证规模化运行。

详见 [docs/FDE_CASE_STUDY.md](docs/FDE_CASE_STUDY.md)。

## 仓库边界

仓库不包含：

- API Key、AppSecret、access token；
- 公众号草稿 ID、MediaID 或出口 IP；
- 用户原始 PDF、生成图片或账号内容；
- 正式发表和群发功能。
