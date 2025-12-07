import logging
import json
import google.generativeai as genai
from google.api_core import exceptions
from src import config

logger = logging.getLogger(__name__)

# Configure the Gemini API client
try:
    if not config.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
    genai.configure(api_key=config.GEMINI_API_KEY)
except ValueError as e:
    logger.critical(e)
    # Exit or handle gracefully if the key is missing
    # For this script, we'll let it raise an exception upon model generation
    pass

class GeminiAPIError(Exception):
    """Gemini API 호출 중 발생하는 사용자 정의 예외."""
    pass

async def _generate_content(model_name, prompt, is_json=False):
    """Gemini 모델을 호출하여 콘텐츠를 생성하는 비동기 래퍼 함수."""
    try:
        generation_config = {"response_mime_type": "application/json"} if is_json else {}
        model = genai.GenerativeModel(model_name, generation_config=generation_config)
        
        logger.debug(f"Gemini API 호출: {model_name}, JSON 모드: {is_json}")
        # The SDK's generate_content is not natively async, run it in an executor
        response = await model.generate_content_async(prompt)
        
        return response.text
    except exceptions.ResourceExhausted as e:
        logger.error(f"Gemini API 할당량(Quota) 초과: {e}")
        raise GeminiAPIError(f"API Quota Exceeded: {e}")
    except Exception as e:
        logger.error(f"Gemini API '{model_name}' 호출 중 오류: {e}")
        raise GeminiAPIError(f"API call failed: {e}")

async def organize_text_with_gemini(text: str) -> dict:
    """Gemini를 사용하여 텍스트를 구조화된 JSON으로 변환합니다."""
    logger.debug("Gemini로 텍스트 구조화 및 정제 중 (JSON 출력)...")
    prompt = f"""
    You are a paper analysis AI. Extract key sections from the following raw text and output them in the specified JSON format.

    [JSON Schema]
    {{
        "abstract": "Abstract content (or an empty string if not found)",
        "introduction": "Introduction and problem definition (or an empty string if not found)",
        "method": "Proposed methodology and architecture (or an empty string if not found)",
        "conclusion": "Conclusion and summary (or an empty string if not found)",
        "experiments": "Experiment results and comparisons (or an empty string if not found)"
    }}
    
    [Important Notes]
    1. Exclude sections like References, Appendix, and Acknowledgments.
    2. Extract original sentences as much as possible; do not summarize.
    3. Maintain the original language (e.g., English).
    4. The output must be valid JSON.

    [Raw Text]
    {text}
    """
    try:
        response_text = await _generate_content(config.STRUCTURING_MODEL_NAME, prompt, is_json=True)
        return json.loads(response_text)
    except (json.JSONDecodeError, GeminiAPIError) as e:
        logger.error(f"텍스트 구조화(JSON) 중 오류 발생: {e}")
        return {} # 실패 시 빈 딕셔너리 반환

async def first_pass_classify_with_abstract(target_paper, citing_paper_abstract):
    """【1단계-A】 API가 제공한 깨끗한 초록을 이용해 신속히 분류합니다."""
    logger.debug("1단계-A 분류 수행 (API 초록 기반)...")
    prompt_template = config.load_prompt(config.CLASSIFICATION_PROMPT_SIMPLE_FILE)
    if not prompt_template:
        raise GeminiAPIError("단순 초록 분류 프롬프트를 찾을 수 없습니다.")

    prompt = prompt_template.format(
        target_abstract=target_paper.get('abstract', ''),
        citing_paper_abstract=citing_paper_abstract
    )
    
    try:
        result = (await _generate_content(config.CLASSIFICATION_MODEL_NAME, prompt)).strip().upper()
        if "YES" in result:
            return "same_task"
        return "uncertain"
    except GeminiAPIError:
        return "uncertain" # API 오류 시 안전하게 '불확실'로 처리

async def first_pass_classify_with_snippet(target_paper, citing_paper_snippet):
    """【1단계-B】 Raw text 일부에서 초록을 추출하고 분류합니다."""
    logger.debug("1단계-B 분류 수행 (Raw Text 스니펫 기반)...")
    prompt_template = config.load_prompt(config.CLASSIFICATION_PROMPT_ABSTRACT_FILE)
    if not prompt_template:
        raise GeminiAPIError("스니펫 초록 분류 프롬프트를 찾을 수 없습니다.")

    prompt = prompt_template.format(
        target_abstract=target_paper.get('abstract', ''),
        citing_paper_snippet=citing_paper_snippet
    )

    try:
        result = (await _generate_content(config.CLASSIFICATION_MODEL_NAME, prompt)).strip().upper()
        if "YES" in result:
            return "same_task"
        return "uncertain"
    except GeminiAPIError:
        return "uncertain"

async def full_text_classify(target_paper, citing_paper, structured_text):
    """【2단계】 구조화된 전체 텍스트를 기반으로 최종 분류를 수행합니다."""
    logger.debug(f"2단계 분류 수행 (전체 텍스트 기반)...")
    prompt_template = config.load_prompt(config.CLASSIFICATION_PROMPT_FILE)
    if not prompt_template:
        raise GeminiAPIError("전체 텍스트 분류 프롬프트를 찾을 수 없습니다.")

    prompt = prompt_template.format(
        target_title=target_paper.get('title', ''),
        target_abstract=target_paper.get('abstract', ''),
        title=citing_paper.get('title', ''),
        abstract=citing_paper.get('abstract', '초록 정보 없음'),
        full_text=structured_text
    )
    
    try:
        result = (await _generate_content(config.CLASSIFICATION_MODEL_NAME, prompt)).strip().upper()
        return "same_task" if "YES" in result else "other"
    except GeminiAPIError:
        return "other" # API 오류 시 안전하게 '기타'로 처리

async def summarize_with_gemini(target_paper, citing_paper, full_text=None):
    """Gemini를 사용하여 논문을 요약합니다. full_text가 있으면 이를 우선적으로 사용합니다."""
    logger.info(f"'{citing_paper.get('title', 'N/A')}' 논문 요약 중...")
    prompt_template = config.load_prompt(config.SUMMARY_PROMPT_FILE)
    if not prompt_template:
        raise GeminiAPIError("요약 프롬프트를 찾을 수 없습니다.")

    # full_text가 None이면 안내 메시지 삽입
    full_text_content = full_text if full_text else "본문 텍스트가 제공되지 않았습니다. 초록을 기반으로 요약하세요."

    prompt = prompt_template.format(
        target_title=target_paper.get('title', ''),
        target_abstract=target_paper.get('abstract', ''),
        title=citing_paper.get('title', ''),
        abstract=citing_paper.get('abstract', '초록 정보 없음'),
        full_text=full_text_content
    )
    
    summary = await _generate_content(config.SUMMARIZATION_MODEL_NAME, prompt)
    return summary