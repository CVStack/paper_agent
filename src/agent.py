import asyncio
import json
import logging
import os
import re
import aiohttp
from datetime import datetime

from src import config
from src.clients import gemini, semantic_scholar
from src.processing import document_parser
from src.storage import database

logger = logging.getLogger(__name__)

SURVEY_KEYWORDS = [
    "survey", "review", "overview", "roadmap", 
    "state of the art", "state-of-the-art", 
    "comprehensive study", "meta-analysis"
]

def _is_survey_paper(paper):
    # 1. 메타데이터 체크
    pub_types = paper.get('publicationTypes') or []
    if 'Review' in pub_types:
        return True
        
    # 2. 제목 키워드 체크
    title = paper.get('title', '').lower()
    if any(keyword in title for keyword in SURVEY_KEYWORDS):
        return True
        
    return False

def load_papers_config():
    """papers.json 설정 파일을 로드합니다."""
    try:
        with open(config.PAPERS_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"`{config.PAPERS_CONFIG_FILE}` 파일 로드 오류: {e}")
        return []

def save_summary_to_md(paper, summary, target_paper_alias, classification):
    """요약 내용을 동적 폴더 구조에 Markdown 파일로 저장합니다."""
    is_base_summary = classification == '_base'
    
    if is_base_summary:
        output_dir = os.path.join(config.SUMMARY_DIR, target_paper_alias)
        filename = "_base_summary.md"
    else:
        output_dir = os.path.join(config.SUMMARY_DIR, target_paper_alias, classification)
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
    logger.info(f"요약본 저장 완료: {filepath}")

async def process_citing_paper(session, conn, citing_paper, target_paper_details, target_alias):
    """단일 인용 논문을 비동기적으로 처리합니다 (분류, 요약, 저장)."""
    paper_id = citing_paper.get('paperId')
    paper_title = citing_paper.get('title', 'N/A')

    if not paper_id:
        logger.warning(f"ID가 없는 인용 논문을 건너웁니다: {paper_title}")
        return

    # [NEW] 서베이 논문 필터링
    if _is_survey_paper(citing_paper):
        logger.info(f"'{paper_title}' - 서베이 논문으로 감지되어 처리를 건너뜁니다.")
        database.record_failure(conn, paper_id, "Skipped: Survey/Review Paper") 
        return

    try:
        # --- 1단계 분류 (Fast Filter) ---
        api_abstract = citing_paper.get('abstract')
        first_pass_class = "other"

        if api_abstract:
            # 경로 A: API 초록이 있을 경우
            logger.debug(f"'{paper_title}' - 경로 A: API 초록으로 1차 분류 수행")
            first_pass_class = await gemini.first_pass_classify_with_abstract(
                target_paper_details, api_abstract
            )
        else:
            # 경로 B: API 초록이 없을 경우 (Smarter Fallback)
            logger.debug(f"'{paper_title}' - 경로 B: Raw text 일부로 1차 분류 수행")
            try:
                raw_text_snippet = await document_parser.extract_raw_text(session, citing_paper, pages=3)
                first_pass_class = await gemini.first_pass_classify_with_snippet(
                    target_paper_details, raw_text_snippet
                )
                # 후속 단계를 위해 초록을 채워넣음
                citing_paper['abstract'] = raw_text_snippet[:1500]
            except document_parser.PDFExtractionError as e:
                logger.error(f"'{paper_title}' 논문 처리 실패 (1차 분류용 텍스트 추출): {e}")
                database.record_failure(conn, paper_id, str(e))
                return

        logger.info(f"'{paper_title}' 1차 분류 결과: {first_pass_class}")
        
        # [Strict Mode] 1단계에서 same_task가 아니면 즉시 종료
        if first_pass_class != "same_task":
            logger.info(f"'{paper_title}' - 1단계 분류 탈락 ({first_pass_class}). 처리를 종료합니다.")
            # DB에 'filtered' 등으로 기록하거나 그냥 로그만 남기고 종료
            # 여기서는 processed가 아니므로 다음에 다시 시도되지 않도록 failure로 기록하되 사유를 명시
            database.record_failure(conn, paper_id, f"Filtered: {first_pass_class}")
            return

        # --- 2단계: 본문 확보 및 정밀 검증 (Deep Verification) ---
        logger.info(f"'{paper_title}' - 1단계 통과! 본문 확보 및 정밀 검증 시작.")
        
        try:
            # 1. 본문 다운로드 및 구조화 (필수)
            full_raw_text = await document_parser.extract_raw_text(session, citing_paper)
            structured_text = await document_parser.structure_text(full_raw_text)
            
            # 2. 2단계 정밀 분류 (Double Check)
            final_classification = await gemini.full_text_classify(
                target_paper_details, citing_paper, structured_text
            )
            
            if final_classification != "same_task":
                logger.info(f"'{paper_title}' - 2단계 정밀 분류 탈락. 처리를 종료합니다.")
                database.record_failure(conn, paper_id, "Filtered: 2nd pass failed")
                return

            # --- 3단계: 심층 요약 및 저장 (High Quality Summary) ---
            logger.info(f"'{paper_title}' - 최종 합격! 심층 요약을 생성합니다.")
            summary = await gemini.summarize_with_gemini(
                target_paper_details, citing_paper, full_text=structured_text
            )
            
            if not summary:
                logger.warning(f"'{paper_title}' 논문 요약 생성 실패. 건너뜁니다.")
                database.record_failure(conn, paper_id, "요약 생성 실패")
                return
                
            save_summary_to_md(citing_paper, summary, target_alias, final_classification)
            database.add_paper_to_history(conn, paper_id, status='processed')

        except document_parser.PDFExtractionError as e:
            logger.error(f"'{paper_title}' 본문 확보 실패: {e}")
            database.record_failure(conn, paper_id, f"PDF Error: {e}")
            return

    except gemini.GeminiAPIError as e:
        logger.critical(f"'{paper_title}' 처리 중 Gemini API 오류: {e}")
        database.record_failure(conn, paper_id, f"Gemini API Error: {e}")
    except Exception as e:
        logger.exception(f"'{paper_title}' 논문 처리 중 예기치 않은 오류 발생")
        database.record_failure(conn, paper_id, f"Unexpected Error: {e}")


async def run_cycle():
    """에이전트의 메인 실행 사이클."""
    logger.info(f"--- [{datetime.now()}] 새로운 사이클 시작 ---")
    
    target_papers = load_papers_config()
    if not target_papers:
        logger.warning(f"`{config.PAPERS_CONFIG_FILE}`에 추적할 논문이 없습니다.")
        return

    db_conn = database.get_db_connection(config.DB_PATH)
    
    async with aiohttp.ClientSession() as session:
        for target_paper_config in target_papers:
            target_id = target_paper_config.get("id")
            target_alias = target_paper_config.get("alias", target_id)
            target_alias = re.sub(r'[\\/*?:"<>|]', "", target_alias)
            logger.info(f"\n>> '{target_alias}' 논문 처리 시작...")

            target_paper_details = await semantic_scholar.fetch_paper_details(session, target_id)
            if not target_paper_details:
                logger.error(f"'{target_alias}' 정보 조회를 실패하여 건너뜁니다.")
                continue

            # (생략) 기준 논문 요약 로직은 필요 시 여기에 추가
            
            citations = await semantic_scholar.fetch_citations(session, target_id)
            if not citations:
                logger.info(f"'{target_alias}'에 대한 새로운 인용을 찾지 못했습니다.")
                continue

            citing_paper_ids = [item['citingPaper']['paperId'] for item in citations if item.get('citingPaper', {}).get('paperId')]
            
            # DB에서 이미 처리했거나 실패한 논문 제외
            papers_to_process_ids = database.get_papers_to_process(db_conn, citing_paper_ids)
            
            papers_to_process = [
                item['citingPaper'] for item in citations 
                if item.get('citingPaper', {}).get('paperId') in papers_to_process_ids
            ]

            if not papers_to_process:
                logger.info(f"'{target_alias}'의 모든 신규 인용은 이미 처리되었거나 실패 목록에 있습니다.")
                continue
            
            if config.MAX_CITATIONS_TO_PROCESS_PER_RUN == -1:
                # -1이면 모든 논문을 처리
                limited_papers_to_process = papers_to_process
                logger.info(f"'{target_alias}'에 대해 제한 없이 모든 신규 인용 {len(limited_papers_to_process)}개를 처리합니다.")
            else:
                # 설정된 값만큼만 처리
                limited_papers_to_process = papers_to_process[:config.MAX_CITATIONS_TO_PROCESS_PER_RUN]
                logger.info(f"'{target_alias}'에 대해 처리할 신규 인용 {len(limited_papers_to_process)}개 (최대 {config.MAX_CITATIONS_TO_PROCESS_PER_RUN}개)를 발견했습니다.")

            # 동시 처리 작업 생성
            tasks = [
                process_citing_paper(session, db_conn, paper, target_paper_details, target_alias)
                for paper in limited_papers_to_process
            ]
            
            await asyncio.gather(*tasks)

    db_conn.close()
    logger.info("--- 사이클 종료 ---")