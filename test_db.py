# test_connection.py
import psycopg2

def test_connection():
    try:
        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            database="testDB",
            user="postgres",
            password="admin123"  # <-- Replace with your actual password
        )
        
        print("✅ Connected to PostgreSQL successfully!")
        
        with conn.cursor() as cur:
            cur.execute("SELECT version();")
            version = cur.fetchone()
            print(f"Version: {version[0]}")
            
            cur.execute("SELECT COUNT(*) FROM sao_student_profile;")
            count = cur.fetchone()
            print(f"Records in sao_student_profile: {count[0]}")
            
            cur.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'sao_student_profile';
            """)
            columns = cur.fetchall()
            print(f"Table columns: {len(columns)}")
            for col in columns:
                print(f"  - {col[0]}: {col[1]}")
        
        conn.close()
        print("✅ Connection closed")
        return True
        
    except psycopg2.Error as e:
        print(f"❌ Connection failed: {e}")
        return False

if __name__ == "__main__":
    test_connection()