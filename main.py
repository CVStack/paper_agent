
import asyncio
import logging
import sys
import time
from src.agent import run_cycle
from src.storage import database
from src import config

def setup_logging():
    """애플리케이션의 로깅 설정을 구성합니다."""
    # 로거가 중복으로 추가되는 것을 방지
    if logging.getLogger().hasHandlers():
        logging.getLogger().handlers.clear()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("agent.log", mode='a', encoding='utf-8')
        ]
    )
    # httpx와 같은 라이브러리의 과도한 로그 출력을 방지
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google.api_core").setLevel(logging.WARNING)


async def main():
    """메인 실행 함수."""
    setup_logging()
    
    # 데이터베이스 초기화
    try:
        conn = database.get_db_connection(config.DB_PATH)
        database.initialize_db(conn)
        conn.close()
    except Exception as e:
        logging.critical(f"데이터베이스 초기화 실패: {e}. 프로그램을 종료합니다.")
        return

    logging.info("Paper Agent 시작...")

    while True:
        try:
            await run_cycle()
        except Exception as e:
            logging.critical(f"처리 사이클 중 치명적 오류 발생: {e}", exc_info=True)
        
        logging.info(f"다음 사이클까지 {config.CHECK_INTERVAL_SECONDS}초 대기...")
        try:
            time.sleep(config.CHECK_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logging.info("사용자에 의해 프로그램이 중단되었습니다.")
            break

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("프로그램 실행이 중단되었습니다.")
    except Exception as e:
        logging.critical(f"예상치 못한 최상위 오류 발생: {e}", exc_info=True)

