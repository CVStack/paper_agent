# Paper Agent 🤖

관심 있는 연구 논문을 등록해두면, 해당 논문을 인용한 최신 신규 논문들을 자동으로 추적하고 AI를 통해 분석 및 요약해주는 에이전트입니다.

## ✨ 주요 기능

- **논문 모니터링**: `papers.json`에 등록된 핵심 논문들을 주기적으로 감시합니다.
- **신규 인용 추적**: Semantic Scholar API를 활용하여 타겟 논문을 인용한 최신 연구를 감지합니다.
- **심층 내용 분석**:
  - **PDF 파싱**: 논문 원문을 다운로드하고, Gemini 1.5 Flash를 이용해 노이즈를 제거하고 핵심 섹션(Intro, Method, Exp, Conclusion)을 구조화하여 추출합니다.
  - **AI 분류**: 인용 논문이 타겟 논문과 동일한 과업(Task)을 다루는지 판단합니다.
  - **AI 요약**: Gemini 2.5 Pro를 활용하여 연구자를 위한 고품질 기술 요약본을 생성합니다.
- **자동 아카이빙**: 분석된 내용은 `summaries/` 폴더에 Markdown 형식으로 깔끔하게 저장됩니다.

## 🛠️ 설치 방법 (uv 사용 권장)

이 프로젝트는 `uv`를 사용하여 패키지 및 환경을 관리하는 것을 권장합니다.

### 1. 레포지토리 클론
```bash
git clone https://github.com/your-username/paper-agent.git
cd paper-agent
```

### 2. 가상환경 생성 및 패키지 설치
`uv`를 사용하는 경우:
```bash
# 가상환경 생성
uv venv

# 가상환경 활성화
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 패키지 설치
uv pip install -r requirements.txt
```

## ⚙️ 설정

### 1. 환경 변수 설정
`.env.example` 파일을 복사하여 `.env` 파일을 생성하고, 필요한 API 키를 입력하세요.

```bash
cp .env.example .env
```

- `GEMINI_API_KEY`: Google Gemini API 키 (필수)
- `SEMANTIC_SCHOLAR_API_KEY`: Semantic Scholar API 키 (권장, 없을 경우 속도 제한 있음)
- `CLASSIFICATION_MODEL`: 구조화 및 분류용 모델 (기본값: `gemini-1.5-flash`)
- `SUMMARIZATION_MODEL`: 요약용 모델 (기본값: `gemini-2.5-pro`)

### 2. 추적할 논문 등록
`papers.example.json`을 참고하여 `papers.json` 파일을 생성하고, 추적하고 싶은 논문 정보를 입력하세요.

```json
[
  {
    "id": "ARXIV:1706.03762", 
    "alias": "Transformer"
  },
  {
    "id": "Semantic Scholar ID 입력",
    "alias": "My Target Paper"
  }
]
```
- `id`: Semantic Scholar Paper ID 또는 ArXiv ID (예: `ARXIV:2310.xxxxx`)
- `alias`: 결과물 폴더명으로 사용될 별칭

## 🚀 실행

```bash
python agent.py
```

스크립트가 실행되면 주기적으로(기본 1시간) 논문을 확인하고 새로운 인용이 발견되면 `summaries/` 폴더에 요약본을 저장합니다.
