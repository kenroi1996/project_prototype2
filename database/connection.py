import psycopg2
from database.db_config import DB_CONFIG


def get_connection():
    try:
        connection = psycopg2.connect(
            host=DB_CONFIG["host"],
            database=DB_CONFIG["database"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            port=DB_CONFIG["port"]
        )

        print("Database connected successfully.")
        return connection

    except Exception as e:
        print("Database connection error:")
        print(e)
        return None