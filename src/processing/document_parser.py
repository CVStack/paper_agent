import io
import logging
import re
import asyncio
import aiohttp
import pypdf
import arxiv
from rapidfuzz import fuzz
from src import config
from src.clients import gemini as gemini_client

logger = logging.getLogger(__name__)

class PDFExtractionError(Exception):
    """PDF 텍스트 추출 중 발생하는 사용자 정의 예외."""
    pass

def _normalize_text(text: str) -> str:
    """텍스트 정규화: 소문자, 특수문자 제거, 공백 정리."""
    if not text:
        return ""
    # 소문자 변환
    text = text.lower()
    # 특수문자 제거 (알파벳, 숫자, 공백만 남김)
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    # 다중 공백을 하나로
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def _extract_first_author_lastname(authors: list) -> str:
    """논문 정보에서 첫 번째 저자의 성(Last Name)을 추출합니다."""
    if not authors:
        return ""
    # Semantic Scholar authors format: [{'name': 'John Doe'}, ...]
    first_author_name = authors[0].get('name')
    if not first_author_name:
        return ""
    # 이름에서 성(Last Name) 추출
    return first_author_name.split()[-1]

def _sanitize_title_for_query(title: str) -> str:
    """ArXiv 쿼리용 제목 정제: 특수문자 제거."""
    if not title:
        return ""
    # 알파벳, 숫자, 공백만 남김
    sanitized = re.sub(r'[^a-zA-Z0-9\s]', '', title)
    # 다중 공백을 하나로
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    return sanitized

def _are_authors_matching(target_authors, result_authors) -> bool:
    """
    저자 목록 비교 (최소 1명 이상 성(Last Name)이 일치하는지 확인).
    target_authors: [{'name': 'John Doe'}, ...] (Semantic Scholar format)
    result_authors: [arxiv.Result.Author('John Doe'), ...] (ArXiv format)
    """
    if not target_authors or not result_authors:
        return False
        
    def get_last_name(name):
        return name.split()[-1].lower() if name else ""

    target_last_names = {get_last_name(a.get('name')) for a in target_authors if a.get('name')}
    result_last_names = {get_last_name(a.name) for a in result_authors if a.name}
    
    # 교집합 확인
    return bool(target_last_names & result_last_names)

def _is_fuzzy_match(title1: str, title2: str) -> bool:
    """
    제목 유사도 비교 (하이브리드 방식).
    1. 포함 관계 (Substring)
    2. Fuzzy Ratio > 90
    """
    norm1 = _normalize_text(title1)
    norm2 = _normalize_text(title2)
    
    if not norm1 or not norm2:
        return False

    # 1. 포함 관계 확인 (부제 등으로 인해 한쪽이 길어진 경우)
    if norm1 in norm2 or norm2 in norm1:
        return True
        
    # 2. 유사도 점수 확인
    ratio = fuzz.ratio(norm1, norm2)
    return ratio >= 90

def _search_arxiv_pdf_sync(title: str, authors: list) -> str | None:
    """
    (동기 함수) ArXiv에서 제목으로 검색하여 검증된 PDF URL을 반환.
    """
    try:
        clean_title = _sanitize_title_for_query(title)
        first_author_lastname = _extract_first_author_lastname(authors)
        
        # 쿼리 구성
        if first_author_lastname:
            # 저자 성과 정제된 제목으로 쿼리 (정확한 구문을 위해 따옴표 사용)
            query = f'au:{first_author_lastname} AND ti:("{clean_title}")'
        else:
            # 저자 정보 없으면 정제된 제목만으로 쿼리
            query = f'ti:("{clean_title}")'

        logger.debug(f"ArXiv 검색 쿼리: '{query}'")

        search = arxiv.Search(
            query=query,
            max_results=5, # 충분히 많은 결과를 확인
            sort_by=arxiv.SortCriterion.Relevance
        )
        
        for result in search.results():
            # ArXiv 검색 결과의 제목과 원본 제목 비교
            if _is_fuzzy_match(title, result.title): # 원본 title과 비교
                # 저자 정보가 있다면, 저자도 비교
                if not first_author_lastname or _are_authors_matching(authors, result.authors):
                    logger.info(f"ArXiv 제목 검색 성공 (유사도/저자 검증 통과): '{title}' -> {result.pdf_url}")
                    return result.pdf_url
                else:
                    logger.debug(f"ArXiv 결과 제목은 유사하나 저자가 불일치: '{title}' vs '{result.title}'")
            else:
                 logger.debug(f"ArXiv 결과 제목 불일치: '{title}' vs '{result.title}' (Fuzzy Match 실패)")

    except Exception as e:
        logger.warning(f"ArXiv 검색 중 오류 발생: {e}", exc_info=True)
        
    return None

async def _search_arxiv_pdf(title: str, authors: list) -> str | None:
    """
    (비동기 래퍼) ArXiv 검색을 별도 스레드에서 실행.
    """
    return await asyncio.to_thread(_search_arxiv_pdf_sync, title, authors)

def _get_pdf_url(paper_details):
    """논문 정보에서 PDF 다운로드 URL을 결정합니다."""
    # 1. ArXiv ID (가장 신뢰성 높음)
    external_ids = paper_details.get('externalIds') or {}
    arxiv_id = external_ids.get('ArXiv')
    if arxiv_id:
        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        logger.debug(f"ArXiv ID 발견: {arxiv_id} -> {url}")
        return url
    
    # 2. Semantic Scholar 제공 Open Access PDF
    open_access_pdf = paper_details.get('openAccessPdf')
    if open_access_pdf and open_access_pdf.get('url'):
        url = open_access_pdf.get('url')
        logger.debug(f"OpenAccess PDF 링크 발견: {url}")
        return url

    # 3. 일반 URL (대체)
    url = paper_details.get('url', '')
    if 'arxiv.org/abs/' in url:
        return url.replace('/abs/', '/pdf/') + '.pdf'
    if url.endswith('.pdf'):
        return url
        
    return None

async def extract_raw_text(session, paper_details, pages=None):
    """
    PDF를 비동기적으로 다운로드하고 원시 텍스트를 추출합니다.
    'pages' 인자를 통해 추출할 페이지 수를 제한할 수 있습니다.
    """
    # 1차 시도: 기존 메타데이터 기반 URL 추출
    pdf_url = _get_pdf_url(paper_details)
    
    # 2차 시도: URL이 없으면 ArXiv 제목 검색 (Fallback)
    if not pdf_url:
        title = paper_details.get('title')
        authors = paper_details.get('authors')
        if title:
            logger.info(f"PDF URL 없음. ArXiv 제목 검색 시도: '{title}'")
            pdf_url = await _search_arxiv_pdf(title, authors)

    if not pdf_url:
        raise PDFExtractionError("PDF 다운로드 URL을 찾을 수 없습니다 (ArXiv 검색 포함).")

    try:
        logger.debug(f"PDF 다운로드 시도: {pdf_url}")
        headers = {"User-Agent": config.REQUESTS_USER_AGENT}
        
        async with session.get(pdf_url, headers=headers, timeout=30) as response:
            response.raise_for_status()
            pdf_content = await response.read()

        logger.debug("PDF 다운로드 완료, 텍스트 추출 중...")
        pdf_file = io.BytesIO(pdf_content)
        reader = pypdf.PdfReader(pdf_file)
        
        page_range = reader.pages[:pages] if pages else reader.pages
        text = "".join(page.extract_text() or "" for page in page_range)

        if not text.strip():
            raise PDFExtractionError("PDF에서 텍스트를 추출하지 못했습니다 (내용이 비어 있음).")
        
        logger.info(f"텍스트 추출 성공 ({len(text)}자, {len(page_range)} 페이지).")
        return text

    except aiohttp.ClientError as e:
        raise PDFExtractionError(f"PDF 다운로드 실패: {e}")
    except pypdf.errors.PdfReadError as e:
        raise PDFExtractionError(f"PDF 파싱 오류: {e}")
    except Exception as e:
        raise PDFExtractionError(f"PDF 처리 중 예기치 않은 오류: {e}")


async def structure_text(text: str):
    """
    추출된 원시 텍스트를 Gemini를 사용하여 구조화된 마크다운으로 변환합니다.
    """
    logger.info(f"AI를 통해 텍스트 구조화 진행 ({len(text)}자)...")
        
    # Gemini를 이용한 텍스트 구조화 (JSON 반환)
    structured_data = await gemini_client.organize_text_with_gemini(text[:config.PDF_MAX_TEXT_LENGTH])
    
    # JSON 데이터를 Markdown으로 변환
    if structured_data and "abstract" in structured_data:
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
        
        logger.info(f"텍스트 구조화 완료 (Markdown 변환됨, {len(md_output)}자).")
        return md_output
    else:
        logger.warning("텍스트 구조화 실패. 원본 텍스트 일부를 반환합니다.")
        return text[:config.FALLBACK_TEXT_LENGTH]
