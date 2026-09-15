#!/usr/bin/env python3
"""
get-gmail-token.py — 一次性获取 Gmail refresh_token
在本地 Mac 上运行一次，将输出的 refresh_token 填入 GitHub Secrets。

前提：已在 Google Cloud Console 为 OAuth App 添加以下 redirect URI：
  http://localhost

依赖安装：
  pip install google-auth-oauthlib

用法：
  python3 get-gmail-token.py

成功后会在浏览器中弹出 Google 授权页面，授权完成后终端输出 refresh_token。
"""

import os
import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("❌ 缺少依赖，请先运行：pip install google-auth-oauthlib")
    sys.exit(1)

CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ 缺少 GMAIL_CLIENT_ID 或 GMAIL_CLIENT_SECRET 环境变量")
    print("   本地运行: infisical run -- python3 get-gmail-token.py")
    sys.exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/adwords",
]

CLIENT_CONFIG = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, SCOPES)

print("\n" + "=" * 60)
print("即将打开浏览器，请用 soundboxbooth@gmail.com 登录并授权")
print("=" * 60)

creds = flow.run_local_server(
    port=0,
    authorization_prompt_message="浏览器应已自动打开，请完成授权: {url}",
    success_message="授权成功！可以关闭此页面了。",
    open_browser=True,
    access_type="offline",
    prompt="consent",
)

print("\n" + "=" * 60)
print("✅ 授权成功！请将 refresh_token 填入 GitHub Secrets：")
print("=" * 60)
print(f"\nGMAIL_REFRESH_TOKEN:\n{creds.refresh_token}")
print("\n" + "=" * 60)
print("⚠️  refresh_token 请妥善保管，不要提交到 Git 仓库！")
print("    建议填完 GitHub Secrets 后立即执行 clear 清除终端记录")
