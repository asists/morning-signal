# Morning Signal — GitHub Pages 자동 갱신 버전

매일 오전 8시(한국시간)에 GitHub Actions가 네이버 뉴스 검색 API로 뉴스를 가져오고, Gemini API로 분석한 뒤 GitHub Pages에 정적 웹사이트로 배포합니다.

- 휴대폰에는 Python이나 별도 앱 설치가 필요 없습니다.
- API 키는 GitHub Secrets에만 저장되며 웹페이지로 전달되지 않습니다.
- 매일 생성된 브리핑은 `public/data/history/YYYY-MM-DD.json`에 누적됩니다.
- 사이트의 날짜 선택 메뉴에서 이전 브리핑을 볼 수 있습니다.
- API가 연결되기 전에는 샘플 화면이 표시됩니다.

## 1. GitHub 저장소 만들기

1. GitHub에서 새 저장소를 만듭니다.
2. 저장소 이름 예시: `morning-signal`
3. GitHub Free를 사용한다면 **Public** 저장소로 만드는 것이 가장 간단합니다.
4. 이 ZIP의 내용 전체를 저장소 최상단에 업로드합니다. `.github` 폴더도 빠뜨리면 안 됩니다.

최종 구조는 다음과 같아야 합니다.

```text
.github/workflows/update-and-deploy.yml
public/index.html
public/data/latest.json
scripts/generate_briefing.py
requirements.txt
README.md
```

## 2. API 키를 GitHub Secrets에 넣기

저장소에서 다음 메뉴로 이동합니다.

`Settings → Secrets and variables → Actions → New repository secret`

다음 3개 Secret을 각각 만듭니다.

| Secret 이름 | 넣을 값 |
|---|---|
| `NAVER_CLIENT_ID` | 네이버 검색 API Client ID |
| `NAVER_CLIENT_SECRET` | 네이버 검색 API Client Secret |
| `GEMINI_API_KEY` | Google AI Studio Gemini API Key |

키 값을 코드나 `public` 폴더에 직접 적지 마세요.

### Gemini 모델 바꾸기

기본 모델은 `gemini-2.5-flash-lite`입니다. 바꾸려면:

`Settings → Secrets and variables → Actions → Variables → New repository variable`

- Name: `GEMINI_MODEL`
- Value: 사용할 모델 코드

모델 변수는 선택 사항입니다.

## 3. GitHub Pages 켜기

저장소에서:

`Settings → Pages → Build and deployment → Source → GitHub Actions`

을 선택합니다.

## 4. 처음 한 번 직접 실행

1. 저장소 상단의 `Actions`로 이동합니다.
2. 왼쪽에서 `Morning Signal 자동 갱신`을 선택합니다.
3. `Run workflow`를 누릅니다.
4. 실행이 완료되면 `Settings → Pages`에 공개 주소가 표시됩니다.

주소 예시:

```text
https://사용자이름.github.io/morning-signal/
```

이 주소를 휴대폰에서 열고 **홈 화면에 추가**하면 앱처럼 사용할 수 있습니다.

## 5. 자동 갱신 시간

워크플로는 한국시간 매일 오전 8시에 실행되도록 설정되어 있습니다.

```yaml
schedule:
  - cron: '0 8 * * *'
    timezone: 'Asia/Seoul'
```

GitHub의 예약 작업은 서버 상황에 따라 몇 분 늦게 시작될 수 있습니다. 사이트에서 `최신본` 버튼을 누르면 이미 배포된 최신 JSON을 다시 불러옵니다. 버튼이 Gemini를 즉시 재호출하는 것은 아닙니다.

## 6. 실제 작동 흐름

```text
오전 8시 GitHub Actions 실행
        ↓
네이버 뉴스 API로 최근 40시간 뉴스 수집
        ↓
중복 기사 제거
        ↓
Gemini가 핵심 뉴스 3개와 투자 관점 분석
        ↓
latest.json + 날짜별 history 저장
        ↓
GitHub Pages 자동 재배포
        ↓
휴대폰 웹주소에서 확인
```

## 7. 화면 상태 확인

- 초록 점: 네이버 뉴스 + Gemini 분석 정상
- 주황 점: 샘플 또는 뉴스만 연결된 기본 요약
- 빨간 점: 일부 분석 오류가 기록됨

화면 하단에는 사용한 Gemini 모델과 데이터 상태가 표시됩니다.

## 8. 문제가 생겼을 때

### 계속 샘플 화면이 보임

- Secrets 이름의 대소문자가 정확한지 확인합니다.
- `.github/workflows/update-and-deploy.yml`이 업로드됐는지 확인합니다.
- Actions에서 수동 실행 후 로그의 `오늘 브리핑 생성` 단계를 확인합니다.

### 네이버 API 오류

- 네이버 개발자센터 애플리케이션에서 검색 API 사용 권한을 확인합니다.
- Client ID와 Client Secret을 반대로 입력하지 않았는지 확인합니다.

### Gemini 429 오류

무료 등급 호출 한도에 도달했을 가능성이 있습니다. 다음 날 자동 실행을 기다리거나 Google AI Studio에서 현재 한도를 확인합니다.

### 사이트가 404로 나옴

- `Settings → Pages`의 Source가 `GitHub Actions`인지 확인합니다.
- Actions의 배포 단계가 성공했는지 확인합니다.
- 저장소가 Private인 경우 사용 중인 GitHub 요금제에서 Pages가 지원되는지 확인합니다.

## 9. 수동으로 지금 다시 분석하기

GitHub 웹사이트에서:

`Actions → Morning Signal 자동 갱신 → Run workflow`

를 누르면 예약 시간을 기다리지 않고 새 브리핑을 생성할 수 있습니다.

## 10. 주의

- Gemini 무료 등급에서는 입력 데이터가 서비스 개선에 사용될 수 있습니다. 이 앱은 공개 뉴스만 전달하도록 구성되어 있습니다.
- 시장 지표는 공개 데이터 소스가 일시적으로 응답하지 않으면 일부 항목이 생략될 수 있습니다.
- 분석은 투자 판단 보조 자료이며 자동 매매 신호가 아닙니다.
