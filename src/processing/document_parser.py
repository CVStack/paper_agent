
import io
import logging
import aiohttp
import pypdf
from src import config
from src.clients import gemini as gemini_client

logger = logging.getLogger(__name__)

class PDFExtractionError(Exception):
    """PDF 텍스트 추출 중 발생하는 사용자 정의 예외."""
    pass

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
    pdf_url = _get_pdf_url(paper_details)
    if not pdf_url:
        raise PDFExtractionError("PDF 다운로드 URL을 찾을 수 없습니다.")

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
