# 팀 프로젝트 팀 편성 앱

> ✏️ 표시가 있는 곳은 직접 채워야 함. 5~7항은 본인이 실제로 겪은 일과 그 근거(화면, 커밋, 시나리오 단계)만 적을 것(꾸며 내면 시나리오 밖 점수 0점, 공고 6.3절 20번).

## 1. 배포 주소
https://team-forming-grre2zgjfmp9krrgegyjvk.streamlit.app/

## 2. PRD와 구현 계획의 위치
- `docs/PRD.md`
- `docs/구현 계획.md`

## 3. 고른 해결 방법
| 기능 | 고른 해결 | 구현 |
|---|---|---|
| 다른 사람의 변경 보기 | 어려운 해결 | `st.fragment(run_every=3)` 로 학생·관리자 화면의 바뀐 부분만 3초마다 다시 그림 |
| 필수 포함 역할을 지킬 수 없는 팀 | 어려운 해결 | 지원할 때 "들어간 뒤 남는 자리 < 채우지 못한 필수 포함 역할 수"면 막음(`tf/rules.py` `join_problem`) |
| 같은 팀에 대기 중인 지원이 여럿 | 어려운 해결 | 모든 팀원이 찬성한 순간 규칙을 다시 검사(투표가 끝난 순서로 처리) |
| 마지막 자리에 두 명이 동시에 들어감 | 어려운 해결 | `tf_members` 의 `(team_id, slot_no)` 기본 키와 `student_id` 유일성 제약(`schema.sql`) |
| AI 응답이 늦을 때 | 어려운 해결 | 모델마다 부른 때부터 전체 120초를 재서 끊고 다음 모델로(`tf/ai.py` `call_model`) |
| 3차 모델까지 실패 | 어려운 해결 | 검사 다섯 가지를 모두 통과하는 배정을 모든 경우를 따져 찾음(`tf/rules.py` `solve`) |
| AI 대기 중 화면 | 어려운 해결 | "2차 모델(…)의 응답을 기다리는 중 · 35초 지남"처럼 차수와 경과 시간을 보여 줌 |
| DB 일시정지 막기 | 어려운 해결 | GitHub Actions 예약 작업이 매일 09:17(한국 시각)에 DB에 질의(`.github/workflows/keepalive.yml`) |

## 4. 쓴 모델 3개
- 1차 `google/gemma-4-31b-it:free`
- 2차 `poolside/laguna-s-2.1:free`
- 3차 `nvidia/nemotron-3-super-120b-a12b:free`

처음에는 1차 `nvidia/nemotron-3-super-120b-a12b:free`, 2차 `openai/gpt-oss-120b:free`, 3차 `google/gemma-4-31b-it:free` 를 썼으나, 10/26 쉬움 시나리오에서 세 모델이 모두 실패해(404, 무료 중단, 429) 위 모델로 바꿈.

## 5. 오픈라우터 AI 모델 호출 실패 사례
✏️ 관리자 화면의 "AI 배정 진행 안내"와 "AI 호출 기록과 설정"을 캡처해 `img/` 에 넣고 적음.
예 형식: "1차 모델이 120초 안에 응답하지 않아 2차 모델로 넘어감(화면 `img/ai-timeout.png`)"

## 6. 클로드가 잘못된 추론이나 답변을 한 사례
✏️ 본인이 클로드와 작업하며 알아챈 사례, 알아챈 방법, 고친 커밋 번호.
예 형식: "클로드가 …라고 했으나 연습용 중간 시나리오 6단계에서 …가 나와 알아챘고, 커밋 `abc1234`에서 고침"

## 7. 제출한 앱의 문제점이나 개선 필요 사항
✏️ 연습용 시나리오를 해 보며 찾은 것과 그 단계, 화면. "문제 없음"은 인정되지 않음.
`docs/구현 계획.md` 6절의 알려진 한계를 직접 확인해 보는 것에서 시작해도 됨.

---

## 실행 방법

### 로컬
1. 파이썬 3.11 이상에서 `pip install -r requirements.txt`
2. `.env.example` 을 복사해 이름을 정확히 `.env` 로 바꾸고 값을 채움(두 번 눌러 열지 말 것)
   - `DATABASE_URL`: Supabase 대시보드 > Connect > **Transaction pooler** 주소(포트 6543). `[YOUR-PASSWORD]` 를 DB 비밀번호로 바꿈
   - `OPENROUTER_API_KEY`: 오픈라우터 키
   - `ADMIN_PASSWORD`: 관리자 비밀번호(e-class 제출 칸에만 적음)
3. `streamlit run streamlit_app.py`
4. 처음 실행하면 `schema.sql` 이 실행되어 `tf_` 테이블이 만들어짐(이미 있으면 그대로 둠)

### 배포(Streamlit Community Cloud)
1. 깃허브 비공개 저장소에 올림(`.env` 가 올라가지 않았는지 확인)
2. Streamlit Workspace settings > Linked accounts > Private access 옆 "Connect here"
3. Create app: Branch `main`(또는 `master`), Main file path `streamlit_app.py`
4. Advanced settings > Secrets에 `.env` 와 같은 이름으로 넣음
   ```
   OPENROUTER_API_KEY = "sk-or-v1-..."
   DATABASE_URL = "postgresql://..."
   ADMIN_PASSWORD = "..."
   ```
5. 배포 뒤 Settings > Sharing > "This app is public and searchable" > Save changes
6. 시크릿 창으로 배포 주소를 열어 로그인 화면이 보이는지 확인

### DB 일시정지 막기
저장소 Settings > Secrets and variables > Actions > New repository secret 에 `DATABASE_URL` 을 넣음. Actions 탭 > "DB 일시정지 막기" > Run workflow 로 한 번 실행해 성공하는지 확인. 그 뒤로 매일 자동으로 실행됨.

### 시험
로컬 PostgreSQL 이 있으면 다음으로 연습용 시나리오를 단계마다 확인할 수 있음(시험용 DB를 지우고 다시 만드니 Supabase 주소로는 돌리지 말 것).
```
TF_TEST_PSQL="-h localhost -U postgres -d tftest" TF_CSV_DIR=(CSV 폴더) python tests/test_scenarios.py
python tests/test_rules.py
```

### 제출용 압축 파일
커밋한 파일만 압축함(숨은 파일 `.env` 가 들어가지 않게): `git archive -o 학번.zip HEAD` 뒤 `unzip -l 학번.zip` 으로 `.env` 가 없는지 확인.
