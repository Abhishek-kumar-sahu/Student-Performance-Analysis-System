import sqlite3
import os

db_path = os.path.join("instance", "spas.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("--- student_registrations Schema ---")
cursor.execute("PRAGMA table_info(student_registrations)")
cols = cursor.fetchall()
for c in cols:
    print(c)

conn.close()
