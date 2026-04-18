import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['SECRET_KEY'] = 'test-key'

from database import init_db
from app_objects import app

init_db()

with app.test_client() as client:
    # Try to access the main page (should redirect to login)
    resp = client.get('/', follow_redirects=True)
    print(f"Response: {resp.status_code}")
    if resp.status_code == 500:
        print(f"Data: {resp.data.decode()[:500]}")
