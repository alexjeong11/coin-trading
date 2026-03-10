#!/bin/bash

echo "🚀 Bithumb Trading Bot 자동 배포를 시작합니다..."

# 1. 기존에 돌고 있는 봇과 API 서버 안전하게 종료
echo "🛑 기존 백그라운드 프로세스 종료 중..."
pkill -f 'bot\.py'
pkill -f 'api_server\.py'
sleep 2 # 프로세스가 완전히 죽을 때까지 대기

# 2. OpenClaw 스킬 설정 업데이트 
echo "🔄 OpenClaw 메신저 연동 스킬 동기화 중..."
mkdir -p /Users/jeongcheol/.openclaw/workspace/skills/bithumb-bot
cp openclaw_skill/SKILL.md /Users/jeongcheol/.openclaw/workspace/skills/bithumb-bot/SKILL.md

# 3. 새로운 로직으로 봇과 API 서버 재시작
echo "🟢 봇 및 API 서버 재가동 중..."
# 프로젝트 가상환경 활성화
source venv/bin/activate

# 기존 로그 백업 후 백그라운드 실행
nohup python -u bot.py >> nohup.out 2>&1 &
nohup python -u api_server.py >> nohup_api.out 2>&1 &

echo "✅ 배포 완료! 자동매매 봇과 API 서버가 최신 로직으로 구동되었습니다."
