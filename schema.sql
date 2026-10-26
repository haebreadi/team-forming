-- 팀 편성 앱의 테이블(이름은 모두 tf_ 로 시작함, 공고 4.2절)
-- 앱이 시작할 때 이 파일을 그대로 실행하므로(create ... if not exists),
-- Supabase SQL 편집기에서 따로 만들 필요는 없음. 앱이 실제로 쓰는 테이블, 열, 제약과 같음.

-- 학생과 관리자. 비밀번호는 bcrypt 해시만 저장함
create table if not exists tf_students (
    id            bigserial primary key,
    student_no    text not null unique,                 -- 학번(유일). 관리자는 'admin'
    name          text not null,
    main_role     text check (main_role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너',
                                            '테스터', '보고서 작성자', '발표자')),
    password_hash text not null,                         -- bcrypt 해시(원래 비밀번호는 저장하지 않음)
    is_admin      boolean not null default false,
    check (is_admin or main_role is not null)            -- 학생은 주 역할이 꼭 있음
);

-- 보조 역할. 학생 1명에 0~2행
create table if not exists tf_student_sub_roles (
    student_id bigint not null references tf_students(id) on delete cascade,
    role       text not null check (role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너',
                                             '테스터', '보고서 작성자', '발표자')),
    ord        smallint not null default 1 check (ord between 1 and 2),
    primary key (student_id, role)
);

-- 팀. team_no 는 화면 key(team_1, apply_1 …)의 번호
create table if not exists tf_teams (
    id      bigserial primary key,
    team_no integer not null unique check (team_no >= 1),
    name    text not null unique
);

-- 팀원. (team_id, slot_no) 기본 키와 student_id 유일성 제약이
--   마지막 자리에 두 명이 동시에 들어갈 때 먼저 도착한 학생만 기록하고(3장),
--   한 학생이 한 팀에만 속하게 함
create table if not exists tf_members (
    team_id    bigint  not null references tf_teams(id) on delete cascade,
    slot_no    integer not null check (slot_no between 1 and 4),   -- 다섯 번째 자리를 막음
    student_id bigint  not null unique references tf_students(id) on delete cascade,
    joined_at  timestamptz not null default now(),
    primary key (team_id, slot_no)
);

-- 지원
create table if not exists tf_applications (
    id         bigserial primary key,
    team_id    bigint not null references tf_teams(id) on delete cascade,
    student_id bigint not null references tf_students(id) on delete cascade,
    status     text   not null check (status in ('대기', '승인', '거절')),
    reason     text,
    created_at timestamptz not null default now(),
    decided_at timestamptz
);
-- 한 학생은 결과를 기다리는 지원을 하나만 가질 수 있음(DB에서도 지킴)
create unique index if not exists tf_applications_one_pending
    on tf_applications (student_id) where status = '대기';

-- 투표. 한 팀원은 한 지원에 한 번만 투표함
create table if not exists tf_votes (
    application_id bigint not null references tf_applications(id) on delete cascade,
    voter_id       bigint not null references tf_students(id) on delete cascade,
    vote           text   not null check (vote in ('찬성', '거부')),
    created_at     timestamptz not null default now(),
    primary key (application_id, voter_id)
);

-- 마감 여부, 확정 여부, 배정안 등
create table if not exists tf_settings (
    key   text primary key,
    value text not null
);

-- AI 호출 기록(몇 차 모델이 어떻게 실패했는지 확인용)
create table if not exists tf_ai_logs (
    id          bigserial primary key,
    created_at  timestamptz not null default now(),
    attempt     integer not null,          -- 1, 2, 3차
    model       text not null,
    outcome     text not null,             -- 성공, 시간 초과, HTTP 429, 빈 응답, 검사 실패 …
    detail      text,
    elapsed_sec numeric(7, 1)
);

-- Supabase의 공개 API(PostgREST)로는 이 테이블을 읽고 쓰지 못하게 함.
-- 앱은 DB 주소(DATABASE_URL)로 테이블 주인 계정으로 접속하므로 영향이 없음
alter table tf_students          enable row level security;
alter table tf_student_sub_roles enable row level security;
alter table tf_teams             enable row level security;
alter table tf_members           enable row level security;
alter table tf_applications      enable row level security;
alter table tf_votes             enable row level security;
alter table tf_settings          enable row level security;
alter table tf_ai_logs           enable row level security;
