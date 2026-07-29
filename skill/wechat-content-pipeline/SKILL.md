---
name: wechat-content-pipeline
description: "Turn PDF, Markdown, or text source material into a reviewable WeChat Official Account native image-post pipeline: plan non-duplicative topics with source traceability, independently rewrite articles and six-image prompts, generate images through APIMart, save resumable official newspic drafts, and audit the batch. Use for 公众号选题、公众号贴图、六图内容、批量生图、公众号草稿、知识资料内容化、内容流水线, or when Codex must reproduce a source-to-draft workflow without fragile browser automation."
---

# WeChat Content Pipeline

Build a human-reviewed path from source material to native six-image WeChat
drafts. Keep every stage resumable and observable.

Use the scripts for deterministic execution and keep editorial decisions at
the explicit review gates. The expected flow is source to topic plan, article
and six-image contract, image manifest, official newspic draft manifest, saved
drafts, then audit.

## Protect the boundary

- Treat source material as read-only.
- Independently rewrite; do not copy layouts, screenshots, watermarks, or long
  passages.
- Keep exactly one cover plus five content images per post.
- Default to dry-run for image generation and draft creation.
- Save drafts only after the user explicitly authorizes the target account.
- Never call free-publish, mass-send, or publication endpoints.
- Keep API keys and account identifiers in an ignored local env file.

Read [references/content-contract.md](references/content-contract.md) before
planning or writing. Read
[references/operations.md](references/operations.md) before image or draft
operations. Read
[references/wechat-official-newspic-api.md](references/wechat-official-newspic-api.md)
before touching a WeChat account.

## Run the stages

Use `scripts/pipeline.py` as the normal entry point.

### 1. Plan topics

```powershell
python scripts/pipeline.py topics `
  --source "<source.pdf>" `
  --series-dir "<output-directory>" `
  --count 10 `
  --env-file "<local-env-file>"
```

Review `topic_plan.json` and `选题矩阵.md`. Reject duplicate promises,
weak source references, stale facts, and topics that cannot fill six useful
images.

### 2. Write content and image instructions

```powershell
python scripts/pipeline.py content `
  --source "<source.pdf>" `
  --series-dir "<output-directory>" `
  --env-file "<local-env-file>"
```

Require every article directory to contain:

- `来源卡.md`
- `公众号终稿_图片标注版.md`
- `贴图指令.md`

Require `贴图指令.md` to contain a compact title, description, and exactly six
numbered image sections.

### 3. Generate images

Dry-run first:

```powershell
python scripts/pipeline.py images --series-dir "<output-directory>" `
  --env-file "<local-env-file>"
```

Generate only after the task count is correct:

```powershell
python scripts/pipeline.py images --series-dir "<output-directory>" `
  --env-file "<local-env-file>" --execute-images
```

Do not use `--force` unless the user explicitly requests regeneration.

### 4. Save WeChat drafts

Build and validate the manifest without account mutation:

```powershell
python scripts/pipeline.py drafts --series-dir "<output-directory>" `
  --env-file "<local-env-file>"
```

After explicit authorization, save native image drafts:

```powershell
python scripts/pipeline.py drafts --series-dir "<output-directory>" `
  --env-file "<local-env-file>" --save-drafts
```

The draft runner must persist permanent-image MediaIDs by SHA-256, persist a
returned draft MediaID before verification, resume after interruption, and
repair an existing `needs_update` draft in place.

### 5. Audit

```powershell
python scripts/pipeline.py audit --series-dir "<output-directory>"
```

Completion requires:

- planned count equals article-directory count;
- each article has the three required Markdown files;
- each article has exactly six generated sticker images;
- every draft result is `saved`;
- every draft has a unique `appmsgid` or `draft_media_id`;
- failed and `needs_update` counts are zero;
- published count is explicitly zero.

## Recover safely

- Rerun the same stage; scripts skip current outputs.
- If image generation stops, retain `image_generation_manifest.json` and
  resume without `--force`.
- If draft creation returns a MediaID but verification fails, retain
  `公众号贴图草稿结果.json`; rerun to update that same draft.
- If WeChat returns error `40164`, add the current egress IP to the official
  account whitelist, then resume.
- If a title returns as literal `\\uXXXX`, keep the MediaID and repair the
  same draft with explicit UTF-8 JSON.

Report stage counts and paths. Never report success from a process exit alone;
read the manifests and run the audit.
