
import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def get_db_connection(db_path='paper_agent.db'):
    """데이터베이스 연결을 생성하고 반환합니다."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_db(conn):
    """데이터베이스 테이블을 초기화합니다."""
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                paper_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                processed_at TIMESTAMP NOT NULL,
                retry_count INTEGER DEFAULT 0,
                error_message TEXT
            )
        """)
        logger.info("데이터베이스 테이블이 성공적으로 초기화되었습니다.")

def check_paper_status(conn, paper_id):
    """
    논문의 현재 상태를 확인합니다.
    반환값: 'processed', 'failed', 'not_found'
    """
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM history WHERE paper_id = ?", (paper_id,))
    result = cursor.fetchone()
    if result:
        return result['status']
    return 'not_found'

def add_paper_to_history(conn, paper_id, status='processed'):
    """처리된 논문을 히스토리에 추가하거나 상태를 업데이트합니다."""
    with conn:
        conn.execute("""
            INSERT INTO history (paper_id, status, processed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(paper_id) DO UPDATE SET
            status = excluded.status,
            processed_at = excluded.processed_at
        """, (paper_id, status, datetime.now()))
    logger.debug(f"'{paper_id}' 논문 상태를 '{status}'로 저장했습니다.")

def record_failure(conn, paper_id, error_message, max_retries=3):
    """
    논문 처리 실패를 기록하고, 재시도 횟수를 업데이트합니다.
    최대 재시도 횟수에 도달하면 'failed' 상태로 변경합니다.
    """
    with conn:
        cursor = conn.cursor()
        cursor.execute("SELECT retry_count FROM history WHERE paper_id = ?", (paper_id,))
        result = cursor.fetchone()
        
        current_retries = result['retry_count'] if result else 0
        new_retry_count = current_retries + 1
        
        status = 'pending'  # 아직은 재시도할 수 있는 상태
        if new_retry_count >= max_retries:
            status = 'failed' # 최대 재시도 도달, DLQ로 이동
            logger.warning(f"'{paper_id}' 논문이 최대 재시도 횟수({max_retries})에 도달하여 'failed' 처리됩니다.")

        conn.execute("""
            INSERT INTO history (paper_id, status, processed_at, retry_count, error_message)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(paper_id) DO UPDATE SET
            status = excluded.status,
            retry_count = excluded.retry_count,
            error_message = excluded.error_message,
            processed_at = excluded.processed_at
        """, (paper_id, status, datetime.now(), new_retry_count, error_message))
        
        if status != 'failed':
            logger.info(f"'{paper_id}' 논문 처리 실패 기록. 재시도 횟수: {new_retry_count}/{max_retries}")

def get_papers_to_process(conn, paper_ids):
    """
    주어진 논문 ID 목록에서 'processed' 또는 'failed' 상태가 아닌 ID들만 필터링하여 반환합니다.
    """
    if not paper_ids:
        return []
        
    placeholders = ','.join('?' for _ in paper_ids)
    query = f"SELECT paper_id FROM history WHERE paper_id IN ({placeholders}) AND status IN ('processed', 'failed')"
    
    cursor = conn.cursor()
    cursor.execute(query, paper_ids)
    
    processed_or_failed_ids = {row['paper_id'] for row in cursor.fetchall()}
    
    return [pid for pid in paper_ids if pid not in processed_or_failed_ids]

