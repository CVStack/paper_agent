
import asyncio
import logging
import re
import aiohttp
from src import config

logger = logging.getLogger(__name__)

def _get_paper_id_for_api(paper_id):
    """API 호출을 위한 논문 ID 형식을 맞춥니다 (e.g., ArXiv ID)."""
    if re.match(r'^\d{4}\.\d{4,5}$', paper_id):
        return f"ARXIV:{paper_id}"
    return paper_id

async def _fetch_data(session, url, params):
    """주어진 URL과 파라미터로 Semantic Scholar API에서 데이터를 비동기적으로 가져옵니다."""
    headers = {
        "x-api-key": config.SEMANTIC_SCHOLAR_API_KEY,
        "User-Agent": config.REQUESTS_USER_AGENT
    } if config.SEMANTIC_SCHOLAR_API_KEY else {}

    for retry_count in range(config.MAX_RETRIES):
        try:
            async with session.get(url, params=params, headers=headers) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 429:  # Rate Limit
                delay = config.INITIAL_RETRY_DELAY * (2 ** retry_count)
                logger.warning(
                    f"Rate limit hit for {url}. Retrying in {delay}s... "
                    f"({retry_count + 1}/{config.MAX_RETRIES})"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"HTTP Error fetching {url}: {e}")
                return None
        except Exception as e:
            logger.error(f"Exception fetching {url}: {e}")
            return None
            
    logger.error(f"Failed to fetch {url} after {config.MAX_RETRIES} retries.")
    return None

async def fetch_paper_details(session, paper_id):
    """특정 논문의 상세 정보를 비동기적으로 조회합니다."""
    api_paper_id = _get_paper_id_for_api(paper_id)
    logger.info(f"기준 논문 정보 조회 중: {api_paper_id}")
    url = f"https://api.semanticscholar.org/graph/v1/paper/{api_paper_id}"
    params = {"fields": "title,abstract,year,url,externalIds,openAccessPdf"}
    
    data = await _fetch_data(session, url, params)
    return data

async def fetch_citations(session, paper_id):
    """특정 논문의 인용 목록을 비동기적으로 조회합니다."""
    api_paper_id = _get_paper_id_for_api(paper_id)
    logger.info(f"{api_paper_id}의 신규 인용 확인 중...")
    url = f"https://api.semanticscholar.org/graph/v1/paper/{api_paper_id}/citations"
    params = {"fields": "title,abstract,year,url,isOpenAccess,externalIds,openAccessPdf", "limit": 50}
    
    data = await _fetch_data(session, url, params)
    return data.get('data', []) if data else []
