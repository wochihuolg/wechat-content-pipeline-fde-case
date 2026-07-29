# WeChat official newspic draft API

Use only documented server APIs under `https://api.weixin.qq.com`.

## Sequence

1. Obtain an access token with `GET /cgi-bin/token` when AppID/AppSecret are
   used.
2. Upload each image as permanent material with
   `POST /cgi-bin/material/add_material?type=image`.
3. Create the image draft with `POST /cgi-bin/draft/add` and
   `article_type: "newspic"`.
4. Verify the returned MediaID with `POST /cgi-bin/draft/get`.
5. Repair the same draft with `POST /cgi-bin/draft/update` when needed.

Send JSON as UTF-8 bytes with
`Content-Type: application/json; charset=utf-8`. Some accounts persist literal
`\\uXXXX` text when clients send ASCII-escaped JSON.

Use six permanent image MediaIDs in manifest order; the first image is the
cover. A newspic draft supports at most 20 images. Permanent images support
BMP, PNG, JPEG/JPG, and GIF up to 10 MB.

## Safety

- Persist a returned draft MediaID before verification.
- Cache image MediaIDs by SHA-256.
- If error `40164` occurs, add the current egress IP to the account whitelist.
- Never call `/cgi-bin/freepublish/*`, mass-send, or publication endpoints.

Official documentation:

- https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add.html
- https://developers.weixin.qq.com/doc/service/api/material/permanent/api_addmaterial.html
