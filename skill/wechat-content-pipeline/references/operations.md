# Operations

## Environment

Use an ignored env file. Supported keys:

- `CONTENT_API_KEY` or fallback `APIMART_API_KEY`
- `CONTENT_API_URL`
- `CONTENT_MODEL`
- `APIMART_API_KEY`
- `APIMART_API_BASE`
- `APIMART_IMAGE_MODEL`
- `APIMART_CONCURRENCY` (maximum 3)
- `WECHAT_ACCESS_TOKEN`, or `WECHAT_APP_ID` plus `WECHAT_APP_SECRET`

Never print or commit their values.

## Review gates

1. Review the topic matrix before content generation.
2. Review article promises, source cards, and six-image text before paid image
   generation.
3. Run the image stage without `--execute-images` and confirm the task count.
4. Run the draft stage without `--save-drafts` and confirm the manifest.
5. Obtain explicit target-account authorization before `--save-drafts`.

## Resume behavior

- Content generation skips complete article directories unless `--force` is
  supplied.
- Image generation records prompt hashes and output paths in
  `image_generation_manifest.json`.
- Draft generation caches permanent MediaIDs by image SHA-256 and persists
  every returned draft MediaID before verification.
- Rerun the same command after interruption. Avoid `--force` unless the user
  requests replacement.

## Completion

Run `pipeline.py audit`. Do not infer success from a zero exit code on the
batch process alone. Require matching counts, six images per article, unique
draft identifiers, zero failures, zero pending updates, and published=0.
