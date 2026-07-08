# 네이버 스토어 리뷰 수집기

네이버 스마트스토어/브랜드스토어 상품 리뷰를 수집해 엑셀 파일로 저장하는 도구입니다.

두 가지 방식으로 사용할 수 있습니다.

- Windows 데스크톱 앱: `app.py` 또는 빌드된 exe
- NAS 웹 서비스: `web_app.py` 또는 Docker Compose

## 설치

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Windows 데스크톱 앱은 설치된 Microsoft Edge를 사용합니다. NAS 웹 서비스도 Docker 이미지 안에 Linux용 Microsoft Edge Stable을 설치해 사용하며, 기본값은 브라우저 화면 보기 모드입니다.

## Windows 앱 실행

```powershell
python app.py
```

1. 네이버 스마트스토어 또는 브랜드스토어 상품 URL을 입력합니다.
2. 최대 리뷰 수를 지정합니다.
3. `실행`을 누르면 Edge가 앱 전용 임시 프로필로 열립니다.
4. 앱이 광고/추적 파라미터를 제거한 기본 상품 URL로 자동 접속합니다.
5. `리뷰` 탭과 `리뷰 전체보기` 버튼을 누른 뒤 화면 리뷰를 수집합니다.
6. 수집이 끝나면 `저장`을 눌러 `.xlsx` 파일을 저장합니다.

## 웹 서비스 실행

네이버 로그인이 필요한 경우 `.env` 파일을 만들고 아래 값을 설정합니다. `.env`는 GitHub에 올리지 않습니다.

```text
NAVER_LOGIN_ID=네이버아이디
NAVER_LOGIN_PASSWORD=네이버비밀번호
```

로컬 테스트:

```powershell
python web_app.py
```

Docker/NAS 실행:

```bash
docker compose up -d --build
```

기본 포트는 `8502`입니다. `docker-compose.yml`은 `127.0.0.1:8502`로 바인딩되어 있으므로, NAS 리버스 프록시를 붙이거나 LAN에서 바로 열려면 포트 바인딩을 `8502:8502`로 바꾸면 됩니다. 웹 서비스 화면은 일반 HTTP 폴링 방식이라 Streamlit WebSocket 설정이 필요하지 않습니다.

수집 화면의 `브라우저 화면 보기`를 켜면 Edge를 headless가 아닌 화면 보기 모드로 실행하고, 현재 브라우저 화면을 웹 페이지에서 자동 갱신합니다. NAS Docker에서는 가상 디스플레이를 사용하므로 실제 모니터 창 대신 웹 화면 안에서 확인합니다.

## GitHub Actions CI/CD

`main` 브랜치에 push하면 `.github/workflows/ci-cd.yml`이 Python smoke test, Docker build, NAS 배포를 실행합니다.

NAS 배포 설정은 [CI_CD_NAS_DEPLOY.md](CI_CD_NAS_DEPLOY.md)를 참고하세요.

## 저장 형식

엑셀 컬럼은 아래 순서로 저장합니다.

`리뷰번호, 상품번호, 상품이름, 작성자, 작성일, 평점, 리뷰, 옵션, 첨부파일`

`옵션` 컬럼에는 리뷰 화면에서 확인된 구매 옵션을 저장합니다. `첨부파일` 컬럼에는 화면에서 확인된 이미지 URL을 쉼표로 연결해 저장합니다. 별도 이미지 폴더나 이미지 파일은 생성하지 않습니다. 화면 수집 모드에서는 네이버가 화면에 표시하지 않는 실제 리뷰번호 대신 `screen-...` 형식의 내부 식별자를 사용합니다.

## EXE 빌드

```powershell
.\build.ps1
```

빌드 결과는 `dist\NaverSmartStoreReviewCollector.exe`에 생성됩니다.
