
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
        safe_title = re.sub(r'[\\/*?:":<>|]', "", paper['title'])
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
        logger.warning(f"ID가 없는 인용 논문을 건너뜁니다: {paper_title}")
        return

    try:
        # --- 1단계 분류 ---
        api_abstract = citing_paper.get('abstract')
        first_pass_class = "uncertain"

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
        
        # --- 2단계 분류 (필요 시) ---
        classification = "other"
        structured_text = None

        if first_pass_class == "uncertain":
            logger.info(f"'{paper_title}' 2단계 정밀 분석 진행...")
            try:
                # 전체 Raw Text 추출
                full_raw_text = await document_parser.extract_raw_text(session, citing_paper)
                if not citing_paper.get('abstract'): # 스니펫에서도 초록을 못가져온 경우
                    citing_paper['abstract'] = full_raw_text[:1500]

                # 텍스트 구조화 (비용 발생)
                structured_text = await document_parser.structure_text(full_raw_text)
                
                # 최종 분류
                classification = await gemini.full_text_classify(
                    target_paper_details, citing_paper, structured_text
                )
            except document_parser.PDFExtractionError as e:
                logger.error(f"'{paper_title}' 논문 처리 실패 (2차 분류용 텍스트 추출): {e}")
                database.record_failure(conn, paper_id, str(e))
                return
        else: # 'same_task'
            logger.info(f"'{paper_title}' 1차 분류 통과, 2단계 분석을 건너뜁니다.")
            classification = "same_task"

        logger.info(f"'{paper_title}' 최종 분류 결과: {classification}")

        # --- 요약 및 저장 ---
        summary = await gemini.summarize_with_gemini(target_paper_details, citing_paper)
        if not summary:
            logger.warning(f"'{paper_title}' 논문 요약 생성 실패. 건너뜁니다.")
            database.record_failure(conn, paper_id, "요약 생성 실패")
            return
            
        save_summary_to_md(citing_paper, summary, target_alias, classification)
        database.add_paper_to_history(conn, paper_id, status='processed')

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
            target_alias = re.sub(r'[\\/*?:":<>|]', "", target_alias)
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
            
            logger.info(f"'{target_alias}'에 대해 처리할 신규 인용 {len(papers_to_process)}개를 발견했습니다.")

            # 동시 처리 작업 생성
            tasks = [
                process_citing_paper(session, db_conn, paper, target_paper_details, target_alias)
                for paper in papers_to_process[:config.MAX_CITATIONS_TO_PROCESS_PER_RUN]
            ]
            
            await asyncio.gather(*tasks)

    db_conn.close()
    logger.info("--- 사이클 종료 ---")
