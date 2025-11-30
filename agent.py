import os
import json
import re
import sys
import time
import requests
import io
import pypdf
import google.generativeai as genai
from google.api_core import exceptions
from dotenv import load_dotenv
from datetime import datetime

# 1. 설정
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
SEMANTIC_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY")

# --- 파일 및 디렉토리 상수 ---
PAPERS_CONFIG_FILE = "papers.json"
HISTORY_FILE = "history.json"
SUMMARY_DIR = "summaries"
BASE_SUMMARY_PROMPT_FILE = "prompts/base_summary_prompt.md"
SUMMARY_PROMPT_FILE = "prompts/summary_prompt.md"
CLASSIFICATION_PROMPT_FILE = "prompts/classification_prompt.md"

# --- 실행 설정 상수 ---
CHECK_INTERVAL = 3600  # 작업 반복 주기 (초)
MAX_RETRIES = 5
INITIAL_DELAY = 1
CLASSIFICATION_MODEL_NAME = os.getenv("CLASSIFICATION_MODEL", "gemini-1.5-flash")
SUMMARIZATION_MODEL_NAME = os.getenv("SUMMARIZATION_MODEL", "gemini-2.5-pro")

def load_json_file(file_path, default_value):
    """JSON 파일을 로드, 없으면 기본값 반환"""
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return default_value
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json_file(file_path, data):
    """JSON 파일 저장"""
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_papers_config():
    """papers.json 설정 파일 로드"""
    return load_json_file(PAPERS_CONFIG_FILE, [])

def load_history():
    """history.json 처리 내역 로드"""
    return load_json_file(HISTORY_FILE, [])

def save_history(paper_id):
    """처리 내역에 논문 ID 추가"""
    history = load_history()
    if paper_id not in history:
        history.append(paper_id)
        save_json_file(HISTORY_FILE, history)

def load_prompt(prompt_file):
    """프롬프트 파일 읽기"""
    if not os.path.exists(prompt_file):
        print(f"🚨 프롬프트 파일 없음: {prompt_file}")
        return None
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read()

def _get_paper_id_for_api(paper_id):
    """API 호출을 위한 논문 ID 형식 맞추기 (e.g., ArXiv ID)"""
    if re.match(r'^\d{4}\.\d{4,5}$', paper_id):
        return f"ARXIV:{paper_id}"
    return paper_id

def fetch_paper_details(paper_id):
    """Semantic Scholar에서 특정 논문 정보 조회 (재시도 로직 포함)"""
    api_paper_id = _get_paper_id_for_api(paper_id)
    print(f"🔍 기준 논문 정보 조회 중: {api_paper_id}")
    url = f"https://api.semanticscholar.org/graph/v1/paper/{api_paper_id}"
    params = {"fields": "title,abstract,year,url,externalIds,openAccessPdf"}
    headers = {"x-api-key": SEMANTIC_API_KEY} if SEMANTIC_API_KEY else {}
    
    for retry_count in range(MAX_RETRIES):
        try:
            res = requests.get(url, params=params, headers=headers)
            res.raise_for_status()
            return res.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                delay = INITIAL_DELAY * (2 ** retry_count)
                print(f"⚠️ 429 Rate Limit Hit. Retrying in {delay} seconds (Retry {retry_count + 1}/{MAX_RETRIES})...")
                time.sleep(delay)
            else:
                print(f"❌ 기준 논문 정보 조회 오류 (HTTP Error): {e}")
                return None
        except Exception as e:
            print(f"❌ 기준 논문 정보 조회 중 예외 발생: {e}")
            return None
    print(f"🚨 {MAX_RETRIES}번의 재시도 후에도 기준 논문 정보 조회 실패: {api_paper_id}")
    return None

def fetch_citations(paper_id):
    """Semantic Scholar에서 인용 논문 조회 (재시도 로직 포함)"""
    api_paper_id = _get_paper_id_for_api(paper_id)
    print(f"📄 {api_paper_id}의 신규 인용 확인 중...")
    url = f"https://api.semanticscholar.org/graph/v1/paper/{api_paper_id}/citations"
    params = {"fields": "title,abstract,year,url,isOpenAccess,externalIds,openAccessPdf", "limit": 20}
    headers = {"x-api-key": SEMANTIC_API_KEY} if SEMANTIC_API_KEY else {}

    for retry_count in range(MAX_RETRIES):
        try:
            res = requests.get(url, params=params, headers=headers)
            res.raise_for_status()
            return res.json().get('data', [])
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                delay = INITIAL_DELAY * (2 ** retry_count)
                print(f"⚠️ 429 Rate Limit Hit. Retrying in {delay} seconds (Retry {retry_count + 1}/{MAX_RETRIES})...")
                time.sleep(delay)
            else:
                print(f"❌ 인용 논문 조회 API 오류 (HTTP Error): {e}")
                return []
        except Exception as e:
            print(f"❌ 인용 논문 조회 중 예외 발생: {e}")
            return []
    print(f"🚨 {MAX_RETRIES}번의 재시도 후에도 인용 논문 조회 실패: {api_paper_id}")
    return []

def organize_text_with_gemini(text):
    """
    Gemini 1.5 Flash를 사용하여 텍스트를 구조화된 JSON으로 반환합니다.
    """
    if not text:
        return {}

    print("   🤖 Gemini Flash로 텍스트 구조화 및 정제 중 (JSON 출력)...")
    
    input_text = text[:30000]
    
    prompt = f"""
    당신은 논문 분석 AI입니다. 아래 논문 텍스트(Raw Text)에서 핵심 섹션을 추출하여 다음 JSON 형식으로 출력하세요.
    
    [JSON 스키마]
    {{
        "abstract": "초록 내용 (없으면 빈 문자열)",
        "introduction": "서론 및 문제 정의 (없으면 빈 문자열)",
        "method": "제안 방법론 및 아키텍처 (없으면 빈 문자열)",
        "conclusion": "결론 및 요약 (없으면 빈 문자열)",
        "experiments": "실험 결과 및 비교 (없으면 빈 문자열)"
    }}
    
    [주의사항]
    1. References, Appendix, Acknowledgments는 제외하세요.
    2. 내용은 요약하지 말고 원문 문장들을 최대한 유지하여 발췌하세요.
    3. 언어는 원문(영어) 그대로 유지하세요.
    4. 반드시 유효한 JSON 형식이어야 합니다.

    [원본 텍스트]
    {input_text}
    """

    try:
        model = genai.GenerativeModel(
            CLASSIFICATION_MODEL_NAME,
            generation_config={"response_mime_type": "application/json"}
        )
        response = model.generate_content(prompt)
        return json.loads(response.text)
    except Exception as e:
        print(f"   ⚠️ 텍스트 구조화(JSON) 중 오류 발생: {e}")
        return {"full_text": text[:20000]} # 실패 시 원본을 통째로 반환

def fetch_full_text(paper_details):
    """
    논문 정보를 기반으로 PDF를 다운로드하고 텍스트를 추출합니다.
    우선순위:
    1. ArXiv ID가 있는 경우 ArXiv PDF 서버 직접 이용
    2. Semantic Scholar가 제공하는 openAccessPdf 링크 이용
    3. 일반 url이 pdf로 끝나는 경우 이용
    """
    pdf_url = None
    
    # 1. ArXiv ID 확인 (가장 확실한 방법)
    external_ids = paper_details.get('externalIds') or {}
    arxiv_id = external_ids.get('ArXiv')
    if arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        print(f"   🎯 ArXiv ID 발견: {arxiv_id} -> {pdf_url}")
    
    # 2. Semantic Scholar 제공 Open Access PDF 확인
    if not pdf_url:
        open_access_pdf = paper_details.get('openAccessPdf')
        if open_access_pdf and open_access_pdf.get('url'):
            pdf_url = open_access_pdf.get('url')
            print(f"   🎯 OpenAccess PDF 링크 발견: {pdf_url}")

    # 3. 일반 URL 확인 (Fallback)
    if not pdf_url:
        url = paper_details.get('url', '')
        if 'arxiv.org/abs/' in url:
            pdf_url = url.replace('/abs/', '/pdf/') + '.pdf'
        elif url.endswith('.pdf'):
            pdf_url = url
    
    if not pdf_url:
        return ""

    try:
        print(f"   ⬇️ PDF 다운로드 시도: {pdf_url}")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        response = requests.get(pdf_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        pdf_file = io.BytesIO(response.content)
        reader = pypdf.PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        
        print(f"   ✅ 텍스트 추출 성공 ({len(text)}자). AI 구조화 진행...")
        
        # Gemini Flash를 이용한 텍스트 구조화 (JSON 반환)
        structured_data = organize_text_with_gemini(text)
        
        # JSON 실패 시 원본 텍스트 처리
        if "full_text" in structured_data:
             print("   ⚠️ 구조화 실패로 원본 텍스트 반환.")
             return structured_data["full_text"]

        # JSON 데이터를 보기 좋은 Markdown으로 변환
        md_output = ""
        if structured_data.get("abstract"):
            md_output += f"## Abstract\n{structured_data['abstract']}\n\n"
        if structured_data.get("introduction"):
            md_output += f"## Introduction\n{structured_data['introduction']}\n\n"
        if structured_data.get("method"):
            md_output += f"## Method\n{structured_data['method']}\n\n"
        if structured_data.get("conclusion"):
            md_output += f"## Conclusion\n{structured_data['conclusion']}\n\n"
        if structured_data.get("experiments"):
            md_output += f"## Experiments\n{structured_data['experiments']}\n\n"
            
        print(f"   ✨ 텍스트 구조화 완료 (Markdown 변환됨, {len(md_output)}자).")
        return md_output

    except Exception as e:
        print(f"   ❌ PDF 처리 중 오류: {e}")
    
    return ""

def classify_paper(citing_paper, target_paper_details):
    """Gemini를 사용해 논문을 'same_task' 또는 'other'로 분류"""
    print(f"🧐 '{citing_paper['title']}' 논문 분류 중...")
    classification_prompt = load_prompt(CLASSIFICATION_PROMPT_FILE)
    if not classification_prompt:
        return 'other'

    full_text = fetch_full_text(citing_paper)

    prompt = classification_prompt.replace('{{target_title}}', target_paper_details.get('title', '')).replace('{{target_abstract}}', target_paper_details.get('abstract', '')).replace('{{title}}', citing_paper.get('title', '')).replace('{{abstract}}', citing_paper.get('abstract', '초록 정보 없음')).replace('{{full_text}}', full_text)

    try:
        model = genai.GenerativeModel(CLASSIFICATION_MODEL_NAME)
        response = model.generate_content(prompt)
        result_text = response.text.strip().lower()
        
        if "yes" in result_text:
            print("➡️ 분류 결과: same_task")
            return "same_task"
        else:
            print("➡️ 분류 결과: other")
            return "other"
    except exceptions.ResourceExhausted as e:
        print("\n🚨 [비용 방지] Gemini API 할당량(Quota)을 초과했습니다. 스크립트를 종료합니다.")
        print(f"   오류 상세: {e}")
        sys.exit()
    except Exception as e:
        print(f"❌ 분류 중 Gemini API 오류: {e}")
        return "other"

def summarize_with_gemini(paper_title, formatted_prompt):
    """Gemini에게 요약 요청"""
    print(f"🤖 '{paper_title}' 요약 중...")
    
    try:
        model = genai.GenerativeModel(SUMMARIZATION_MODEL_NAME)
        response = model.generate_content(formatted_prompt)
        return response.text
    except exceptions.ResourceExhausted as e:
        print("\n🚨 [비용 방지] Gemini API 할당량(Quota)을 초과했습니다. 스크립트를 종료합니다.")
        print(f"   오류 상세: {e}")
        sys.exit()
    except Exception as e:
        print(f"❌ 요약 중 Gemini API 오류: {e}")
        return ""

def save_summary_to_md(paper, summary, target_paper_alias, classification):
    """요약 내용을 동적 폴더 구조에 Markdown 파일로 저장"""
    is_base_summary = classification == '_base'
    
    if is_base_summary:
        output_dir = os.path.join(SUMMARY_DIR, target_paper_alias)
        filename = "_base_summary.md"
    else:
        output_dir = os.path.join(SUMMARY_DIR, target_paper_alias, classification)
        safe_title = re.sub(r'[\\/*?:"<>|]', "", paper['title'])
        filename = f"{safe_title} ({paper.get('year', 'N/A')}).md"

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)

    md_content = f"# {paper['title']} ({paper.get('year', 'N/A')})\n\n"
    if paper.get('url'):
        md_content += f"**🔗 링크:** [{paper['url']}]({paper['url']})\n\n"
    md_content += f"---\n\n{summary}"
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(md_content.strip())
    print(f"✅ 요약본 저장 완료: {filepath}")

def main():
    """메인 실행 함수"""
    while True:
        print(f"\n--- [{datetime.now()}] 새로운 사이클 시작 ---")
        
        target_papers = load_papers_config()
        if not target_papers:
            print("🚨 `papers.json`에 추적할 논문이 없습니다. 파일을 확인해주세요.")
            time.sleep(CHECK_INTERVAL)
            continue

        base_summary_prompt_template = load_prompt(BASE_SUMMARY_PROMPT_FILE)
        summary_prompt_template = load_prompt(SUMMARY_PROMPT_FILE)

        if not base_summary_prompt_template or not summary_prompt_template:
            print("🚨 필수 프롬프트 파일이 없습니다. 프로그램을 종료합니다.")
            break
        
        history = load_history()

        for target_paper in target_papers:
            target_id = target_paper.get("id")
            target_alias = target_paper.get("alias", target_id)
            target_alias = re.sub(r'[\\/*?:"<>|]', "", target_alias)
            print(f"\n>> '{target_alias}' 논문 처리 시작...")

            target_paper_details = fetch_paper_details(target_id)
            if not target_paper_details:
                print(f"   '{target_alias}' 정보 조회를 건너뜁니다.")
                continue
            
            base_summary_path = os.path.join(SUMMARY_DIR, target_alias, '_base_summary.md')
            if not os.path.exists(base_summary_path):
                print(f"   '{target_alias}'의 기준 논문 요약본이 없습니다. 요약을 생성합니다.")
                
                full_text = fetch_full_text(target_paper_details)
                if not full_text:
                    print("   ⚠️ 전체 텍스트 추출 실패. 초록(Abstract)으로 대체합니다.")
                    full_text = target_paper_details.get('abstract', '내용 없음')

                formatted_prompt = base_summary_prompt_template.replace('{{title}}', target_paper_details.get('title','')).replace('{{full_text}}', full_text)
                base_summary = summarize_with_gemini(target_paper_details.get('title', ''), formatted_prompt)
                
                if base_summary:
                    save_summary_to_md(target_paper_details, base_summary, target_alias, '_base')
            
            time.sleep(1) 
            citations = fetch_citations(target_id)
            new_papers_found_for_target = False
            processed_count = 0
            MAX_TO_PROCESS = 3

            for item in citations:
                citing_paper = item.get('citingPaper', {})
                if not citing_paper.get('paperId'):
                    continue
                
                if citing_paper['paperId'] in history:
                    continue
                
                new_papers_found_for_target = True
                
                # API 초록이 없으면 원문에서 가져오도록 시도
                api_abstract = citing_paper.get('abstract')
                if not api_abstract or api_abstract == "초록 정보 없음": # API 초록이 없거나 비어있는 경우
                    print(f"   API 초록 없음. '{citing_paper.get('title')}' 논문의 원문에서 텍스트 추출 시도...")
                    full_text_from_pdf = fetch_full_text(citing_paper)
                    if full_text_from_pdf:
                        # 원문에서 초록 대용으로 사용할 텍스트 설정
                        citing_paper['abstract'] = full_text_from_pdf[:1500] if len(full_text_from_pdf) > 1500 else full_text_from_pdf
                        print(f"   ✅ 원문 텍스트에서 초록 대용으로 {len(citing_paper['abstract'])}자 추출 성공.")
                    else:
                        citing_paper['abstract'] = "초록 정보 없음"
                
                if citing_paper['abstract'] == "초록 정보 없음":
                    print(f"   '{citing_paper.get('title')}' 논문은 최종적으로 초록 정보가 없어 건너뜁니다.")
                    continue

                classification = classify_paper(citing_paper, target_paper_details)
                
                formatted_prompt = summary_prompt_template.replace('{{target_title}}', target_paper_details.get('title', '')).replace('{{target_abstract}}', target_paper_details.get('abstract', '')).replace('{{title}}', citing_paper.get('title', '')).replace('{{abstract}}', citing_paper.get('abstract', '초록 정보 없음'))
                summary = summarize_with_gemini(citing_paper.get('title',''), formatted_prompt)
                
                if summary:
                    save_summary_to_md(citing_paper, summary, target_alias, classification)
                    save_history(citing_paper['paperId'])
                    processed_count += 1
                
                time.sleep(1)

                if processed_count >= MAX_TO_PROCESS:
                    print(f"   테스트를 위해 최대 {MAX_TO_PROCESS}개의 논문만 처리하고 중단합니다.")
                    break
            
            if not new_papers_found_for_target:
                print(f"✅ '{target_alias}'에 대한 새로운 인용 논문이 없습니다.")

        print(f"\n--- 사이클 종료. 다음 확인까지 {CHECK_INTERVAL}초 대기... ---")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()