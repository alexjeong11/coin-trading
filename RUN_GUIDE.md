# 빗썸 자동매매 봇 실행 가이드 (Mac Mini 기준)

본 가이드는 작성된 `bot.py`를 맥미니(Mac Mini) 환경에서 24시간 백그라운드로 안전하게 구동하기 위한 방법을 안내합니다.

## 1. 초기 설정 (필수)

### 가상환경 생성 및 패키지 설치
맥미니의 터미널(Terminal)을 열고, 프로젝트 디렉토리로 이동한 뒤 아래 명령어를 차례대로 입력하세요.

```bash
# 1. 프로젝트 폴더로 이동 (경로는 본인 환경에 맞게 수정)
cd /Users/jeongcheol/Documents/ai-projects/coin-trading

# 2. 파이썬 가상환경(venv) 생성
python3 -m venv venv

# 3. 가상환경 활성화
source venv/bin/activate

# 4. 필수 패키지 설치
pip install -r requirements.txt
```

### API 키 설정 (.env 파일)
프로젝트 폴더 내에 있는 `.env.example` 파일을 복사하여 `.env` 파일을 생성합니다.

```bash
cp .env.example .env
nano .env
```
발급받은 빗썸 API의 `Connect Key`와 `Secret Key`를 입력하고 저장합니다. (Ctrl+O, Enter, Ctrl+X)

---

## 2. 백그라운드 무중단 실행 방법 (nohup 활용)

작성된 코드는 `bot.py`(자동매매 핵심 로직)와 `api_server.py`(텔레그램-OpenClaw 연동 API 서버) 두개로 구성되어 있습니다. `crontab` 대신 `nohup`을 통해 두 스크립트를 백그라운드에 계속 띄워두는 것이 효율적입니다.

### 봇 및 API 서버 실행하기 (터미널 창을 닫아도 유지됨)
```bash
# 가상환경이 활성화된 상태에서 아래 명령어 실행 (순서대로)
nohup python bot.py &
nohup python api_server.py &
```
- 실행 후 각각 `[1] 12345`, `[2] 12346`과 같은 프로세스 ID(PID)가 출력되며 백그라운드 구동을 시작합니다.
- 터미널을 종료하거나 SSH 접속을 끊어도 봇은 맥미니가 켜져있는 한 계속 동작합니다.

### 실행 상태 및 로그 확인하기
봇은 두 가지 방식으로 로그를 남깁니다.
1. `nohup.out` (print문 및 시스템 에러 출력)
2. `trade.log` (봇 알고리즘에서 기록하는 체결 및 상태 로그)

실시간으로 봇이 잘 동작하는지 보려면 아래 명령어를 사용하세요.
```bash
# 실시간 매매 로그 확인 (종료하려면 Ctrl + C)
tail -f trade.log
```

---

## 3. 프로그램 종료 방법

자동매매를 완전히 중단하려면 텔레그램 OpenClaw에서 "매매 봇 중지해줘" 라고 명령하거나 직접 터미널에서 강제 종료해야 합니다.

```bash
# 백그라운드 프로세스 중 모두 종료 (추천)
pkill -f 'python bot.py'
pkill -f 'python api_server.py'
```

---

## (참고) Crontab을 사용하는 방식의 경우
만약 내부 `while True` 루프를 지우고 순수하게 1회 매매를 수행하는 스크립트로 변경하여, 맥의 스케줄러를 활용하고 싶다면 `crontab`을 셋팅할 수 있습니다.
그러나 맥(macOS)에서는 시스템 절전 모드 등의 이유로 `cron`보다 `launchd`를 권장하며, 개인용 봇의 경우 위에서 제시한 `nohup` + `while True` 루프 방식이 가장 설정이 간편하고 직관적입니다. (디스플레이가 꺼지더라도 Mac이 잠자기 모드에 들어가지 않도록 시스템 설정에서 "디스플레이가 꺼졌을 때 Mac이 자동으로 잠자지 않게 방지" 옵션을 켜두는 것을 권장합니다.)
