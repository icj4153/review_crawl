# GitHub Actions NAS CI/CD

`main` 브랜치에 push하면 GitHub Actions가 아래 순서로 실행됩니다.

1. Python 파일 문법 검사
2. URL 정규화/엑셀 저장 smoke test
3. Docker 이미지 빌드
4. NAS에 SSH 접속
5. NAS의 `/volume1/docker/review_crawl/nas_update.sh` 실행
6. `docker-compose up -d --build`로 웹 서비스를 재빌드/재시작

## GitHub Secrets

Repository `Settings > Secrets and variables > Actions > New repository secret`에 아래 값을 등록합니다.

| Secret | 설명 |
| --- | --- |
| `NAS_HOST` | NAS 접속 도메인 또는 IP |
| `NAS_PORT` | NAS SSH 포트 |
| `NAS_USER` | NAS SSH 사용자 |
| `NAS_SSH_PRIVATE_KEY` | GitHub Actions가 NAS에 접속할 private key 전체 내용 |
| `NAS_KNOWN_HOSTS` | `ssh-keyscan -p <포트> <호스트>` 결과 |

## NAS 최초 준비

NAS에 프로젝트를 한 번 clone합니다.

```bash
cd /volume1/docker
git clone git@github.com:icj4153/review_crawl.git review_crawl
cd /volume1/docker/review_crawl
```

private repo라면 NAS가 GitHub에서 pull할 수 있도록 deploy key를 준비합니다.

```bash
mkdir -p /volume1/docker/review_crawl_secrets
chmod 700 /volume1/docker/review_crawl_secrets
```

`/volume1/docker/review_crawl_secrets/github_deploy_key`에 GitHub deploy key private key를 저장합니다.

```bash
chmod 600 /volume1/docker/review_crawl_secrets/github_deploy_key
```

public key는 GitHub repository `Settings > Deploy keys`에 등록합니다. `Allow write access`는 필요 없습니다.

## 수동 배포 테스트

NAS에서 직접 아래 명령이 성공해야 GitHub Actions 배포도 성공합니다.

```bash
cd /volume1/docker/review_crawl
sh nas_update.sh
```

컨테이너 확인:

```bash
/usr/local/bin/docker ps --filter name=naver-review-web
/usr/local/bin/docker-compose logs --tail=60 review-web
```

## 웹 접속

기본 compose 설정은 리버스 프록시용입니다.

```yaml
ports:
  - "127.0.0.1:8502:8502"
```

LAN에서 바로 접속하려면 아래처럼 바꿉니다.

```yaml
ports:
  - "8502:8502"
```
