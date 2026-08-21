#!/bin/sh
# 毎朝のビルド後に Netlify へ本番公開する。
set -e
cd /Users/apple/gallery-digest/deploy
netlify deploy --prod --dir . --functions netlify/functions
