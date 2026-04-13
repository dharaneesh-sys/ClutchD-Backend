import asyncio
import asyncpg
import os

async def test_db():
    try:
        conn = await asyncpg.connect("postgresql://clutchd:clutchD123@localhost:5432/clutchd")
        
        # Check notifications table
        try:
            records = await conn.fetch("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'notifications'")
            print("Notifications table columns:")
            for r in records:
                print(r['column_name'], r['data_type'])
                
            # Try to insert a dummy notification
            await conn.execute("""
                INSERT INTO notifications (id, user_id, title, body, type, read) 
                VALUES ('00000000-0000-0000-0000-000000000000', (SELECT id FROM users LIMIT 1), 'Test', 'Test Body', 'system', false)
            """)
            print("Insert into notifications successful")
        except Exception as e:
            print(f"Error querying notifications: {e}")
            
        await conn.close()
    except Exception as e:
        print(f"Connection error: {e}")

asyncio.run(test_db())
