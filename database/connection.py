import psycopg2
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD


def get_connection():
    try:
        connection = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            port=DB_PORT
        )

        print("Database connected successfully.")
        return connection

    except Exception as e:
        print("Database connection error:")
        print(e)
        return None


def get_server_version(conn) -> str:
    """
    Short PostgreSQL server version string, e.g. 'PostgreSQL 16.2'.
    Moved here from ui/pages/settings/about_tab.py — a UI page has no
    business opening its own cursor against the connection.
    """
    if not conn:
        return "Not connected"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            ver = cur.fetchone()[0]
        return ver.split(",")[0]
    except Exception:
        return "Connected"