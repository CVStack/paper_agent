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

## 🌊 워크플로우 (Workflow)

에이전트는 비용 효율성과 정확도를 모두 잡기 위해 다음과 같은 지능적 워크플로우에 따라 동작합니다.

```mermaid
graph TD
    A[신규 인용 논문 발견] --> B{API가 제공한<br>깨끗한 초록이 있는가?};
    B -- 예 (경로 A) --> C[1-A단계 분류<br>(단순 초록 비교)];
    B -- 아니오 (경로 B) --> D[PDF 초반부<br>Raw Text 추출];
    D --> E[1-B단계 분류<br>(AI가 초록 추출 후 분류)];
    C --> F{1단계 분류 결과가<br>'UNCERTAIN'인가?};
    E --> F;
    F -- 아니오 ('same_task') --> G((비용 절감));
    G --> H[최종 분류: 'same_task'];
    
    F -- 예 ('uncertain') --> I[전체 PDF<br>Raw Text 추출];
    I --> J[2단계: 텍스트 구조화<br>(API 호출)];
    J --> K[2단계: 최종 분류<br>(API 호출)];
    K --> L{최종 분류가<br>'same_task'인가?};
    L -- 예 --> H;
    L -- 아니오 --> M[최종 분류: 'other'];
    
    H --> N[논문 요약<br>(API 호출)];
    M --> O[요약 없이<br>분류 결과만 저장];
    N --> P[결과를 Markdown으로 저장];
    O --> P;
```

### 워크플로우 (텍스트 설명)

1.  **신규 논문 발견**: 추적 중인 논문을 인용한 새로운 논문을 발견합니다.
2.  **초록 확인**: Semantic Scholar API가 제공한 깨끗한 초록(Abstract)이 있는지 확인합니다.
    *   **[경로 A] 초록이 있는 경우**:
        1.  **1-A 단계 분류**: API 초록을 사용하여 빠르고 저렴하게 관련성을 1차 판단합니다.
    *   **[경로 B] 초록이 없는 경우**:
        1.  **PDF 일부 추출**: 논문 PDF의 앞부분(3페이지) 텍스트를 추출합니다.
        2.  **1-B 단계 분류**: AI가 추출된 텍스트에서 직접 초록을 찾아내어 관련성을 1차 판단합니다.
3.  **1차 분류 결과 판단**:
    *   **결과가 'same_task'인 경우 (관련 높음)**:
        1.  **비용 절감**: 2단계 분석(전체 텍스트 구조화 및 분류)을 건너뜁니다.
        2.  최종 분류를 'same_task'로 확정하고 4단계로 넘어갑니다.
    *   **결과가 'uncertain'인 경우 (애매함)**:
        1.  **2단계 분석 시작**: 전체 PDF의 텍스트를 추출합니다.
        2.  **텍스트 구조화**: AI를 호출하여 전체 텍스트를 논리적 구조(서론, 본론 등)로 정리합니다. (비용 발생)
        3.  **최종 분류**: 구조화된 전체 텍스트를 바탕으로 AI가 최종적으로 'same_task' 또는 'other'로 분류합니다.
4.  **요약 및 저장**:
    *   최종 분류가 'same_task'인 논문에 대해서만 **요약 생성 API를 호출**합니다.
    *   분석된 결과를 Markdown 파일로 `summaries` 폴더에 저장합니다.

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
- `CLASSIFICATION_MODEL`: 구조화 및 분류용 모델 (기본값: `gemini-2.5-flash`)
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

## ✅ To-Do List

1.  **1차 분류 로직 검증**: 엄격 모드 적용 및 분류 정확도 테스트.
2.  **Summary 로직 검증**: 본문 기반 요약 품질 및 프롬프트 동작 확인.
3.  **중간 로그 시각화 코드 구현**: 에이전트 동작 과정을 실시간으로 시각화하는 도구 개발.