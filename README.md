# tern-build

Tern 安卓客户端的打包流水线。这个仓库是公开的,GitHub Actions 对公开仓库免费;客户端源码在私有仓库
`leaderen/tern-android`,流水线用部署密钥拉取。

## 怎么打包

- **机器人**:`leaderen/tern-bot`,在 Telegram 里建品牌、点打包,APK 直接发回会话。
- **手动**:Actions → Build → Run workflow,填应用名 / 包名 / 面板地址等。签名用仓库 secrets 里的 `KEYSTORE_B64`。

## 仓库 secrets

| 名字 | 说明 |
|---|---|
| `SOURCE_REPO` | 客户端源码仓库,`leaderen/tern-android` |
| `SOURCE_SSH_KEY` | 上面那个仓库的只读部署密钥(私钥) |
| `TELEGRAM_BOT_TOKEN` | 机器人的 token。CI 用它把 APK 发回 Telegram,也用它取用户上传的 logo |
| `KEYSTORE_B64` / `KEYSTORE_PASSWORD` / `KEY_ALIAS` | 可选。手动打包用的签名(PKCS12 或 JKS 的 base64)。机器人打包时每个品牌自带签名,不用这个 |

## 流程

1. 读参数(`repository_dispatch` 的 `client_payload`,或手动输入)。
2. 拉源码;按 `core/` 内容缓存内核 `mihomo.aar`,源码没变就不重编(重编约十几分钟)。
3. `scripts/android_icons.py`:logo → 启动器图标 / 通知栏剪影 / 品牌色。
4. Gradle 出 release 包,`aapt` 核对包名。
5. 上传 artifact(保留 3 天),有 `notify_chat_id` 时发到 Telegram。

机器人传的参数(`client_payload`):`build_id`、`app_name`、`package_name`、`version_name`、`version_code`、`panel_urls`、
`api_prefix`、`config_urls`、`config_xor_key`、`logo_url` 或 `logo_file_id`、`brand_color`、`abis`、
`keystore_b64`、`keystore_password`、`key_alias`、`notify_chat_id`、`source_ref`。
