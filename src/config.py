
import os
from dotenv import load_dotenv

# .env 파일에서 환경 변수 로드
load_dotenv()

# --- API Keys ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY")

# --- File and Directory Paths ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPERS_CONFIG_FILE = os.path.join(BASE_DIR, "papers.json")
DB_PATH = os.path.join(BASE_DIR, "paper_agent.db")
SUMMARY_DIR = os.path.join(BASE_DIR, "summaries")
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")

# --- Prompt File Paths ---
BASE_SUMMARY_PROMPT_FILE = os.path.join(PROMPTS_DIR, "base_summary_prompt.md")
SUMMARY_PROMPT_FILE = os.path.join(PROMPTS_DIR, "summary_prompt.md")
CLASSIFICATION_PROMPT_FILE = os.path.join(PROMPTS_DIR, "classification_prompt.md")
# For hierarchical classification
CLASSIFICATION_PROMPT_ABSTRACT_FILE = os.path.join(PROMPTS_DIR, "classification_prompt_abstract.md")
CLASSIFICATION_PROMPT_SIMPLE_FILE = os.path.join(PROMPTS_DIR, "classification_prompt_simple.md")


# --- Execution Settings ---
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", 3600))
MAX_CITATIONS_TO_PROCESS_PER_RUN = int(os.getenv("MAX_CITATIONS_TO_PROCESS_PER_RUN", 3))
MAX_RETRIES = int(os.getenv("MAX_API_RETRIES", 5))
INITIAL_RETRY_DELAY = int(os.getenv("INITIAL_RETRY_DELAY", 1))

# --- Model Names ---
# Model for structuring raw text and for classification
STRUCTURING_MODEL_NAME = os.getenv("STRUCTURING_MODEL", "gemini-2.5-flash")
CLASSIFICATION_MODEL_NAME = os.getenv("CLASSIFICATION_MODEL", "gemini-2.5-flash")
# More powerful model for summarization
SUMMARIZATION_MODEL_NAME = os.getenv("SUMMARIZATION_MODEL", "gemini-2.5-pro")

# --- PDF Processing ---
# Maximum characters to extract from a PDF to avoid excessive token usage
PDF_MAX_TEXT_LENGTH = 30000 
# Fallback text length if structuring fails
FALLBACK_TEXT_LENGTH = 20000

# --- User Agent for requests ---
REQUESTS_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

def load_prompt(prompt_file_path):
    """지정된 경로의 프롬프트 파일을 읽어서 반환합니다."""
    if not os.path.exists(prompt_file_path):
        # logging will be handled by the calling function
        return None
    with open(prompt_file_path, 'r', encoding='utf-8') as f:
        return f.read()
