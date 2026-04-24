# Nosearch Static Prototype

노써치 HTML 프로토타입을 GitHub와 Vercel에 연결해 테스트 배포하기 위한 정적 사이트 폴더입니다.

## Files

- `index.html`: 랜딩 페이지
- `main.html`: 메인 페이지 시안
- `design-system.html`: 디자인 시스템 레퍼런스
- `service-storyboard.html`: 서비스 스토리보드
- `update-schedule.html`: 업데이트 일정 페이지

## Deploy

1. GitHub에 이 폴더를 새 리포지토리로 업로드합니다.
2. Vercel에서 해당 GitHub 리포지토리를 Import 합니다.
3. Framework Preset은 `Other`를 선택합니다.
4. Build Command와 Output Directory는 비워둡니다.
5. Deploy를 누르면 바로 정적 사이트로 배포됩니다.

## Notes

- 기본 진입 주소는 `index.html`입니다.
- 페이지 간 내부 링크는 현재 배포용 파일명 기준으로 연결되어 있습니다.
- 디자인 변경 시 `design-system.html`의 토큰과 원칙을 먼저 확인한 뒤 다른 페이지에 반영하는 흐름을 권장합니다.
